from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def settings(tmp_path: Path):
    from app.config import Settings

    return Settings(
        app_mode="demo",
        database_path=str(tmp_path / "test.db"),
        base_url="http://testserver",
        admin_username="demo",
        admin_password="demo-pass",
        scheduler_enabled=False,
        buffer_live_posting=False,
        buffer_api_key="",
        buffer_channel_id="",
        groq_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
        slack_webhook_url="",
    )


@pytest.fixture()
def repository(settings):
    from app.db import Database
    from app.repository import Repository

    database = Database(settings.database_path)
    database.initialize()
    return Repository(database)


@pytest.fixture()
def pipeline(settings, repository):
    from app.services.factory import build_services

    return build_services(settings, repository).pipeline
