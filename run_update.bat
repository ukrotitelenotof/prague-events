@echo off
cd /d "%~dp0"
echo Updating Prague Cultural Events...
python scraper.py
echo.
echo Done! Open index.html in your browser.
pause
