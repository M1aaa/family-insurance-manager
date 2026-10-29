"""Step 1 (v3): 拉取 python:3.13-slim (linux/amd64)。

改进：
- 镜像站按实测速度排序（daocloud 825KB/s > 1ms 447KB/s > rat.dev 376KB/s）
- 分块 Range 下载（每块 4MB），单块超时 60s，卡住立刻重连续传
- 全程不调用 os.remove（本机「安全删除」沙箱会拦截）
"""
import gzip
import hashlib
import io
import json
import os
import ssl
import sys
import tarfile
import tempfile
import time
import urllib.request

WORK = os.environ.get("IMGBUILD_WORK") or os.path.join(tempfile.gettempdir(), "_imgbuild")
BASE = os.path.join(WORK, "base")
LAYERS = os.path.join(BASE, "layers")
REPO = "library/python"
REF = "3.13-slim"
CHUNK = 4 * 1024 * 1024

MIRRORS = [
    ("docker.m.daocloud.io", "https://m.daocloud.io/auth/token", "docker.m.daocloud.io"),
    ("docker.1ms.run", "https://docker.1ms.run/openapi/v1/auth/token", "docker.1ms.run"),
    ("hub.rat.dev", "https://docker.1ms.run/openapi/v1/auth/token", "docker.1ms.run"),
]

ACCEPT_INDEX = ", ".join([
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
])
ACCEPT_MANIFEST = ", ".join([
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                      urllib.request.HTTPSHandler(context=ctx))

TOKENS = {}


def open_url(url, accept=None, token=None, extra=None, timeout=60):
    h = {"User-Agent": "docker/24.0.0", "Accept-Encoding": "identity"}
    if accept:
        h["Accept"] = accept
    if token:
        h["Authorization"] = "Bearer " + token
    if extra:
        h.update(extra)
    return _OPENER.open(urllib.request.Request(url, headers=h), timeout=timeout)


def token_for(host, realm, service):
    if host in TOKENS:
        return TOKENS[host]
    u = "%s?service=%s&scope=repository:%s:pull" % (realm, service, REPO)
    d = json.loads(open_url(u).read())
    TOKENS[host] = d.get("token") or d.get("access_token")
    return TOKENS[host]


def api(path, accept=None, timeout=60):
    last = None
    for host, realm, service in MIRRORS:
        for _ in range(2):
            try:
                tok = token_for(host, realm, service)
                return json.loads(open_url("https://%s%s" % (host, path), accept=accept,
                                           token=tok, timeout=timeout).read())
            except Exception as e:  # noqa: BLE001
                last = e
                TOKENS.pop(host, None)
                time.sleep(1)
    raise last


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def is_valid(path, digest):
    return os.path.exists(path) and os.path.getsize(path) > 0 and \
        "sha256:" + sha256_file(path) == digest


def download_blob(digest, dest, size):
    if is_valid(dest, digest):
        print("    [cache] %s  %.1f MB" % (digest[:19], os.path.getsize(dest) / 1024 ** 2))
        return
    tmp = dest + ".part"
    if not os.path.exists(tmp):
        open(tmp, "wb").close()
    t_start = time.time()
    restarts = 0
    while True:
        have = os.path.getsize(tmp)
        if size and have >= size:
            if is_valid(tmp, digest):
                os.replace(tmp, dest)
                print("    [ok   ] %s  %.1f MB  %.0fs" %
                      (digest[:19], os.path.getsize(dest) / 1024 ** 2, time.time() - t_start))
                return
            print("      大小已足但摘要不符 -> 重下")
            open(tmp, "wb").close()
            restarts += 1
            if restarts > 3:
                raise SystemExit("反复失败: " + digest)
            continue
        end = have + CHUNK - 1
        got_any = False
        for host, realm, service in MIRRORS:
            try:
                tok = token_for(host, realm, service)
                r = open_url("https://%s/v2/%s/blobs/%s" % (host, REPO, digest), token=tok,
                             extra={"Range": "bytes=%d-%d" % (have, end)}, timeout=60)
                data = r.read()
                if not data:
                    continue
                if r.status == 200 and have:
                    open(tmp, "wb").close()
                    have = 0
                    with open(tmp, "wb") as f:
                        f.write(data)
                else:
                    with open(tmp, "ab") as f:
                        f.write(data)
                got_any = True
                break
            except Exception as e:  # noqa: BLE001
                TOKENS.pop(host, None)
                print("      %s 块失败 %s，换站重试" % (host, type(e).__name__), flush=True)
                time.sleep(0.5)
        if not got_any:
            print("      所有镜像站均失败，2s 后重试", flush=True)
            time.sleep(2)


def main():
    os.makedirs(LAYERS, exist_ok=True)
    print("[1] manifest list: %s:%s" % (REPO, REF))
    idx = api("/v2/%s/manifests/%s" % (REPO, REF), ACCEPT_INDEX)
    amd64 = next(e for e in idx["manifests"]
                 if e["platform"].get("os") == "linux" and e["platform"].get("architecture") == "amd64")
    print("    linux/amd64 =", amd64["digest"])

    print("[2] image manifest")
    man = api("/v2/%s/manifests/%s" % (REPO, amd64["digest"]), ACCEPT_MANIFEST)
    print("    config=%s  layers=%d" % (man["config"]["digest"][:19], len(man["layers"])))

    print("[3] config blob")
    download_blob(man["config"]["digest"], os.path.join(BASE, "config.json"), man["config"]["size"])

    print("[4] layer blobs")
    files = []
    for i, L in enumerate(man["layers"]):
        name = "%02d_%s.tar" % (i, L["digest"].split(":")[1][:16])
        print("  layer %d/%d  %.1f MB" % (i + 1, len(man["layers"]), L["size"] / 1024 ** 2))
        download_blob(L["digest"], os.path.join(LAYERS, name), L["size"])
        files.append(name)

    print("[5] 校验 diff_ids")
    cfg = json.load(open(os.path.join(BASE, "config.json"), "r", encoding="utf-8"))
    diff_ids = cfg["rootfs"]["diff_ids"]
    ok = len(files) == len(diff_ids)
    for f, want in zip(sorted(files), diff_ids):
        raw = open(os.path.join(LAYERS, f), "rb").read()
        data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        good = "sha256:" + hashlib.sha256(data).hexdigest() == want
        ok = ok and good
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
            n = sum(1 for _ in tf)
        print("    %s %s  raw=%.1fMB unpack=%.1fMB entries=%d" %
              ("OK " if good else "BAD", f[:22], len(raw) / 1024 ** 2, len(data) / 1024 ** 2, n))
    print("[5] 结果:", "全部通过" if ok else "不一致")
    if not ok:
        sys.exit(1)
    json.dump({"index_digest": amd64["digest"], "manifest": man,
               "diff_ids": diff_ids, "layer_files": sorted(files)},
              open(os.path.join(BASE, "pull_info.json"), "w", encoding="utf-8"), indent=2)
    print("\nDONE ->", BASE)


if __name__ == "__main__":
    main()
