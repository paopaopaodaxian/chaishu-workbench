#!/bin/bash
# 打包 macOS 应用：生成 dist/拆书工作台.app 和 dist/ChaiShu-<版本>-macOS.zip
# （zip 用英文文件名：GitHub Releases 会删掉附件名里的中文）
# 用法：scripts/build_macos_app.sh [版本号]      （不依赖 macOS，Linux 上也能打包）
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(cat VERSION)}"
VERSION="${VERSION#v}"
APP="dist/拆书工作台.app"
rm -rf dist && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
cp -R server.py core static README.md LICENSE "$APP/Contents/Resources/app/"
echo "$VERSION" > "$APP/Contents/Resources/app/VERSION"
find "$APP" -name __pycache__ -prune -exec rm -rf {} +
cp macos/launcher "$APP/Contents/MacOS/launcher" && chmod 755 "$APP/Contents/MacOS/launcher"
cp macos/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
sed "s/__VERSION__/$VERSION/g" macos/Info.plist > "$APP/Contents/Info.plist"
# 用 Python 打 zip：文件名一律按 UTF-8 写入并保留可执行权限，不受打包机器语言环境影响
python3 - "$VERSION" <<'PY'
import os, sys, zipfile
out = f"dist/ChaiShu-{sys.argv[1]}-macOS.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk("dist/拆书工作台.app"):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            arc = os.path.relpath(path, "dist")
            info = zipfile.ZipInfo.from_file(path, arc)
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as f:
                z.writestr(info, f.read())
PY
echo "已生成 dist/ChaiShu-$VERSION-macOS.zip"
