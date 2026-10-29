"""Step 2: 下载 linux/amd64 + CPython 3.13 的依赖 wheel，并安装到层目录。

- 用清华源（本机可达）
- --only-binary=:all: 确保不出现源码包（镜像里没有编译器）
- pip install --target 得到「site-packages + bin/」两棵树
"""
import os
import shutil
import subprocess
import sys
import tempfile

WORK = os.environ.get("IMGBUILD_WORK") or os.path.join(tempfile.gettempdir(), "_imgbuild")
REPO = os.environ.get("IMGBUILD_REPO") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
WHEELS = os.path.join(WORK, "wheels")
DEPS = os.path.join(WORK, "layer_deps")
INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"

PLATFORMS = [
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux_2_28_x86_64",
    "manylinux_2_34_x86_64",
    "manylinux_2_35_x86_64",
    "manylinux_2_36_x86_64",
    "linux_x86_64",
]


def run(args, **kw):
    print("$", " ".join(args[:6]), "...", flush=True)
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if p.returncode != 0:
        print(p.stdout[-4000:])
        print(p.stderr[-4000:], file=sys.stderr)
        raise SystemExit("命令失败 rc=%d" % p.returncode)
    return p.stdout


def main():
    os.makedirs(WHEELS, exist_ok=True)
    # 注意：本机 IDE 的「安全删除」沙箱会拦截 rmtree/remove，因此不清理旧目录，
    # 改用全新目录名，保证「全新安装」语义。
    deps = DEPS
    if os.path.isdir(deps) and os.listdir(deps):
        import datetime
        deps = deps + "-" + datetime.datetime.now().strftime("%H%M%S")
        print("旧目录已存在，改用:", deps)
    os.makedirs(deps, exist_ok=True)

    plat = []
    for p in PLATFORMS:
        plat += ["--platform", p]

    req = os.path.join(REPO, "requirements.txt")

    print("=" * 70)
    print("[1] 下载 wheel（含 uvicorn[standard] 的 C 扩展 + tzdata）")
    args = [PY, "-m", "pip", "download",
            "-r", req,
            "tzdata",
            "-d", WHEELS,
            "--only-binary=:all:",
            "--python-version", "3.13",
            "--implementation", "cp",
            "--abi", "cp313",
            "--index-url", INDEX,
            "--disable-pip-version-check"] + plat
    out = run(args)
    print(out[-2500:])

    print("=" * 70)
    print("[2] 安装到层目录")
    args = [PY, "-m", "pip", "install",
            "--target", deps,
            "--no-index", "--find-links", WHEELS,
            "--only-binary=:all:",
            "--python-version", "3.13",
            "--implementation", "cp",
            "--abi", "cp313",
            "--disable-pip-version-check",
            "--no-compile"] + plat + ["-r", req, "tzdata"]
    out = run(args)
    print(out[-2500:])

    print("=" * 70)
    print("[3] 结果概览")
    for sub in ("", "bin"):
        d = os.path.join(deps, sub)
        if os.path.isdir(d):
            items = sorted(os.listdir(d))
            print("  %-6s %d 项: %s" % (sub or "(root)", len(items), ", ".join(items[:14])))
    print("\n  wheels 目录 %d 个文件" % len(os.listdir(WHEELS)))
    print("DONE ->", deps)


if __name__ == "__main__":
    main()
