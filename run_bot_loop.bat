@echo off
REM ============================================================
REM  Bitcoin Trading Signal Bot - continuous mode
REM  Double-click to run the bot on a recurring schedule.
REM ============================================================

setlocal
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo.
echo  Starting Trading Signal Bot in loop mode (Ctrl+C to stop)...
echo.

"%PY%" bot.py --loop

pause
endlocal
