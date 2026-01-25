#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv"

cd "$ROOT"

if ! command -v git >/dev/null 2>&1; then
  echo "git not found. Please install git first."
  exit 1
fi

if [ ! -d ".git" ]; then
  echo "This directory is not a git repo: ${ROOT}"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Uncommitted changes detected. Commit or stash before updating."
  exit 1
fi

echo "Updating repo..."
git pull --ff-only

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Please install Python 3 first."
  exit 1
fi

if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi

PIP="${VENV}/bin/pip"
UVICORN="${VENV}/bin/uvicorn"

if [ ! -x "$PIP" ]; then
  echo "pip not found in venv at ${PIP}"
  exit 1
fi

echo "Installing requirements..."
"$PIP" install -r requirements.txt

if [ ! -x "$UVICORN" ]; then
  echo "uvicorn not found in venv at ${UVICORN}"
  exit 1
fi

exec "$UVICORN" app.main:app --host 0.0.0.0 --port 8000
