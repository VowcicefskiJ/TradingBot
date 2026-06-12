@echo off
REM Penny-Stock Day-Trading Signal Bot - one scan, then exit.
REM Double-click to get a single snapshot of buy candidates + watchlist.

cd /d "%~dp0"
py bot.py
echo.
echo Press any key to close this window.
pause >nul
