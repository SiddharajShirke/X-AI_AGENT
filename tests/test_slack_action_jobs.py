from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import httpx
import pytest
from cryptography.fernet import Fernet

from app.db import Database
from app.models import PublishResult, SlackActionJob
from app.repository import Repository
from app.services.factory import build_services
from app.services.slack_actions import SlackActionProcessor


def _pending_draft(repository, account_id: int):
    context = repository.list_contexts(account_id)[0]
    return repository.create_draft(
        account_id,
        context_id=context.id,
        schedule_id=None,
        text="A pending post for explicit Slack review.",
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


def _processor_services(settings, repository, handler):
    configured = settings.model_copy(
        update={
            "app_encryption_key": Fernet.generate_key().decode(),
            "buffer_live_posting": True,
            "buffer_api_url": "https://buffer.test/graphql",
            "slack_action_lease_seconds": 300,
            "slack_action_poll_seconds": 0.1,
        }
    )
    services = build_services(configured, repository)
    services.publishers._client = httpx.Client(transport=httpx.MockTransport(handler))
    return services


def test_processor_approval_publishes_through_account_scoped_pipeline(
    settings, repository
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "createPost": {
                        "__typename": "PostActionSuccess",
                        "post": {
                            "id": "buffer-post-1",
                            "externalLink": "https://buffer.test/post/1",
                            "status": "sent",
                            "shareMode": "shareNow",
                            "channelId": "channel-1",
                        },
                    }
                }
            },
            request=request,
        )

    services = _processor_services(settings, repository, handler)
    account = repository.list_accounts()[0]
    repository.update_account(account.id, {"live_posting_enabled": True})
    buffer_connection = services.integrations.save_connection(
        "buffer", "Buffer", {"api_key": "buffer-private-key"}
    )
    services.integrations.bind(
        account.id, "buffer", buffer_connection.id, "channel-1"
    )
    slack_connection = repository.create_integration_connection(
        "slack", "Slack", "encrypted"
    )
    pending = _pending_draft(repository, account.id)
    job, _ = repository.enqueue_slack_action(
        idempotency_key="processor-approval",
        connection_id=slack_connection.id,
        x_account_id=account.id,
        draft_id=pending.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )
    processor = SlackActionProcessor(
        services.pipeline.settings,
        repository,
        services.pipeline,
        services.notifiers,
    )

    processed = processor.tick()
    draft = repository.get_draft(account.id, pending.id)
    job = repository.get_slack_action_job(account.id, job.id)

    assert processed == 1
    assert len(requests) == 1
    assert draft.status == "published"
    assert draft.publisher_provider == "buffer"
    assert job.status == "completed"
    assert job.result_draft_id == draft.id
    with repository.database.connection() as conn:
        attempt = conn.execute(
            "SELECT origin, reviewer FROM publish_attempts WHERE draft_id = ?",
            (draft.id,),
        ).fetchone()
    assert dict(attempt) == {
        "origin": "slack",
        "reviewer": "slack:U123:reviewer",
    }
    slack_events = [
        event
        for event in reversed(repository.list_events(account.id))
        if event["event_type"].startswith("slack_action_")
    ]
    assert [event["event_type"] for event in slack_events] == [
        "slack_action_processing",
        "slack_action_completed",
    ]
    assert json.loads(slack_events[-1]["details_json"]) == {
        "x_account_id": account.id,
        "job_id": job.id,
        "action_id": "approve_draft",
        "draft_id": draft.id,
        "result_status": "published",
        "provider": "buffer",
    }


