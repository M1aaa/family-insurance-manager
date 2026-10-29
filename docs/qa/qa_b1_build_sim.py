"""QA 阶段 B —— B1 完整构建模拟（等价于 Docker 镜像内容）。

模拟思路（等价于 Dockerfile 的 ``COPY requirements.txt + app + static``）：
1. 在**仓库之外**的干净目录（系统临时目录下的 ``_qa_build_sim``）中，只放入
   ``requirements.txt`` + ``app/`` + ``static/``（不含 tests/、data/、docs/、脚本）；
2. 用**全新 venv** 仅 ``pip install -r requirements.txt``；
3. 以该 venv 起 ``uvicorn app.main:app --port 8011``（真实进程、真实 HTTP）；
4. 走通 v1.2 全部功能：health / meta / 成员 / 保单 / 售后 7 字段 / 批量三态 /
   suggestions / 附件上传·列表·查看·下载·删除 / 看板 / 静态前端；
5. 顺带验证 **B2 旧库冷启动升级**（v1.1 结构库 → 启动即补列建表）。

不触碰真实 ``data/insurance.db``（全部用临时目录）。
用法：``python qa_b1_build_sim.py``（用仓库 .venv 或任意 python 运行皆可）。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

SIM_DIR = Path(os.environ.get("QA_SIM_DIR") or Path(tempfile.gettempdir()) / "_qa_build_sim")
PY = SIM_DIR / ".venv" / "Scripts" / "python.exe"
PORT = 8011
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

S: dict = {}


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


def _http(method, path, data=None, headers=None, files=None):
    with httpx.Client(base_url=BASE, timeout=20) as c:
        r = c.request(method, path, content=data, headers=headers or {}, files=files)
        return r.status_code, dict(r.headers), r.content


def _json_req(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    st, hdrs, body = _http(method, path, data=data, headers=headers)
    try:
        return st, hdrs, json.loads(body)
    except Exception:  # noqa: BLE001
        return st, hdrs, body


def _pdf(payload=b"obj"):
    return b"%PDF-1.4\n" + payload + b"\n%%EOF\n"


def _upload(pid, files):
    payload = [("files", (n, c, t)) for n, c, t in files]
    return _http("POST", f"/api/policies/{pid}/attachments", files=payload)


def _wait_health(timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            st, _, body = _http("GET", "/api/health")
            if st == 200:
                S["health"] = json.loads(body)
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    if not PY.exists():
        print(f"!! 模拟 venv 不存在：{PY}")
        return 2
    S["sim_dir"] = str(SIM_DIR)
    S["sim_dir_contents"] = sorted(p.name for p in SIM_DIR.iterdir())
    S["venv_python"] = str(PY)

    tmp = Path(tempfile.mkdtemp(prefix="qa_b1_"))
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
        cwd=str(SIM_DIR),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_health():
            print("!! uvicorn 未在超时内就绪")
            print((tmp / "uvicorn.log").read_text(encoding="utf-8", errors="replace")[-3000:])
            return 1

        # --- B2：启动期迁移 ---
        conn = sqlite3.connect(str(db_path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(policies)")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old = conn.execute("SELECT * FROM policies WHERE id='p_old'").fetchall()
        conn.close()
        S["migration"] = {
            "after_sales_columns_added": all(c in cols for c in AFTER_SALES_COLUMNS),
            "attachment_table_created": "policy_attachments" in tables,
            "legacy_row_intact": len(old) == 1 and old[0][1] == "平安",
        }

        # --- meta enums ---
        st, _, enums = _json_req("GET", "/api/meta/enums")
        S["meta_enums"] = {"status": st, "keys": sorted(enums.keys()) if isinstance(enums, dict) else None}

        # --- 成员 ---
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

        # --- 售后 7 字段落库 ---
        p1 = mk(
            "产品A",
            advisorName="原顾问1",
            advisorPhone="111",
            advisorWechat="wx1",
            advisorEmail="a@x.com",
            companyPhone="400-1",
            channelName="渠道1",
            channelPhone="c1",
        )
        p2 = mk("产品B", advisorName="原顾问2", advisorPhone="222", channelName="渠道2")
        st, _, got1 = _json_req("GET", f"/api/policies/{p1['id']}")
        S["after_sales_persist"] = {
            "advisorName": got1.get("advisorName"),
            "advisorWechat": got1.get("advisorWechat"),
            "advisorEmail": got1.get("advisorEmail"),
            "companyPhone": got1.get("companyPhone"),
            "channelPhone": got1.get("channelPhone"),
        }

        # --- 批量三态 ---
        st, _, batch = _json_req(
            "POST",
            "/api/policies/batch-update",
            {"ids": [p1["id"], p2["id"]], "values": {"advisorName": "新顾问", "advisorPhone": None}},
        )
        S["batch_update_status"] = st
        a = _json_req("GET", f"/api/policies/{p1['id']}")[2]
        b = _json_req("GET", f"/api/policies/{p2['id']}")[2]
        S["batch_three_state"] = {
            "advisorName_set": a["advisorName"] == "新顾问" and b["advisorName"] == "新顾问",
            "advisorPhone_cleared": a["advisorPhone"] is None and b["advisorPhone"] is None,
            "channelName_untouched": a["channelName"] == "渠道1" and b["channelName"] == "渠道2",
        }

        # --- suggestions ---
        st, _, sug = _json_req("GET", "/api/policies/suggestions")
        S["suggestions"] = {
            "status": st,
            "keys": sorted(sug.keys()) if isinstance(sug, dict) else None,
            "advisorName": sug.get("advisorName") if isinstance(sug, dict) else None,
        }

        # --- 附件全流程 ---
        st, _, up = _upload(p1["id"], [("电子保单_产品A.pdf", _pdf(b"hello"), "application/pdf")])
        upj = json.loads(up)
        S["upload"] = {"status": st, "attachmentCount": upj.get("attachmentCount")}
        aid = upj["uploaded"][0]["id"]
        st, _, lst = _json_req("GET", f"/api/policies/{p1['id']}/attachments")
        S["list"] = {"status": st, "count": lst.get("attachmentCount"), "name": lst["attachments"][0]["fileName"]}
        st, hdrs, content = _http(
            "GET", f"/api/policies/{p1['id']}/attachments/{aid}/content?disposition=attachment"
        )
        S["content"] = {
            "status": st,
            "bytes": len(content),
            "content_disposition": hdrs.get("Content-Disposition") or hdrs.get("content-disposition"),
        }
        st, _, bad = _upload(p1["id"], [("bad.pdf", b"not a pdf", "application/pdf")])
        S["reject_non_pdf"] = {"status": st, "code": json.loads(bad).get("code")}
        st, _, dele = _json_req("DELETE", f"/api/policies/{p1['id']}/attachments/{aid}")
        S["delete_attachment"] = {"status": st}
        st, _, dp = _json_req("DELETE", f"/api/policies/{p1['id']}")
        S["delete_policy"] = {"status": st, "deletedAttachments": dp.get("deletedAttachments")}

        # --- 看板 ---
        st, _, dash = _json_req("GET", "/api/stats/dashboard")
        S["dashboard"] = {"status": st, "keys": sorted(dash.keys()) if isinstance(dash, dict) else None}

        # --- 静态前端 ---
        st, _, idx = _http("GET", "/")
        S["static_index"] = {"status": st, "has_html": b"<html" in idx.lower()}
        st, _, js = _http("GET", "/js/main.js")
        S["static_js"] = {"status": st, "bytes": len(js)}

        # --- 旧保单可读 ---
        st, _, oldp = _json_req("GET", "/api/policies/p_old")
        S["legacy_policy_readable"] = {"status": st, "advisorName": oldp.get("advisorName")}

        print(json.dumps(S, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
