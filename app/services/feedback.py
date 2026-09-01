from __future__ import annotations

import re

from app.models import Draft, FeedbackRecord
from app.repository import Repository


_REASON_RULES = {
    "too_generic": "Prefer a concrete, specific observation over a generic industry claim.",
    "sounds_ai": "Use a natural, conversational founder voice; avoid formulaic AI phrasing.",
    "sounds_ai_generated": "Use a natural, conversational founder voice; avoid formulaic AI phrasing.",
    "wrong_topic": "Stay tightly aligned with the selected content context and startup domain.",
    "too_promotional": "Prefer useful insight over promotional or sales language.",
    "reveals_too_much": "Discuss the domain-level problem without revealing product implementation or private details.",
    "repetitive": "Choose a new topic angle and avoid semantic repetition of previous posts.",
    "wrong_tone": "Match the configured brand voice and the selected context tone.",
    "too_long": "Prefer a shorter post with one clear point.",
    "timeout": "Do not publish without explicit approval; replace stale drafts with a fresh angle.",
    "edited": "Use the human-edited version as a positive style example.",
    "approved": "Preserve the qualities of approved posts without copying their wording.",
    "other": "Apply the reviewer's written feedback to future drafts.",
}


def _note_rule(notes: str) -> str:
    cleaned = " ".join(notes.strip().split())
    if not cleaned:
        return ""
    # Keep feedback useful but bounded; this is prompt memory, not a transcript.
    cleaned = re.sub(r"[\r\n]+", " ", cleaned)[:220]
    return f"Reviewer preference: {cleaned}"


class FeedbackEngine:
    def __init__(self, repository: Repository):
        self.repository = repository

    def record_rejection(
        self,
        draft: Draft,
        *,
        reason: str,
        notes: str,
        reviewer: str,
        decision: str = "rejected",
    ) -> FeedbackRecord:
        base_rule = _REASON_RULES.get(reason, _REASON_RULES["other"])
        note_rule = _note_rule(notes)
        learned_rule = f"{base_rule} {note_rule}".strip()
        feedback = self.repository.create_feedback(
            draft_id=draft.id,
            decision=decision,
            reason=reason,
            notes=notes,
            learned_rule=learned_rule,
            reviewer=reviewer,
        )
        self.repository.upsert_preference(
            rule=base_rule,
            source_feedback_id=feedback.id,
            delta=1.0,
        )
        if note_rule:
            self.repository.upsert_preference(
                rule=note_rule,
                source_feedback_id=feedback.id,
                delta=1.0,
            )
        return feedback

    def record_approval(self, draft: Draft, reviewer: str) -> FeedbackRecord:
        rule = _REASON_RULES["approved"]
        feedback = self.repository.create_feedback(
            draft_id=draft.id,
            decision="approved",
            reason="approved",
            notes="",
            learned_rule=rule,
            reviewer=reviewer,
        )
        self.repository.upsert_preference(
            rule=rule,
            source_feedback_id=feedback.id,
            delta=0.25,
        )
        return feedback

    def record_edit(
        self,
        draft: Draft,
        *,
        edited_text: str,
        reviewer: str,
        notes: str = "",
    ) -> FeedbackRecord:
        comparison_note = (
            f"Human changed '{draft.text[:90]}' to '{edited_text[:90]}'. {notes}".strip()
        )
        return self.record_rejection(
            draft,
            reason="edited",
            notes=comparison_note,
            reviewer=reviewer,
            decision="edited",
        )

    def memory_bundle(self) -> tuple[list[str], list[str], list[str]]:
        approved = self.repository.example_texts(["published", "approved"], limit=5)
        rejected = self.repository.example_texts(
            ["rejected", "expired", "needs_guidance"], limit=7
        )
        preferences = [item.rule for item in self.repository.list_preferences(limit=12)]
        return approved, rejected, preferences
