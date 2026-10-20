# dmgbuild 配置：生成带「拖到应用程序」引导的安装窗口
# 用法（只能在 macOS 上运行）：
#   dmgbuild -s macos/dmg_settings.py -D app=dist/拆书工作台.app -D background=dist/dmg-background.tiff "拆书工作台" dist/xxx.dmg
import os.path

app = defines["app"]  # noqa: F821  （dmgbuild 注入）
app_name = os.path.basename(app)

format = "UDZO"
filesystem = "HFS+"
files = [app]
symlinks = {"Applications": "/Applications"}
icon = os.path.join(app, "Contents", "Resources", "AppIcon.icns")

background = defines.get("background", "builtin-arrow")  # noqa: F821
window_rect = ((200, 120), (640, 400))
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 96
text_size = 13
icon_locations = {app_name: (170, 215), "Applications": (470, 215)}
