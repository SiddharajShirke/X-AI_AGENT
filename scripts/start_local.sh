#!/usr/bin/env bash
set -euo pipefail
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
[ -f .env ] || cp .env.example .env
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
