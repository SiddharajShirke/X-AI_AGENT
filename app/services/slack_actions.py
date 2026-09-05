from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.models import SlackActionJob
from app.repository import Repository
from app.services.notifiers import NotifierManager
from app.services.pipeline import Pipeline, PipelineError


logger = logging.getLogger(__name__)

_UNEXPECTED_FAILURE = "Unexpected Slack action processing failure"
_RECONCILIATION_REQUIRED = (
    "Publication was interrupted; operator reconciliation is required"
)
_PUBLICATION_MODE_CHANGED = "Publication mode changed; request a fresh Slack review"
_ACCOUNT_PAUSED = "X account is paused"
_DRAFT_NOT_PENDING = "Draft is no longer pending"
_ACTION_REJECTED = "Slack action could not be processed"


class SlackActionProcessor:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        pipeline: Pipeline,
        notifiers: NotifierManager,
    ):
        self.settings = settings
        self.repository = repository
        self.pipeline = pipeline
        self.notifiers = notifiers
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()

    def tick(self) -> int:
        stale_before = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self.settings.slack_action_lease_seconds)
        ).isoformat(timespec="seconds")
        processed = 0
        while job := self.repository.claim_next_slack_action(stale_before):
            try:
                self._process(job)
            except Exception as exc:
                logger.error(
                    "Slack action processing failed: job=%s account=%s draft=%s error=%s",
                    job.id,
                    job.x_account_id,
                    job.draft_id,
                    type(exc).__name__,
                )
                self.repository.fail_slack_action(
                    job.x_account_id,
                    job.id,
                    _UNEXPECTED_FAILURE,
                )
            processed += 1
        return processed

    def _process(self, job: SlackActionJob) -> None:
        account = self.repository.get_account(job.x_account_id)
        draft = self.repository.get_draft(job.x_account_id, job.draft_id)
        if job.action_id == "approve_draft" and draft.status == "publishing":
            self.repository.fail_slack_action(
                job.x_account_id,
                job.id,
                _RECONCILIATION_REQUIRED,
            )
            return
        if job.action_id == "approve_draft" and draft.status in {
            "published",
            "failed",
            "blocked",
        }:
            self.repository.complete_slack_action(
                job.x_account_id, job.id, result_draft_id=draft.id
            )
            return
        if job.action_id == "reject_draft" and draft.status in {
            "rejected",
            "needs_guidance",
        }:
            self.repository.complete_slack_action(
                job.x_account_id, job.id, result_draft_id=draft.id
            )
            return
        try:
            if job.action_id == "approve_draft":
                result = self.pipeline.approve(
                    job.x_account_id,
                    job.draft_id,
                    reviewer=job.reviewer,
                    origin="slack",
                    expected_live_posting=job.expected_live,
                )
            else:
                result = self.pipeline.reject_and_regenerate(
                    job.x_account_id,
                    job.draft_id,
                    reason="other",
                    notes="Rejected from Slack",
                    reviewer=job.reviewer,
                    origin="slack",
                )
            self.repository.complete_slack_action(
                job.x_account_id,
                job.id,
                result_draft_id=result.id if result is not None else job.draft_id,
            )
        except PipelineError as exc:
            safe_error = self._normalize_pipeline_error(exc)
            self.repository.fail_slack_action(job.x_account_id, job.id, safe_error)
            self.notifiers.notify_status(draft, account, safe_error)

    @staticmethod
    def _normalize_pipeline_error(exc: PipelineError) -> str:
        message = str(exc)
        if message == "Publication mode changed; refresh the review and confirm again":
            return _PUBLICATION_MODE_CHANGED
        if message.startswith("X account @") and message.endswith(" is paused"):
            return _ACCOUNT_PAUSED
        if message.startswith("Only pending drafts can be"):
            return _DRAFT_NOT_PENDING
        return _ACTION_REJECTED

    async def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            self._wake_event.clear()
            try:
                await asyncio.to_thread(self.tick)
            except Exception as exc:
                logger.error(
                    "Slack action worker tick failed: %s", type(exc).__name__
                )
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self.settings.slack_action_poll_seconds,
                )
            except TimeoutError:
                continue

    def wake(self) -> None:
        self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
