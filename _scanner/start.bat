@echo off
setlocal
cd /d "%~dp0"
title OrderBlock Scanner

rem ---- 1. Find Python, or install it once with winget ----
set "PY="
set "PYARGS="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py" && set "PYARGS=-3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto keys

echo.
echo  Python is not installed. Installing it now - this takes 1 to 3 minutes...
echo.
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto keys

echo.
echo  Could not install Python automatically.
echo  The Python download page will open. Install it, tick "Add python.exe to PATH",
echo  then double-click start.bat again.
start "" https://www.python.org/downloads/
pause
exit /b 1

rem ---- 2. Settings file (keys are entered on the dashboard: Connect Dhan) ----
:keys
if not exist .env copy .env.example .env >nul

rem ---- 3. Start the scanner (it opens in your browser) ----
echo  Starting OrderBlock Scanner. Keep this window open while you use it.
"%PY%" %PYARGS% app.py %*
if errorlevel 1 pause
