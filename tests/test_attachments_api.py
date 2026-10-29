"""v1.2 附件接口测试（含路径穿越等安全回归用例）。"""

from __future__ import annotations

import itertools
import os

PDF_BYTES = b"%PDF-1.4\n%fake pdf content for testing\n%%EOF"

_counter = itertools.count(1)


def _member(client, name: str = "张三", relation: str = "本人") -> dict:
    resp = client.post(
        "/api/members",
        json={"name": name, "relation": relation, "birthDate": "1990-01-01", "gender": "男"},
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


def _new_policy_id(client) -> str:
    """新建一个成员（唯一名字 + 非「本人」，避免主成员唯一约束）并建一张保单。"""
    n = next(_counter)
    member = _member(client, name=f"成员{n}", relation="子女")
    return _policy(client, member["id"])["id"]


def _upload(client, pid: str, files):
    return client.post(f"/api/policies/{pid}/attachments", files=files)


def _list(client, pid: str) -> dict:
    resp = client.get(f"/api/policies/{pid}/attachments")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 上传 / 列表 / 计数
# ---------------------------------------------------------------------------
def test_upload_success_and_count(client, attachments_dir):
    pid = _new_policy_id(client)
    resp = _upload(
        client,
        pid,
        [("files", ("小圆满医疗险_电子保单.pdf", PDF_BYTES, "application/pdf"))],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["attachmentCount"] == 1
    assert body["uploaded"][0]["fileName"] == "小圆满医疗险_电子保单.pdf"
    assert body["uploaded"][0]["fileSize"] == len(PDF_BYTES)
    assert body["uploaded"][0]["contentType"] == "application/pdf"

    listed = _list(client, pid)
    assert listed["attachmentCount"] == 1
    assert listed["attachments"][0]["id"] == body["uploaded"][0]["id"]

    # 磁盘上确有 1 个文件，且为服务端生成的 uuid.pdf
    files = list(attachments_dir.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".pdf"


def test_upload_multiple_files(client, attachments_dir):
    pid = _new_policy_id(client)
    resp = _upload(
        client,
        pid,
        [
            ("files", ("a.pdf", PDF_BYTES, "application/pdf")),
            ("files", ("b.pdf", PDF_BYTES + b"2", "application/pdf")),
        ],
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["attachmentCount"] == 2
    assert len(list(attachments_dir.iterdir())) == 2


def test_upload_non_pdf_422(client, attachments_dir):
    pid = _new_policy_id(client)
    resp = _upload(client, pid, [("files", ("note.txt", b"not a pdf", "text/plain"))])
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_FILE_TYPE"
    assert len(list(attachments_dir.iterdir())) == 0


def test_upload_fake_pdf_422(client, attachments_dir):
    """扩展名是 .pdf 但内容不是 PDF → 422。"""
    pid = _new_policy_id(client)
    resp = _upload(client, pid, [("files", ("fake.pdf", b"NOTPDF content", "application/pdf"))])
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_FILE_TYPE"
    assert len(list(attachments_dir.iterdir())) == 0


def test_upload_empty_file_422(client, attachments_dir):
    pid = _new_policy_id(client)
    resp = _upload(client, pid, [("files", ("empty.pdf", b"", "application/pdf"))])
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_FILE_TYPE"


def test_upload_too_large_413(client, attachments_dir, small_attachment_size):
    pid = _new_policy_id(client)
    big = b"%PDF" + b"x" * (small_attachment_size + 100)
    resp = _upload(client, pid, [("files", ("big.pdf", big, "application/pdf"))])
    assert resp.status_code == 413
    assert resp.json()["code"] == "FILE_TOO_LARGE"
    # 超限文件不应残留
    assert len(list(attachments_dir.iterdir())) == 0


def test_upload_limit_exceeded_409(client, attachments_dir, small_attachment_limit):
    pid = _new_policy_id(client)
    # 先传满上限（2 个）
    ok = _upload(
        client,
        pid,
        [
            ("files", ("a.pdf", PDF_BYTES, "application/pdf")),
            ("files", ("b.pdf", PDF_BYTES, "application/pdf")),
        ],
    )
    assert ok.status_code == 201
    # 再传 1 个 → 409，且不写任何文件
    resp = _upload(client, pid, [("files", ("c.pdf", PDF_BYTES, "application/pdf"))])
    assert resp.status_code == 409
    assert resp.json()["code"] == "ATTACHMENT_LIMIT_EXCEEDED"
    assert len(list(attachments_dir.iterdir())) == 2


def test_upload_policy_not_found_404(client, attachments_dir):
    resp = _upload(client, "p_not_exist", [("files", ("a.pdf", PDF_BYTES, "application/pdf"))])
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 安全：路径穿越
# ---------------------------------------------------------------------------
def test_upload_path_traversal_blocked(client, attachments_dir):
    """客户端文件名含 ``../`` 不得影响落盘路径（安全回归）。"""
    pid = _new_policy_id(client)
    resp = _upload(
        client,
        pid,
        [("files", ("../../evil.pdf", PDF_BYTES, "application/pdf"))],
    )
    assert resp.status_code == 201, resp.text
    # 入库的展示名应为 basename
    assert resp.json()["uploaded"][0]["fileName"] == "evil.pdf"

    # 落盘目录内只有服务端生成的 uuid.pdf，没有 evil.pdf
    stored = list(attachments_dir.iterdir())
    assert len(stored) == 1
    assert stored[0].name != "evil.pdf"
    assert stored[0].suffix == ".pdf"
    # 目录外（父目录）也不应出现 evil.pdf
    assert not (attachments_dir.parent / "evil.pdf").exists()
    assert not (attachments_dir.parent.parent / "evil.pdf").exists()


# ---------------------------------------------------------------------------
# 查看 / 下载
# ---------------------------------------------------------------------------
def test_content_inline_and_attachment_disposition(client, attachments_dir):
    pid = _new_policy_id(client)
    att = _upload(
        client,
        pid,
        [("files", ("电子保单.pdf", PDF_BYTES, "application/pdf"))],
    ).json()["uploaded"][0]

    inline = client.get(f"/api/policies/{pid}/attachments/{att['id']}/content?disposition=inline")
    assert inline.status_code == 200
    cd_inline = inline.headers["content-disposition"]
    assert cd_inline.startswith("inline")
    # Starlette 输出 RFC 5987 编码（中文名不乱码；大小写不敏感）
    assert "filename*=utf-8''" in cd_inline.lower()
    assert "%e7%94%b5%e5%ad%90%e4%bf%9d%e5%8d%95" in cd_inline.lower()  # 「电子保单」
    assert inline.headers["content-type"].startswith("application/pdf")

    download = client.get(
        f"/api/policies/{pid}/attachments/{att['id']}/content?disposition=attachment"
    )
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment")

    # 默认（不传 disposition）= inline
    default = client.get(f"/api/policies/{pid}/attachments/{att['id']}/content")
    assert default.headers["content-disposition"].startswith("inline")


def test_content_missing_disk_file_404(client, attachments_dir):
    pid = _new_policy_id(client)
    att = _upload(client, pid, [("files", ("a.pdf", PDF_BYTES, "application/pdf"))]).json()[
        "uploaded"
    ][0]
    # 手工删掉磁盘文件
    stored = list(attachments_dir.iterdir())
    assert len(stored) == 1
    os.remove(stored[0])

    resp = client.get(f"/api/policies/{pid}/attachments/{att['id']}/content")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ATTACHMENT_NOT_FOUND"


def test_content_wrong_policy_404(client, attachments_dir):
    pid1 = _new_policy_id(client)
    pid2 = _new_policy_id(client)
    att = _upload(client, pid1, [("files", ("a.pdf", PDF_BYTES, "application/pdf"))]).json()[
        "uploaded"
    ][0]
    # 用另一张保单的 id 取该附件 → 404
    resp = client.get(f"/api/policies/{pid2}/attachments/{att['id']}/content")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ATTACHMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 删除附件
# ---------------------------------------------------------------------------
def test_delete_attachment_removes_file_and_row(client, attachments_dir):
    pid = _new_policy_id(client)
    att = _upload(client, pid, [("files", ("a.pdf", PDF_BYTES, "application/pdf"))]).json()[
        "uploaded"
    ][0]
    assert len(list(attachments_dir.iterdir())) == 1

    resp = client.delete(f"/api/policies/{pid}/attachments/{att['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "id": att["id"]}

    assert _list(client, pid)["attachmentCount"] == 0
    assert len(list(attachments_dir.iterdir())) == 0


def test_delete_attachment_not_found_404(client, attachments_dir):
    pid = _new_policy_id(client)
    resp = client.delete(f"/api/policies/{pid}/attachments/a_not_exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ATTACHMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 删除保单：级联清理
# ---------------------------------------------------------------------------
def test_delete_policy_cascades_attachments(client, attachments_dir):
    pid = _new_policy_id(client)
    _upload(
        client,
        pid,
        [
            ("files", ("a.pdf", PDF_BYTES, "application/pdf")),
            ("files", ("b.pdf", PDF_BYTES, "application/pdf")),
        ],
    )
    assert len(list(attachments_dir.iterdir())) == 2

    resp = client.delete(f"/api/policies/{pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    assert body["deletedAttachments"] == 2

    # 记录与文件都被清理
    assert len(list(attachments_dir.iterdir())) == 0
    assert client.get(f"/api/policies/{pid}/attachments").status_code == 404


# ---------------------------------------------------------------------------
# all-or-nothing
# ---------------------------------------------------------------------------
def test_upload_all_or_nothing(client, attachments_dir):
    """一次传 2 个文件、第 2 个非法 → 第 1 个也不落盘、DB 无新增行。"""
    pid = _new_policy_id(client)
    resp = _upload(
        client,
        pid,
        [
            ("files", ("good.pdf", PDF_BYTES, "application/pdf")),
            ("files", ("bad.txt", b"not a pdf", "text/plain")),
        ],
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_FILE_TYPE"

    assert len(list(attachments_dir.iterdir())) == 0
    assert _list(client, pid)["attachmentCount"] == 0
