@echo off
rem Chaishu Workbench - double-click to start (Windows)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set CHAISHU_OPEN_BROWSER=1
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python server.py
  ) else (
    echo Python 3.9+ is required: https://www.python.org/downloads/
  )
)
pause
