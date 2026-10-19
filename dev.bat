@echo off
rem Development: API on :8000 in its own window, Vite dev server with hot reload on :5173.
cd /d "%~dp0"
start "PartyLinks API" cmd /k "set PYTHONPATH=src&& python -m partylinks serve"
cd web
if not exist node_modules call npm install --no-audit --no-fund || exit /b 1
start "" http://localhost:5173/
call npm run dev
