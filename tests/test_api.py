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
        response = client.get("/api/profile")

    assert response.status_code == 401


def test_profile_update_generation_rejection_and_approval(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        headers = _auth()
        update = client.put(
            "/api/profile",
            headers=headers,
            json={"domain": "Secure AI workflow infrastructure", "brand_voice": "Direct founder voice"},
        )
        generated = client.post("/api/drafts/generate", headers=headers, json={"context_id": 1})
        draft_id = generated.json()["id"]
        rejected = client.post(
            f"/api/drafts/{draft_id}/reject",
            headers=headers,
            json={"reason": "too_generic", "notes": "Use a specific observation.", "reviewer": "founder"},
        )
        replacement_id = rejected.json()["replacement"]["id"]
        approved = client.post(
            f"/api/drafts/{replacement_id}/approve",
            headers=headers,
            json={"reviewer": "founder"},
        )

    assert update.status_code == 200
    assert generated.status_code == 201
    assert rejected.status_code == 200
    assert replacement_id != draft_id
    assert approved.json()["status"] == "published"


def test_dashboard_renders_configuration_and_review_controls(settings):
    from app.main import create_app

    with TestClient(create_app(settings=settings, start_scheduler=False)) as client:
        response = client.get("/", headers=_auth())

    assert response.status_code == 200
    assert "Startup profile" in response.text
    assert "Generate a draft" in response.text
    assert "Approve" in response.text
    assert "10 editable posting slots" in response.text
