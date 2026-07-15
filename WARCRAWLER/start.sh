#!/usr/bin/env bash
# Portable launcher (Linux/macOS). Runs a prebuilt binary if present, else
# bootstraps a local .venv on the stick and runs from source.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"

# 1) Prebuilt one-file binary (no Python needed).
for b in "bin/warcrawler" "bin/${OS}/warcrawler"; do
  if [ -x "$b" ]; then exec "$b" "$@"; fi
done

# 2) Source mode: create/reuse a venv on the stick.
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.9+ not found. Install Python or build a binary (see README.md)."
  exit 1
fi
if [ ! -d ".venv" ]; then
  echo "[warcrawler] first run: creating .venv and installing dependencies..."
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt
fi
exec ./.venv/bin/python -m warcrawler "$@"
