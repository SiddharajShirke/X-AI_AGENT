from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings


def main() -> int:
    settings = Settings()
    missing = [
        name
        for name, value in {
            "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token,
            "TELEGRAM_WEBHOOK_SECRET": settings.telegram_webhook_secret,
            "BASE_URL": settings.base_url,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing values: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not settings.base_url.startswith("https://"):
        print("Telegram webhooks require a public HTTPS BASE_URL.", file=sys.stderr)
        return 2
    url = f"{settings.base_url.rstrip('/')}/integrations/telegram/webhook"
    response = httpx.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
        json={"url": url, "secret_token": settings.telegram_webhook_secret},
        timeout=15.0,
    )
    response.raise_for_status()
    print(response.json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
