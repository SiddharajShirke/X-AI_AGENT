$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
