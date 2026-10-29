"""QA 独立验证 · v1.2 附件安全与生命周期（第 2 组）。

覆盖：
- 路径穿越（``../../``、``..\\..\\``、绝对路径、盘符路径、控制字符文件名）；
- magic bytes（假 PDF 拒绝 / 真 PDF 改名 .txt 接受）；
- 空文件 422；
- 流式限流 413 且不留半截文件；
- all-or-nothing（多文件中第 2 个非法 → 第 1 个也不落盘、DB 无行）；
- 删除顺序（先提交 DB 再 best-effort 删文件；磁盘文件缺失 → 404）；
- Content-Disposition（RFC 5987 中文名 + 无 CRLF 注入）；
- 数量上限边界（恰好 10 通过 / 第 11 个 409 且不写文件）；
- CASCADE（删保单 → 附件行与磁盘文件均清理）；
- 同名 / 超长 / Unicode 文件名；越权访问。

所有文件系统操作使用 ``tmp_path`` 覆盖 ``settings.attachments_dir``，绝不触碰真实 ``data/``。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import PolicyAttachment

_UUID_STORED = re.compile(r"^[0-9a-f]{32}\.pdf$")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _pdf(payload: bytes = b"1 0 obj<<>>endobj\n") -> bytes:
    """构造一个以 ``%PDF`` 开头的最小合法 PDF 字节串。"""
    return b"%PDF-1.4\n" + payload + b"\n%%EOF\n"


def _member(client, name: str = "张三") -> dict:
    resp = client.post(
        "/api/members",
        json={"name": name, "relation": "本人", "birthDate": "1990-01-01", "gender": "男"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _policy(client, holder_id: str) -> dict:
    resp = client.post(
        "/api/policies",
        json={
            "company": "众安",
            "insuranceType": "医疗险",
            "productName": "测试产品",
            "policyholderId": holder_id,
            "insuredId": holder_id,
            "effectiveDate": "2024-01-01",
            "coverageTermType": "固定年限",
            "coverageTermValue": 30,
            "paymentTermType": "固定年限",
            "paymentTermValue": 10,
            "paymentFrequency": "年缴",
            "premiumPerPeriod": 8000,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload(client, policy_id: str, files):
    """files: list[(filename, content_bytes, content_type)]。"""
    payload = [("files", (name, content, ctype)) for name, content, ctype in files]
    return client.post(f"/api/policies/{policy_id}/attachments", files=payload)


def _files_under(root: Path) -> set[str]:
    return {str(p) for p in root.rglob("*") if p.is_file()}


def _dir_files(d: Path) -> set[str]:
    return {str(p) for p in d.iterdir() if p.is_file()}


# ---------------------------------------------------------------------------
# 路径穿越
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "evil",
    [
        "../../evil.pdf",
        "..\\..\\evil.pdf",
        "/etc/passwd",
        "C:\\Windows\\evil.pdf",
        "....//....//evil.pdf",
        "sub/dir/evil.pdf",
    ],
)
def test_path_traversal_blocked(client, attachments_dir, evil):
    holder = _member(client)
    p = _policy(client, holder["id"])
    # 扫描两层父目录，足以捕获任何向上逃逸
    scan_root = attachments_dir.parent.parent
    before = _files_under(scan_root)

    resp = _upload(client, p["id"], [(evil, _pdf(), "application/pdf")])
    assert resp.status_code == 201, resp.text

    new_files = _files_under(scan_root) - before
    assert new_files, "应恰有 1 个落盘文件"
    for f in new_files:
        assert Path(f).parent == attachments_dir, f"文件逃逸到目录外：{f}"
        assert _UUID_STORED.match(Path(f).name), f"落盘名非服务端 uuid：{f}"

    # DB 中记录的原始文件名已剥离路径分隔符
    lst = client.get(f"/api/policies/{p['id']}/attachments").json()
    assert lst["attachmentCount"] == 1
    file_name = lst["attachments"][0]["fileName"]
    assert "/" not in file_name and "\\" not in file_name
    assert "evil.pdf" not in file_name or file_name == "evil.pdf"


def test_control_char_filenames_sanitized(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    scan_root = attachments_dir.parent.parent
    before = _files_under(scan_root)

    for name in ["evil\r\nInjected.pdf", "a\x00b.pdf", "\r\n.pdf", "  ..\\..\\x.pdf  "]:
        try:
            resp = _upload(client, p["id"], [(name, _pdf(), "application/pdf")])
        except Exception:  # noqa: BLE001 - 客户端 / 框架层直接拒绝亦可接受
            continue
        assert resp.status_code in (201, 400, 422), (name, resp.status_code, resp.text)

    new_files = _files_under(scan_root) - before
    for f in new_files:
        assert Path(f).parent == attachments_dir
        assert _UUID_STORED.match(Path(f).name)
        assert "\r" not in Path(f).name and "\n" not in Path(f).name
    # DB 中的 file_name 也不得含控制字符
    lst = client.get(f"/api/policies/{p['id']}/attachments").json()
    for item in lst["attachments"]:
        assert not any(ord(c) < 32 for c in item["fileName"])


# ---------------------------------------------------------------------------
# magic bytes
# ---------------------------------------------------------------------------
def test_fake_pdf_rejected_by_magic_bytes(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    resp = _upload(client, p["id"], [("fake.pdf", b"hello, not a pdf at all", "application/pdf")])
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_FILE_TYPE"
    assert _dir_files(attachments_dir) == set()


def test_real_pdf_named_txt_accepted_by_magic_bytes(client, attachments_dir):
    """真 PDF 改名 .txt：按设计**以文件头为准**，应成功。"""
    holder = _member(client)
    p = _policy(client, holder["id"])
    resp = _upload(client, p["id"], [("real.txt", _pdf(), "text/plain")])
    assert resp.status_code == 201, resp.text
    assert resp.json()["attachmentCount"] == 1
    assert len(_dir_files(attachments_dir)) == 1


def test_empty_file_rejected(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    resp = _upload(client, p["id"], [("empty.pdf", b"", "application/pdf")])
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_FILE_TYPE"
    assert _dir_files(attachments_dir) == set()


# ---------------------------------------------------------------------------
# 流式限流
# ---------------------------------------------------------------------------
def test_size_limit_413_leaves_no_partial_file(client, attachments_dir, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_size", 1024, raising=False)
    holder = _member(client)
    p = _policy(client, holder["id"])
    big = b"%PDF" + b"x" * 5000
    resp = _upload(client, p["id"], [("big.pdf", big, "application/pdf")])
    assert resp.status_code == 413, resp.text
    assert resp.json()["code"] == "FILE_TOO_LARGE"
    assert _dir_files(attachments_dir) == set(), "413 后不应残留半截文件"


# ---------------------------------------------------------------------------
# all-or-nothing
# ---------------------------------------------------------------------------
def test_all_or_nothing_when_second_file_invalid(client, attachments_dir, db_session):
    holder = _member(client)
    p = _policy(client, holder["id"])
    files = [
        ("good.pdf", _pdf(), "application/pdf"),
        ("bad.pdf", b"not a pdf", "application/pdf"),
    ]
    resp = _upload(client, p["id"], files)
    assert resp.status_code == 422, resp.text
    assert _dir_files(attachments_dir) == set(), "第 1 个合法文件也应被回滚清理"
    count = db_session.execute(select(func.count()).select_from(PolicyAttachment)).scalar()
    assert count == 0, "all-or-nothing 失败时不得写任何 DB 行"


def test_all_or_nothing_when_second_file_too_large(client, attachments_dir, db_session, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_size", 1024, raising=False)
    holder = _member(client)
    p = _policy(client, holder["id"])
    files = [
        ("good.pdf", _pdf(), "application/pdf"),
        ("big.pdf", b"%PDF" + b"y" * 5000, "application/pdf"),
    ]
    resp = _upload(client, p["id"], files)
    assert resp.status_code == 413, resp.text
    assert _dir_files(attachments_dir) == set()
    assert db_session.execute(select(func.count()).select_from(PolicyAttachment)).scalar() == 0


# ---------------------------------------------------------------------------
# 删除顺序 / 文件缺失
# ---------------------------------------------------------------------------
def test_content_404_when_disk_file_manually_removed(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    up = _upload(client, p["id"], [("a.pdf", _pdf(), "application/pdf")]).json()
    aid = up["uploaded"][0]["id"]
    # 手工删掉磁盘文件
    for f in attachments_dir.iterdir():
        f.unlink()
    resp = client.get(f"/api/policies/{p['id']}/attachments/{aid}/content")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "ATTACHMENT_NOT_FOUND"


def test_delete_commits_db_even_if_unlink_fails(client, attachments_dir, db_session, monkeypatch):
    """先提交 DB 再 best-effort 删文件：删文件失败不影响 200，DB 行仍被删。"""
    holder = _member(client)
    p = _policy(client, holder["id"])
    up = _upload(client, p["id"], [("a.pdf", _pdf(), "application/pdf")]).json()
    aid = up["uploaded"][0]["id"]

    import app.services.attachment_service as svc

    def _boom(_path):
        raise OSError("simulated unlink failure")

    monkeypatch.setattr(svc.os, "remove", _boom)
    resp = client.delete(f"/api/policies/{p['id']}/attachments/{aid}")
    assert resp.status_code == 200, resp.text
    assert db_session.get(PolicyAttachment, aid) is None, "DB 行应已删除（先提交 DB）"


def test_attachment_not_found_cases(client, attachments_dir):
    holder = _member(client)
    p1 = _policy(client, holder["id"])
    p2 = _policy(client, holder["id"])
    up = _upload(client, p1["id"], [("a.pdf", _pdf(), "application/pdf")]).json()
    aid = up["uploaded"][0]["id"]

    # 附件不存在
    r = client.get(f"/api/policies/{p1['id']}/attachments/a_notexist/content")
    assert r.status_code == 404 and r.json()["code"] == "ATTACHMENT_NOT_FOUND"
    # 附件不属于该保单
    r = client.get(f"/api/policies/{p2['id']}/attachments/{aid}/content")
    assert r.status_code == 404 and r.json()["code"] == "ATTACHMENT_NOT_FOUND"
    # 删除不属于该保单的附件
    r = client.delete(f"/api/policies/{p2['id']}/attachments/{aid}")
    assert r.status_code == 404


def test_list_and_upload_on_missing_policy_404(client, attachments_dir):
    r = client.get("/api/policies/p_nope/attachments")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    r = _upload(client, "p_nope", [("a.pdf", _pdf(), "application/pdf")])
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Content-Disposition
# ---------------------------------------------------------------------------
def test_content_disposition_rfc5987_chinese(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    name = "保单_电子合同.pdf"
    up = _upload(client, p["id"], [(name, _pdf(), "application/pdf")]).json()
    aid = up["uploaded"][0]["id"]

    resp = client.get(
        f"/api/policies/{p['id']}/attachments/{aid}/content",
        params={"disposition": "attachment"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd
    assert "filename*=utf-8''" in cd.lower(), cd
    assert "%E4%BF%9D" in cd  # 「保」的 UTF-8 百分号编码
    assert "\r" not in cd and "\n" not in cd, "响应头不得含 CRLF（防注入）"
    # 内容往返一致
    assert resp.content == _pdf()


def test_inline_disposition_default(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    up = _upload(client, p["id"], [("a.pdf", _pdf(), "application/pdf")]).json()
    aid = up["uploaded"][0]["id"]
    resp = client.get(f"/api/policies/{p['id']}/attachments/{aid}/content")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("inline")


# ---------------------------------------------------------------------------
# 数量上限边界
# ---------------------------------------------------------------------------
def test_attachment_count_limit_boundary(client, attachments_dir):
    """默认上限 10：恰好 10 个通过，第 11 个 409 且不写任何文件。"""
    holder = _member(client)
    p = _policy(client, holder["id"])
    files = [(f"f{i}.pdf", _pdf(), "application/pdf") for i in range(10)]
    resp = _upload(client, p["id"], files)
    assert resp.status_code == 201, resp.text
    assert resp.json()["attachmentCount"] == 10
    assert len(_dir_files(attachments_dir)) == 10

    before = _dir_files(attachments_dir)
    resp2 = _upload(client, p["id"], [("f10.pdf", _pdf(), "application/pdf")])
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["code"] == "ATTACHMENT_LIMIT_EXCEEDED"
    assert _dir_files(attachments_dir) == before, "超限时不得写入任何文件"


def test_attachment_limit_precheck_batch(client, attachments_dir, monkeypatch):
    """一次批量上传使总数超限 → 409，且一个文件都不写。"""
    monkeypatch.setattr(settings, "max_attachments_per_policy", 3, raising=False)
    holder = _member(client)
    p = _policy(client, holder["id"])
    resp = _upload(client, p["id"], [(f"f{i}.pdf", _pdf(), "application/pdf") for i in range(4)])
    assert resp.status_code == 409, resp.text
    assert _dir_files(attachments_dir) == set()


# ---------------------------------------------------------------------------
# CASCADE
# ---------------------------------------------------------------------------
def test_delete_policy_cascades_attachments(client, attachments_dir, db_session):
    holder = _member(client)
    p = _policy(client, holder["id"])
    _upload(client, p["id"], [("a.pdf", _pdf(), "application/pdf"), ("b.pdf", _pdf(), "application/pdf")])
    assert len(_dir_files(attachments_dir)) == 2

    resp = client.delete(f"/api/policies/{p['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deletedAttachments"] == 2
    remaining = db_session.execute(
        select(func.count()).select_from(PolicyAttachment).where(PolicyAttachment.policy_id == p["id"])
    ).scalar()
    assert remaining == 0, "CASCADE 后不应残留附件行"
    assert _dir_files(attachments_dir) == set(), "磁盘文件应被 best-effort 清理"


# ---------------------------------------------------------------------------
# 其他边界
# ---------------------------------------------------------------------------
def test_same_filename_uploaded_twice_is_independent(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    a = _upload(client, p["id"], [("same.pdf", _pdf(b"aaa"), "application/pdf")]).json()
    b = _upload(client, p["id"], [("same.pdf", _pdf(b"bbb"), "application/pdf")]).json()
    assert a["uploaded"][0]["id"] != b["uploaded"][0]["id"]
    assert a["attachmentCount"] == 1 and b["attachmentCount"] == 2
    assert len(_dir_files(attachments_dir)) == 2  # stored_name 不冲突


def test_long_filename_truncated_to_255(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    long_name = "x" * 300 + ".pdf"
    resp = _upload(client, p["id"], [(long_name, _pdf(), "application/pdf")])
    assert resp.status_code == 201, resp.text
    file_name = resp.json()["uploaded"][0]["fileName"]
    assert len(file_name) <= 255


def test_unicode_filename_roundtrip(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    name = "家庭保单·2024年·重疾险.pdf"
    up = _upload(client, p["id"], [(name, _pdf(), "application/pdf")]).json()
    assert up["uploaded"][0]["fileName"] == name
    lst = client.get(f"/api/policies/{p['id']}/attachments").json()
    assert lst["attachments"][0]["fileName"] == name
    assert lst["attachmentCount"] == 1


def test_uploaded_at_present_and_ordering(client, attachments_dir):
    holder = _member(client)
    p = _policy(client, holder["id"])
    _upload(client, p["id"], [("a.pdf", _pdf(b"1"), "application/pdf")])
    _upload(client, p["id"], [("b.pdf", _pdf(b"2"), "application/pdf")])
    lst = client.get(f"/api/policies/{p['id']}/attachments").json()
    names = [x["fileName"] for x in lst["attachments"]]
    assert names == ["a.pdf", "b.pdf"], "应按 uploaded_at ASC, id ASC 排序"
    for item in lst["attachments"]:
        assert item["uploadedAt"]
        assert isinstance(item["fileSize"], int) and item["fileSize"] > 0
