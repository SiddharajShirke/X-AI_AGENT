from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.repository import Repository
from app.services.factory import build_services


def _runtime(database_path: str):
    settings = Settings(
        app_mode="demo",
        database_path=database_path,
        scheduler_enabled=False,
        buffer_live_posting=False,
        groq_api_key="",
        telegram_bot_token="",
        telegram_chat_id="",
        slack_webhook_url="",
    )
    database = Database(settings.database_path)
    database.initialize()
    repository = Repository(database)
    services = build_services(settings, repository)
    return settings, repository, services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Startup X Agent prototype CLI")
    parser.add_argument("--database-path", default="./data/x_agent.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="Initialize the database and sample configuration")
    sub.add_parser("show-state", help="Print a compact state summary")
    sub.add_parser("demo-flow", help="Run generate → reject → regenerate → approve")
    args = parser.parse_args(argv)

    _, repository, services = _runtime(args.database_path)
    if args.command == "seed":
        if not repository.list_trends():
            repository.add_trend(
                title="Teams are discussing reliable agent handoffs",
                summary="A sample signal about visibility, handoffs, and human control.",
                source="seed-demo",
            )
        print(f"Seeded {args.database_path}")
        print("10 schedule slots and 10 content contexts are ready.")
        return 0

    if args.command == "show-state":
        profile = repository.get_profile()
        state = {
            "startup": profile.name,
            "domain": profile.domain,
            "configuration_version": repository.current_config_version(),
            "contexts": len(repository.list_contexts()),
            "schedule_slots": len(repository.list_schedules()),
            "drafts": len(repository.list_drafts()),
            "learned_preferences": len(repository.list_preferences()),
        }
        print(json.dumps(state, indent=2))
        return 0

    first = services.pipeline.generate_draft(context_id=1)
    print(f"1. Generated: {first.text}")
    second = services.pipeline.reject_and_regenerate(
        first.id,
        reason="too_generic",
        notes="Use a different, concrete founder observation.",
        reviewer="cli-demo-human",
    )
    if second is None:
        raise RuntimeError("Demo unexpectedly reached the attempt limit")
    print(f"2. Rejected and learned. Replacement: {second.text}")
    published = services.pipeline.approve(second.id, reviewer="cli-demo-human")
    print(f"3. Approved: status={published.status}, provider={published.publisher_provider}")
    print(f"4. Demo URL: {published.post_url}")
    print(f"5. Learned rules: {len(repository.list_preferences())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
