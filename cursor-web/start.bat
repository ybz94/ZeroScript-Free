:: One-click launcher: Cursor Web Assistant (Bridge + model endpoint).
:: Replaces the old three-step start: bridge.py -> --sessions -> --session <id>.
@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Cursor Web Assistant (one-click)

echo.
echo   === Cursor Web Assistant - 一键启动 ===
echo.

if not exist "%~dp0bridge.py" (
    echo   错误：start.bat 旁边没有找到 bridge.py（文件不全？重新 git pull 试试）。
    pause
    exit /b 1
)

REM --- Find Python: prefer the py launcher, then PATH python (skipping the
REM Microsoft Store stub), then the standard per-user install folders -------
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
    echo   未找到 Python。请到 https://www.python.org/downloads/ 安装，
    echo   安装时勾选 "Add python.exe to PATH"，然后重新运行本文件。
    pause
    exit /b 1
)
echo   使用 Python: %PY%

%PY% launcher.py %*
pause
