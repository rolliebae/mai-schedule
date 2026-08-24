@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo Rebuilding the full Fall 2026/27 MAI database...
py -m pip install -r requirements.txt >nul 2>&1
if errorlevel 1 python -m pip install -r requirements.txt
if errorlevel 1 goto :error

py refresh_database.py
set CODE=%errorlevel%
echo.
if "%CODE%"=="0" (
  echo Done. database_v413.json and database_v413.js are synchronized.
) else (
  echo Database rebuild finished with code %CODE%.
)
pause
exit /b %CODE%

:error
echo.
echo Could not install dependencies or start Python.
pause
exit /b 1
