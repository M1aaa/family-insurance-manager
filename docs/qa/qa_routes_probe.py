import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.main import app  # noqa: E402

print("total app.routes:", len(app.routes))
for r in app.routes:
    print(type(r).__name__, getattr(r, "path", None), getattr(r, "methods", None))
