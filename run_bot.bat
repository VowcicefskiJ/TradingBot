@echo off
REM ============================================================
REM  Bitcoin Trading Signal Bot - one-click launcher
REM  Double-click this file from your desktop (or a shortcut to it)
REM  to run the bot without typing commands.
REM ============================================================

setlocal
cd /d "%~dp0"

REM Use the bundled venv if it exists, otherwise fall back to system python.
if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo.
echo  Starting Trading Signal Bot...
echo.

"%PY%" bot.py %*

echo.
echo  Bot finished. Press any key to close this window.
pause >nul
endlocal
