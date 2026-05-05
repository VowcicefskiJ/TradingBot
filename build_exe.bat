@echo off
REM ============================================================
REM  Build a standalone TradingBot.exe with PyInstaller.
REM  Run this ONCE on your Windows machine. The resulting exe
REM  will be at: dist\TradingBot.exe
REM  You can then copy that exe (or a shortcut to it) to your
REM  desktop and double-click to launch the bot.
REM ============================================================

setlocal
cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo.
echo  Installing build dependencies...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
"%PY%" -m pip install pyinstaller

echo.
echo  Building TradingBot.exe (this takes a minute)...
"%PY%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --console ^
    --name TradingBot ^
    --collect-all robin_stocks ^
    --collect-all ta ^
    bot.py

if exist "dist\TradingBot.exe" (
    echo.
    echo  Build complete: dist\TradingBot.exe
    echo  Copy that exe to your desktop and double-click to run.
) else (
    echo.
    echo  Build FAILED. Check the output above for errors.
)

echo.
pause
endlocal
