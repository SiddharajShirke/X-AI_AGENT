from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_explicit_approval_publishes_draft(pipeline, repository):
    draft = pipeline.generate_draft(context_id=repository.list_contexts()[0].id)

    published = pipeline.approve(draft.id, reviewer="founder")

    assert published.status == "published"
    assert published.publisher_provider == "dry_run"
    assert published.post_url.startswith("https://example.com/demo-x/")
    assert repository.get_draft(draft.id).status == "published"


def test_rejection_learns_and_generates_different_post(pipeline, repository):
    original = pipeline.generate_draft(context_id=repository.list_contexts()[1].id)

    replacement = pipeline.reject_and_regenerate(
        original.id,
        reason="too_generic",
        notes="Use a concrete observation and a different angle.",
        reviewer="founder",
    )

    assert repository.get_draft(original.id).status == "rejected"
    assert replacement is not None
    assert replacement.status == "pending"
    assert replacement.text != original.text
    assert replacement.parent_draft_id == original.id
    assert replacement.attempt == 2
    assert repository.list_preferences()


def test_timeout_never_publishes_and_regenerates(pipeline, repository):
    draft = pipeline.generate_draft(context_id=repository.list_contexts()[2].id)
    repository.update_draft(
        draft.id,
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )

    replacements = pipeline.expire_and_regenerate(datetime.now(timezone.utc))

    assert repository.get_draft(draft.id).status == "expired"
    assert len(replacements) == 1
    assert replacements[0].status == "pending"
    assert all(item.status != "published" for item in repository.list_drafts())


def test_unsafe_human_edit_is_blocked(pipeline, repository):
    profile = repository.update_profile({"never_reveal": ["secret cobalt architecture"]})
    assert "secret cobalt architecture" in profile.never_reveal
    draft = pipeline.generate_draft(context_id=repository.list_contexts()[3].id)

    result = pipeline.edit(
        draft.id,
        "Our secret cobalt architecture is the product advantage.",
        reviewer="founder",
        approve=True,
    )

    assert result.status == "blocked"
    assert repository.get_draft(draft.id).status == "pending"


def test_attempt_limit_stops_automatic_regeneration(pipeline, repository):
    repository.update_profile({"max_attempts": 1})
    draft = pipeline.generate_draft(context_id=repository.list_contexts()[4].id)

    replacement = pipeline.reject_and_regenerate(
        draft.id, reason="wrong_topic", notes="", reviewer="founder"
    )

    assert replacement is None
    assert repository.get_draft(draft.id).status == "needs_guidance"
