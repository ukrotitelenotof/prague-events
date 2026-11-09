@echo off
cd /d "%~dp0"
echo ============================================
echo  Prague Events - One-time Setup
echo ============================================
echo.

echo [1/2] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is in PATH.
    pause
    exit /b 1
)

echo.
echo [2/2] Creating daily Task Scheduler entry (07:00 every day)...
schtasks /create /tn "Prague Events Update" /tr "cmd /c cd /d \"%~dp0\" && python scraper.py" /sc daily /st 07:00 /f
if %errorlevel% neq 0 (
    echo WARNING: Could not create Task Scheduler entry.
    echo You can still run run_update.bat manually each morning.
) else (
    echo Task Scheduler entry created: "Prague Events Update" runs at 07:00 daily.
)

echo.
echo ============================================
echo  Setup complete!
echo  - Run run_update.bat any time to refresh.
echo  - Open index.html in your browser.
echo ============================================
pause
