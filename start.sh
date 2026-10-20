#!/bin/sh
# 拆书工作台：Linux / macOS 从源码启动。启动后自动打开浏览器，关闭终端窗口即停止。
cd "$(dirname "$0")" || exit 1
CHAISHU_OPEN_BROWSER=1 exec python3 server.py
