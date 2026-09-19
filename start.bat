:: SPDX-License-Identifier: GPL-3.0-or-later
@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title ZeroScript 桥接
cd /d "%~dp0"

if not exist "%~dp0logs" mkdir "%~dp0logs" >nul 2>nul
set "LOGFILE=%~dp0logs\start.log"
call :log "===== %DATE% %TIME%  start.bat launched ====="
REM Log the Windows build once per launch - most support requests arrive as a
REM single terminal screenshot, so anything that identifies the machine's
REM environment must be either ON SCREEN or in this log.
for /f "tokens=*" %%v in ('ver') do call :log "%%v"

echo.
echo   === ZeroScript 桥接 ===
echo.

REM Refuse to run from inside a ZIP preview: Explorer extracts start.bat alone
REM to %TEMP%, so bridge.py is missing and the launch fails with a confusing
REM Python error. Detect the missing file up front with a plain explanation -
REM this is one of the most common first-run mistakes.
if not exist "%~dp0bridge.py" (
    echo   错误：start.bat 旁边没有找到 bridge.py。
    echo.
    echo   如果你是直接在下载的 ZIP 压缩包里打开的 start.bat，请先解压整个 ZIP
    echo   ^（右键，"全部解压缩..."^），再从解压出来的文件夹里运行 start.bat。
    echo.
    call :log "FATAL: bridge.py missing next to start.bat (run from inside ZIP?)."
    pause
    exit /b 1
)

REM --- 1. Find Python ---------------------------------------------------------
echo   [1/3] 正在查找 Python...
set "PY="

REM Prefer the py launcher - it never resolves to the Microsoft Store stub.
where py >nul 2>nul && set "PY=py -3"
call :validate_py && goto :found

REM Fall back to python on PATH, but skip the Store stub (WindowsApps) which
REM cannot run pip and silently fails.
set "PY=python"
call :validate_py && goto :found

REM Last resort: scan the standard install folders directly. Covers the common
REM case where Python was installed WITHOUT "Add to PATH" and without the py
REM launcher, so neither "py" nor "python" resolves. Newest version first.
for %%R in (
    "%LOCALAPPDATA%\Programs\Python"
    "%ProgramFiles%"
    "%ProgramFiles(x86)%"
) do (
    if exist "%%~R" (
        for /f "delims=" %%D in ('dir /b /ad /o-n "%%~R\Python3*" 2^>nul') do (
            if exist "%%~R\%%D\python.exe" (
                set PY="%%~R\%%D\python.exe"
                call :validate_py && goto :found
            )
        )
    )
)

set "PY="
call :log "Python not found on PATH or in standard install folders."
goto :need_install

:found
REM Print the exact interpreter VERSION on screen (not just the launcher name):
REM a user screenshot must tell us whether the failure is a too-old Python
REM without asking them to run anything else.
REM "call" prefix: when %PY% is a quoted full path (the no-PATH scan case), a
REM bare quoted command inside for /f trips cmd's leading-quote stripping rule;
REM call re-parses the line and keeps the quotes intact.
for /f "tokens=*" %%v in ('call %PY% --version 2^>^&1') do (
    echo         已找到: %PY%  ^(%%v^)
    call :log "Python found: %PY% (%%v)"
)
goto :install_deps

:need_install
REM --- Python not found, try winget -------------------------------------------
REM winget itself may be absent (LTSC / old Win10 / stripped installs). Without
REM this check the "winget" line fails with an unrelated "not recognized" error
REM that users screenshot without context - name the real problem instead.
where winget >nul 2>nul
if errorlevel 1 (
    echo   错误：本机没有安装 Python，也没有 winget ^(Windows 包管理器^)，
    echo   所以无法自动安装。
    echo.
    echo   请手动安装 Python: https://www.python.org/downloads/
    echo   重要：安装时勾选 "Add python.exe to PATH"，然后重新运行 start.bat。
    echo.
    call :log "FATAL: no Python and no winget on this machine."
    pause
    exit /b 1
)
echo         未找到，正在通过 winget 安装...
echo.
winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 call :log "winget install returned an error (see console output above)."
echo.
echo   正在再次检查...
set "PY=py -3"
call :validate_py && goto :ready
set "PY=python"
call :validate_py && goto :ready
REM A winget install does NOT refresh THIS console's PATH, so "py"/"python" can
REM stay unresolvable in the very session that installed them. Rescan the
REM standard install folders directly (same scan as the pre-install fallback)
REM before telling the user it failed - a plain restart-and-retry would have
REM worked, so we do its equivalent for them.
for %%R in (
    "%LOCALAPPDATA%\Programs\Python"
    "%ProgramFiles%"
    "%ProgramFiles(x86)%"
) do (
    if exist "%%~R" (
        for /f "delims=" %%D in ('dir /b /ad /o-n "%%~R\Python3*" 2^>nul') do (
            if exist "%%~R\%%D\python.exe" (
                set PY="%%~R\%%D\python.exe"
                call :validate_py && goto :ready
            )
        )
    )
)
echo.
echo   错误：安装之后仍然找不到 Python。
echo   请手动安装: https://www.python.org/downloads/
echo   安装时勾选 "Add python.exe to PATH"，然后重新运行本脚本。
echo.
call :log "FATAL: no usable Python found even after winget install."
pause
exit /b 1
:ready
echo         Python 已就绪！
call :log "Python ready after winget install: %PY%"

