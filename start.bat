@echo off
rem API + scheduler + built website on http://localhost:8000/ (run build.bat first).
cd /d "%~dp0"
if not exist "web\dist\index.html" call "%~dp0build.bat" || exit /b 1
set PYTHONPATH=src
start "" http://localhost:8000/
python -m partylinks serve
