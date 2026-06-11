@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================================
REM  RO Localization Tool launcher
REM  - Does NOT kill your normal Chrome windows.
REM  - Launches a dedicated debugging profile (chrome-session).
REM  - If the debug port is already open, reuses it (no restart).
REM ============================================================

set "PORT=9222"
set "SESSION_DIR=%~dp0chrome-session"

REM ---- locate chrome.exe ----
set "CHROME="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not defined CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined CHROME (
    echo [ERROR] chrome.exe not found. Please check the install path.
    pause
    exit /b 1
)

REM ---- check if debug port is already listening ----
set "PORT_OPEN="
for /f %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING" 2^>nul') do set "PORT_OPEN=1"

if defined PORT_OPEN (
    echo [INFO] Debug Chrome already running on port %PORT%. Reusing it.
    goto run_python
)

REM ---- launch dedicated-profile Chrome as a separate instance ----
echo [INFO] Launching translation Chrome ^(your normal Chrome is left alone^)...
start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%SESSION_DIR%" --no-first-run --no-default-browser-check --no-restore-last-session --disable-session-crashed-bubble --disable-infobars --disable-background-timer-throttling --disable-renderer-backgrounding

REM ---- wait until the port is actually open (max ~10s) ----
echo [INFO] Waiting for Chrome to be ready...
for /l %%i in (1,1,20) do (
    timeout /t 1 /nobreak >nul
    set "READY="
    for /f %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING" 2^>nul') do set "READY=1"
    if defined READY goto run_python
)
echo [WARN] Debug port did not open in time. Launching the tool anyway.

:run_python
echo [INFO] Starting RO Localization Tool...
start "" pythonw main_ui.py
exit /b 0
