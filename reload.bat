@echo off
rem Rebuild the website and serve it with auto-restart on code changes (http://localhost:8000/).
cd /d "%~dp0"
call "%~dp0build.bat" || exit /b 1
set PYTHONPATH=src
start "" http://localhost:8000/
python -m partylinks serve --reload
