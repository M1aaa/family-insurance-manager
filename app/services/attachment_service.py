"""电子保单附件服务（v1.2）。

职责：附件的**落盘 / 校验 / 列表 / 删除 / 计数**。三条安全红线：

1. **落盘文件名由服务端生成**（``uuid4().hex + ".pdf"``），客户端文件名
   **绝不参与拼路径**，防止 ``../../`` 路径穿越；
2. **以文件头（magic bytes ``%PDF``）判定类型**，``content-type`` 仅记录不作依据；
   空文件同样拒绝；
3. **流式读取、边读边累计大小**（``CHUNK_SIZE`` 分块），超过上限立即中止，
   不把整个文件先读进内存。

其他契约：

- **all-or-nothing**：多文件上传时任一文件失败 → 删除本次已写入的所有文件、
  **不提交任何 DB 行**，再抛错；
- **数量前置校验**：上传前先校验「现有数 + 新增数 ≤ 上限」，超限时**尚未写任何文件**；
- **删除顺序**：**先删 DB 行并 commit，再 best-effort 删磁盘文件**（孤儿文件无害，
  悬挂记录有害）；文件删除失败只记 WARNING，不影响接口返回。
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import AppError
from ..models import Policy, PolicyAttachment, new_attachment_id

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免运行时强依赖
    from fastapi import UploadFile

logger = logging.getLogger(__name__)

#: PDF 文件头（校验首块）
PDF_MAGIC = b"%PDF"
#: 流式读取块大小（1 MiB）
CHUNK_SIZE = 1024 * 1024
#: 入库文件名的最大长度
_MAX_FILE_NAME_LEN = 255
#: 落盘文件扩展名
_STORED_SUFFIX = ".pdf"


def _safe_file_name(raw: Optional[str]) -> str:
    """把客户端文件名清洗为可安全入库 / 展示的原始文件名。

    仅保留 basename，剔除控制字符（含 ``\\r`` / ``\\n``），截断到 255 字符。
    """
    name = os.path.basename(raw or "")
    # 剔除所有 C0 控制字符与 DEL（含 \r \n \t）
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    name = name.strip()
    if not name:
        name = "attachment.pdf"
    return name[:_MAX_FILE_NAME_LEN]


def _safe_unlink(path: str) -> None:
    """best-effort 删除磁盘文件；失败仅记 WARNING。"""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("删除附件文件失败，已忽略：%s", path, exc_info=True)


def remove_stored_files(stored_names: list[str]) -> None:
    """按 ``stored_name`` 列表 best-effort 删除磁盘文件（供级联删除调用方使用）。"""
    for name in stored_names:
        _safe_unlink(os.path.join(settings.attachments_dir, name))


def count_by_policy(db: Session, policy_id: str) -> int:
    """统计某保单的附件数。"""
    return int(
        db.scalar(
            select(func.count())
            .select_from(PolicyAttachment)
            .where(PolicyAttachment.policy_id == policy_id)
        )
        or 0
    )


def counts_by_policy_ids(db: Session, policy_ids: list[str]) -> dict[str, int]:
    """一次 ``GROUP BY`` 统计多张保单的附件数（避免 N+1）。

    Returns:
        ``{policy_id: count}``；无附件的保单不出现在结果里。
    """
    if not policy_ids:
        return {}
    rows = db.execute(
        select(PolicyAttachment.policy_id, func.count())
        .where(PolicyAttachment.policy_id.in_(list(policy_ids)))
        .group_by(PolicyAttachment.policy_id)
    ).all()
    return {policy_id: int(cnt) for policy_id, cnt in rows}


def list_by_policy(db: Session, policy_id: str) -> list[PolicyAttachment]:
    """按 ``uploaded_at ASC, id ASC`` 返回某保单的附件 ORM 行。"""
    return list(
        db.scalars(
            select(PolicyAttachment)
            .where(PolicyAttachment.policy_id == policy_id)
            .order_by(PolicyAttachment.uploaded_at.asc(), PolicyAttachment.id.asc())
        ).all()
    )


def delete_attachments_for_policy(db: Session, policy_id: str) -> list[str]:
    """取出某保单**全部附件的 ``stored_name`` 列表**（供级联删除后 best-effort 删文件）。

    注意：本函数**只读取** ``stored_name``，**不删除 DB 行**——附件行由删除保单时的
    ``FK ON DELETE CASCADE`` 自动连带删除（见 ARCHITECTURE 6.5.3 时序图）。
    磁盘文件的删除由调用方在 DB 提交后 best-effort 完成。
    """
    return [row.stored_name for row in list_by_policy(db, policy_id)]


def _load_policy(db: Session, policy_id: str) -> Policy:
    """加载保单，不存在则抛 404。"""
    policy = db.get(Policy, policy_id)
    if policy is None:
        raise AppError(404, "NOT_FOUND", f"保单不存在：{policy_id}", {"policyId": policy_id})
    return policy


def _stream_to_disk(upload: "UploadFile", dest_path: str) -> tuple[int, bool, bool]:
    """流式写入单个上传文件到 ``dest_path``。

    Args:
        upload: FastAPI ``UploadFile``（使用其底层同步文件对象分块读取）。
        dest_path: 服务端生成的落盘绝对 / 相对路径。

    Returns:
        ``(size, magic_ok, too_large)``：
        - ``size``：累计字节数；
        - ``magic_ok``：首块是否以 ``PDF_MAGIC`` 开头；
        - ``too_large``：是否超过 ``settings.max_attachment_size``。
    """
    size = 0
    first_chunk = True
    magic_ok = False
    too_large = False
    # 使用底层同步文件对象，避免在同步服务里 await
    source = upload.file
    try:
        source.seek(0)
    except (OSError, ValueError):  # pragma: no cover - 极端情况下不支持 seek
        pass
    with open(dest_path, "wb") as out:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            if first_chunk:
                magic_ok = chunk.startswith(PDF_MAGIC)
                first_chunk = False
            size += len(chunk)
            if size > settings.max_attachment_size:
                too_large = True
                break
            out.write(chunk)
    return size, magic_ok, too_large


def save_attachments(db: Session, policy_id: str, files: list["UploadFile"]) -> dict:
    """上传并挂载若干 PDF 附件（all-or-nothing）。

    Raises:
        AppError: 404 保单不存在；409 超数量上限；413 单文件过大；422 非 PDF / 空文件。
    """
    _load_policy(db, policy_id)

    if not files:
        raise AppError(
            422, "INVALID_FILE_TYPE", "未提供任何文件", {"policyId": policy_id}
        )

    existing = count_by_policy(db, policy_id)
    if existing + len(files) > settings.max_attachments_per_policy:
        raise AppError(
            409,
            "ATTACHMENT_LIMIT_EXCEEDED",
            (
                f"附件数量超过上限（每张保单最多 {settings.max_attachments_per_policy} 个，"
                f"当前已有 {existing} 个，本次新增 {len(files)} 个）"
            ),
            {
                "policyId": policy_id,
                "existing": existing,
                "incoming": len(files),
                "limit": settings.max_attachments_per_policy,
            },
        )

    written_paths: list[str] = []
    pending: list[tuple[str, str, int, str]] = []  # (stored_name, file_name, size, content_type)

    try:
        for upload in files:
            stored_name = uuid.uuid4().hex + _STORED_SUFFIX
            dest_path = os.path.join(settings.attachments_dir, stored_name)
            size, magic_ok, too_large = _stream_to_disk(upload, dest_path)
            if too_large:
                _safe_unlink(dest_path)
                raise AppError(
                    413,
                    "FILE_TOO_LARGE",
                    (
                        f"单个文件超过大小上限"
                        f"（{settings.max_attachment_size // (1024 * 1024)}MB）："
                        f"{_safe_file_name(upload.filename)}"
                    ),
                    {
                        "fileName": _safe_file_name(upload.filename),
                        "limit": settings.max_attachment_size,
                    },
                )
            if size == 0 or not magic_ok:
                _safe_unlink(dest_path)
                raise AppError(
                    422,
                    "INVALID_FILE_TYPE",
                    f"仅支持 PDF 文件：{_safe_file_name(upload.filename)}",
                    {"fileName": _safe_file_name(upload.filename)},
                )
            written_paths.append(dest_path)
            pending.append(
                (
                    stored_name,
                    _safe_file_name(upload.filename),
                    size,
                    upload.content_type or "application/pdf",
                )
            )
    except BaseException:
        # all-or-nothing：清掉本次已写入的所有文件，不提交任何 DB 行
        for path in written_paths:
            _safe_unlink(path)
        raise

    rows: list[PolicyAttachment] = []
    for stored_name, file_name, size, content_type in pending:
        row = PolicyAttachment(
            id=new_attachment_id(),
            policy_id=policy_id,
            file_name=file_name,
            stored_name=stored_name,
            file_size=size,
            content_type=content_type,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)

    return {
        "uploaded": [_row_to_dict(row) for row in rows],
        "attachment_count": count_by_policy(db, policy_id),
    }


def list_attachments(db: Session, policy_id: str) -> dict:
    """返回某保单的附件列表（含计数）。保单不存在 → 404。"""
    _load_policy(db, policy_id)
    rows = list_by_policy(db, policy_id)
    return {
        "policy_id": policy_id,
        "attachment_count": len(rows),
        "attachments": [_row_to_dict(row) for row in rows],
    }


def resolve_file(db: Session, policy_id: str, attachment_id: str) -> tuple[PolicyAttachment, str]:
    """定位附件记录与磁盘路径。

    附件不存在 / 不属于该保单 / **磁盘文件缺失** 一律抛 404 ``ATTACHMENT_NOT_FOUND``。

    Returns:
        ``(row, path)``。
    """
    row = db.get(PolicyAttachment, attachment_id)
    if row is None or row.policy_id != policy_id:
        raise AppError(
            404,
            "ATTACHMENT_NOT_FOUND",
            f"附件不存在：{attachment_id}",
            {"policyId": policy_id, "attachmentId": attachment_id},
        )
    path = os.path.join(settings.attachments_dir, row.stored_name)
    if not os.path.exists(path):
        raise AppError(
            404,
            "ATTACHMENT_NOT_FOUND",
            f"附件文件缺失：{row.file_name}",
            {"policyId": policy_id, "attachmentId": attachment_id},
        )
    return row, path


def delete_attachment(db: Session, policy_id: str, attachment_id: str) -> dict:
    """删除单个附件：**先删 DB 行并 commit，再 best-effort 删磁盘文件**。"""
    row = db.get(PolicyAttachment, attachment_id)
    if row is None or row.policy_id != policy_id:
        raise AppError(
            404,
            "ATTACHMENT_NOT_FOUND",
            f"附件不存在：{attachment_id}",
            {"policyId": policy_id, "attachmentId": attachment_id},
        )
    stored_name = row.stored_name
    db.delete(row)
    db.commit()
    _safe_unlink(os.path.join(settings.attachments_dir, stored_name))
    return {"deleted": True, "id": attachment_id}


def _row_to_dict(row: PolicyAttachment) -> dict:
    """ORM 行 → 附件响应字典（键与 ``AttachmentOut`` 的 snake_case 字段对应）。"""
    return {
        "id": row.id,
        "file_name": row.file_name,
        "file_size": row.file_size,
        "content_type": row.content_type,
        "uploaded_at": row.uploaded_at,
    }


__all__ = [
    "PDF_MAGIC",
    "CHUNK_SIZE",
    "count_by_policy",
    "counts_by_policy_ids",
    "list_by_policy",
    "delete_attachments_for_policy",
    "remove_stored_files",
    "save_attachments",
    "list_attachments",
    "resolve_file",
    "delete_attachment",
]
