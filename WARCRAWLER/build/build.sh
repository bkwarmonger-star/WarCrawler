#!/usr/bin/env bash
# Build a single-file `warcrawler` binary for the CURRENT OS (Linux/macOS).
# PyInstaller does not cross-compile: run this on each OS you want to support.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/.."

PY="${PYTHON:-python3}"
"$PY" -m venv .buildenv
# shellcheck disable=SC1091
. .buildenv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt pyinstaller

pyinstaller --onefile --clean --noconfirm --name warcrawler \
  --collect-all trafilatura --collect-all courlan --collect-all justext \
  --collect-submodules warcrawler \
  --hidden-import cssselect --hidden-import lxml._elementpath \
  --hidden-import httpx_socks --hidden-import aiosqlite \
  build/entry.py

mkdir -p bin
cp dist/warcrawler bin/warcrawler
echo "Built -> bin/warcrawler   (copy the whole USB folder to deploy)"
