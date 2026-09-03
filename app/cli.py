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
        account = repository.list_accounts()[0]
        if not repository.list_trends(account.id):
            repository.add_trend(
                account.id,
                title="Teams are discussing reliable agent handoffs",
                summary="A sample signal about visibility, handoffs, and human control.",
                source="seed-demo",
            )
        print(f"Seeded {args.database_path}")
        print(f"Account @{account.handle} has 10 schedule slots and 10 content contexts ready.")
        return 0

    if args.command == "show-state":
        state = []
        for account in repository.list_accounts():
            profile = repository.get_profile(account.id)
            state.append(
                {
                    "x_account_id": account.id,
                    "handle": account.handle,
                    "startup": profile.name,
                    "domain": profile.domain,
                    "configuration_version": repository.current_config_version(account.id),
                    "contexts": len(repository.list_contexts(account.id)),
                    "schedule_slots": len(repository.list_schedules(account.id)),
                    "drafts": len(repository.list_drafts(account.id)),
                    "learned_preferences": len(repository.list_preferences(account.id)),
                }
            )
        print(json.dumps(state, indent=2))
        return 0

    accounts = repository.list_accounts()
    account_one = accounts[0]
    account_two = next((item for item in accounts[1:] if item.handle == "demo_second"), None)
    if account_two is None:
        account_two = repository.create_account(
            "Demo Account 2",
            "demo_second",
            "UTC",
            copy_from_id=account_one.id,
        )
    repository.update_profile(
        account_one.id, {"domain": "Founder workflow reliability"}
    )
    repository.update_profile(
        account_two.id, {"domain": "Independent market research"}
    )

    first_context = repository.list_contexts(account_one.id)[0]
    first = services.pipeline.generate_draft(
        account_one.id, context_id=first_context.id
    )
    print(f"Account 1 (@{account_one.handle}): generated {first.text}")
    replacement = services.pipeline.reject_and_regenerate(
        account_one.id,
        first.id,
        reason="too_generic",
        notes="Use a different, concrete founder observation.",
        reviewer="cli-demo-human",
    )
    if replacement is None:
        raise RuntimeError("Demo unexpectedly reached the attempt limit")
    print(f"Account 1: rejected and learned; replacement {replacement.text}")

    account_two_rejected = services.feedback_engine.memory_bundle(account_two.id)[1]
    if first.text in account_two_rejected or repository.list_feedback(account_two.id):
        raise RuntimeError("Cross-account learning isolation failed")
    print("Account 2: isolation verified; Account 1 feedback is absent")

    published_one = services.pipeline.approve(
        account_one.id, replacement.id, reviewer="cli-demo-human"
    )
    second_context = repository.list_contexts(account_two.id)[0]
    second_draft = services.pipeline.generate_draft(
        account_two.id, context_id=second_context.id
    )
    published_two = services.pipeline.approve(
        account_two.id, second_draft.id, reviewer="cli-demo-human"
    )
    print(
        f"Account 1: status={published_one.status}, provider={published_one.publisher_provider}"
    )
    print(
        f"Account 2 (@{account_two.handle}): status={published_two.status}, provider={published_two.publisher_provider}"
    )
    print("Both publications required explicit cli-demo-human approval actions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