def _live_processor_job(settings, repository, *, expected_live=True):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "createPost": {
                        "__typename": "PostActionSuccess",
                        "post": {
                            "id": "buffer-post-recovery",
                            "externalLink": None,
                            "status": "sent",
                            "shareMode": "shareNow",
                            "channelId": "channel-recovery",
                        },
                    }
                }
            },
            request=request,
        )

    services = _processor_services(settings, repository, handler)
    account = repository.list_accounts()[0]
    repository.update_account(account.id, {"live_posting_enabled": True})
    buffer_connection = services.integrations.save_connection(
        "buffer", "Buffer recovery", {"api_key": "buffer-recovery-key"}
    )
    services.integrations.bind(
        account.id, "buffer", buffer_connection.id, "channel-recovery"
    )
    slack_connection = repository.create_integration_connection(
        "slack", "Slack recovery", "encrypted"
    )
    draft = _pending_draft(repository, account.id)
    job, _ = repository.enqueue_slack_action(
        idempotency_key="processor-recovery",
        connection_id=slack_connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=expected_live,
        reviewer="slack:U123:reviewer",
    )
    return services, account, slack_connection, draft, job, requests


def _make_job_stale(repository, job_id):
    claimed = repository.claim_next_slack_action("2000-01-01T00:00:00+00:00")
    assert claimed is not None and claimed.id == job_id
    with repository.database.connection() as conn:
        conn.execute(
            "UPDATE slack_action_jobs SET claimed_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", job_id),
        )


def _record_slack_status_notifications(services, account):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    services.notifiers.slack_client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )
    connection = services.integrations.save_connection(
        "slack",
        "Slack status notifications",
        {
            "webhook_url": "https://slack.test/status",
            "signing_secret": "slack-status-secret",
        },
    )
    services.integrations.bind(account.id, "slack", connection.id, "")
    return requests


def test_processor_recovers_stale_pending_job(settings, repository):
    services, account, _, draft, job, requests = _live_processor_job(
        settings, repository
    )
    _make_job_stale(repository, job.id)

    processed = services.slack_actions.tick()

    assert processed == 1
    assert len(requests) == 1
    assert repository.get_draft(account.id, draft.id).status == "published"
    assert repository.get_slack_action_job(account.id, job.id).status == "completed"


def test_processor_recovers_published_job_without_republication(settings, repository):
    services, account, _, draft, job, requests = _live_processor_job(
        settings, repository
    )
    _make_job_stale(repository, job.id)
    claimed = repository.claim_publish(
        account.id,
        draft.id,
        reviewer="slack:U123:reviewer",
        origin="slack",
    )
    assert claimed is not None
    _, attempt = claimed
    repository.complete_publish(
        account.id,
        draft.id,
        attempt.id,
        PublishResult(
            success=True,
            provider="buffer",
            external_post_id="already-published",
        ),
    )
    status_requests = _record_slack_status_notifications(services, account)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert requests == []
    assert recovered.status == "completed"
    assert recovered.result_draft_id == draft.id
    assert len(status_requests) == 1
    assert "Slack approval recovered: draft was already published" in (
        json.loads(status_requests[0].content.decode())["text"]
    )
    with repository.database.connection() as conn:
        attempts = conn.execute(
            "SELECT COUNT(*) FROM publish_attempts WHERE draft_id = ?", (draft.id,)
        ).fetchone()[0]
    assert attempts == 1


@pytest.mark.parametrize(
    ("terminal_status", "status_message"),
    [
        ("failed", "Slack approval recovered: publishing had already failed"),
        ("blocked", "Slack approval recovered: approval had already been blocked"),
    ],
)
def test_processor_notifies_recovered_approval_terminal_state(
    settings, repository, terminal_status, status_message
):
    services, account, _, draft, job, requests = _live_processor_job(
        settings, repository
    )
    _make_job_stale(repository, job.id)
    repository.update_draft(account.id, draft.id, status=terminal_status)
    status_requests = _record_slack_status_notifications(services, account)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert requests == []
    assert recovered.status == "completed"
    assert recovered.result_draft_id == draft.id
    assert len(status_requests) == 1
    assert status_message in json.loads(status_requests[0].content.decode())["text"]


