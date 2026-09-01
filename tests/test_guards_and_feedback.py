from __future__ import annotations

from app.services.feedback import FeedbackEngine
from app.services.prompts import PromptBuilder
from app.services.safety import SafetyGuard
from app.services.similarity import SimilarityGuard


def test_safety_guard_blocks_confidential_disclosure(repository):
    profile = repository.update_profile(
        {"never_reveal": ["Project Atlas scoring algorithm", "customer names"]}
    )
    result = SafetyGuard().check(
        "Today we are revealing the Project Atlas scoring algorithm.", profile
    )

    assert result.safe is False
    assert any("confidential" in reason.lower() for reason in result.reasons)


def test_similarity_guard_rejects_near_duplicate():
    guard = SimilarityGuard(threshold=0.75)
    result = guard.check(
        "Reliable agents matter after hundreds of repeated runs.",
        ["Agent reliability matters after hundreds of repeated runs."],
    )

    assert result.unique is False
    assert result.score >= 0.75


def test_rejection_creates_durable_learning(repository):
    context = repository.list_contexts()[0]
    draft = repository.create_draft(
        context_id=context.id,
        schedule_id=None,
        text="AI is revolutionary and changes everything.",
        topic="AI",
        source_summary="Demo",
        status="pending",
        safety_status="safe",
        similarity_score=0.0,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(),
        expires_at=None,
        generator_provider="demo",
    )
    engine = FeedbackEngine(repository)

    feedback = engine.record_rejection(
        draft, reason="sounds_ai", notes="Avoid revolutionary language.", reviewer="founder"
    )

    assert "natural" in feedback.learned_rule.lower()
    assert any("revolutionary" in item.rule.lower() for item in repository.list_preferences())


def test_prompt_contains_positive_and_negative_examples(repository):
    profile = repository.get_profile()
    context = repository.list_contexts()[0]
    approved = repository.create_draft(
        context_id=context.id,
        schedule_id=None,
        text="The hard part starts after a workflow succeeds once.",
        topic="Reliability",
        source_summary="Demo",
        status="published",
        safety_status="safe",
        similarity_score=0.0,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(),
        expires_at=None,
        generator_provider="demo",
    )
    rejected = repository.create_draft(
        context_id=context.id,
        schedule_id=None,
        text="The future is here and AI changes everything.",
        topic="AI",
        source_summary="Demo",
        status="rejected",
        safety_status="safe",
        similarity_score=0.0,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(),
        expires_at=None,
        generator_provider="demo",
    )
    repository.create_feedback(
        draft_id=rejected.id,
        decision="rejected",
        reason="too_generic",
        notes="",
        learned_rule="Avoid generic future-of-AI claims.",
        reviewer="founder",
    )

    prompt = PromptBuilder().build(
        profile=profile,
        context=context,
        trends=[],
        approved_examples=[approved.text],
        rejected_examples=[rejected.text],
        learned_preferences=["Avoid generic future-of-AI claims."],
        attempt=2,
        parent_text=rejected.text,
    )

    assert approved.text in prompt
    assert rejected.text in prompt
    assert "Avoid generic future-of-AI claims" in prompt
    assert "Never copy" in prompt
