from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or explicit values.

    Safe prototype defaults intentionally keep the application in demo mode,
    disable real Buffer posting, and require a human review action.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Startup X Agent"
    app_mode: str = "demo"
    database_path: str = "./data/x_agent.db"
    base_url: str = "http://localhost:8000"

    admin_username: str = "admin"
    admin_password: str = "change-me"
    app_encryption_key: str = ""
    app_csrf_secret: str = ""

    scheduler_enabled: bool = True
    scheduler_poll_seconds: int = Field(default=20, ge=5, le=300)
    slack_action_worker_enabled: bool = True
    slack_action_poll_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    slack_action_lease_seconds: int = Field(default=300, ge=30, le=3600)

    # ---------------------------------------------------------------------------
    # Groq content generation
    # ---------------------------------------------------------------------------
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_timeout_seconds: float = Field(default=30.0, ge=5.0, le=180.0)

    # ---------------------------------------------------------------------------
    # X trend research (Tweepy / bearer token — unchanged from before)
    # Do NOT use this for publishing; it is for X recent-search only.
    # ---------------------------------------------------------------------------
    twitter_bearer_token: str = ""

    # ---------------------------------------------------------------------------
    # Buffer publishing
    # Credentials and channel targets are stored encrypted in SQLite.
    # ---------------------------------------------------------------------------
    x_live_posting: bool = False
    buffer_live_posting: bool = False
    buffer_api_url: str = "https://api.buffer.com"
    buffer_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    buffer_share_mode: str = "shareNow"

    # ---------------------------------------------------------------------------
    # Notification channels
    # ---------------------------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = ""

    rss_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    max_generation_candidates: int = Field(default=5, ge=2, le=12)

    @field_validator("buffer_share_mode")
    @classmethod
    def _validate_share_mode(cls, v: str) -> str:
        allowed = {"shareNow", "addToQueue", "shareNext"}
        if v not in allowed:
            raise ValueError(f"buffer_share_mode must be one of {sorted(allowed)}, got {v!r}")
        return v

    @property
    def demo_mode(self) -> bool:
        return self.app_mode.lower() != "live"

    @property
    def database_directory(self) -> Path | None:
        if self.database_path == ":memory:":
            return None
        return Path(self.database_path).expanduser().resolve().parent
