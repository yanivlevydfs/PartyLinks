@echo off
rem Scrape the `home: true` sources from this PC's Israeli IP and upload them to Railway
rem (see scripts/home_runner.py). Windows Task Scheduler runs this twice a day.
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
echo ===== %date% %time% >> data\logs\home_runner.log
railway run --service web -- .venv\Scripts\python.exe scripts\home_runner.py >> data\logs\home_runner.log 2>&1
