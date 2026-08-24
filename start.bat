@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "APP_VERSION=4.1.7"

echo MAI Schedule %APP_VERSION%
echo Project: %CD%
echo.

where py >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_CMD=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 goto :no_python
  set "PYTHON_CMD=python"
)

echo [1/3] Checking dependencies...
%PYTHON_CMD% -m pip install -r requirements.txt >nul 2>&1
if errorlevel 1 (
  echo Dependency install failed. Retrying with visible output...
  %PYTHON_CMD% -m pip install -r requirements.txt
)
if errorlevel 1 goto :error

echo [2/3] Checking local Fall 2026 database...
%PYTHON_CMD% refresh_database.py --if-needed
if errorlevel 1 (
  echo.
  echo Full database refresh failed. The UI will still start.
  echo Live checks for a single group can continue through the local server.
  echo Run update_database.bat to retry the full rebuild.
  echo.
)

echo [3/3] Starting MAI Schedule %APP_VERSION%...
%PYTHON_CMD% server.py --port 8765 --port-attempts 20
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Server stopped with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%

:no_python
echo.
echo Python 3 was not found.
echo Install Python 3 and make sure either "py" or "python" is available in PATH.
pause
exit /b 1

:error
echo.
echo Could not install dependencies.
pause
exit /b 1
