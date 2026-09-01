#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="startup-x-agent-codex-prototype"
OUT="${ROOT}/../${NAME}.zip"
cd "$ROOT/.."
rm -f "$OUT"
zip -qr "$OUT" "$(basename "$ROOT")" \
  -x '*/.git/*' '*/.venv/*' '*/__pycache__/*' '*/.pytest_cache/*' \
     '*/.env' '*/data/*.db' '*/data/*.sqlite*' '*/htmlcov/*' '*/.coverage'
echo "$OUT"
