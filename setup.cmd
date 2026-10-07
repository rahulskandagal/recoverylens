@echo off
REM One-time setup: Python virtualenv + backend deps, frontend deps.
setlocal

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found. Install Python 3.12 or newer from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during installation, then run setup.cmd again.
  pause & exit /b 1
)
where npm.cmd >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js was not found. Install the LTS version from https://nodejs.org/ then run setup.cmd again.
  pause & exit /b 1
)

echo === Installing backend (Python) dependencies ===
cd /d "%~dp0backend"
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] Backend install failed - see the messages above. & pause & exit /b 1 )
if exist "%~dp0presentation_cases\*.zip" (
  echo === Loading pre-analysed presentation cases ===
  for %%f in ("%~dp0presentation_cases\*.zip") do .venv\Scripts\python -m scripts.case_bundle import "%%f"
)

echo === Installing frontend (Node) dependencies ===
cd /d "%~dp0frontend"
call npm.cmd install
if errorlevel 1 ( echo [ERROR] Frontend install failed - see the messages above. & pause & exit /b 1 )

echo.
echo Setup complete. Double-click start-dev.cmd to launch RecoveryLens.
pause