def test_processor_fails_stale_publishing_job_for_reconciliation(settings, repository):
    services, account, _, draft, job, requests = _live_processor_job(
        settings, repository
    )
    _make_job_stale(repository, job.id)
    claimed = repository.claim_publish(
        account.id,
        draft.id,
        reviewer="slack:U123:reviewer",
        origin="slack",
    )
    assert claimed is not None
    status_requests = _record_slack_status_notifications(services, account)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert requests == []
    assert recovered.status == "failed"
    assert recovered.safe_error == (
        "Publication was interrupted; operator reconciliation is required"
    )
    assert repository.get_draft(account.id, draft.id).status == "publishing"
    assert len(status_requests) == 1
    assert recovered.safe_error in json.loads(status_requests[0].content.decode())["text"]


def test_processor_duplicate_callback_creates_one_publication_attempt(
    settings, repository
):
    services, account, slack_connection, draft, job, requests = _live_processor_job(
        settings, repository
    )
    duplicate, created = repository.enqueue_slack_action(
        idempotency_key="processor-recovery",
        connection_id=slack_connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )

    processed = services.slack_actions.tick()

    assert created is False
    assert duplicate.id == job.id
    assert processed == 1
    assert len(requests) == 1
    with repository.database.connection() as conn:
        attempts = conn.execute(
            "SELECT COUNT(*) FROM publish_attempts WHERE draft_id = ?", (draft.id,)
        ).fetchone()[0]
    assert attempts == 1


def test_processor_normalizes_publication_mode_error(settings, repository):
    services, account, _, _, job, requests = _live_processor_job(
        settings, repository, expected_live=False
    )

    processed = services.slack_actions.tick()

    failed = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert requests == []
    assert failed.status == "failed"
    assert failed.safe_error == "Publication mode changed; request a fresh Slack review"


def test_processor_rejection_regenerates_through_account_scoped_pipeline(
    settings, repository
):
    def handler(request):
        raise AssertionError("A rejection must not call Buffer")

    services = _processor_services(settings, repository, handler)
    account = repository.list_accounts()[0]
    slack_connection = repository.create_integration_connection(
        "slack", "Slack rejection", "encrypted"
    )
    pending = _pending_draft(repository, account.id)
    job, _ = repository.enqueue_slack_action(
        idempotency_key="processor-rejection",
        connection_id=slack_connection.id,
        x_account_id=account.id,
        draft_id=pending.id,
        action_id="reject_draft",
        expected_live=False,
        reviewer="slack:U123:reviewer",
    )

    processed = services.slack_actions.tick()

    original = repository.get_draft(account.id, pending.id)
    completed = repository.get_slack_action_job(account.id, job.id)
    replacement = repository.get_draft(account.id, completed.result_draft_id)
    assert processed == 1
    assert original.status == "rejected"
    assert replacement.status == "pending"
    assert replacement.parent_draft_id == original.id
    assert completed.status == "completed"


def _stale_rejection_job(settings, repository):
    def handler(request):
        raise AssertionError("Rejection recovery must not call Buffer")

    services = _processor_services(settings, repository, handler)
    account = repository.list_accounts()[0]
    connection = repository.create_integration_connection(
        "slack", "Slack rejection recovery", "encrypted"
    )
    original = _pending_draft(repository, account.id)
    job, _ = repository.enqueue_slack_action(
        idempotency_key="processor-rejection-recovery",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=original.id,
        action_id="reject_draft",
        expected_live=False,
        reviewer="slack:U123:reviewer",
    )
    _make_job_stale(repository, job.id)
    return services, account, original, job


@pytest.mark.parametrize("interrupted_status", ["rejecting", "rejected"])
def test_processor_reconciles_interrupted_rejection_without_child(
    settings, repository, interrupted_status
):
    services, account, original, job = _stale_rejection_job(settings, repository)
    repository.update_draft(account.id, original.id, status=interrupted_status)
    status_requests = _record_slack_status_notifications(services, account)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert recovered.status == "failed"
    assert recovered.safe_error == (
        "Rejection was interrupted; operator reconciliation is required"
    )
    assert recovered.result_draft_id is None
    assert len(status_requests) == 1
    assert recovered.safe_error in json.loads(status_requests[0].content.decode())["text"]


