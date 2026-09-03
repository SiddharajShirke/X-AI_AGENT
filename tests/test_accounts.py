from __future__ import annotations

import sqlite3

import pytest


def _create_draft(repository, x_account_id: int, *, text: str = "Account-local draft"):
    context = repository.list_contexts(x_account_id)[0]
    return repository.create_draft(
        x_account_id,
        context_id=context.id,
        schedule_id=None,
        text=text,
        topic="Isolation",
        source_summary="Account persistence test",
        status="pending",
        safety_status="safe",
        similarity_score=0.0,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(x_account_id),
        expires_at=None,
        generator_provider="demo",
    )


def test_two_accounts_have_independent_profiles_contexts_and_schedules(repository):
    first = repository.list_accounts()[0]
    second = repository.create_account(
        name="Second Brand",
        handle="second",
        timezone="UTC",
        copy_from_id=first.id,
    )

    repository.update_profile(second.id, {"domain": "Second account domain"})
    second_context = repository.list_contexts(second.id)[0]
    repository.update_context(second.id, second_context.id, {"name": "Second Context"})
    second_schedule = repository.list_schedules(second.id)[0]
    repository.update_schedule(second.id, second_schedule.id, {"time_local": "08:30"})

    assert repository.get_profile(first.id).domain != repository.get_profile(second.id).domain
    assert "timezone" not in repository.get_profile(second.id).model_dump()
    assert len(repository.list_contexts(first.id)) == 10
    assert len(repository.list_contexts(second.id)) == 10
    assert repository.list_contexts(first.id)[0].name != "Second Context"
    assert repository.list_schedules(first.id)[0].time_local != "08:30"
    assert {item.x_account_id for item in repository.list_contexts(second.id)} == {second.id}
    assert {item.x_account_id for item in repository.list_schedules(second.id)} == {second.id}


def test_account_copy_excludes_history_learning_and_integrations(repository):
    source = repository.list_accounts()[0]
    draft = _create_draft(repository, source.id)
    feedback = repository.create_feedback(
        source.id,
        draft_id=draft.id,
        decision="rejected",
        reason="too_generic",
        notes="Use specifics",
        learned_rule="Prefer specific observations.",
        reviewer="tester",
    )
    repository.upsert_preference(
        source.id,
        rule=feedback.learned_rule,
        source_feedback_id=feedback.id,
        delta=1.0,
    )
    repository.add_trend(source.id, "Source trend", "Source-only signal")
    repository.log_event(source.id, "source_event", draft.id, {"source": True})
    connection = repository.create_integration_connection("buffer", "Shared", "ciphertext")
    repository.bind_account_integration(source.id, "buffer", connection.id, "source-channel")

    copied = repository.create_account(
        name="Copied Brand",
        handle="copied_brand",
        timezone="Europe/London",
        copy_from_id=source.id,
    )

    assert repository.get_profile(copied.id).domain == repository.get_profile(source.id).domain
    assert repository.list_drafts(copied.id) == []
    assert repository.list_trends(copied.id) == []
    assert repository.list_feedback(copied.id) == []
    assert repository.list_preferences(copied.id) == []
    assert repository.list_events(copied.id) == []
    assert repository.get_account_integration(copied.id, "buffer") is None
    assert [item["version"] for item in repository.list_config_versions(copied.id)] == [1]
    assert all(item.last_run_date == "" for item in repository.list_schedules(copied.id))


def test_handles_are_normalized_unique_and_accounts_can_be_disabled(repository):
    account = repository.create_account(
        name="Second Brand",
        handle="  @Second_Brand  ",
        timezone="UTC",
    )

    assert account.handle == "second_brand"
    with pytest.raises((ValueError, sqlite3.IntegrityError), match="handle|UNIQUE"):
        repository.create_account(name="Duplicate", handle="@SECOND_BRAND", timezone="UTC")

    paused = repository.update_account(account.id, {"enabled": False})
    assert paused.enabled is False
    assert account.id not in {item.id for item in repository.list_accounts(enabled_only=True)}
    assert account.id in {item.id for item in repository.list_accounts()}


def test_connection_public_model_never_contains_ciphertext(repository):
    item = repository.create_integration_connection("buffer", "Main Buffer", "encrypted-token")

    assert item.credentials_configured is True
    assert "encrypted" not in item.model_dump()
    assert repository.get_encrypted_credentials(item.id) == "encrypted-token"


def test_shared_connection_bindings_keep_account_targets_independent(repository):
    first = repository.list_accounts()[0]
    second = repository.create_account(
        name="Second Brand",
        handle="second",
        timezone="UTC",
        copy_from_id=first.id,
    )
    connection = repository.create_integration_connection("buffer", "Shared Buffer", "ciphertext")

    first_binding = repository.bind_account_integration(
        first.id, "buffer", connection.id, "channel-one"
    )
    second_binding = repository.bind_account_integration(
        second.id, "buffer", connection.id, "channel-two", enabled=False
    )

    assert first_binding.connection_id == second_binding.connection_id == connection.id
    assert repository.get_account_integration(first.id, "buffer").target_id == "channel-one"
    assert repository.get_account_integration(second.id, "buffer").target_id == "channel-two"
    assert repository.get_account_integration(second.id, "buffer").enabled is False


def test_draft_lookup_rejects_a_different_account(repository):
    first = repository.list_accounts()[0]
    second = repository.create_account(
        name="Second Brand",
        handle="second",
        timezone="UTC",
        copy_from_id=first.id,
    )
    draft = _create_draft(repository, first.id)

    with pytest.raises(KeyError, match=f"account {second.id}"):
        repository.get_draft(second.id, draft.id)
    assert repository.list_drafts(second.id) == []
