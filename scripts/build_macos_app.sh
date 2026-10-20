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
find "$APP" -name __pycache__ -prune -exec rm -rf {} +
cp macos/launcher "$APP/Contents/MacOS/launcher" && chmod 755 "$APP/Contents/MacOS/launcher"
cp macos/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
sed "s/__VERSION__/$VERSION/g" macos/Info.plist > "$APP/Contents/Info.plist"
(cd dist && zip -qry "ChaiShu-$VERSION-macOS.zip" "拆书工作台.app")
echo "已生成 dist/ChaiShu-$VERSION-macOS.zip"
