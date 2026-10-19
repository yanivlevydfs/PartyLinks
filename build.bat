@echo off
rem Build the React website into web\dist (served by start.bat at http://localhost:8000/).
cd /d "%~dp0web"
call npm install --no-audit --no-fund || exit /b 1
call npm run build || exit /b 1
echo.
echo Built web\dist. Run start.bat and open http://localhost:8000/
