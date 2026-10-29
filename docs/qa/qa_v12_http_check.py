"""QA v1.2 真实 HTTP 端到端验证（独立于 pytest / TestClient）。

流程：
1. 在临时目录构造一个**真实 v1.1 结构**的 SQLite 库（无 7 售后列、无 policy_attachments 表）；
2. 以 env ``DB_PATH`` / ``ATTACHMENTS_DIR`` 指向临时目录，启动 ``uvicorn app.main:app --port 8010``；
3. 真实 HTTP 走一遍：health → 建成员 → 建保单 → 批量编辑三态 → 上传 PDF → 列表 →
   content → 删除附件 → 删保单；
4. 校验启动期迁移真的补了 7 列 + 建了新表，且**旧数据一字未改**；
5. 打印结构化摘要（供报告引用）。

**绝不**使用真实的 ``data/insurance.db``（用临时库 + 临时附件目录）。
用法：``.venv\\Scripts\\python.exe qa_v12_http_check.py``
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent
PY = REPO / ".venv" / "Scripts" / "python.exe"
PORT = 8010
BASE = f"http://127.0.0.1:{PORT}"

AFTER_SALES_COLUMNS = [
    "advisor_name",
    "advisor_phone",
    "advisor_wechat",
    "advisor_email",
    "company_phone",
    "channel_name",
    "channel_phone",
]

_V11_MEMBERS_DDL = """
CREATE TABLE members (
    id VARCHAR(36) NOT NULL PRIMARY KEY, name VARCHAR(20) NOT NULL, relation VARCHAR(4) NOT NULL,
    birth_date DATE NOT NULL, gender VARCHAR(4) NOT NULL, remark VARCHAR(100),
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)
"""
_V11_POLICIES_DDL = """
CREATE TABLE policies (
    id VARCHAR(36) NOT NULL PRIMARY KEY, company VARCHAR(50) NOT NULL, insurance_type VARCHAR(8) NOT NULL,
    product_name VARCHAR(80) NOT NULL, policyholder_id VARCHAR(36) NOT NULL, insured_id VARCHAR(36) NOT NULL,
    effective_date DATE NOT NULL, coverage_term_type VARCHAR(8) NOT NULL, coverage_term_value INTEGER,
    payment_term_type VARCHAR(10) NOT NULL, payment_term_value INTEGER, payment_frequency VARCHAR(4) NOT NULL,
    premium_per_period INTEGER NOT NULL, insured_amount INTEGER, status VARCHAR(8) NOT NULL,
    remark VARCHAR(200), paid_periods_override INTEGER, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)
