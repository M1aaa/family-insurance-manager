"""QA Docker 交付物 · 依赖完整性验证（阶段 A2）。

在**全新临时 venv** 里只用 ``requirements.txt`` 安装（强制仅 wheel，证明无需编译），
再启动服务并逐个打核心接口，确认没有 ``ModuleNotFoundError``。
结论写入 ``_qa_docker_deps.txt``。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_qa_docker_deps.txt"
lines: list[str] = []


def say(*parts) -> None:
    lines.append(" ".join(str(p) for p in parts))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qa_deps_"))
    venv_dir = tmp / "venv"
    say("=" * 78)
    say("A2 依赖完整性验证（全新 venv + 仅 requirements.txt）")
    say("=" * 78)
    say(f"临时目录：{tmp}")

    # 1) 建全新 venv
    r = run([sys.executable, "-m", "venv", str(venv_dir)])
    say(f"[1] python -m venv → rc={r.returncode} {r.stderr.strip()[:300]}")
    vpy = venv_dir / "Scripts" / "python.exe"

    # 2) 仅 requirements.txt，强制 --only-binary（证明全部为预编译 wheel，无需编译）
    say("[2] pip install --only-binary=:all: -r requirements.txt （只用运行依赖，不装 dev）")
    r = run([str(vpy), "-m", "pip", "install", "--only-binary=:all:", "--no-input",
             "-r", str(ROOT / "requirements.txt")], cwd=str(ROOT), timeout=900)
    say(f"    rc={r.returncode}")
    for ln in (r.stdout or "").splitlines():
        if "Downloading" in ln or "Using cached" in ln or "Installing collected" in ln or "Successfully installed" in ln:
            say("    " + ln.strip())
    if r.returncode != 0:
        say("    STDERR:")
        for ln in (r.stderr or "").splitlines()[-40:]:
            say("      " + ln)
        say("  [FAIL] 全新 venv 安装失败")
    else:
        say("  [PASS] 全新 venv 仅用 requirements.txt 安装成功（且全部为 wheel，无需编译）")

    # 3) 记录关键包版本 + python-multipart 是否为纯 py3-none-any
    r = run([str(vpy), "-m", "pip", "list", "--format=json"])
    try:
        pkgs = {p["name"].lower(): p["version"] for p in json.loads(r.stdout)}
    except Exception:  # noqa: BLE001
        pkgs = {}
    for name in ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "pydantic-settings",
                 "openpyxl", "python-multipart"]:
        say(f"    {name}=={pkgs.get(name, 'MISSING')}")

    # 用 pip download 单独确认 python-multipart wheel 名
    dl = tmp / "dl"
    dl.mkdir()
    r = run([str(vpy), "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
             "python-multipart==0.0.20", "-d", str(dl)], timeout=300)
    wheels = [p.name for p in dl.iterdir()] if dl.exists() else []
    say(f"    [{'PASS' if any('py3-none-any' in w for w in wheels) else 'CHECK'}] python-multipart wheel = {wheels}")

    # 4) 启动服务（用全新 venv 的 python）并逐个打接口
    db_path = tmp / "data" / "insurance.db"
    att = tmp / "data" / "attachments"
    env = os.environ.copy()
    env["DB_PATH"] = str(db_path)
    env["ATTACHMENTS_DIR"] = str(att)
    env["PORT"] = "8012"
    env["PYTHONIOENCODING"] = "utf-8"
    logf = open(tmp / "uvicorn.log", "wb")
    proc = subprocess.Popen([str(vpy), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8012"],
                            cwd=str(ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT)
    ready = False
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8012/api/health", timeout=2) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    say(f"[3] 服务就绪：{ready}")

    try:
        if not ready:
            logf.close()
            say("    uvicorn 日志尾部：")
            for ln in (tmp / "uvicorn.log").read_text(encoding="utf-8", errors="replace").splitlines()[-30:]:
                say("      " + ln)
            say("  [FAIL] 全新 venv 无法启动服务")
        else:
            endpoints = ["/api/health", "/api/meta/enums", "/api/members", "/api/policies",
                         "/api/policies/suggestions", "/api/stats/dashboard"]
            for ep in endpoints:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:8012{ep}", timeout=5) as resp:
                        status = resp.status
                except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
                    status = exc.code
                except Exception as exc:  # noqa: BLE001
                    status = f"ERR {exc}"
                ok = status == 200
                say(f"    [{'PASS' if ok else 'FAIL'}] GET {ep} → {status}")
            # 自动创建目录
            say(f"  [{'PASS' if db_path.exists() else 'FAIL'}] 启动后自动创建 DB：{db_path}")
            say(f"  [{'PASS' if att.exists() else 'FAIL'}] 启动后自动创建 attachments 目录：{att}")
            # 日志里有无 ModuleNotFoundError
            logf.close()
            logtxt = (tmp / "uvicorn.log").read_text(encoding="utf-8", errors="replace")
            say(f"  [{'PASS' if 'ModuleNotFoundError' not in logtxt else 'FAIL'}] 日志无 ModuleNotFoundError")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        try:
            logf.close()
        except Exception:  # noqa: BLE001
            pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
