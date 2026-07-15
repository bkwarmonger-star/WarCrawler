#!/usr/bin/env bash
# Run the test suite. Creates/uses a local venv if needed.
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
PY="${PYTHON:-python3}"
if [ ! -d ".venv" ]; then
  "$PY" -m venv .venv
fi
# Idempotent: ensures pytest is present even if .venv was created by start.sh.
./.venv/bin/pip install -q -r requirements-dev.txt
./.venv/bin/python -m pytest
