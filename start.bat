@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [1/3] Checking dependencies...
py -m pip install -r requirements.txt >nul 2>&1
if errorlevel 1 python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [2/3] Checking local Fall 2026 database...
py refresh_database.py --if-needed
if errorlevel 1 (
  echo.
  echo Full database refresh failed. The UI will still start.
  echo Live checks for a single group can continue through the local server.
  echo Run update_database.bat to retry the full rebuild.
  echo.
)

echo [3/3] Starting MAI Schedule 4.1.6...
py server.py
if errorlevel 9009 python server.py
exit /b %errorlevel%

:error
echo.
echo Could not install dependencies or start Python.
pause
exit /b 1
