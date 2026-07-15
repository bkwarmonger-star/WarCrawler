@echo off
REM Portable launcher (Windows). Prebuilt exe if present, else bootstrap venv.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if exist "bin\warcrawler.exe" (
  "bin\warcrawler.exe" %*
  goto :eof
)
if exist "bin\windows\warcrawler.exe" (
  "bin\windows\warcrawler.exe" %*
  goto :eof
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9+ not found. Install Python or build a binary. See README.md
  exit /b 1
)
if not exist ".venv" (
  echo [warcrawler] first run: creating .venv and installing dependencies...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\pip.exe" install -r requirements.txt
)
".venv\Scripts\python.exe" -m warcrawler %*
endlocal
