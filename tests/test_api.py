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
    assert "Startup profile" in response.text
    assert "Generate a draft" in response.text
    assert "Approve" in response.text
    assert "10 editable posting slots" in response.text
