@echo off
title Trading Bot

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python and add it to your PATH.
    pause
    exit /b 1
)

:: Move to the folder this bat file lives in
cd /d "%~dp0"

:: Install/update dependencies silently
echo Installing dependencies...
python -m pip install -r requirements.txt -q

echo.
echo Choose a mode:
echo   1  ^|  Single scan then exit
echo   2  ^|  Loop every 5 minutes  (Ctrl+C to stop)
echo.
set /p choice="Enter 1 or 2: "

if "%choice%"=="2" (
    echo.
    echo Starting loop mode... press Ctrl+C to stop.
    echo.
    python bot.py --loop
) else (
    echo.
    python bot.py
)

echo.
pause
