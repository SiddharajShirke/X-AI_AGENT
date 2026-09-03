from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from cryptography.fernet import Fernet

from app.models import PublishResult
from app.services.factory import build_services
from app.services.integrations import IntegrationService
from app.services.pipeline import PipelineError


def _settings(settings, **updates):
    return settings.model_copy(update=updates)


def _pending(pipeline, repository, account_id: int):
    return pipeline.generate_draft(
        account_id, context_id=repository.list_contexts(account_id)[0].id
    )


class RecordingPublisher:
    def __init__(self, results: list[PublishResult] | None = None):
        self.calls = 0
        self.channel_ids: list[str | None] = []
        self._results = list(results or [])
        self._lock = Lock()

    def publish(self, draft, account, target):
        with self._lock:
            self.calls += 1
            self.channel_ids.append(target.channel_id if target else None)
            if self._results:
                return self._results.pop(0)
        return PublishResult(
            success=True,
            provider="buffer" if target else "dry_run",
            external_post_id=f"post-{draft.id}",
        )


def _live_pipeline(settings, repository):
    key = Fernet.generate_key().decode()
    configured = _settings(
        settings, app_encryption_key=key, buffer_live_posting=True
    )
    return build_services(configured, repository).pipeline, configured


def _bind_buffer(pipeline, account_id: int, channel_id: str):
    connection = pipeline.integrations.save_connection(
        "buffer", f"Buffer {channel_id}", {"api_key": f"key-{channel_id}"}
    )
    pipeline.integrations.bind(
        account_id, "buffer", connection.id, channel_id
    )


def test_two_accounts_publish_to_their_bound_channels(settings, repository):
    first = repository.list_accounts()[0]
    second = repository.create_account(
        "Second", "second", "UTC", copy_from_id=first.id
    )
    for account in (first, second):
        repository.update_account(account.id, {"live_posting_enabled": True})
    pipeline, _ = _live_pipeline(settings, repository)
    _bind_buffer(pipeline, first.id, "channel-a")
    _bind_buffer(pipeline, second.id, "channel-b")
    publisher = RecordingPublisher()
    pipeline.publishers = publisher

    pipeline.approve(first.id, _pending(pipeline, repository, first.id).id, reviewer="admin")
    pipeline.approve(second.id, _pending(pipeline, repository, second.id).id, reviewer="admin")

    assert publisher.channel_ids == ["channel-a", "channel-b"]


def test_repeated_approval_calls_publisher_once(pipeline, repository, x_account):
    publisher = RecordingPublisher()
    pipeline.publishers = publisher
    draft = _pending(pipeline, repository, x_account.id)

    first = pipeline.approve(x_account.id, draft.id, reviewer="admin")
    second = pipeline.approve(x_account.id, draft.id, reviewer="admin")

    assert publisher.calls == 1
    assert second.status == first.status


def test_overlapping_approvals_claim_only_once(pipeline, repository, x_account):
    started = Event()
    release = Event()

    class BlockingPublisher(RecordingPublisher):
        def publish(self, draft, account, target):
            with self._lock:
                self.calls += 1
            started.set()
            assert release.wait(timeout=5)
            return PublishResult(success=True, provider="dry_run")

    publisher = BlockingPublisher()
    pipeline.publishers = publisher
    draft = _pending(pipeline, repository, x_account.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            pipeline.approve, x_account.id, draft.id, reviewer="first"
        )
        assert started.wait(timeout=5)
        second = executor.submit(
            pipeline.approve, x_account.id, draft.id, reviewer="second"
        )
        second.result(timeout=5)
        release.set()
        first.result(timeout=5)

    assert publisher.calls == 1
    assert repository.get_draft(x_account.id, draft.id).status == "published"


def test_global_off_and_account_off_are_intentional_dry_runs(
    settings, repository, x_account
):
    repository.update_account(x_account.id, {"live_posting_enabled": True})
    global_off = build_services(settings, repository).pipeline
    first = global_off.approve(
        x_account.id,
        _pending(global_off, repository, x_account.id).id,
        reviewer="admin",
    )

    repository.update_account(x_account.id, {"live_posting_enabled": False})
    account_off, _ = _live_pipeline(settings, repository)
    second = account_off.approve(
        x_account.id,
        _pending(account_off, repository, x_account.id).id,
        reviewer="admin",
    )

    assert first.publisher_provider == "dry_run"
    assert second.publisher_provider == "dry_run"


def test_live_account_with_missing_connection_fails_visibly(
    settings, repository, x_account
):
    repository.update_account(x_account.id, {"live_posting_enabled": True})
    pipeline, _ = _live_pipeline(settings, repository)
    draft = _pending(pipeline, repository, x_account.id)

    result = pipeline.approve(x_account.id, draft.id, reviewer="admin")

    assert result.status == "failed"
    assert result.publisher_provider == "buffer"
    assert "connection" in result.error.lower()


def test_live_account_with_wrong_master_key_fails_without_dry_run(
    settings, repository, x_account
):
    repository.update_account(x_account.id, {"live_posting_enabled": True})
    good_key = Fernet.generate_key().decode()
    good_settings = _settings(
        settings, app_encryption_key=good_key, buffer_live_posting=True
    )
    connection_service = IntegrationService(good_settings, repository)
    connection = connection_service.save_connection(
        "buffer", "Locked Buffer", {"api_key": "secret"}
    )
    connection_service.bind(x_account.id, "buffer", connection.id, "channel-a")
    wrong_settings = _settings(
        settings,
        app_encryption_key=Fernet.generate_key().decode(),
        buffer_live_posting=True,
    )
    pipeline = build_services(wrong_settings, repository).pipeline

    result = pipeline.approve(
        x_account.id,
        _pending(pipeline, repository, x_account.id).id,
        reviewer="admin",
    )

    assert result.status == "failed"
    assert result.publisher_provider == "buffer"
    assert "locked" in result.error.lower()


def test_failed_publication_requires_explicit_retry(pipeline, repository, x_account):
    publisher = RecordingPublisher(
        [
            PublishResult(success=False, provider="buffer", error="temporary failure"),
            PublishResult(success=True, provider="buffer", external_post_id="ok"),
        ]
    )
    pipeline.publishers = publisher
    draft = _pending(pipeline, repository, x_account.id)
    failed = pipeline.approve(x_account.id, draft.id, reviewer="admin")

    retried = pipeline.retry_publish(x_account.id, draft.id, reviewer="admin")

    assert failed.status == "failed"
    assert retried.status == "published"
    assert publisher.calls == 2


def test_published_draft_refuses_retry(pipeline, repository, x_account):
    draft = _pending(pipeline, repository, x_account.id)
    published = pipeline.approve(x_account.id, draft.id, reviewer="admin")

    with pytest.raises(PipelineError, match="failed"):
        pipeline.retry_publish(x_account.id, published.id, reviewer="admin")
