@echo off
REM Penny-Stock Day-Trading Signal Bot - standard mode (looped every 5 min)
REM Double-click to run. Press Ctrl+C in the window to stop.

cd /d "%~dp0"
py bot.py --loop
echo.
echo Bot stopped. Press any key to close this window.
pause >nul
