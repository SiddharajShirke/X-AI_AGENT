from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import NotificationResult
from app.services.pipeline import PipelineError
from app.services.scheduler import SchedulerService


@pytest.fixture()
def two_accounts(repository):
    first = repository.list_accounts()[0]
    repository.update_account(first.id, {"timezone": "UTC"})
    second = repository.create_account(
        name="Second Brand",
        handle="account_two",
        timezone="Asia/Kolkata",
        copy_from_id=first.id,
    )
    return repository.get_account(first.id), second


def _disable_all_schedules(repository, account_id: int) -> None:
    for schedule in repository.list_schedules(account_id):
        repository.update_schedule(account_id, schedule.id, {"enabled": False})


def test_generation_memory_and_similarity_are_account_local(
    pipeline, repository, two_accounts
):
    first, second = two_accounts
    rejected = pipeline.generate_draft(
        first.id, context_id=repository.list_contexts(first.id)[0].id
    )
    pipeline.reject_and_regenerate(
        first.id, rejected.id, reason="too_generic", reviewer="founder"
    )

    second_draft = pipeline.generate_draft(
        second.id, context_id=repository.list_contexts(second.id)[0].id
    )

    assert second_draft.x_account_id == second.id
    assert rejected.text not in pipeline.feedback_engine.memory_bundle(second.id)[1]


def test_cross_account_parent_review_and_edit_are_rejected(
    pipeline, repository, two_accounts
):
    first, second = two_accounts
    draft = pipeline.generate_draft(
        first.id, context_id=repository.list_contexts(first.id)[0].id
    )

    with pytest.raises(KeyError, match=f"account {second.id}"):
        pipeline.generate_draft(
            second.id,
            context_id=repository.list_contexts(second.id)[0].id,
            parent_draft_id=draft.id,
            attempt=2,
        )
    with pytest.raises(KeyError, match=f"account {second.id}"):
        pipeline.reject_and_regenerate(
            second.id, draft.id, reason="other", reviewer="founder"
        )
    with pytest.raises(KeyError, match=f"account {second.id}"):
        pipeline.edit(second.id, draft.id, "Different text", reviewer="founder")
    assert repository.get_draft(first.id, draft.id).status == "pending"


def test_paused_account_cannot_generate_review_or_timeout(
    pipeline, repository, two_accounts
):
    first, _ = two_accounts
    draft = pipeline.generate_draft(
        first.id, context_id=repository.list_contexts(first.id)[0].id
    )
    repository.update_draft(
        first.id,
        draft.id,
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    repository.update_account(first.id, {"enabled": False})

    with pytest.raises(PipelineError, match="paused"):
        pipeline.generate_draft(
            first.id, context_id=repository.list_contexts(first.id)[0].id
        )
    with pytest.raises(PipelineError, match="paused"):
        pipeline.approve(first.id, draft.id, reviewer="founder")
    assert pipeline.expire_and_regenerate(x_account_id=first.id) == []
    assert repository.get_draft(first.id, draft.id).status == "pending"


def test_attempt_limit_is_loaded_from_originating_account(
    pipeline, repository, two_accounts
):
    first, second = two_accounts
    repository.update_profile(first.id, {"max_attempts": 1})
    repository.update_profile(second.id, {"max_attempts": 4})
    draft = pipeline.generate_draft(
        first.id, context_id=repository.list_contexts(first.id)[1].id
    )

    replacement = pipeline.reject_and_regenerate(
        first.id, draft.id, reason="wrong_topic", reviewer="founder"
    )

    assert replacement is None
    assert repository.get_draft(first.id, draft.id).status == "needs_guidance"
    assert repository.get_profile(second.id).max_attempts == 4


def test_review_notification_receives_account_identity(
    pipeline, repository, two_accounts
):
    class CapturingNotifier:
        def __init__(self):
            self.accounts = []

        def notify_for_review(self, draft, account, profile, context):
            self.accounts.append((account.id, account.handle, draft.x_account_id))
            return [NotificationResult(success=True, provider="capture")]

        def notify_status(self, draft, account, message):
            return [NotificationResult(success=True, provider="capture")]

    capture = CapturingNotifier()
    pipeline.notifiers = capture
    _, second = two_accounts

    pipeline.generate_draft(
        second.id, context_id=repository.list_contexts(second.id)[0].id
    )

    assert capture.accounts == [(second.id, second.handle, second.id)]


def test_scheduler_runs_each_account_in_its_own_timezone(
    settings, repository, pipeline, two_accounts
):
    first, second = two_accounts
    _disable_all_schedules(repository, first.id)
    _disable_all_schedules(repository, second.id)
    first_slot = repository.list_schedules(first.id)[0]
    second_slot = repository.list_schedules(second.id)[0]
    repository.update_schedule(
        first.id, first_slot.id, {"time_local": "09:00", "enabled": True}
    )
    repository.update_schedule(
        second.id, second_slot.id, {"time_local": "14:30", "enabled": True}
    )
    scheduler = SchedulerService(settings, repository, pipeline)

    result = scheduler.tick(datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc))

    assert {draft.x_account_id for draft in result.generated} == {first.id, second.id}
    assert repository.get_schedule(first.id, first_slot.id).last_run_date == "2026-09-03"
    assert repository.get_schedule(second.id, second_slot.id).last_run_date == "2026-09-03"
