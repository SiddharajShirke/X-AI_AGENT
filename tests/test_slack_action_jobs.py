from __future__ import annotations

import pytest

from app.models import SlackActionJob


def test_enqueue_slack_action_is_idempotent_and_secret_free(repository):
    account = repository.list_accounts()[0]
    context = repository.list_contexts(account.id)[0]
    draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="A pending post for explicit review.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    connection = repository.create_integration_connection(
        "slack", "Review", "encrypted-value"
    )

    first, first_created = repository.enqueue_slack_action(
        idempotency_key="digest-1",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )
    second, second_created = repository.enqueue_slack_action(
        idempotency_key="digest-1",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )

    assert isinstance(first, SlackActionJob)
    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert first.status == "pending"
    assert "encrypted-value" not in first.model_dump_json()


def test_claims_actions_atomically_and_sets_terminal_status(repository):
    account = repository.list_accounts()[0]
    context = repository.list_contexts(account.id)[0]
    draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="A pending post for action processing.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    connection = repository.create_integration_connection("slack", "Review", "encrypted")
    repository.enqueue_slack_action(
        idempotency_key="digest-1",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )
    repository.enqueue_slack_action(
        idempotency_key="digest-2",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="reject_draft",
        expected_live=False,
        reviewer="slack:U123:reviewer",
    )

    first = repository.claim_next_slack_action(stale_before="2000-01-01T00:00:00+00:00")
    second = repository.claim_next_slack_action(stale_before="2000-01-01T00:00:00+00:00")

    assert first is not None and first.status == "processing"
    assert second is not None and second.id != first.id

    completed = repository.complete_slack_action(
        first.x_account_id, first.id, result_draft_id=first.draft_id
    )
    failed = repository.fail_slack_action(
        second.x_account_id, second.id, "Publication mode changed"
    )

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert failed.status == "failed"
    assert failed.safe_error == "Publication mode changed"


def test_slack_action_reads_are_scoped_to_account_and_connection(repository):
    first_account = repository.list_accounts()[0]
    second_account = repository.create_account(
        name="Second Brand",
        handle="second_brand",
        timezone="UTC",
        copy_from_id=first_account.id,
    )
    connection = repository.create_integration_connection("slack", "Review", "encrypted")

    def enqueue_for(account_id: int, key: str):
        context = repository.list_contexts(account_id)[0]
        draft = repository.create_draft(
            account_id,
            context_id=context.id,
            schedule_id=None,
            text=f"A pending post for account {account_id}.",
            topic="review",
            source_summary="manual",
            status="pending",
            safety_status="safe",
            similarity_score=0.1,
            attempt=1,
            parent_draft_id=None,
            config_version=repository.current_config_version(account_id),
            expires_at=None,
            generator_provider="demo",
            prompt_snapshot="",
        )
        return repository.enqueue_slack_action(
            idempotency_key=key,
            connection_id=connection.id,
            x_account_id=account_id,
            draft_id=draft.id,
            action_id="approve_draft",
            expected_live=False,
            reviewer="slack:U123:reviewer",
        )[0]

    first_job = enqueue_for(first_account.id, "digest-first")
    second_job = enqueue_for(second_account.id, "digest-second")

    assert repository.latest_slack_action(first_account.id, connection.id).id == first_job.id
    assert repository.latest_slack_action(second_account.id, connection.id).id == second_job.id
    assert [job.id for job in repository.list_slack_actions(first_account.id)] == [first_job.id]
    with pytest.raises(KeyError):
        repository.get_slack_action_job(first_account.id, second_job.id)
