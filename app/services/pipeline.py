from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.models import Draft, PublishResult
from app.repository import Repository
from app.services.feedback import FeedbackEngine
from app.services.generation import ContentGenerator, GenerationInput
from app.services.integrations import IntegrationError, IntegrationService
from app.services.notifiers import NotifierManager
from app.services.publishers import PublisherManager
from app.services.safety import SafetyGuard
from app.services.similarity import SimilarityGuard
from app.services.trends import TrendCollector


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        generator: ContentGenerator,
        trend_collector: TrendCollector,
        safety_guard: SafetyGuard,
        similarity_guard: SimilarityGuard,
        feedback_engine: FeedbackEngine,
        integrations: IntegrationService,
        notifiers: NotifierManager,
        publishers: PublisherManager,
    ):
        self.settings = settings
        self.repository = repository
        self.generator = generator
        self.trend_collector = trend_collector
        self.safety_guard = safety_guard
        self.similarity_guard = similarity_guard
        self.feedback_engine = feedback_engine
        self.integrations = integrations
        self.notifiers = notifiers
        self.publishers = publishers

    def generate_draft(
        self,
        x_account_id: int,
        *,
        context_id: int | None = None,
        schedule_id: int | None = None,
        parent_draft_id: str | None = None,
        attempt: int = 1,
    ) -> Draft:
        account = self.repository.get_account(x_account_id)
        if not account.enabled:
            raise PipelineError(f"X account @{account.handle} is paused")
        profile = self.repository.get_profile(x_account_id)
        schedule = (
            self.repository.get_schedule(x_account_id, schedule_id) if schedule_id else None
        )
        resolved_context_id = context_id or (schedule.context_id if schedule else None)
        if resolved_context_id is None:
            enabled = self.repository.list_contexts(x_account_id, enabled_only=True)
            if not enabled:
                raise PipelineError("No enabled content context is available")
            resolved_context_id = enabled[0].id
        context = self.repository.get_context(x_account_id, resolved_context_id)
        if not context.enabled:
            raise PipelineError(f"Content context {context.id} is disabled")

        trends = (
            self.trend_collector.collect(x_account_id, profile)
            if context.live_trends_required
            else []
        )
        approved, rejected, preferences = self.feedback_engine.memory_bundle(x_account_id)
        parent_text = ""
        if parent_draft_id:
            parent_text = self.repository.get_draft(x_account_id, parent_draft_id).text
            if parent_text not in rejected:
                rejected = [parent_text, *rejected]

        history = self.repository.recent_draft_texts(x_account_id, limit=100)
        chosen = None
        chosen_safety = None
        chosen_similarity = None
        for variation in range(self.settings.max_generation_candidates):
            generated = self.generator.variation(
                GenerationInput(
                    profile=profile,
                    context=context,
                    trends=trends,
                    approved_examples=approved,
                    rejected_examples=rejected,
                    learned_preferences=preferences,
                    attempt=attempt,
                    parent_text=parent_text,
                    sequence=len(history),
                ),
                variation=variation,
            )
            safety = self.safety_guard.check(generated.text, profile)
            similarity = self.similarity_guard.check(generated.text, history)
            chosen = generated
            chosen_safety = safety
            chosen_similarity = similarity
            if safety.safe and similarity.unique:
                break

        if chosen is None or chosen_safety is None or chosen_similarity is None:
            raise PipelineError("No content candidate was generated")

        status = "pending" if chosen_safety.safe and chosen_similarity.unique else "blocked"
        expires_at = None
        if status == "pending":
            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(minutes=profile.approval_timeout_minutes)
            ).isoformat(timespec="seconds")

        draft = self.repository.create_draft(
            x_account_id,
            context_id=context.id,
            schedule_id=schedule.id if schedule else None,
            text=chosen.text,
            topic=chosen.topic,
            source_summary=chosen.source_summary,
            status=status,
            safety_status=chosen_safety.status,
            similarity_score=chosen_similarity.score,
            attempt=attempt,
            parent_draft_id=parent_draft_id,
            config_version=self.repository.current_config_version(x_account_id),
            expires_at=expires_at,
            generator_provider=chosen.provider,
            prompt_snapshot=chosen.prompt_snapshot,
        )
        self.repository.log_event(
            x_account_id,
            "draft_generated",
            draft.id,
            {
                "status": status,
                "context": context.name,
                "attempt": attempt,
                "similarity": chosen_similarity.score,
                "safety_reasons": chosen_safety.reasons,
            },
        )
        if status == "pending":
            results = self.notifiers.notify_for_review(draft, account, profile, context)
            self.repository.log_event(
                x_account_id,
                "review_notification_sent",
                draft.id,
                {"results": [result.model_dump() for result in results]},
            )
        return draft

    def approve(
        self,
        x_account_id: int,
        draft_id: str,
        *,
        reviewer: str,
        origin: str = "dashboard",
        expected_live_posting: bool | None = None,
    ) -> Draft:
        account = self.repository.get_account(x_account_id)
        if not account.enabled:
            raise PipelineError(f"X account @{account.handle} is paused")
        draft = self.repository.get_draft(x_account_id, draft_id)
        if draft.status in {"publishing", "published", "failed", "blocked"}:
            return draft
        if draft.status != "pending":
            raise PipelineError(f"Only pending drafts can be approved; status is {draft.status}")
        self._require_expected_publication_mode(account, expected_live_posting)
        profile = self.repository.get_profile(x_account_id)
        safety = self.safety_guard.check(draft.text, profile)
        if not safety.safe:
            blocked = self.repository.transition_draft(
                x_account_id,
                draft.id,
                from_status="pending",
                to_status="blocked",
                safety_status="blocked",
                error="; ".join(safety.reasons),
                reviewer=reviewer,
            )
            if blocked is None:
                return self.repository.get_draft(x_account_id, draft.id)
            self.repository.log_event(
                x_account_id,
                "approval_blocked_by_safety",
                draft.id,
                {"reasons": safety.reasons, "origin": origin, "reviewer": reviewer},
            )
            return blocked

        claimed = self.repository.claim_publish(
            x_account_id,
            draft.id,
            reviewer=reviewer,
            origin=origin,
        )
        if claimed is None:
            return self.repository.get_draft(x_account_id, draft.id)
        draft, attempt = claimed
        return self._publish_claimed(account, draft, attempt.id, reviewer, origin)

    def _publish_claimed(
        self,
        account,
        draft: Draft,
        attempt_id: int,
        reviewer: str,
        origin: str,
    ) -> Draft:
        target = None
        if self.settings.buffer_live_posting and account.live_posting_enabled:
            try:
                target = self.integrations.resolve_buffer(account.id)
            except IntegrationError as exc:
                result = PublishResult(
                    success=False,
                    provider="buffer",
                    error=str(exc),
                )
            else:
                result = self.publishers.publish(draft, account, target)
        else:
            result = self.publishers.publish(draft, account, target)

        draft = self.repository.complete_publish(
            account.id, draft.id, attempt_id, result
        )
        if result.success:
            self.feedback_engine.record_approval(draft, reviewer)
            self.repository.log_event(
                account.id,
                "draft_published",
                draft.id,
                {
                    "provider": result.provider,
                    "post_url": result.post_url,
                    "origin": origin,
                    "reviewer": reviewer,
                },
            )
            self.notifiers.notify_status(draft, account, "Approved and published")
        else:
            self.repository.log_event(
                account.id,
                "publish_failed",
                draft.id,
                {
                    "provider": result.provider,
                    "error": result.error,
                    "origin": origin,
                    "reviewer": reviewer,
                },
            )
            self.notifiers.notify_status(
                draft, account, "Approval succeeded, but publishing failed"
            )
        return draft

    def retry_publish(
        self,
        x_account_id: int,
        draft_id: str,
        *,
        reviewer: str,
        origin: str = "dashboard",
        expected_live_posting: bool | None = None,
    ) -> Draft:
        account = self.repository.get_account(x_account_id)
        if not account.enabled:
            raise PipelineError(f"X account @{account.handle} is paused")
        draft = self.repository.get_draft(x_account_id, draft_id)
        if draft.status != "failed":
            raise PipelineError(
                f"Only failed drafts can retry publishing; status is {draft.status}"
            )
        self._require_expected_publication_mode(account, expected_live_posting)
        profile = self.repository.get_profile(x_account_id)
        safety = self.safety_guard.check(draft.text, profile)
        if not safety.safe:
            blocked = self.repository.transition_draft(
                x_account_id,
                draft.id,
                from_status="failed",
                to_status="blocked",
                safety_status="blocked",
                error="; ".join(safety.reasons),
                reviewer=reviewer,
            )
            return blocked or self.repository.get_draft(x_account_id, draft.id)
        claimed = self.repository.claim_publish(
            x_account_id,
            draft.id,
            reviewer=reviewer,
            origin=origin,
            allow_retry=True,
        )
        if claimed is None:
            return self.repository.get_draft(x_account_id, draft.id)
        claimed_draft, attempt = claimed
        return self._publish_claimed(
            account, claimed_draft, attempt.id, reviewer, origin
        )

    def reject_and_regenerate(
        self,
        x_account_id: int,
        draft_id: str,
        *,
        reason: str,
        notes: str = "",
        reviewer: str,
        origin: str = "dashboard",
    ) -> Draft | None:
        account = self.repository.get_account(x_account_id)
        if not account.enabled:
            raise PipelineError(f"X account @{account.handle} is paused")
        draft = self.repository.get_draft(x_account_id, draft_id)
        if draft.status != "pending":
            raise PipelineError(f"Only pending drafts can be rejected; status is {draft.status}")
        claimed = self.repository.transition_draft(
            x_account_id,
            draft.id,
            from_status="pending",
            to_status="rejecting",
            reviewer=reviewer,
            expires_at=None,
        )
        if claimed is None:
            current = self.repository.get_draft(x_account_id, draft.id)
            raise PipelineError(
                f"Only pending drafts can be rejected; status is {current.status}"
            )
        draft = claimed
        self.feedback_engine.record_rejection(
            draft,
            reason=reason,
            notes=notes,
            reviewer=reviewer,
        )
        profile = self.repository.get_profile(x_account_id)
        if draft.attempt >= profile.max_attempts:
            self.repository.transition_draft(
                x_account_id,
                draft.id,
                from_status="rejecting",
                to_status="needs_guidance",
                rejection_reason=reason,
                reviewer_notes=notes,
                reviewer=reviewer,
                expires_at=None,
            )
            self.repository.log_event(
                x_account_id,
                "regeneration_limit_reached",
                draft.id,
                {
                    "attempt": draft.attempt,
                    "max_attempts": profile.max_attempts,
                    "origin": origin,
                },
            )
            return None

        self.repository.transition_draft(
            x_account_id,
            draft.id,
            from_status="rejecting",
            to_status="rejected",
            rejection_reason=reason,
            reviewer_notes=notes,
            reviewer=reviewer,
            expires_at=None,
        )
        self.repository.log_event(
            x_account_id,
            "draft_rejected",
            draft.id,
            {
                "reason": reason,
                "notes": notes,
                "next_attempt": draft.attempt + 1,
                "origin": origin,
            },
        )
        replacement = self.generate_draft(
            x_account_id,
            context_id=draft.context_id,
            schedule_id=draft.schedule_id,
            parent_draft_id=draft.id,
            attempt=draft.attempt + 1,
        )
        return replacement

    def edit(
        self,
        x_account_id: int,
        draft_id: str,
        new_text: str,
        *,
        reviewer: str,
        notes: str = "",
        approve: bool = False,
        expected_live_posting: bool | None = None,
    ) -> Draft:
        account = self.repository.get_account(x_account_id)
        if not account.enabled:
            raise PipelineError(f"X account @{account.handle} is paused")
        draft = self.repository.get_draft(x_account_id, draft_id)
        if draft.status != "pending":
            raise PipelineError(f"Only pending drafts can be edited; status is {draft.status}")
        if approve:
            self._require_expected_publication_mode(account, expected_live_posting)
        profile = self.repository.get_profile(x_account_id)
        safety = self.safety_guard.check(new_text, profile)
        similarity = self.similarity_guard.check(
            new_text,
            self.repository.recent_draft_texts(
                x_account_id, limit=100, exclude_id=draft.id
            ),
        )
        claimed = self.repository.transition_draft(
            x_account_id,
            draft.id,
            from_status="pending",
            to_status="editing",
        )
        if claimed is None:
            current = self.repository.get_draft(x_account_id, draft.id)
            raise PipelineError(
                f"Only pending drafts can be edited; status is {current.status}"
            )
        draft = claimed
        if not safety.safe or not similarity.unique:
            blocked = self.repository.create_draft(
                x_account_id,
                context_id=draft.context_id,
                schedule_id=draft.schedule_id,
                text=new_text,
                topic=draft.topic,
                source_summary="Human edit blocked by guard",
                status="blocked",
                safety_status=safety.status,
                similarity_score=similarity.score,
                attempt=draft.attempt + 1,
                parent_draft_id=draft.id,
                config_version=self.repository.current_config_version(x_account_id),
                expires_at=None,
                generator_provider="human_edit",
                prompt_snapshot="",
            )
            self.repository.update_draft(
                x_account_id,
                blocked.id,
                reviewer=reviewer,
                reviewer_notes=notes,
                error="; ".join(safety.reasons)
                or f"Too similar to a previous post ({similarity.score:.2f})",
            )
            self.repository.log_event(
                x_account_id,
                "human_edit_blocked",
                blocked.id,
                {
                    "safety": safety.reasons,
                    "similarity": similarity.score,
                    "origin": "dashboard",
                },
            )
            self.repository.transition_draft(
                x_account_id,
                draft.id,
                from_status="editing",
                to_status="pending",
            )
            return self.repository.get_draft(x_account_id, blocked.id)

        self.feedback_engine.record_edit(
            draft,
            edited_text=new_text,
            reviewer=reviewer,
            notes=notes,
        )
        self.repository.transition_draft(
            x_account_id,
            draft.id,
            from_status="editing",
            to_status="rejected",
            rejection_reason="edited",
            reviewer_notes=notes,
            reviewer=reviewer,
            expires_at=None,
        )
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(minutes=profile.approval_timeout_minutes)
        ).isoformat(timespec="seconds")
        edited = self.repository.create_draft(
            x_account_id,
            context_id=draft.context_id,
            schedule_id=draft.schedule_id,
            text=new_text.strip(),
            topic=draft.topic,
            source_summary="Human-edited draft",
            status="pending",
            safety_status="safe",
            similarity_score=similarity.score,
            attempt=draft.attempt + 1,
            parent_draft_id=draft.id,
            config_version=self.repository.current_config_version(x_account_id),
            expires_at=expires_at,
            generator_provider="human_edit",
            prompt_snapshot="",
        )
        self.repository.log_event(
            x_account_id,
            "draft_edited",
            edited.id,
            {"parent": draft.id, "origin": "dashboard"},
        )
        return (
            self.approve(
                x_account_id,
                edited.id,
                reviewer=reviewer,
                origin="dashboard",
                expected_live_posting=expected_live_posting,
            )
            if approve
            else edited
        )

    def _require_expected_publication_mode(
        self, account, expected_live_posting: bool | None
    ) -> None:
        if expected_live_posting is None:
            return
        effective_live = bool(
            self.settings.buffer_live_posting and account.live_posting_enabled
        )
        if expected_live_posting != effective_live:
            raise PipelineError(
                "Publication mode changed; refresh the review and confirm again"
            )

    def expire_and_regenerate(
        self,
        now: datetime | None = None,
        x_account_id: int | None = None,
    ) -> list[Draft]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        replacements: list[Draft] = []
        if x_account_id is None:
            accounts = self.repository.list_accounts(enabled_only=True)
        else:
            account = self.repository.get_account(x_account_id)
            if not account.enabled:
                return replacements
            accounts = [account]
        cutoff = now.astimezone(timezone.utc).isoformat(timespec="seconds")
        for account in accounts:
            profile = self.repository.get_profile(account.id)
            for draft in self.repository.pending_expired_before(account.id, cutoff):
                claimed = self.repository.transition_draft(
                    account.id,
                    draft.id,
                    from_status="pending",
                    to_status="expiring",
                    expires_at=None,
                )
                if claimed is None:
                    continue
                draft = claimed
                self.feedback_engine.record_rejection(
                    draft,
                    reason="timeout",
                    notes="No explicit human approval was received before the deadline.",
                    reviewer="system",
                    decision="expired",
                )
                if draft.attempt >= profile.max_attempts:
                    self.repository.transition_draft(
                        account.id,
                        draft.id,
                        from_status="expiring",
                        to_status="needs_guidance",
                        rejection_reason="timeout",
                        reviewer_notes="Approval timeout and attempt limit reached.",
                        expires_at=None,
                    )
                    continue
                self.repository.transition_draft(
                    account.id,
                    draft.id,
                    from_status="expiring",
                    to_status="expired",
                    rejection_reason="timeout",
                    reviewer_notes="No explicit approval received.",
                    expires_at=None,
                )
                replacement = self.generate_draft(
                    account.id,
                    context_id=draft.context_id,
                    schedule_id=draft.schedule_id,
                    parent_draft_id=draft.id,
                    attempt=draft.attempt + 1,
                )
                replacements.append(replacement)
        return replacements
