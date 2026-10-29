"""QA 独立复核：枚举 app 的真实路由，与 README 第四节 API 表逐条比对。

只读，不修改任何业务代码。
"""
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

# ---- 1. 枚举真实路由（来自 OpenAPI schema，权威） ----
schema = app.openapi()
real = []
for path, methods in schema.get("paths", {}).items():
    for method in methods:
        if method.lower() in {"get", "post", "put", "delete", "patch"}:
            real.append((method.upper(), path))
real_sorted = sorted(real)

# 也直接枚举 app.routes 作交叉验证
route_objs = []
for r in app.routes:
    methods = getattr(r, "methods", None)
    path = getattr(r, "path", None)
    if methods and path:
        for m in methods:
            if m in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                route_objs.append((m, path))
route_objs_sorted = sorted(set(route_objs))

# ---- 2. 解析 README 第四节 API 表 ----
readme = (ROOT / "README.md").read_text(encoding="utf-8")
readme_api = []
in_api_section = False
for line in readme.splitlines():
    if line.strip().startswith("## 四、API"):
        in_api_section = True
        continue
    if in_api_section and line.strip().startswith("## "):
        break
    if in_api_section and line.strip().startswith("|"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] not in ("方法", "---") and not set(cells[0]) <= {"-", " "}:
            methods_cell = cells[0]
            path_cell = cells[1].strip("`")
            for m in methods_cell.split("/"):
                m = m.strip()
                if m and m != "方法":
                    readme_api.append((m.upper(), path_cell))

# 规范化：把 {id} / {attId} / {policy_id} 统一为 {} 以便比对
def norm(p):
    return re.sub(r"\{[^}]+\}", "{}", p)

real_norm = sorted({(m, norm(p)) for m, p in real})
readme_norm = sorted({(m, norm(p)) for m, p in readme_api})

missing_in_readme = sorted(set(real_norm) - set(readme_norm))
extra_in_readme = sorted(set(readme_norm) - set(real_norm))

# ---- 3. 解析 docs/DEPLOY-ZSPACE.md 中的路由引用 ----
deploy = ROOT / "docs" / "DEPLOY-ZSPACE.md"
deploy_text = deploy.read_text(encoding="utf-8") if deploy.exists() else ""
deploy_routes = re.findall(r"/api/[A-Za-z0-9_\-/{}\.]+", deploy_text)
deploy_routes = sorted(set(deploy_routes))

out = {
    "real_route_count_from_openapi": len(real_sorted),
    "real_routes_from_openapi": [f"{m} {p}" for m, p in real_sorted],
    "real_route_count_from_app_routes": len(route_objs_sorted),
    "real_routes_from_app_routes": [f"{m} {p}" for m, p in route_objs_sorted],
    "openapi_vs_approutes_mismatch": sorted(
        set(f"{m} {p}" for m, p in real_sorted) ^ set(f"{m} {p}" for m, p in route_objs_sorted)
    ),
    "readme_route_count": len(readme_norm),
    "readme_routes": [f"{m} {p}" for m, p in readme_norm],
    "missing_in_readme": [f"{m} {p}" for m, p in missing_in_readme],
    "extra_in_readme": [f"{m} {p}" for m, p in extra_in_readme],
    "deploy_zspace_route_refs": deploy_routes,
}

print(json.dumps(out, ensure_ascii=False, indent=2))
