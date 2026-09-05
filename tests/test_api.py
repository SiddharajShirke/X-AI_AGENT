from __future__ import annotations

import base64

from fastapi.testclient import TestClient


def _auth(username: str = "demo", password: str = "demo-pass") -> dict[str, str]:
    value = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def test_health_is_public(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_admin_api_requires_authentication(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        account_id = client.app.state.repository.list_accounts()[0].id
        response = client.get(f"/api/accounts/{account_id}/profile")

    assert response.status_code == 401


def test_profile_update_generation_rejection_and_approval(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        headers = _auth()
        account_id = client.app.state.repository.list_accounts()[0].id
        context_id = client.app.state.repository.list_contexts(account_id)[0].id
        update = client.put(
            f"/api/accounts/{account_id}/profile",
            headers=headers,
            json={"domain": "Secure AI workflow infrastructure", "brand_voice": "Direct founder voice"},
        )
        generated = client.post(
            f"/api/accounts/{account_id}/drafts/generate",
            headers=headers,
            json={"context_id": context_id},
        )
        draft_id = generated.json()["id"]
        rejected = client.post(
            f"/api/accounts/{account_id}/drafts/{draft_id}/reject",
            headers=headers,
            json={"reason": "too_generic", "notes": "Use a specific observation.", "reviewer": "founder"},
        )
        replacement_id = rejected.json()["replacement"]["id"]
        approved = client.post(
            f"/api/accounts/{account_id}/drafts/{replacement_id}/approve",
            headers=headers,
            json={"reviewer": "founder"},
        )

    assert update.status_code == 200
    assert generated.status_code == 201
    assert rejected.status_code == 200
    assert replacement_id != draft_id
    assert approved.json()["status"] == "published"
    assert approved.json()["reviewer"] == "demo"


def test_account_api_never_returns_another_accounts_drafts(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        repository = client.app.state.repository
        first = repository.list_accounts()[0]
        second = repository.create_account("Second", "second", "UTC", copy_from_id=first.id)
        first_draft = client.app.state.services.pipeline.generate_draft(
            first.id, context_id=repository.list_contexts(first.id)[0].id
        )
        second_draft = client.app.state.services.pipeline.generate_draft(
            second.id, context_id=repository.list_contexts(second.id)[0].id
        )

        response = client.get(f"/api/accounts/{first.id}/drafts", headers=_auth())

        assert response.status_code == 200
        assert {item["id"] for item in response.json()} == {first_draft.id}
        assert second_draft.id not in {item["id"] for item in response.json()}
        assert all(item["x_account_id"] == first.id for item in response.json())


def test_account_creation_can_copy_configuration_without_history(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        repository = client.app.state.repository
        first = repository.list_accounts()[0]
        draft = client.app.state.services.pipeline.generate_draft(
            first.id, context_id=repository.list_contexts(first.id)[0].id
        )
        client.app.state.services.pipeline.reject_and_regenerate(
            first.id, draft.id, reason="too_generic", reviewer="demo"
        )

        response = client.post(
            "/api/accounts",
            headers=_auth(),
            json={
                "name": "Copied account",
                "handle": "copied",
                "timezone": "UTC",
                "copy_from_id": first.id,
            },
        )

        assert response.status_code == 201
        copied_id = response.json()["id"]
        assert repository.get_profile(copied_id).domain == repository.get_profile(first.id).domain
        assert repository.list_feedback(copied_id) == []


def test_connection_api_never_echoes_plaintext_credentials(settings):
    from cryptography.fernet import Fernet
    from app.main import create_app

    configured = settings.model_copy(
        update={"app_encryption_key": Fernet.generate_key().decode()}
    )
    with TestClient(create_app(settings=configured, start_scheduler=False)) as client:
        response = client.post(
            "/api/connections",
            headers=_auth(),
            json={
                "provider": "slack",
                "label": "Review Slack",
                "credentials": {
                    "webhook_url": "https://hooks.slack.test/private",
                    "signing_secret": "very-secret",
                },
            },
        )

        assert response.status_code == 201
        body = response.text
        assert "very-secret" not in body
        assert "hooks.slack.test/private" not in body
        assert response.json()["credentials_configured"] is True


def test_dashboard_renders_configuration_and_review_controls(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        response = client.get("/", headers=_auth())

    assert response.status_code == 200
    assert "Account workspaces" in response.text
    assert "Add X Account" in response.text
    assert "Open review workspace" in response.text
    assert "every post requires an explicit human approval" in response.text


def test_accounts_page_shows_status_cards_for_each_account(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        repository = client.app.state.repository
        first = repository.list_accounts()[0]
        second = repository.create_account("Second", "second", "UTC", copy_from_id=first.id)

        response = client.get("/", headers=_auth())

        assert response.status_code == 200
        assert f"@{first.handle}" in response.text
        assert f"@{second.handle}" in response.text
        assert "Slack not connected" in response.text
        assert "Buffer dry-run" in response.text
        assert f'href="/accounts/{second.id}"' in response.text


def test_account_workspace_names_publish_destination_and_has_csrf(settings):
    from app.main import create_app

    configured = settings.model_copy(update={"buffer_live_posting": True})
    with TestClient(create_app(settings=configured, start_scheduler=False)) as client:
        repository = client.app.state.repository
        account = repository.list_accounts()[0]
        repository.update_account(account.id, {"live_posting_enabled": True})
        client.app.state.services.pipeline.generate_draft(
            account.id, context_id=repository.list_contexts(account.id)[0].id
        )

        response = client.get(f"/accounts/{account.id}", headers=_auth())

        assert response.status_code == 200
        assert f"Approve and publish to @{account.handle}" in response.text
        assert "LIVE" in response.text
        assert 'name="csrf_token"' in response.text
        assert 'content="width=device-width,initial-scale=1"' in response.text
        assert 'data-edit-form' in response.text


def test_failed_retry_keeps_text_in_live_confirmation_scope(settings):
    from app.main import create_app

    configured = settings.model_copy(update={"buffer_live_posting": True})
    with TestClient(create_app(settings=configured, start_scheduler=False)) as client:
        repository = client.app.state.repository
        account = repository.list_accounts()[0]
        repository.update_account(account.id, {"live_posting_enabled": True})
        draft = client.app.state.services.pipeline.generate_draft(
            account.id, context_id=repository.list_contexts(account.id)[0].id
        )
        repository.update_draft(account.id, draft.id, status="failed", error="retry")

        response = client.get(f"/accounts/{account.id}", headers=_auth())

        assert response.status_code == 200
        assert f'id="draft-{draft.id}"' in response.text
        assert draft.text in response.text
        assert 'name="expected_live" value="true"' in response.text


def test_setup_page_has_seven_guided_steps_and_reusable_connections(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        account = client.app.state.repository.list_accounts()[0]
        response = client.get(f"/accounts/{account.id}/setup", headers=_auth())

        assert response.status_code == 200
        for number in range(1, 8):
            assert f"Step {number}" in response.text
        assert "Reuse a saved Buffer connection" in response.text
        assert "Reuse a saved Slack connection" in response.text
        assert "Things never to reveal" in response.text


def test_connections_page_masks_all_saved_secrets(settings):
    from cryptography.fernet import Fernet
    from app.main import create_app

    configured = settings.model_copy(
        update={"app_encryption_key": Fernet.generate_key().decode()}
    )
    with TestClient(create_app(settings=configured, start_scheduler=False)) as client:
        service = client.app.state.services.integrations
        service.save_connection(
            "buffer", "Founder Buffer", {"api_key": "buffer-private-key"}
        )
        service.save_connection(
            "slack",
            "Team Slack",
            {
                "webhook_url": "https://hooks.slack.test/private-value",
                "signing_secret": "slack-private-secret",
            },
        )

        response = client.get("/connections", headers=_auth())

        assert response.status_code == 200
        assert "Founder Buffer" in response.text
        assert "Team Slack" in response.text
        assert "Configured ••••••••" in response.text
        assert "buffer-private-key" not in response.text
        assert "private-value" not in response.text
        assert "slack-private-secret" not in response.text


def test_slack_callback_url_and_outbound_test_are_connection_specific(settings):
    from cryptography.fernet import Fernet
    from app.main import create_app

    configured = settings.model_copy(
        update={
            "app_encryption_key": Fernet.generate_key().decode(),
            "base_url": "https://review.example/",
        }
    )
    with TestClient(create_app(settings=configured, start_scheduler=False)) as client:
        repository = client.app.state.repository
        account = repository.list_accounts()[0]
        connection = client.app.state.services.integrations.save_connection(
            "slack",
            "Team Slack",
            {
                "webhook_url": "https://hooks.slack.test/private-value",
                "signing_secret": "slack-private-secret",
            },
        )
        client.app.state.services.integrations.bind(
            account.id, "slack", connection.id, "#x-review"
        )

        connections = client.get(
            f"/connections?account_id={account.id}", headers=_auth()
        )
        setup = client.get(f"/accounts/{account.id}/setup", headers=_auth())

    callback_url = "https://review.example/integrations/slack/1/actions"
    assert connections.status_code == 200
    assert callback_url in connections.text
    assert "Disable Socket Mode" in connections.text
    assert "Interactivity &amp; Shortcuts must be On" in connections.text
    assert setup.status_code == 200
    assert callback_url in setup.text
    assert "Test outbound Slack message" in setup.text
    assert "Inbound actions: Not verified" in setup.text


def test_slack_inbound_action_health_is_account_and_connection_scoped(settings):
    from cryptography.fernet import Fernet
    from app.main import create_app

    configured = settings.model_copy(
        update={"app_encryption_key": Fernet.generate_key().decode()}
    )
    with TestClient(create_app(settings=configured, start_scheduler=False)) as client:
        repository = client.app.state.repository
        first = repository.list_accounts()[0]
        second = repository.create_account("Second", "second", "UTC", copy_from_id=first.id)
        connection = client.app.state.services.integrations.save_connection(
            "slack",
            "Team Slack",
            {
                "webhook_url": "https://hooks.slack.test/private-value",
                "signing_secret": "slack-private-secret",
            },
        )
        service = client.app.state.services.integrations
        service.bind(first.id, "slack", connection.id, "#first-review")
        service.bind(second.id, "slack", connection.id, "#second-review")
        draft = client.app.state.services.pipeline.generate_draft(
            first.id, context_id=repository.list_contexts(first.id)[0].id
        )
        job, created = repository.enqueue_slack_action(
            idempotency_key="digest-never-rendered",
            connection_id=connection.id,
            x_account_id=first.id,
            draft_id=draft.id,
            action_id="approve_draft",
            expected_live=False,
            reviewer="slack:reviewer",
        )
        claimed = repository.claim_next_slack_action("2000-01-01T00:00:00+00:00")
        assert created is True
        assert claimed is not None
        completed = repository.complete_slack_action(
            first.id, job.id, result_draft_id=draft.id
        )

        first_setup = client.get(f"/accounts/{first.id}/setup", headers=_auth())
        second_setup = client.get(f"/accounts/{second.id}/setup", headers=_auth())

    assert first_setup.status_code == 200
    assert "Inbound actions: Completed" in first_setup.text
    assert completed.completed_at in first_setup.text
    assert "slack-private-secret" not in first_setup.text
    assert "private-value" not in first_setup.text
    assert "digest-never-rendered" not in first_setup.text
    assert second_setup.status_code == 200
    assert "Inbound actions: Not verified" in second_setup.text
    assert completed.completed_at not in second_setup.text


def test_slack_inbound_action_health_renders_received_and_failed_safely(settings):
    from cryptography.fernet import Fernet
    from app.main import create_app

    configured = settings.model_copy(
        update={"app_encryption_key": Fernet.generate_key().decode()}
    )
    with TestClient(create_app(settings=configured, start_scheduler=False)) as client:
        repository = client.app.state.repository
        account = repository.list_accounts()[0]
        connection = client.app.state.services.integrations.save_connection(
            "slack",
            "Team Slack",
            {
                "webhook_url": "https://hooks.slack.test/private-value",
                "signing_secret": "slack-private-secret",
            },
        )
        client.app.state.services.integrations.bind(
            account.id, "slack", connection.id, "#x-review"
        )
        draft = client.app.state.services.pipeline.generate_draft(
            account.id, context_id=repository.list_contexts(account.id)[0].id
        )
        job, _ = repository.enqueue_slack_action(
            idempotency_key="received-digest-never-rendered",
            connection_id=connection.id,
            x_account_id=account.id,
            draft_id=draft.id,
            action_id="reject_draft",
            expected_live=False,
            reviewer="slack:reviewer",
        )

        received = client.get(f"/accounts/{account.id}/setup", headers=_auth())
        claimed = repository.claim_next_slack_action("2000-01-01T00:00:00+00:00")
        assert claimed is not None
        failed = repository.fail_slack_action(
            account.id,
            job.id,
            "raw exception with response URL https://slack.test/private",
        )
        failed_response = client.get(f"/accounts/{account.id}/setup", headers=_auth())

    assert "Inbound actions: Received" in received.text
    assert job.created_at in received.text
    assert "Inbound actions: Failed" in failed_response.text
    assert failed.completed_at in failed_response.text
    assert "raw exception" not in failed_response.text
    assert "slack.test/private" not in failed_response.text
    assert "received-digest-never-rendered" not in failed_response.text


def test_account_without_copy_source_is_immediately_usable(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        response = client.post(
            "/api/accounts",
            headers=_auth(),
            json={"name": "Fresh", "handle": "fresh", "timezone": "UTC"},
        )
        account_id = response.json()["id"]
        overview = client.get("/", headers=_auth())
        setup = client.get(f"/accounts/{account_id}/setup", headers=_auth())

        assert response.status_code == 201
        assert overview.status_code == 200
        assert setup.status_code == 200
        assert "@fresh" in overview.text
        assert len(client.app.state.repository.list_contexts(account_id)) == 10


def test_telegram_actions_are_disabled_without_webhook_secret(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        repository = client.app.state.repository
        account = repository.list_accounts()[0]
        draft = client.app.state.services.pipeline.generate_draft(
            account.id, context_id=repository.list_contexts(account.id)[0].id
        )
        response = client.post(
            "/integrations/telegram/webhook",
            json={
                "callback_query": {
                    "id": "callback",
                    "from": {"id": 123, "username": "forged"},
                    "data": f"approve:{account.id}:{draft.id}",
                }
            },
        )

        assert response.status_code == 403
        assert repository.get_draft(account.id, draft.id).status == "pending"
