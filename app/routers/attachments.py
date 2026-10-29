"""附件路由（``/api/policies/{policy_id}/attachments``）。

4 个接口：上传（多选）/ 列表 / 查看下载 / 删除。
错误码见 ARCHITECTURE 6.3：``NOT_FOUND`` / ``ATTACHMENT_LIMIT_EXCEEDED`` /
``FILE_TOO_LARGE`` / ``INVALID_FILE_TYPE`` / ``ATTACHMENT_NOT_FOUND``。
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas import AttachmentListOut, AttachmentUploadOut
from ..services import attachment_service

router = APIRouter(prefix="/api/policies/{policy_id}/attachments", tags=["attachments"])


@router.post("", response_model=AttachmentUploadOut, status_code=201)
def upload_attachments(
    policy_id: str,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> AttachmentUploadOut:
    """上传并挂载一个或多个 PDF 附件（all-or-nothing）。"""
    return attachment_service.save_attachments(db, policy_id, files)


@router.get("", response_model=AttachmentListOut)
def list_attachments(policy_id: str, db: Session = Depends(get_db)) -> AttachmentListOut:
    """返回该保单的附件列表（``uploaded_at ASC, id ASC``）。"""
    return attachment_service.list_attachments(db, policy_id)


@router.get("/{attachment_id}/content")
def attachment_content(
    policy_id: str,
    attachment_id: str,
    disposition: str = Query("inline", alias="disposition"),
    db: Session = Depends(get_db),
) -> FileResponse:
    """在线查看（``inline``，默认）或下载（``attachment``）附件内容。"""
    row, path = attachment_service.resolve_file(db, policy_id, attachment_id)
    disposition_type = "attachment" if disposition == "attachment" else "inline"
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=row.file_name,
        content_disposition_type=disposition_type,
    )


@router.delete("/{attachment_id}")
def delete_attachment(
    policy_id: str,
    attachment_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """删除附件（先删 DB 行，再 best-effort 删磁盘文件）。"""
    return attachment_service.delete_attachment(db, policy_id, attachment_id)


__all__ = ["router"]
