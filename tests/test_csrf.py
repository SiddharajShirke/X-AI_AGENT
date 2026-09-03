from __future__ import annotations

import base64

from fastapi.testclient import TestClient


def _auth(username: str = "demo", password: str = "demo-pass") -> dict[str, str]:
    value = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def _pending(app):
    account = app.state.repository.list_accounts()[0]
    draft = app.state.services.pipeline.generate_draft(
        account.id,
        context_id=app.state.repository.list_contexts(account.id)[0].id,
    )
    return account, draft


def test_approval_form_without_csrf_token_changes_nothing(settings):
    from app.main import create_app

    app = create_app(settings=settings, start_scheduler=False)
    with TestClient(app) as client:
        account, draft = _pending(app)
        response = client.post(
            f"/ui/accounts/{account.id}/drafts/{draft.id}/approve",
            headers=_auth(),
        )

        assert response.status_code == 403
        assert app.state.repository.get_draft(account.id, draft.id).status == "pending"


def test_valid_csrf_approval_uses_authenticated_reviewer(settings):
    from app.main import create_app

    app = create_app(settings=settings, start_scheduler=False)
    with TestClient(app) as client:
        account, draft = _pending(app)
        token = app.state.csrf.issue("demo")
        response = client.post(
            f"/ui/accounts/{account.id}/drafts/{draft.id}/approve",
            headers=_auth(),
            data={"csrf_token": token, "reviewer": "forged-reviewer"},
            follow_redirects=False,
        )

        stored = app.state.repository.get_draft(account.id, draft.id)
        assert response.status_code == 303
        assert stored.status == "published"
        assert stored.reviewer == "demo"


def test_csrf_token_is_bound_to_authenticated_subject(settings):
    from app.main import create_app

    app = create_app(settings=settings, start_scheduler=False)
    with TestClient(app) as client:
        account, draft = _pending(app)
        wrong_subject_token = app.state.csrf.issue("someone-else")
        response = client.post(
            f"/ui/accounts/{account.id}/drafts/{draft.id}/approve",
            headers=_auth(),
            data={"csrf_token": wrong_subject_token},
        )

        assert response.status_code == 403
        assert app.state.repository.get_draft(account.id, draft.id).status == "pending"


def test_ui_account_mismatch_cannot_mutate_draft(settings):
    from app.main import create_app

    app = create_app(settings=settings, start_scheduler=False)
    with TestClient(app) as client:
        repository = app.state.repository
        first, draft = _pending(app)
        second = repository.create_account("Second", "second", "UTC", copy_from_id=first.id)
        response = client.post(
            f"/ui/accounts/{second.id}/drafts/{draft.id}/approve",
            headers=_auth(),
            data={"csrf_token": app.state.csrf.issue("demo")},
        )

        assert response.status_code in {400, 404}
        assert repository.get_draft(first.id, draft.id).status == "pending"