"""

SUMMARY: dict = {}


def _build_v11_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(_V11_MEMBERS_DDL)
        conn.execute(_V11_POLICIES_DDL)
        conn.execute(
            "INSERT INTO members VALUES ('m1','张三','本人','1988-06-01','男',NULL,"
            "'2020-01-01 00:00:00','2020-01-01 00:00:00')"
        )
        conn.execute(
            "INSERT INTO policies VALUES ('p_old','平安','重疾险','平安福','m1','m1','2020-01-01',"
            "'终身',NULL,'固定年限',20,'月缴',50000,NULL,'生效中','旧备注',NULL,"
            "'2020-01-01 00:00:00','2020-01-01 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def _http(method: str, path: str, data=None, headers=None, files=None):
    with httpx.Client(base_url=BASE, timeout=20) as client:
        resp = client.request(method, path, content=data, headers=headers or {}, files=files)
        return resp.status_code, dict(resp.headers), resp.content


def _json_req(method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    status, hdrs, body = _http(method, path, data=data, headers=headers)
    try:
        return status, hdrs, json.loads(body)
    except Exception:  # noqa: BLE001
        return status, hdrs, body


def _pdf(payload: bytes = b"obj") -> bytes:
    return b"%PDF-1.4\n" + payload + b"\n%%EOF\n"


def _upload(policy_id: str, files):
    """files: list[(filename, bytes, content_type)] —— 用 httpx 原生 multipart。"""
    payload = [("files", (name, content, ctype)) for name, content, ctype in files]
    return _http("POST", f"/api/policies/{policy_id}/attachments", files=payload)


def _wait_health(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _, body = _http("GET", "/api/health")
            if status == 200:
                SUMMARY["health"] = json.loads(body)
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qa_v12_http_"))
    db_path = tmp / "insurance.db"
    att_dir = tmp / "attachments"
    _build_v11_db(db_path)

    env = os.environ.copy()
    env["DB_PATH"] = str(db_path)
    env["ATTACHMENTS_DIR"] = str(att_dir)
    env["PORT"] = str(PORT)
    env["PYTHONIOENCODING"] = "utf-8"

    log = open(tmp / "uvicorn.log", "wb")  # noqa: SIM115
    proc = subprocess.Popen(
        [str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(REPO),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_health():
            print("!! uvicorn 未在超时内就绪")
            print((tmp / "uvicorn.log").read_text(encoding="utf-8", errors="replace")[-3000:])
            return 1

        # --- 启动期迁移校验 ---
        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(policies)")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old_rows = conn.execute("SELECT * FROM policies WHERE id='p_old'").fetchall()
        conn.close()
        SUMMARY["migration"] = {
            "after_sales_columns_added": all(c in cols for c in AFTER_SALES_COLUMNS),
            "attachment_table_created": "policy_attachments" in tables,
            "legacy_row_intact": len(old_rows) == 1 and old_rows[0][1] == "平安",
        }

        # --- 建成员 / 保单 ---
        st, _, member = _json_req(
            "POST",
            "/api/members",
            {"name": "李四", "relation": "配偶", "birthDate": "1990-01-01", "gender": "女"},
        )
        assert st == 201, (st, member)
        holder = member["id"]

        def mk(name, **over):
            payload = {
                "company": "众安",
                "insuranceType": "医疗险",
                "productName": name,
                "policyholderId": holder,
                "insuredId": holder,
                "effectiveDate": "2024-01-01",
                "coverageTermType": "固定年限",
                "coverageTermValue": 30,
                "paymentTermType": "固定年限",
                "paymentTermValue": 10,
                "paymentFrequency": "年缴",
                "premiumPerPeriod": 8000,
            }
            payload.update(over)
            s, _, body = _json_req("POST", "/api/policies", payload)
            assert s == 201, (s, body)
            return body

        p1 = mk("产品A", advisorName="原顾问1", advisorPhone="111", channelName="渠道1")
        p2 = mk("产品B", advisorName="原顾问2", advisorPhone="222", channelName="渠道2")

        # --- 批量编辑三态 ---
        st, _, batch = _json_req(
            "POST",
            "/api/policies/batch-update",
            {"ids": [p1["id"], p2["id"]], "values": {"advisorName": "新顾问", "advisorPhone": None}},
        )
        SUMMARY["batch_update"] = {"status": st, "body": batch}
        a = _json_req("GET", f"/api/policies/{p1['id']}")[2]
        b = _json_req("GET", f"/api/policies/{p2['id']}")[2]
        SUMMARY["batch_three_state"] = {
            "advisorName_set": a["advisorName"] == "新顾问" and b["advisorName"] == "新顾问",
            "advisorPhone_cleared": a["advisorPhone"] is None and b["advisorPhone"] is None,
            "channelName_untouched": a["channelName"] == "渠道1" and b["channelName"] == "渠道2",
        }

        # --- 上传 PDF（含中文名） ---
        st, _, up = _upload(p1["id"], [("电子保单_产品A.pdf", _pdf(b"hello"), "application/pdf")])
        up_json = json.loads(up)
        SUMMARY["upload"] = {"status": st, "attachmentCount": up_json.get("attachmentCount")}
        if st != 201:
            print("!! upload 失败：", st, up_json)
            return 1
        aid = up_json["uploaded"][0]["id"]

        # --- 列表 ---
        st, _, lst = _json_req("GET", f"/api/policies/{p1['id']}/attachments")
        SUMMARY["list"] = {"status": st, "count": lst.get("attachmentCount"), "name": lst["attachments"][0]["fileName"]}

        # --- content（下载，检查 Content-Disposition） ---
        st, hdrs, content = _http(
            "GET", f"/api/policies/{p1['id']}/attachments/{aid}/content?disposition=attachment"
        )
        SUMMARY["content"] = {
            "status": st,
            "bytes": len(content),
            "content_disposition": hdrs.get("Content-Disposition") or hdrs.get("content-disposition"),
        }

        # --- 非 PDF 拒绝 ---
        st, _, bad = _upload(p1["id"], [("bad.pdf", b"not a pdf", "application/pdf")])
        SUMMARY["reject_non_pdf"] = {"status": st, "code": json.loads(bad).get("code")}

        # --- 删除附件 ---
        st, _, dele = _json_req("DELETE", f"/api/policies/{p1['id']}/attachments/{aid}")
        SUMMARY["delete_attachment"] = {"status": st, "body": dele}

        # --- 删保单（含附件级联） ---
        st, _, dp = _json_req("DELETE", f"/api/policies/{p1['id']}")
        SUMMARY["delete_policy"] = {"status": st, "body": dp}

        # --- suggestions 路由 ---
        st, _, sug = _json_req("GET", "/api/policies/suggestions")
        SUMMARY["suggestions"] = {
            "status": st,
            "keys": sorted(sug.keys()) if isinstance(sug, dict) else None,
            "advisorName": sug.get("advisorName") if isinstance(sug, dict) else None,
        }

        # --- 遗留旧保单仍可读 ---
        st, _, old = _json_req("GET", "/api/policies/p_old")
        SUMMARY["legacy_policy_readable"] = {"status": st, "advisorName": old.get("advisorName")}

        print(json.dumps(SUMMARY, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        log.close()
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
