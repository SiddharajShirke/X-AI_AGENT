from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.db import Database
from app.models import SlackActionJob
from app.repository import Repository


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


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("connection_id", "other_connection"),
        ("draft_id", "other_draft"),
        ("action_id", "reject_draft"),
        ("expected_live", False),
        ("reviewer", "slack:U999:other-reviewer"),
    ],
)
def test_enqueue_rejects_idempotency_key_reuse_for_different_request_fields(
    repository, changed_field, changed_value
):
    account = repository.list_accounts()[0]
    context = repository.list_contexts(account.id)[0]
    first_draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="First pending post.",
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
    second_draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="Second pending post.",
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
    other_connection = repository.create_integration_connection(
        "slack", "Other review", "encrypted"
    )
    repository.enqueue_slack_action(
        idempotency_key="same-digest",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=first_draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )
    request = {
        "idempotency_key": "same-digest",
        "connection_id": connection.id,
        "x_account_id": account.id,
        "draft_id": first_draft.id,
        "action_id": "approve_draft",
        "expected_live": True,
        "reviewer": "slack:U123:reviewer",
    }
    request[changed_field] = {
        "other_connection": other_connection.id,
        "other_draft": second_draft.id,
    }.get(changed_value, changed_value)

    with pytest.raises(ValueError, match="idempotency key"):
        repository.enqueue_slack_action(**request)


def test_enqueue_rejects_another_accounts_idempotency_key(repository):
    first_account = repository.list_accounts()[0]
    second_account = repository.create_account(
        name="Second Brand",
        handle="second_brand",
        timezone="UTC",
        copy_from_id=first_account.id,
    )
    connection = repository.create_integration_connection("slack", "Review", "encrypted")
    first_draft = repository.create_draft(
        first_account.id,
        context_id=repository.list_contexts(first_account.id)[0].id,
        schedule_id=None,
        text="First account post.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(first_account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    second_draft = repository.create_draft(
        second_account.id,
        context_id=repository.list_contexts(second_account.id)[0].id,
        schedule_id=None,
        text="Second account post.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(second_account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    repository.enqueue_slack_action(
        idempotency_key="shared-digest",
        connection_id=connection.id,
        x_account_id=first_account.id,
        draft_id=first_draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )

    with pytest.raises(ValueError, match="idempotency key"):
        repository.enqueue_slack_action(
            idempotency_key="shared-digest",
            connection_id=connection.id,
            x_account_id=second_account.id,
            draft_id=second_draft.id,
            action_id="approve_draft",
            expected_live=True,
            reviewer="slack:U123:reviewer",
        )


def test_claim_skips_fresh_actions_and_recovers_stale_actions(repository):
    account = repository.list_accounts()[0]
    draft = repository.create_draft(
        account.id,
        context_id=repository.list_contexts(account.id)[0].id,
        schedule_id=None,
        text="Pending post for stale recovery.",
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
    created, _ = repository.enqueue_slack_action(
        idempotency_key="stale-digest",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=False,
        reviewer="slack:U123:reviewer",
    )
    first_claim = repository.claim_next_slack_action("2000-01-01T00:00:00+00:00")

    assert first_claim is not None and first_claim.id == created.id
    assert repository.claim_next_slack_action("2000-01-01T00:00:00+00:00") is None

    with repository.database.connection() as conn:
        conn.execute(
            "UPDATE slack_action_jobs SET claimed_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", created.id),
        )

    recovered = repository.claim_next_slack_action("2001-01-01T00:00:00+00:00")

    assert recovered is not None
    assert recovered.id == created.id
    assert recovered.status == "processing"
    assert recovered.claimed_at != "2000-01-01T00:00:00+00:00"


def test_concurrent_connections_claim_one_action_once(repository):
    account = repository.list_accounts()[0]
    draft = repository.create_draft(
        account.id,
        context_id=repository.list_contexts(account.id)[0].id,
        schedule_id=None,
        text="Pending post for concurrent claim.",
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
    created, _ = repository.enqueue_slack_action(
        idempotency_key="race-digest",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=False,
        reviewer="slack:U123:reviewer",
    )
    barrier = Barrier(3)

    def claim_from_independent_connection():
        independent_repository = Repository(Database(repository.database.path))
        barrier.wait()
        return independent_repository.claim_next_slack_action(
            "2000-01-01T00:00:00+00:00"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim_from_independent_connection) for _ in range(2)]
        barrier.wait()
        results = [future.result() for future in futures]

    claims = [result for result in results if result is not None]
    assert [claim.id for claim in claims] == [created.id]
    assert repository.get_slack_action_job(account.id, created.id).status == "processing"


def test_cross_account_enqueue_and_terminal_updates_are_rejected(repository):
    first_account = repository.list_accounts()[0]
    second_account = repository.create_account(
        name="Second Brand",
        handle="second_brand",
        timezone="UTC",
        copy_from_id=first_account.id,
    )
    connection = repository.create_integration_connection("slack", "Review", "encrypted")
    first_draft = repository.create_draft(
        first_account.id,
        context_id=repository.list_contexts(first_account.id)[0].id,
        schedule_id=None,
        text="First account pending post.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(first_account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    second_draft = repository.create_draft(
        second_account.id,
        context_id=repository.list_contexts(second_account.id)[0].id,
        schedule_id=None,
        text="Second account pending post.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(second_account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )

    with pytest.raises(ValueError, match="does not belong"):
        repository.enqueue_slack_action(
            idempotency_key="cross-account-draft",
            connection_id=connection.id,
            x_account_id=first_account.id,
            draft_id=second_draft.id,
            action_id="approve_draft",
            expected_live=False,
            reviewer="slack:U123:reviewer",
        )

    job, _ = repository.enqueue_slack_action(
        idempotency_key="first-account-job",
        connection_id=connection.id,
        x_account_id=first_account.id,
        draft_id=first_draft.id,
        action_id="approve_draft",
        expected_live=False,
        reviewer="slack:U123:reviewer",
    )
    claimed = repository.claim_next_slack_action("2000-01-01T00:00:00+00:00")

    assert claimed is not None and claimed.id == job.id
    with pytest.raises(KeyError, match="not processing"):
        repository.complete_slack_action(
            second_account.id, job.id, result_draft_id=first_draft.id
        )
    with pytest.raises(KeyError, match="not processing"):
        repository.fail_slack_action(second_account.id, job.id, "safe error")
    assert repository.get_slack_action_job(first_account.id, job.id).status == "processing"
