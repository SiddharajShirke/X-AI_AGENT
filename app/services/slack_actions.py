from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.models import Draft, SlackActionJob, XAccount
from app.repository import Repository
from app.services.notifiers import NotifierManager
from app.services.pipeline import Pipeline, PipelineError


logger = logging.getLogger(__name__)

_UNEXPECTED_FAILURE = "Unexpected Slack action processing failure"
_RECONCILIATION_REQUIRED = (
    "Publication was interrupted; operator reconciliation is required"
)
_REJECTION_RECONCILIATION_REQUIRED = (
    "Rejection was interrupted; operator reconciliation is required"
)
_PUBLICATION_MODE_CHANGED = "Publication mode changed; request a fresh Slack review"
_ACCOUNT_PAUSED = "X account is paused"
_DRAFT_NOT_PENDING = "Draft is no longer pending"
_ACTION_REJECTED = "Slack action could not be processed"
_APPROVAL_RECOVERED_MESSAGES = {
    "published": "Slack approval recovered: draft was already published",
    "failed": "Slack approval recovered: publishing had already failed",
    "blocked": "Slack approval recovered: approval had already been blocked",
}
_REJECTION_REPLACEMENT_RECOVERED = (
    "Slack rejection recovered: replacement draft is ready for review"
)
_REJECTION_GUIDANCE_RECOVERED = (
    "Slack rejection recovered: operator guidance is required"
)


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
        while not self._stop_event.is_set():
            job = self.repository.claim_next_slack_action(stale_before)
            if job is None:
                break
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
                self._settle_after_unexpected_failure(job)
            processed += 1
        return processed

    def _process(self, job: SlackActionJob) -> None:
        account = self.repository.get_account(job.x_account_id)
        draft = self.repository.get_draft(job.x_account_id, job.draft_id)
        if self._settle_recovered_state(job, account, draft):
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
            self._notify_status(job, draft, account, safe_error)

    def _settle_recovered_state(
        self, job: SlackActionJob, account: XAccount, draft: Draft
    ) -> bool:
        if job.action_id == "approve_draft" and draft.status == "publishing":
            self.repository.fail_slack_action(
                job.x_account_id,
                job.id,
                _RECONCILIATION_REQUIRED,
            )
            self._notify_status(job, draft, account, _RECONCILIATION_REQUIRED)
            return True
        if job.action_id == "approve_draft" and draft.status in {
            "published",
            "failed",
            "blocked",
        }:
            self.repository.complete_slack_action(
                job.x_account_id, job.id, result_draft_id=draft.id
            )
            self._notify_status(
                job,
                draft,
                account,
                _APPROVAL_RECOVERED_MESSAGES[draft.status],
            )
            return True
        if job.action_id == "reject_draft" and draft.status == "needs_guidance":
            self.repository.complete_slack_action(
                job.x_account_id, job.id, result_draft_id=draft.id
            )
            self._notify_status(
                job, draft, account, _REJECTION_GUIDANCE_RECOVERED
            )
            return True
        if job.action_id == "reject_draft" and draft.status == "rejected":
            children = self.repository.list_child_drafts(
                job.x_account_id, job.draft_id
            )
            if len(children) == 1:
                child = children[0]
                self.repository.complete_slack_action(
                    job.x_account_id, job.id, result_draft_id=child.id
                )
                self._notify_status(
                    job, child, account, _REJECTION_REPLACEMENT_RECOVERED
                )
            else:
                self.repository.fail_slack_action(
                    job.x_account_id,
                    job.id,
                    _REJECTION_RECONCILIATION_REQUIRED,
                )
                self._notify_status(
                    job,
                    draft,
                    account,
                    _REJECTION_RECONCILIATION_REQUIRED,
                )
            return True
        if job.action_id == "reject_draft" and draft.status == "rejecting":
            self.repository.fail_slack_action(
                job.x_account_id,
                job.id,
                _REJECTION_RECONCILIATION_REQUIRED,
            )
            self._notify_status(
                job,
                draft,
                account,
                _REJECTION_RECONCILIATION_REQUIRED,
            )
            return True
        return False

    def _settle_after_unexpected_failure(self, job: SlackActionJob) -> None:
        account = None
        draft = None
        try:
            account = self.repository.get_account(job.x_account_id)
            draft = self.repository.get_draft(job.x_account_id, job.draft_id)
            if self._settle_recovered_state(job, account, draft):
                return
        except Exception as exc:
            logger.error(
                "Slack action recovery inspection failed: job=%s account=%s "
                "draft=%s error=%s",
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
        if account is not None and draft is not None:
            self._notify_status(job, draft, account, _UNEXPECTED_FAILURE)

    def _notify_status(
        self,
        job: SlackActionJob,
        draft: Draft,
        account: XAccount,
        message: str,
    ) -> None:
        try:
            self.notifiers.notify_status(draft, account, message)
        except Exception as exc:
            logger.error(
                "Slack action status notification failed: job=%s account=%s "
                "draft=%s error=%s",
                job.id,
                job.x_account_id,
                job.draft_id,
                type(exc).__name__,
            )

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
