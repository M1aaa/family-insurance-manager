"""QA Docker 交付物静态审计（阶段 A1/A3/A4）。只读，不修改任何文件。

因本机 shell stdout 捕获异常，本脚本把结论写入 ``_qa_docker_audit.txt``。
用法：``.venv\\Scripts\\python.exe qa_docker_audit.py``
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_qa_docker_audit.txt"
PY = ROOT / ".venv" / "Scripts" / "python.exe"

lines: list[str] = []


def say(*parts) -> None:
    lines.append(" ".join(str(p) for p in parts))


def section(title: str) -> None:
    say("")
    say("=" * 78)
    say(title)
    say("=" * 78)


# ---------------------------------------------------------------------------
# A1-1 Dockerfile
# ---------------------------------------------------------------------------
def audit_dockerfile() -> None:
    section("A1-1 Dockerfile")
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    checks = {
        "ARG BASE_IMAGE=python:3.13-slim": "ARG BASE_IMAGE=python:3.13-slim" in text,
        "FROM ${BASE_IMAGE}": "FROM ${BASE_IMAGE}" in text,
        "ARG PIP_INDEX_URL 有默认": "ARG PIP_INDEX_URL=https://pypi.org/simple" in text,
        "ARG TZ 有默认": "ARG TZ=Asia/Shanghai" in text,
        "ENV DB_PATH=/app/data/insurance.db": "DB_PATH=/app/data/insurance.db" in text,
        "ENV PORT=8000": "PORT=8000" in text,
        "ENV TZ=${TZ}": "TZ=${TZ}" in text,
        "WORKDIR /app": "WORKDIR /app" in text,
        "安装 tzdata": "apt-get install -y --no-install-recommends tzdata" in text,
        "软链 /etc/localtime": "ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime" in text,
        "COPY requirements.txt 先于代码": text.find("COPY requirements.txt") < text.find("COPY app ./app"),
        "COPY app ./app": "COPY app ./app" in text,
        "COPY static ./static": "COPY static ./static" in text,
        "mkdir -p /app/data": "mkdir -p /app/data" in text,
        "LABEL version=1.2.0": 'org.opencontainers.image.version="1.2.0"' in text,
        "EXPOSE 8000": "EXPOSE 8000" in text,
        "HEALTHCHECK 存在": "HEALTHCHECK" in text,
        "HEALTHCHECK 用 urllib": "urllib.request" in text,
        "CMD 为 exec 形式(JSON数组)": bool(re.search(r'CMD\s*\[\s*"sh"', text)),
        "CMD 内含 exec 前缀": "exec uvicorn app.main:app" in text,
    }
    for k, v in checks.items():
        say(f"  [{'PASS' if v else 'FAIL'}] {k}")

    # 提取 HEALTHCHECK 的 python -c 代码并编译
    m = re.search(r'HEALTHCHECK[^\n]*\n\s*CMD python -c "(.+)"\s*$', text, re.MULTILINE)
    if m:
        code = m.group(1)
        say("  HEALTHCHECK python 片段：")
        say("    " + code)
        try:
            compile(code, "<healthcheck>", "exec")
            say("  [PASS] HEALTHCHECK python 片段语法可编译")
        except SyntaxError as exc:
            say(f"  [FAIL] HEALTHCHECK python 片段语法错误：{exc}")
        # 提取端口默认值
        say(f"  [INFO] HEALTHCHECK 端口取自 env PORT（默认 8000）")
    else:
        say("  [FAIL] 未能提取 HEALTHCHECK 的 python -c 片段")


# ---------------------------------------------------------------------------
# A1-2/3 Compose
# ---------------------------------------------------------------------------
def audit_compose() -> None:
    section("A1-2 docker-compose.yml（极空间运行用）")
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        say(f"  [FAIL] 无法 import yaml：{exc}")
        return
    data = yaml.safe_load(raw)
    svc = data["services"]["insurance-tool"]
    checks = {
        "image == family-insurance-manager:1.2.0": svc.get("image") == "family-insurance-manager:1.2.0",
        "无 build: 段": "build" not in svc,
        "ports == ['8000:8000']": svc.get("ports") == ["8000:8000"],
        "env DB_PATH == /app/data/insurance.db": svc["environment"].get("DB_PATH") == "/app/data/insurance.db",
        "env PORT == '8000'": str(svc["environment"].get("PORT")) == "8000",
        "env TZ == Asia/Shanghai": svc["environment"].get("TZ") == "Asia/Shanghai",
        "volumes == ['./data:/app/data']": svc.get("volumes") == ["./data:/app/data"],
        "restart == unless-stopped": svc.get("restart") == "unless-stopped",
        "healthcheck 存在": "healthcheck" in svc,
    }
    for k, v in checks.items():
        say(f"  [{'PASS' if v else 'FAIL'}] {k}")

    # 非注释行内无 ${...}
    bad = []
    for i, ln in enumerate(raw.splitlines(), 1):
        s = ln.strip()
        if s.startswith("#") or not s:
            continue
        if "${" in ln:
            bad.append((i, ln.strip()))
    say(f"  [{'PASS' if not bad else 'FAIL'}] 非注释行内无 ${{...}}（命中 {len(bad)} 行）")
    for i, ln in bad:
        say(f"        L{i}: {ln}")

    # healthcheck 与 Dockerfile 一致
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    df_cmd = re.search(r'HEALTHCHECK[^\n]*\n\s*CMD python -c "(.+)"', df)
    yc = svc.get("healthcheck", {}).get("test", [])
    yc_cmd = yc[-1] if yc else ""
    same = df_cmd and df_cmd.group(1) in yc_cmd
    say(f"  [{'PASS' if same else 'FAIL'}] compose healthcheck 与 Dockerfile 一致")
    say(f"    Dockerfile: {df_cmd.group(1) if df_cmd else 'N/A'}")
    say(f"    compose   : {yc_cmd}")
    # 间隔一致性
    hc = svc.get("healthcheck", {})
    say(f"    compose interval/timeout/start_period/retries = "
        f"{hc.get('interval')}/{hc.get('timeout')}/{hc.get('start_period')}/{hc.get('retries')}")
    say(f"    Dockerfile 对应 = 30s/5s/10s/3")

    section("A1-3 docker-compose.build.yml（本机构建用）")
    rawb = (ROOT / "docker-compose.build.yml").read_text(encoding="utf-8")
    db = yaml.safe_load(rawb)
    svcb = db["services"]["insurance-tool"]
    checksb = {
        "有 build: 段": "build" in svcb,
        "build.context == '.'": svcb.get("build", {}).get("context") == ".",
        "build.dockerfile == Dockerfile": svcb.get("build", {}).get("dockerfile") == "Dockerfile",
        "image tag 一致 1.2.0": svcb.get("image") == "family-insurance-manager:1.2.0",
        "BASE_IMAGE 有默认值": svcb["build"]["args"].get("BASE_IMAGE") == "${BASE_IMAGE:-python:3.13-slim}",
        "PIP_INDEX_URL 有默认值": "${PIP_INDEX_URL:-" in str(svcb["build"]["args"].get("PIP_INDEX_URL")),
        "TZ 有默认值": "${TZ:-" in str(svcb["build"]["args"].get("TZ")),
        "volumes == ['./data:/app/data']": svcb.get("volumes") == ["./data:/app/data"],
    }
    for k, v in checksb.items():
        say(f"  [{'PASS' if v else 'FAIL'}] {k}")


# ---------------------------------------------------------------------------
# A1-4 .dockerignore（逐条判断会不会进镜像）
# ---------------------------------------------------------------------------
def audit_dockerignore() -> None:
    section("A1-4 .dockerignore（仓库根逐条判断「是否进入构建上下文」）")
    patterns_raw = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    patterns = []
    for ln in patterns_raw:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        patterns.append(s.rstrip("/"))
    say(f"  有效模式 {len(patterns)} 条：{patterns}")

    MUST_INCLUDE = {"app", "static", "requirements.txt"}
    entries = sorted(p.name for p in ROOT.iterdir())

    def excluded(name: str) -> bool:
        for pat in patterns:
            if fnmatch.fnmatchcase(name, pat):
                return True
        return False

    leaks: list[str] = []
    say("")
    say(f"  {'条目':<28} {'状态':<12} 判定")
    for name in entries:
        if name in MUST_INCLUDE:
            say(f"  {name:<28} {'INCLUDE(必须)':<12} 必须进镜像 ✅")
            continue
        if excluded(name):
            say(f"  {name:<28} {'EXCLUDED':<12} 不进上下文 ✅")
        else:
            say(f"  {name:<28} {'IN-CONTEXT':<12} ⚠️ 会进入构建上下文（漏网）")
            leaks.append(name)

    say("")
    say(f"  [{'PASS' if not leaks else 'FAIL'}] 无漏网：{'是' if not leaks else '否'}")
    for name in leaks:
        p = ROOT / name
        if p.is_dir():
            total = 0
            nfiles = 0
            for root_, _dirs, files in os.walk(p):
                for f in files:
                    nfiles += 1
                    try:
                        total += (Path(root_) / f).stat().st_size
                    except OSError:
                        pass
            say(f"        漏网目录 {name}/：{nfiles} 个文件，约 {total/1024/1024:.1f} MiB")
        else:
            say(f"        漏网文件 {name}：{p.stat().st_size} 字节")

    # 必需文件是否被误排除
    say("")
    for need in ["app", "static", "requirements.txt"]:
        say(f"  [{'PASS' if not excluded(need) else 'FAIL'}] 必需项 {need} 未被排除")
    # 明确不应进镜像的关键项
    for forbid in ["data", ".venv", ".git", "tests", "docs", ".workbuddy-ai", "scripts",
                   "Dockerfile", "docker-compose.yml", "docker-compose.build.yml",
                   ".dockerignore", "start.bat", "start.sh", "requirements-dev.txt"]:
        say(f"  [{'PASS' if excluded(forbid) else 'FAIL'}] {forbid} 已被排除")


# ---------------------------------------------------------------------------
# A1-5 构建脚本
# ---------------------------------------------------------------------------
def audit_scripts() -> None:
    section("A1-5 构建脚本 scripts/build-image.sh / .bat")
    sh = (ROOT / "scripts" / "build-image.sh").read_text(encoding="utf-8")
    bat = (ROOT / "scripts" / "build-image.bat").read_text(encoding="utf-8")
    say(f"  [{'PASS' if 'IMAGE_TAG=\"1.2.0\"' in sh else 'FAIL'}] .sh IMAGE_TAG=1.2.0")
    say(f"  [{'PASS' if 'TAR_NAME=\"${IMAGE_NAME}-${IMAGE_TAG}.tar\"' in sh else 'FAIL'}] .sh tar 名 family-insurance-manager-1.2.0.tar")
    say(f"  [{'PASS' if 'PLATFORM=\"linux/amd64\"' in sh else 'FAIL'}] .sh PLATFORM=linux/amd64")
    say(f"  [{'PASS' if '--build-arg \"BASE_IMAGE=' in sh else 'FAIL'}] .sh 传 build-arg BASE_IMAGE")
    say(f"  [{'PASS' if 'IMAGE_TAG=1.2.0' in bat else 'FAIL'}] .bat IMAGE_TAG=1.2.0")
    say(f"  [{'PASS' if 'TAR_NAME=%IMAGE_NAME%-%IMAGE_TAG%.tar' in bat else 'FAIL'}] .bat tar 名一致")
    say(f"  [{'PASS' if 'PLATFORM=linux/amd64' in bat else 'FAIL'}] .bat PLATFORM=linux/amd64")
    # bash -n 语法检查
    try:
        r = subprocess.run(["bash", "-n", str(ROOT / "scripts" / "build-image.sh")],
                           capture_output=True, text=True, timeout=30)
        say(f"  [{'PASS' if r.returncode == 0 else 'FAIL'}] bash -n build-image.sh（rc={r.returncode}）{r.stderr.strip()}")
    except Exception as exc:  # noqa: BLE001
        say(f"  [SKIP] bash -n 无法执行：{exc}")
    # 可执行位
    st = (ROOT / "scripts" / "build-image.sh").stat()
    say(f"  [INFO] build-image.sh 权限位 = {oct(st.st_mode)}（{'-' if not (st.st_mode & 0o111) else '有'}可执行位）")


# ---------------------------------------------------------------------------
# A1-6 文档一致性
# ---------------------------------------------------------------------------
def audit_docs() -> None:
    section("A1-6 文档一致性（DEPLOY-ZSPACE.md / README.md / overview.md）")
    deploy = (ROOT / "docs" / "DEPLOY-ZSPACE.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    overview = (ROOT / "overview.md").read_text(encoding="utf-8")

    cfg = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    say("  -- DEPLOY-ZSPACE.md --")
    for token, label in [
        ("family-insurance-manager:1.2.0", "镜像 tag 1.2.0"),
        ("family-insurance-manager-1.2.0.tar", "tar 名 1.2.0"),
        ("8000:8000", "端口映射 8000"),
        ("./data:/app/data", "卷路径"),
        ("linux/amd64", "架构 linux/amd64"),
        ("DB_PATH: /app/data/insurance.db", "env DB_PATH"),
        ("PORT", "env PORT"),
        ("attachments/", "备份章节含 attachments/"),
    ]:
        say(f"  [{'PASS' if token in deploy else 'FAIL'}] {label}（'{token}'）")
    for env_name in ["ATTACHMENTS_DIR", "MAX_ATTACHMENT_SIZE", "MAX_ATTACHMENTS_PER_POLICY"]:
        present = env_name in deploy
        say(f"  [{'INFO' if not present else 'PASS'}] DEPLOY-ZSPACE 提及 {env_name}：{present}")

    say("  -- README.md 环境变量表 vs config.py --")
    for env_name, default in [
        ("DB_PATH", "./data/insurance.db"),
        ("PORT", "8000"),
        ("STATIC_DIR", "static"),
        ("ATTACHMENTS_DIR", "./data/attachments"),
        ("MAX_ATTACHMENT_SIZE", "20 * 1024 * 1024"),
        ("MAX_ATTACHMENTS_PER_POLICY", "10"),
    ]:
        in_readme = env_name in readme
        in_cfg = env_name.lower() in cfg
        say(f"  [{'PASS' if (in_readme and in_cfg) else 'FAIL'}] {env_name}：README={in_readme} config={in_cfg}")

    say("  -- README.md API 表 vs 实际路由 --")
    for path in ["/api/policies/batch-update", "/api/policies/suggestions",
                 "/attachments", "/content", "/api/stats/dashboard", "/api/health"]:
        say(f"  [{'PASS' if path in readme else 'FAIL'}] README 含 {path}")

    say("  -- overview.md --")
    say(f"  [INFO] 声明 v1.2 已完成：{'当前版本 **v1.2**' in overview}")
    say(f"  [INFO] 自相矛盾：line 末尾又写「前端 v1.2 交互...待实现」={'前端 v1.2 交互' in overview}")
    say(f"  [INFO] 测试数写 163：{'163' in overview}（当前全量已 214，含 QA 新增）")
    say(f"  [INFO] 已知限制称根目录临时文件已被 .dockerignore 排除，但未提及 _verify/：{'_verify' in overview}")


# ---------------------------------------------------------------------------
# A3 零外部依赖
# ---------------------------------------------------------------------------
def audit_zero_dependency() -> None:
    section("A3 零外部依赖（static/ 无 CDN / 外链 / 构建产物）")
    static = ROOT / "static"
    patterns = ["http://", "https://", "//cdn", "cdn.", "unpkg", "jsdelivr",
                "googleapis", "gstatic", "fonts.", "@import url(", "<script src=\"//",
                "bootstrap", "jquery", "tailwind"]
    hits: list[str] = []
    nfiles = 0
    for p in static.rglob("*"):
        if not p.is_file():
            continue
        nfiles += 1
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        low = txt.lower()
        for pat in patterns:
            if pat in low:
                # 允许 localhost / 127.0.0.1 注释性出现
                hits.append(f"{p.relative_to(ROOT)} ← '{pat}'")
    say(f"  扫描 static/ 下 {nfiles} 个文件")
    say(f"  [{'PASS' if not hits else 'FAIL'}] 无 CDN / 外链命中（{len(hits)} 处）")
    for h in hits[:40]:
        say(f"      {h}")
    # 无构建产物
    for bad in ["package.json", "node_modules", "vite.config", "webpack", "tsconfig.json"]:
        found = list(static.rglob(bad)) or list(static.rglob(bad + "*"))
        say(f"  [{'PASS' if not found else 'FAIL'}] static/ 无 {bad}")


# ---------------------------------------------------------------------------
# A4 用户真实数据不会进镜像
# ---------------------------------------------------------------------------
def audit_user_data() -> None:
    section("A4 用户真实数据不会被镜像捕获")
    data = ROOT / "data"
    files = [p for p in data.rglob("*") if p.is_file()]
    say(f"  data/ 下文件：{[str(p.relative_to(ROOT)) for p in files]}")
    df = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    say(f"  [{'PASS' if 'COPY data' not in df else 'FAIL'}] Dockerfile 无 COPY data（data 不会进镜像）")
    say(f"  [INFO] Dockerfile 仅 COPY：{re.findall(r'COPY \\S+ \\S+', df)}")
    # 真实库现状
    db = data / "insurance.db"
    if db.exists():
        import hashlib
        import sqlite3
        raw = db.read_bytes()
        say(f"  insurance.db md5={hashlib.md5(raw).hexdigest()} size={len(raw)}")
        try:
            conn = sqlite3.connect(str(db))
            cols = {r[1] for r in conn.execute("PRAGMA table_info(policies)")}
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            nm = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
            np_ = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
            conn.close()
            after = ["advisor_name", "advisor_phone", "advisor_wechat", "advisor_email",
                     "company_phone", "channel_name", "channel_phone"]
            say(f"  members={nm} policies={np_}")
            say(f"  [INFO] policies 含 7 售后列：{all(c in cols for c in after)}（应为 False，迁移未执行）")
            say(f"  [INFO] 含 policy_attachments 表：{'policy_attachments' in tables}（应为 False）")
        except Exception as exc:  # noqa: BLE001
            say(f"  [WARN] 读取真实库失败：{exc}")
    say(f"  [INFO] data/attachments/ 存在：{(data / 'attachments').exists()}")


# ---------------------------------------------------------------------------
# A1-1b HEALTHCHECK 真实执行（起服务 + 探活）
# ---------------------------------------------------------------------------
def audit_healthcheck_live() -> None:
    section("A1-1b HEALTHCHECK 命令真实执行（起 uvicorn + 探活 / 探死）")
    tmp = Path(tempfile.mkdtemp(prefix="qa_hc_"))
    env = os.environ.copy()
    env["DB_PATH"] = str(tmp / "insurance.db")
    env["ATTACHMENTS_DIR"] = str(tmp / "attachments")
    env["PORT"] = "8011"
    proc = subprocess.Popen(
        [str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8011"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = False
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8011/api/health", timeout=2) as r:
                ok = r.status == 200
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    try:
        snippet = ("import os,sys,urllib.request; port=os.environ.get('PORT','8000'); "
                   "sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', "
                   "timeout=4).status == 200 else 1)")
        say(f"  服务就绪：{ok}")
        e2 = env.copy(); e2["PORT"] = "8011"
        r = subprocess.run([str(PY), "-c", snippet], env=e2, capture_output=True, text=True, timeout=20)
        say(f"  [{'PASS' if r.returncode == 0 else 'FAIL'}] 对存活服务探活 → exit={r.returncode}（期望 0）")
        e3 = env.copy(); e3["PORT"] = "65533"
        r2 = subprocess.run([str(PY), "-c", snippet], env=e3, capture_output=True, text=True, timeout=20)
        say(f"  [{'PASS' if r2.returncode != 0 else 'FAIL'}] 对空端口探活 → exit={r2.returncode}（期望非 0）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    say(f"QA Docker 交付物静态审计 · 只读 · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say(f"repo = {ROOT}")
    audit_dockerfile()
    audit_compose()
    audit_dockerignore()
    audit_scripts()
    audit_docs()
    audit_zero_dependency()
    audit_user_data()
    audit_healthcheck_live()
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
