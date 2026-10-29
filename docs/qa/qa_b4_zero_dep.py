"""QA 阶段 B —— B4 零外部依赖复核。

扫描 ``static/`` 下全部文件，确认：
1. 无任何 http(s):// 外链（CDN / 远程字体 / 远程脚本 / 远程图片）；
2. 无 ``//`` 协议相对外链（如 ``//cdn.example.com``）；
3. 无 ``import ... from "http..."`` / ``importScripts("http...")``；
4. 无 ``<link ... href="//...">``；
5. HTML 中引用的本地资源确实存在。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"

TEXT_EXT = {".html", ".js", ".css", ".svg", ".json", ".mjs", ".map", ".txt", ".webmanifest"}

http_re = re.compile(r"https?://[^\s\"'()<>]+", re.I)
proto_rel_re = re.compile(r"[\"'(]\s*//[a-z0-9.\-]+\.[a-z]{2,}", re.I)
import_url_re = re.compile(r"""(?:import|from|importScripts)\s*\(?\s*["'](?:https?:)?//""", re.I)

report: dict = {"files_scanned": 0, "external_refs": [], "proto_relative_refs": [], "import_urls": []}
html_refs: dict = {}

for p in sorted(STATIC.rglob("*")):
    if not p.is_file():
        continue
    report["files_scanned"] += 1
    if p.suffix.lower() not in TEXT_EXT:
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    rel = str(p.relative_to(ROOT))
    for m in http_re.findall(text):
        # 允许注释里出现 "http://127.0.0.1" / localhost / w3.org 命名空间（SVG xmlns）等无害引用
        low = m.lower()
        if any(k in low for k in ("127.0.0.1", "localhost", "w3.org/2000/svg", "w3.org/1999/xlink",
                                  "example.com", "example.local")):
            continue
        report["external_refs"].append({"file": rel, "ref": m})
    for m in proto_rel_re.findall(text):
        report["proto_relative_refs"].append({"file": rel, "ref": m.strip()})
    for m in import_url_re.findall(text):
        report["import_urls"].append({"file": rel, "ref": m.strip()})
    if p.suffix.lower() == ".html":
        refs = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", text)
        html_refs[rel] = refs

# 校验 HTML 本地资源存在
missing_assets = []
for rel, refs in html_refs.items():
    base = ROOT / rel
    for r in refs:
        if r.startswith(("http://", "https://", "//", "data:", "#", "mailto:")):
            continue
        clean = r.split("?")[0].split("#")[0]
        # 以 "/" 开头的是「静态根」绝对路径（FastAPI 把 StaticFiles 挂到根），
        # 相对 STATIC 根解析；否则相对该 HTML 所在目录解析。
        if clean.startswith("/"):
            target = (STATIC / clean.lstrip("/")).resolve()
        else:
            target = (base.parent / clean).resolve()
        if not target.exists():
            missing_assets.append({"html": rel, "ref": r, "resolved": str(target)})

report["html_local_assets"] = html_refs
report["missing_local_assets"] = missing_assets
report["PASS"] = (
    not report["external_refs"]
    and not report["proto_relative_refs"]
    and not report["import_urls"]
    and not missing_assets
)
print(json.dumps(report, ensure_ascii=False, indent=2))
