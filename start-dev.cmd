@echo off
REM Launch the API (port 8000) and the web UI (port 5173) in two windows.
if not exist "%~dp0backend\.venv" ( echo Run setup.cmd first. & pause & exit /b 1 )
if not exist "%~dp0frontend\node_modules" ( echo Run setup.cmd first. & pause & exit /b 1 )
start "RecoveryLens API" cmd /k "cd /d "%~dp0backend" && .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
start "RecoveryLens UI" cmd /k "cd /d "%~dp0frontend" && npm.cmd run dev"
REM first start generates the demo datasets; give both servers a moment before opening the browser
timeout /t 8 >nul
start "" http://localhost:5173/new
