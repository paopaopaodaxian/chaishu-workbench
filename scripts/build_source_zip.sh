#!/bin/bash
# 打包通用版（Windows / Linux，或想从源码运行的 Mac 用户）：dist/ChaiShu-<版本>.zip
# 用法：scripts/build_source_zip.sh [版本号]
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(cat VERSION)}"
VERSION="${VERSION#v}"
mkdir -p dist
python3 - "$VERSION" <<'PY'
import os, sys, zipfile
v = sys.argv[1]
top = f"chaishu-workbench-{v}"
out = f"dist/ChaiShu-{v}.zip"
items = ["server.py", "core", "static", "README.md", "LICENSE", "start-windows.bat", "start.sh"]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for item in items:
        paths = [item] if os.path.isfile(item) else sorted(
            os.path.join(r, f) for r, d, fs in os.walk(item) if "__pycache__" not in r for f in fs)
        for p in paths:
            info = zipfile.ZipInfo.from_file(p, f"{top}/{p}")
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as f:
                z.writestr(info, f.read())
    vi = zipfile.ZipInfo(f"{top}/VERSION")
    vi.external_attr = 0o100644 << 16
    z.writestr(vi, v + "\n")
print(f"已生成 {out}")
PY
