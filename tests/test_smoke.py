from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_required_documentation_and_deployment_files_exist():
    root = Path(__file__).resolve().parents[1]
    required = [
        "README.md",
        "AGENTS.md",
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "Makefile",
        "docs/ARCHITECTURE.md",
        "docs/DEMO_SCRIPT.md",
        "docs/LIFECYCLE.md",
        "docs/CODEX_GUIDE.md",
        "docs/ANTIGRAVITY_GUIDE.md",
        "scripts/seed_demo.py",
        "scripts/set_telegram_webhook.py",
    ]
    missing = [item for item in required if not (root / item).exists()]
    assert missing == []


def test_application_imports():
    from app.main import create_app

    assert callable(create_app)


def test_application_assets_resolve_outside_repository_working_directory(
    settings, tmp_path, monkeypatch
):
    from app.main import create_app

    monkeypatch.chdir(tmp_path)
    app = create_app(settings=settings, start_scheduler=False)

    assert app.title == settings.app_name


def test_documented_demo_script_runs_from_repository_root():
    root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "scripts/demo_flow.py"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Rejected and learned" in result.stdout
    assert "status=published" in result.stdout
