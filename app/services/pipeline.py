from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.db import utc_now_iso
from app.models import Draft
from app.repository import Repository
from app.services.feedback import FeedbackEngine
from app.services.generation import ContentGenerator, GenerationInput
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
        self.notifiers = notifiers
        self.publishers = publishers

    def generate_draft(
        self,
        *,
        context_id: int | None = None,
        schedule_id: int | None = None,
        parent_draft_id: str | None = None,
        attempt: int = 1,
    ) -> Draft:
        profile = self.repository.get_profile()
        schedule = self.repository.get_schedule(schedule_id) if schedule_id else None
        resolved_context_id = context_id or (schedule.context_id if schedule else None)
        if resolved_context_id is None:
            enabled = self.repository.list_contexts(enabled_only=True)
            if not enabled:
                raise PipelineError("No enabled content context is available")
            resolved_context_id = enabled[0].id
        context = self.repository.get_context(resolved_context_id)
        if not context.enabled:
            raise PipelineError(f"Content context {context.id} is disabled")

        trends = self.trend_collector.collect(profile) if context.live_trends_required else []
        approved, rejected, preferences = self.feedback_engine.memory_bundle()
        parent_text = ""
        if parent_draft_id:
            parent_text = self.repository.get_draft(parent_draft_id).text
            if parent_text not in rejected:
                rejected = [parent_text, *rejected]

        history = self.repository.recent_draft_texts(limit=100)
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
            config_version=self.repository.current_config_version(),
            expires_at=expires_at,
            generator_provider=chosen.provider,
            prompt_snapshot=chosen.prompt_snapshot,
        )
        self.repository.log_event(
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
            results = self.notifiers.notify_for_review(draft, profile, context)
            self.repository.log_event(
                "review_notification_sent",
                draft.id,
                {"results": [result.model_dump() for result in results]},
            )
        return draft

    def approve(self, draft_id: str, *, reviewer: str = "human") -> Draft:
        draft = self.repository.get_draft(draft_id)
        if draft.status != "pending":
            raise PipelineError(f"Only pending drafts can be approved; status is {draft.status}")
        profile = self.repository.get_profile()
        safety = self.safety_guard.check(draft.text, profile)
        if not safety.safe:
            blocked = self.repository.update_draft(
                draft.id,
                status="blocked",
                safety_status="blocked",
                error="; ".join(safety.reasons),
                reviewer=reviewer,
            )
            self.repository.log_event("approval_blocked_by_safety", draft.id, {"reasons": safety.reasons})
            return blocked

        approved_at = utc_now_iso()
        draft = self.repository.update_draft(
            draft.id,
            status="approved",
            approved_at=approved_at,
            reviewer=reviewer,
            expires_at=None,
        )
        result = self.publishers.publish(draft)
        if result.success:
            draft = self.repository.update_draft(
                draft.id,
                status="published",
                published_at=utc_now_iso(),
                publisher_provider=result.provider,
                external_post_id=result.external_post_id,
                post_url=result.post_url,
                error="",
            )
            self.feedback_engine.record_approval(draft, reviewer)
            self.repository.log_event(
                "draft_published",
                draft.id,
                {"provider": result.provider, "post_url": result.post_url},
            )
            self.notifiers.notify_status(draft, "Approved and published")
        else:
            draft = self.repository.update_draft(
                draft.id,
                status="failed",
                publisher_provider=result.provider,
                error=result.error,
            )
            self.repository.log_event(
                "publish_failed", draft.id, {"provider": result.provider, "error": result.error}
            )
            self.notifiers.notify_status(draft, "Approval succeeded, but publishing failed")
        return draft

    def reject_and_regenerate(
        self,
        draft_id: str,
        *,
        reason: str,
        notes: str = "",
        reviewer: str = "human",
    ) -> Draft | None:
        draft = self.repository.get_draft(draft_id)
        if draft.status != "pending":
            raise PipelineError(f"Only pending drafts can be rejected; status is {draft.status}")
        self.feedback_engine.record_rejection(
            draft,
            reason=reason,
            notes=notes,
            reviewer=reviewer,
        )
        profile = self.repository.get_profile()
        if draft.attempt >= profile.max_attempts:
            self.repository.update_draft(
                draft.id,
                status="needs_guidance",
                rejection_reason=reason,
                reviewer_notes=notes,
                reviewer=reviewer,
                expires_at=None,
            )
            self.repository.log_event(
                "regeneration_limit_reached",
                draft.id,
                {"attempt": draft.attempt, "max_attempts": profile.max_attempts},
            )
            return None

        self.repository.update_draft(
            draft.id,
            status="rejected",
            rejection_reason=reason,
            reviewer_notes=notes,
            reviewer=reviewer,
            expires_at=None,
        )
        self.repository.log_event(
            "draft_rejected",
            draft.id,
            {"reason": reason, "notes": notes, "next_attempt": draft.attempt + 1},
        )
        replacement = self.generate_draft(
            context_id=draft.context_id,
            schedule_id=draft.schedule_id,
            parent_draft_id=draft.id,
            attempt=draft.attempt + 1,
        )
        return replacement

    def edit(
        self,
        draft_id: str,
        new_text: str,
        *,
        reviewer: str = "human",
        notes: str = "",
        approve: bool = False,
    ) -> Draft:
        draft = self.repository.get_draft(draft_id)
        if draft.status != "pending":
            raise PipelineError(f"Only pending drafts can be edited; status is {draft.status}")
        profile = self.repository.get_profile()
        safety = self.safety_guard.check(new_text, profile)
        similarity = self.similarity_guard.check(
            new_text,
            self.repository.recent_draft_texts(limit=100, exclude_id=draft.id),
        )
        if not safety.safe or not similarity.unique:
            blocked = self.repository.create_draft(
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
                config_version=self.repository.current_config_version(),
                expires_at=None,
                generator_provider="human_edit",
                prompt_snapshot="",
            )
            self.repository.update_draft(
                blocked.id,
                reviewer=reviewer,
                reviewer_notes=notes,
                error="; ".join(safety.reasons)
                or f"Too similar to a previous post ({similarity.score:.2f})",
            )
            self.repository.log_event(
                "human_edit_blocked",
                blocked.id,
                {"safety": safety.reasons, "similarity": similarity.score},
            )
            return self.repository.get_draft(blocked.id)

        self.feedback_engine.record_edit(
            draft,
            edited_text=new_text,
            reviewer=reviewer,
            notes=notes,
        )
        self.repository.update_draft(
            draft.id,
            status="rejected",
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
            config_version=self.repository.current_config_version(),
            expires_at=expires_at,
            generator_provider="human_edit",
            prompt_snapshot="",
        )
        self.repository.log_event("draft_edited", edited.id, {"parent": draft.id})
        return self.approve(edited.id, reviewer=reviewer) if approve else edited

    def expire_and_regenerate(self, now: datetime | None = None) -> list[Draft]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        replacements: list[Draft] = []
        for draft in self.repository.pending_expired_before(now.astimezone(timezone.utc).isoformat(timespec="seconds")):
            self.feedback_engine.record_rejection(
                draft,
                reason="timeout",
                notes="No explicit human approval was received before the deadline.",
                reviewer="system",
                decision="expired",
            )
            profile = self.repository.get_profile()
            if draft.attempt >= profile.max_attempts:
                self.repository.update_draft(
                    draft.id,
                    status="needs_guidance",
                    rejection_reason="timeout",
                    reviewer_notes="Approval timeout and attempt limit reached.",
                    expires_at=None,
                )
                continue
            self.repository.update_draft(
                draft.id,
                status="expired",
                rejection_reason="timeout",
                reviewer_notes="No explicit approval received.",
                expires_at=None,
            )
            replacement = self.generate_draft(
                context_id=draft.context_id,
                schedule_id=draft.schedule_id,
                parent_draft_id=draft.id,
                attempt=draft.attempt + 1,
            )
            replacements.append(replacement)
        return replacements
