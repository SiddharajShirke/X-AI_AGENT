from __future__ import annotations


def test_database_seeds_profile_ten_contexts_and_ten_schedules(repository, x_account):
    profile = repository.get_profile(x_account.id)
    contexts = repository.list_contexts(x_account.id)
    schedules = repository.list_schedules(x_account.id)

    assert profile.name == "Stealth Startup"
    assert len(contexts) == 10
    assert len(schedules) == 10
    assert {schedule.context_id for schedule in schedules} == {context.id for context in contexts}


def test_profile_update_creates_version_snapshot(repository, x_account):
    before = repository.current_config_version(x_account.id)

    updated = repository.update_profile(
        x_account.id, {"domain": "Privacy-preserving developer tools"}
    )

    assert updated.domain == "Privacy-preserving developer tools"
    assert repository.current_config_version(x_account.id) == before + 1
    versions = repository.list_config_versions(x_account.id, limit=2)
    assert versions[0]["version"] == before + 1
    assert "Privacy-preserving developer tools" in versions[0]["snapshot_json"]


def test_repository_persists_draft_and_preference(repository, x_account):
    context = repository.list_contexts(x_account.id)[0]
    draft = repository.create_draft(
        x_account.id,
        context_id=context.id,
        schedule_id=None,
        text="Reliability becomes visible after the demo ends.",
        topic="Reliability",
        source_summary="Manual test",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(x_account.id),
        expires_at=None,
        generator_provider="demo",
    )
    feedback = repository.create_feedback(
        x_account.id,
        draft_id=draft.id,
        decision="rejected",
        reason="too_generic",
        notes="Use a concrete founder observation.",
        learned_rule="Prefer concrete founder observations over generic claims.",
        reviewer="tester",
    )
    preference = repository.upsert_preference(
        x_account.id,
        rule=feedback.learned_rule,
        source_feedback_id=feedback.id,
        delta=1.0,
    )

    assert repository.get_draft(x_account.id, draft.id).text == draft.text
    assert preference.weight == 1.0
    assert repository.list_preferences(x_account.id)[0].rule.startswith("Prefer concrete")