:install_deps
REM --- 2. Install websockets --------------------------------------------------
echo.
echo   [2/3] 正在检查 websockets 库...
%PY% -c "import websockets" >nul 2>nul
if errorlevel 1 (
    echo         正在安装 websockets - 仅首次需要...
    %PY% -m pip install --user websockets
    if errorlevel 1 (
        echo.
        echo   错误：无法安装 websockets ^(请看上面的 pip 输出^)。
        echo   常见原因：没有网络、防火墙/杀毒软件拦截了 pip，
        echo   或者这个 Python 的 pip 不可用。如果你用的是 Microsoft Store
        echo   版 Python，请改从 https://www.python.org/downloads/ 安装
        echo   ^（勾选 "Add to PATH"^）。
        echo.
        call :log "FATAL: pip install websockets failed."
        pause
        exit /b 1
    )
)
echo         OK
call :log "websockets library OK"

REM --- 3. Run the bridge ------------------------------------------------------
echo.
echo   [3/3] 正在启动桥接...

REM If a previous bridge is already listening on 17613, say so instead of
REM silently killing it - a double-launch is easy to do by mistake (e.g.
REM double-clicking start.bat twice) and should not look like nothing happened.
set "OLDPID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :17613 ^| findstr LISTENING 2^>nul') do (
    set "OLDPID=%%a"
)
if defined OLDPID (
    echo         端口上已经有一个正在运行的桥接 ^(pid !OLDPID!^)。
    echo         正在用这次的实例替换它...
    call :log "Killing previous bridge instance (pid !OLDPID!) on port 17613."
    taskkill /F /T /PID !OLDPID! >nul 2>nul
    REM Give Windows a moment to actually free the socket before we rebind it.
    timeout /t 1 /nobreak >nul
    set "STILLTHERE="
    for /f "tokens=5" %%a in ('netstat -aon ^| findstr :17613 ^| findstr LISTENING 2^>nul') do (
        set "STILLTHERE=%%a"
    )
    if defined STILLTHERE (
        echo.
        echo   警告：尝试关闭上一个桥接之后，端口 17613 仍被 pid !STILLTHERE! 占用。
        echo   如果下面的桥接启动失败，请在任务管理器里手动结束该进程
        echo   ^（或者重启 Windows^），然后重新运行 start.bat。
        echo.
        call :log "WARNING: port 17613 still held by pid !STILLTHERE! after taskkill."
    )
)

echo.
echo  ############################################################
echo  ##                                                        ##
echo  ##  请保持此终端窗口打开 - 不要关闭它                     ##
echo  ##  关闭此窗口后 ZeroScript 将无法工作。                  ##
echo  ##  只需最小化窗口，让它一直在后台运行。                  ##
echo  ##                                                        ##
echo  ############################################################
echo.
call :log "Launching bridge.py with %PY%"
%PY% "%~dp0bridge.py"
REM Show the exit code ON SCREEN, not only in the log: a screenshot of this
REM terminal is usually the only diagnostic we get, and "Bridge stopped" alone
REM does not say whether it crashed (non-zero) or was closed normally.
set "BRIDGE_EXIT=%errorlevel%"
call :log "bridge.py exited with code %BRIDGE_EXIT%"

echo.
if not "%BRIDGE_EXIT%"=="0" (
    echo   桥接异常退出，错误码 %BRIDGE_EXIT% - 请往上翻看 Python 打印的
    echo   错误信息，并把整个窗口的内容附在问题反馈里。
    echo   日志文件: logs\start.log
) else (
    echo   桥接已正常停止。
)
echo   按任意键关闭窗口。
pause >nul
exit /b 0

REM --- Subroutine: verify %PY% is a real, usable Python ------------------------
REM Returns 0 only if the interpreter runs, has a working pip, AND is Python 3.9
REM or newer. The pip check rejects the Microsoft Store stub (WindowsApps\
REM python.exe). The version check rejects old interpreters (e.g. 3.7/3.8) that
REM lack asyncio.to_thread, which the bridge requires.
:validate_py
%PY% -m pip --version >nul 2>nul || exit /b 1
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
exit /b %errorlevel%

REM --- Subroutine: append a line to start.log (best-effort, never blocks) -----
:log
REM Redirect FIRST, then echo. With the redirect at the end, a message that
REM ENDS IN A DIGIT (e.g. "exited with code 0") makes cmd parse "0>>" as a
REM file-handle redirect: the digit is eaten and the line prints to the console
REM instead of the log (seen live). echo( is the safe echo form for arbitrary text.
>>"%LOGFILE%" 2>nul echo(%~1
exit /b 0
