@echo off
REM Penny-Stock Day-Trading Signal Bot - FAST MOVERS mode (looped every 5 min)
REM Prioritizes stocks accelerating in the last 30 minutes.
REM Higher reward potential, higher risk. Double-click to run.

cd /d "%~dp0"
py bot.py --loop --fast
echo.
echo Bot stopped. Press any key to close this window.
pause >nul
