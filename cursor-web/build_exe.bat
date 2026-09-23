:: One-time packaging: builds CursorWebAssistant.exe (no Python needed to RUN it).
:: Result: dist\CursorWebAssistant.exe - copy it anywhere and double-click.
:: The token files and the extension\ folder are created NEXT TO the exe on first run.
@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Build CursorWebAssistant.exe

echo.
echo   === 打包 CursorWebAssistant.exe ===
echo.

if not exist "%~dp0app.py" (
    echo   错误：找不到 app.py（文件不全？重新 git pull 试试）。
    pause
    exit /b 1
)

REM --- Find Python (same rules as start.bat) ---------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    for /f "delims=" %%v in ('python -V 2^>nul') do if not "%%v"=="" set "PY=python"
)
if not defined PY (
    for %%V in (3.13 3.12 3.11 3.10 3.9) do if not defined PY (
        if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        if not defined PY if exist "C:\Python%%V\python.exe" set "PY=C:\Python%%V\python.exe"
    )
)
if not defined PY (
    echo   未找到 Python（打包需要 Python，运行 exe 不需要）。
    echo   到 https://www.python.org/downloads/ 安装并勾选 "Add python.exe to PATH"，再来。
    pause
    exit /b 1
)
echo   [1/3] 使用 Python: %PY%

echo.
echo   [2/3] 安装打包依赖 + 刷新扩展文件（首次约 1-2 分钟）...
%PY% -m pip install -r requirements-desktop.txt
if errorlevel 1 (
    echo   pip 安装失败：请检查网络后重试（或手动执行: %PY% -m pip install -r requirements-desktop.txt）
    pause
    exit /b 1
)
%PY% prepare_extension.py
if errorlevel 1 (
    echo   prepare_extension.py 失败
    pause
    exit /b 1
)

echo.
echo   [3/3] 打包中（PyInstaller 约 2-5 分钟，请耐心等待）...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name CursorWebAssistant ^
  --add-data "web;web" --add-data "extension;extension" ^
  --hidden-import webview.platforms.edgechromium ^
  --hidden-import webview.platforms.winforms ^
  --hidden-import appdirs ^
  --hidden-import uvicorn.loops.auto ^
  app.py
if errorlevel 1 (
    echo.
    echo   打包失败，上方有错误信息。
    pause
    exit /b 1
)

echo.
echo   ================================================
echo   完成！程序在:  %~dp0dist\CursorWebAssistant.exe
echo.
echo   以后每天只需双击 dist\CursorWebAssistant.exe
echo   （可右键"创建快捷方式"放到桌面）。
echo   首次运行会在 exe 同目录生成 extension\ 和令牌文件。
echo   注意：exe 和 git 仓库是分开的——git pull 更新代码后重新
echo   双击本文件打包一次即可。
echo   ================================================
pause