def test_processor_recovers_rejected_draft_with_generated_child(
    settings, repository
):
    services, account, original, job = _stale_rejection_job(settings, repository)
    repository.update_draft(account.id, original.id, status="rejected")
    child = repository.create_draft(
        account.id,
        context_id=original.context_id,
        schedule_id=original.schedule_id,
        text="A generated replacement after the recovered Slack rejection.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=original.attempt + 1,
        parent_draft_id=original.id,
        config_version=repository.current_config_version(account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    status_requests = _record_slack_status_notifications(services, account)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert recovered.status == "completed"
    assert recovered.result_draft_id == child.id
    assert len(status_requests) == 1
    assert "Slack rejection recovered: replacement draft is ready for review" in (
        json.loads(status_requests[0].content.decode())["text"]
    )


def test_processor_notifies_recovered_rejection_needing_guidance(
    settings, repository
):
    services, account, original, job = _stale_rejection_job(settings, repository)
    repository.update_draft(account.id, original.id, status="needs_guidance")
    status_requests = _record_slack_status_notifications(services, account)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert recovered.status == "completed"
    assert recovered.result_draft_id == original.id
    assert len(status_requests) == 1
    assert "Slack rejection recovered: operator guidance is required" in (
        json.loads(status_requests[0].content.decode())["text"]
    )


def test_processor_does_not_persist_or_log_unexpected_exception_text(
    settings, repository, monkeypatch, caplog
):
    services, account, _, _, job, requests = _live_processor_job(settings, repository)
    status_requests = _record_slack_status_notifications(services, account)

    def fail_approval(*args, **kwargs):
        raise RuntimeError("raw-secret-provider-response")

    monkeypatch.setattr(services.pipeline, "approve", fail_approval)

    processed = services.slack_actions.tick()

    failed = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert requests == []
    assert failed.status == "failed"
    assert failed.safe_error == "Unexpected Slack action processing failure"
    assert len(status_requests) == 1
    assert failed.safe_error in json.loads(status_requests[0].content.decode())["text"]
    assert "raw-secret-provider-response" not in caplog.text
    assert "raw-secret-provider-response" not in json.dumps(
        repository.list_events(account.id)
    )


def test_processor_reconciles_unexpected_failure_after_publication_claim(
    settings, repository, monkeypatch
):
    services, account, _, draft, job, requests = _live_processor_job(
        settings, repository
    )

    def fail_after_claim(account_id, draft_id, *, reviewer, origin, **kwargs):
        claimed = repository.claim_publish(
            account_id,
            draft_id,
            reviewer=reviewer,
            origin=origin,
        )
        assert claimed is not None
        raise RuntimeError("raw-error-after-publication-claim")

    monkeypatch.setattr(services.pipeline, "approve", fail_after_claim)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert requests == []
    assert repository.get_draft(account.id, draft.id).status == "publishing"
    assert recovered.status == "failed"
    assert recovered.safe_error == (
        "Publication was interrupted; operator reconciliation is required"
    )


def test_processor_completes_unexpected_failure_after_durable_publication(
    settings, repository, monkeypatch
):
    services, account, _, draft, job, requests = _live_processor_job(
        settings, repository
    )
    status_requests = _record_slack_status_notifications(services, account)

    def fail_after_publish(account_id, draft_id, *, reviewer, origin, **kwargs):
        claimed = repository.claim_publish(
            account_id,
            draft_id,
            reviewer=reviewer,
            origin=origin,
        )
        assert claimed is not None
        claimed_draft, attempt = claimed
        target = services.integrations.resolve_buffer(account_id)
        result = services.publishers.publish(
            claimed_draft, repository.get_account(account_id), target
        )
        published = repository.complete_publish(
            account_id, draft_id, attempt.id, result
        )
        assert published.status == "published"
        raise RuntimeError("raw-error-after-durable-publication")

    monkeypatch.setattr(services.pipeline, "approve", fail_after_publish)

    processed = services.slack_actions.tick()

    recovered = repository.get_slack_action_job(account.id, job.id)
    assert processed == 1
    assert len(requests) == 1
    assert repository.get_draft(account.id, draft.id).status == "published"
    assert recovered.status == "completed"
    assert recovered.result_draft_id == draft.id
    assert recovered.safe_error == ""
    assert len(status_requests) == 1
    assert "Slack approval recovered: draft was already published" in (
        json.loads(status_requests[0].content.decode())["text"]
    )


def test_processor_stop_during_tick_ends_worker_without_waiting_for_poll(
    settings, repository, monkeypatch
):
    services = build_services(
        settings.model_copy(
            update={
                "slack_action_lease_seconds": 300,
                "slack_action_poll_seconds": 60.0,
            }
        ),
        repository,
    )
    processor = services.slack_actions

    def stopping_tick():
        processor.stop()
        return 0

    monkeypatch.setattr(processor, "tick", stopping_tick)

    async def run_worker():
        await asyncio.wait_for(processor.run_forever(), timeout=0.5)

    asyncio.run(run_worker())


def test_processor_wake_during_tick_triggers_next_tick_without_poll_delay(
    settings, repository, monkeypatch
):
    services = build_services(
        settings.model_copy(
            update={
                "slack_action_lease_seconds": 300,
                "slack_action_poll_seconds": 60.0,
            }
        ),
        repository,
    )
    processor = services.slack_actions
    first_tick_started = Event()
    release_first_tick = Event()
    second_tick_finished = Event()
    calls = 0

    def controlled_tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            first_tick_started.set()
            assert release_first_tick.wait(timeout=2)
        else:
            second_tick_finished.set()
            processor.stop()
        return 0

    monkeypatch.setattr(processor, "tick", controlled_tick)

    async def run_worker():
        worker = asyncio.create_task(processor.run_forever())
        try:
            assert await asyncio.to_thread(first_tick_started.wait, 1)
            processor.wake()
            release_first_tick.set()
            assert await asyncio.to_thread(second_tick_finished.wait, 1)
            await asyncio.wait_for(worker, timeout=0.5)
        finally:
            processor.stop()
            await asyncio.wait_for(worker, timeout=0.5)

    asyncio.run(run_worker())


def test_processor_stop_before_worker_start_does_not_process_pending_job(
    settings, repository
):
    services, account, _, _, job, requests = _live_processor_job(settings, repository)
    services.slack_actions.stop()

    async def run_stopped_worker():
        await asyncio.wait_for(services.slack_actions.run_forever(), timeout=0.5)

    asyncio.run(run_stopped_worker())

    assert requests == []
    assert repository.get_slack_action_job(account.id, job.id).status == "pending"


def test_processor_stop_between_jobs_leaves_remaining_job_pending(
    settings, repository, monkeypatch
):
    services, account, slack_connection, _, first_job, requests = _live_processor_job(
        settings, repository
    )
    second_draft = _pending_draft(repository, account.id)
    second_job, _ = repository.enqueue_slack_action(
        idempotency_key="processor-stop-second-job",
        connection_id=slack_connection.id,
        x_account_id=account.id,
        draft_id=second_draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )
    process = services.slack_actions._process

    def process_then_stop(job):
        process(job)
        services.slack_actions.stop()

    monkeypatch.setattr(services.slack_actions, "_process", process_then_stop)

    processed = services.slack_actions.tick()

    assert processed == 1
    assert len(requests) == 1
    assert repository.get_slack_action_job(account.id, first_job.id).status == "completed"
    assert repository.get_slack_action_job(account.id, second_job.id).status == "pending"


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
