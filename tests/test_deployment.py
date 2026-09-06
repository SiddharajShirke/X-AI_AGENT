from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_pins_sqlite_database_inside_persistent_volume():
    """Copying .env.example must not move the container database out of /data."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = compose.split("  x-agent:\n", 1)[1].split("\nvolumes:\n", 1)[0]

    match = re.search(
        r"(?m)^    environment:\s*\n(?:      .+\n)*?"
        r"      DATABASE_PATH:\s*([^\s#]+)\s*$",
        service,
    )

    assert match is not None, "Compose must override env_file DATABASE_PATH"
    assert match.group(1) == "/data/x_agent.db"
