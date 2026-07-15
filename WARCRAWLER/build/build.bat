@echo off
REM Build a single-file warcrawler.exe for Windows. Run on a Windows machine.
setlocal
cd /d "%~dp0\.."

python -m venv .buildenv
call .buildenv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

pyinstaller --onefile --clean --noconfirm --name warcrawler ^
  --collect-all trafilatura --collect-all courlan --collect-all justext ^
  --collect-submodules warcrawler ^
  --hidden-import cssselect --hidden-import lxml._elementpath ^
  --hidden-import httpx_socks --hidden-import aiosqlite ^
  build\entry.py

if not exist bin mkdir bin
copy /Y dist\warcrawler.exe bin\warcrawler.exe
echo Built -> bin\warcrawler.exe
endlocal
