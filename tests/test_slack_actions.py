from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.services.notifiers import ConsoleNotifier, NotifierManager, SlackNotifier
from app.services.integrations import IntegrationService


def _configured_settings(settings):
    return settings.model_copy(
        update={"app_encryption_key": Fernet.generate_key().decode()}
    )


def _slack_payload(
    action_id: str,
    account_id: int,
    draft_id: str,
    *,
    expected_live: bool = False,
) -> dict:
    return {
        "user": {"id": "U123", "name": "reviewer"},
        "actions": [
            {
                "action_id": action_id,
                "value": json.dumps(
                    {
                        "x_account_id": account_id,
                        "draft_id": draft_id,
                        "expected_live": expected_live,
                    }
                ),
            }
        ],
    }


def _signed_request(payload: dict, secret: str, *, timestamp: int | None = None):
    timestamp = int(time.time()) if timestamp is None else timestamp
    body = urlencode({"payload": json.dumps(payload)}).encode()
    base = f"v0:{timestamp}:".encode() + body
    signature = "v0=" + hmac.new(
        secret.encode(), base, hashlib.sha256
    ).hexdigest()
    return body, {
        "content-type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": signature,
    }


def _connect_slack(app, account_id: int, *, secret: str = "signing-secret"):
    connection = app.state.services.integrations.save_connection(
        "slack",
        f"Slack {account_id}",
        {
            "webhook_url": "https://hooks.slack.test/review",
            "signing_secret": secret,
        },
    )
    app.state.services.integrations.bind(
        account_id, "slack", connection.id, "review-channel"
    )
    return connection


def _pending(app, account_id: int):
    repository = app.state.repository
    app.state.services.notifiers = NotifierManager([ConsoleNotifier()])
    app.state.services.pipeline.notifiers = app.state.services.notifiers
    return app.state.services.pipeline.generate_draft(
        account_id, context_id=repository.list_contexts(account_id)[0].id
    )


def test_slack_review_message_contains_account_actions_and_edit_link(repository):
    account = repository.list_accounts()[0]
    profile = repository.get_profile(account.id)
    context = repository.list_contexts(account.id)[0]
    draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="A specific founder observation.",
        topic="reliability",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={}, request=request)

    notifier = SlackNotifier(
        "https://hooks.slack.test/review",
        "https://review.example",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        live_posting=True,
    )

    result = notifier.notify_for_review(draft, account, profile, context)

    assert result.success is True
    payload = json.loads(captured_requests[0].content)
    assert f"@{account.handle}" in str(payload)
    assert "LIVE via Buffer" in str(payload)
    assert draft.topic in str(payload)
    actions = next(
        block["elements"] for block in payload["blocks"] if block["type"] == "actions"
    )
    assert {item.get("action_id") for item in actions} >= {
        "approve_draft",
        "reject_draft",
    }
    assert f"publish to @{account.handle}" in actions[0]["text"]["text"]
    assert json.loads(actions[0]["value"])["expected_live"] is True
    edit = next(item for item in actions if item.get("url"))
    assert edit["url"] == (
        f"https://review.example/accounts/{account.id}#draft-{draft.id}"
    )


def test_notifier_manager_resolves_each_accounts_saved_slack_connection(
    settings, repository
):
    configured = _configured_settings(settings)
    integrations = IntegrationService(configured, repository)
    first = repository.list_accounts()[0]
    second = repository.create_account("Second", "second", "UTC", copy_from_id=first.id)
    for account, suffix in ((first, "first"), (second, "second")):
        connection = integrations.save_connection(
            "slack",
            f"Slack {suffix}",
            {
                "webhook_url": f"https://hooks.slack.test/{suffix}",
                "signing_secret": f"secret-{suffix}",
            },
        )
        integrations.bind(account.id, "slack", connection.id, f"channel-{suffix}")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={}, request=request)

    manager = NotifierManager(
        [ConsoleNotifier()],
        integrations=integrations,
        base_url=configured.base_url,
        slack_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    for account in (first, second):
        context = repository.list_contexts(account.id)[0]
        draft = repository.create_draft(
            account.id,
            context_id=context.id,
            schedule_id=None,
            text=f"Account-local draft {account.id}",
            topic="local",
            source_summary="manual",
            status="pending",
            safety_status="safe",
            similarity_score=0.1,
            attempt=1,
            parent_draft_id=None,
            config_version=repository.current_config_version(account.id),
            expires_at=None,
            generator_provider="demo",
            prompt_snapshot="",
        )
        manager.notify_for_review(
            draft,
            account,
            repository.get_profile(account.id),
            context,
        )

    assert [request.url.path for request in captured] == ["/first", "/second"]
    assert f"@{first.handle}" in captured[0].content.decode()
    assert f"@{second.handle}" in captured[1].content.decode()


def test_slack_delivery_failure_never_persists_or_logs_webhook_secret(
    settings, repository, caplog
):
    configured = _configured_settings(settings)
    secret_path = "services/SECRET/TOKEN"
    integrations = IntegrationService(configured, repository)
    account = repository.list_accounts()[0]
    connection = integrations.save_connection(
        "slack",
        "Failing Slack",
        {
            "webhook_url": f"https://hooks.slack.test/{secret_path}",
            "signing_secret": "signing-secret",
        },
    )
    integrations.bind(account.id, "slack", connection.id, "review")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden", request=request)

    manager = NotifierManager(
        [ConsoleNotifier()],
        integrations=integrations,
        base_url=configured.base_url,
        slack_client=httpx.Client(transport=httpx.MockTransport(handler)),
        buffer_live_posting=False,
    )
    from app.services.factory import build_services

    pipeline = build_services(configured, repository).pipeline
    pipeline.notifiers = manager
    caplog.set_level(logging.INFO)

    pipeline.generate_draft(
        account.id, context_id=repository.list_contexts(account.id)[0].id
    )

    assert secret_path not in str(repository.list_events(account.id))
    assert secret_path not in caplog.text


def test_valid_slack_approval_is_queued_for_processing(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        draft = _pending(app, account.id)
        body, headers = _signed_request(
            _slack_payload("approve_draft", account.id, draft.id), "signing-secret"
        )
        url = f"/integrations/slack/{connection.id}/actions"

        response = client.post(url, content=body, headers=headers)

        assert response.status_code == 200
        assert response.json()["text"] == "Approval received and queued for processing"
        assert app.state.repository.get_draft(account.id, draft.id).status == "pending"
        jobs = app.state.repository.list_slack_actions(account.id)
        assert len(jobs) == 1
        assert jobs[0].status == "pending"


def test_slack_approval_is_queued_when_publication_mode_changes(settings):
    from app.main import create_app

    configured = _configured_settings(settings).model_copy(
        update={"buffer_live_posting": True}
    )
    app = create_app(settings=configured, start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        draft = _pending(app, account.id)
        app.state.repository.update_account(
            account.id, {"live_posting_enabled": True}
        )
        body, headers = _signed_request(
            _slack_payload(
                "approve_draft",
                account.id,
                draft.id,
                expected_live=False,
            ),
            "signing-secret",
        )

        response = client.post(
            f"/integrations/slack/{connection.id}/actions",
            content=body,
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["text"] == "Approval received and queued for processing"
        assert app.state.repository.get_draft(account.id, draft.id).status == "pending"
        assert len(app.state.repository.list_slack_actions(account.id)) == 1


def test_slack_rejection_is_queued_for_processing(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        draft = _pending(app, account.id)
        body, headers = _signed_request(
            _slack_payload("reject_draft", account.id, draft.id), "signing-secret"
        )

        response = client.post(
            f"/integrations/slack/{connection.id}/actions",
            content=body,
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["text"] == "Rejection received and queued for processing"
        assert app.state.repository.get_draft(account.id, draft.id).status == "pending"
        jobs = app.state.repository.list_slack_actions(account.id)
        assert len(jobs) == 1
        assert jobs[0].status == "pending"
        assert jobs[0].action_id == "reject_draft"


def test_slack_rejects_invalid_and_stale_signatures(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        draft = _pending(app, account.id)
        payload = _slack_payload("approve_draft", account.id, draft.id)
        body, headers = _signed_request(payload, "wrong-secret")
        stale_body, stale_headers = _signed_request(
            payload, "signing-secret", timestamp=int(time.time()) - 301
        )
        url = f"/integrations/slack/{connection.id}/actions"

        invalid = client.post(url, content=body, headers=headers)
        stale = client.post(url, content=stale_body, headers=stale_headers)

        assert invalid.status_code == 403
        assert stale.status_code == 403
        assert app.state.repository.get_draft(account.id, draft.id).status == "pending"
        assert app.state.repository.list_slack_actions(account.id) == []


def test_slack_rejects_connection_account_mismatch(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        repository = app.state.repository
        account = repository.list_accounts()[0]
        bound = _connect_slack(app, account.id, secret="bound-secret")
        other = app.state.services.integrations.save_connection(
            "slack",
            "Other Slack",
            {
                "webhook_url": "https://hooks.slack.test/other",
                "signing_secret": "other-secret",
            },
        )
        draft = _pending(app, account.id)
        body, headers = _signed_request(
            _slack_payload("approve_draft", account.id, draft.id), "other-secret"
        )

        response = client.post(
            f"/integrations/slack/{other.id}/actions", content=body, headers=headers
        )

        assert bound.id != other.id
        assert response.status_code == 403
        assert repository.get_draft(account.id, draft.id).status == "pending"
        assert repository.list_slack_actions(account.id) == []


def test_slack_rejects_draft_account_mismatch(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        repository = app.state.repository
        first = repository.list_accounts()[0]
        second = repository.create_account("Second", "second", "UTC", copy_from_id=first.id)
        connection = _connect_slack(app, first.id)
        second_draft = _pending(app, second.id)
        body, headers = _signed_request(
            _slack_payload("approve_draft", first.id, second_draft.id),
            "signing-secret",
        )

        response = client.post(
            f"/integrations/slack/{connection.id}/actions",
            content=body,
            headers=headers,
        )

        assert response.status_code in {400, 404}
        assert repository.get_draft(second.id, second_draft.id).status == "pending"
        assert repository.list_slack_actions(first.id) == []
        assert repository.list_slack_actions(second.id) == []


def test_slack_malformed_and_unsupported_actions_create_no_jobs(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        draft = _pending(app, account.id)
        malformed, malformed_headers = _signed_request(
            {"actions": []}, "signing-secret"
        )
        unsupported, unsupported_headers = _signed_request(
            _slack_payload("open_modal", account.id, draft.id), "signing-secret"
        )
        url = f"/integrations/slack/{connection.id}/actions"

        malformed_response = client.post(
            url, content=malformed, headers=malformed_headers
        )
        unsupported_response = client.post(
            url, content=unsupported, headers=unsupported_headers
        )

        assert malformed_response.status_code == 400
        assert unsupported_response.status_code == 400
        assert app.state.repository.list_slack_actions(account.id) == []


def test_duplicate_slack_delivery_returns_the_same_queued_job(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        draft = _pending(app, account.id)
        body, headers = _signed_request(
            _slack_payload("approve_draft", account.id, draft.id), "signing-secret"
        )
        url = f"/integrations/slack/{connection.id}/actions"

        first = client.post(url, content=body, headers=headers)
        second = client.post(url, content=body, headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["job_id"] == second.json()["job_id"]
        assert len(app.state.repository.list_slack_actions(account.id)) == 1


def test_slack_reject_requires_an_explicit_publication_mode(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        draft = _pending(app, account.id)
        payload = _slack_payload("reject_draft", account.id, draft.id)
        value = json.loads(payload["actions"][0]["value"])
        del value["expected_live"]
        payload["actions"][0]["value"] = json.dumps(value)
        body, headers = _signed_request(payload, "signing-secret")

        response = client.post(
            f"/integrations/slack/{connection.id}/actions",
            content=body,
            headers=headers,
        )

        assert response.status_code == 400
        assert app.state.repository.list_slack_actions(account.id) == []


def test_slack_rejects_a_disabled_account_binding_without_a_job(settings):
    from app.main import create_app

    app = create_app(settings=_configured_settings(settings), start_scheduler=False, start_slack_worker=False)
    with TestClient(app) as client:
        account = app.state.repository.list_accounts()[0]
        connection = _connect_slack(app, account.id)
        app.state.services.integrations.bind(
            account.id,
            "slack",
            connection.id,
            "review-channel",
            enabled=False,
        )
        draft = _pending(app, account.id)
        body, headers = _signed_request(
            _slack_payload("approve_draft", account.id, draft.id), "signing-secret"
        )

        response = client.post(
            f"/integrations/slack/{connection.id}/actions",
            content=body,
            headers=headers,
        )

        assert response.status_code == 403
        assert app.state.repository.list_slack_actions(account.id) == []


def test_lifespan_recovers_pending_slack_action_job(settings):
    from app.main import create_app

    configured = _configured_settings(settings).model_copy(
        update={
            "slack_action_worker_enabled": True,
            "slack_action_poll_seconds": 0.1,
        }
    )
    app = create_app(
        settings=configured,
        start_scheduler=False,
    )
    repository = app.state.repository
    account = repository.list_accounts()[0]
    draft = _pending(app, account.id)
    connection = repository.create_integration_connection(
        "slack", "Recovery", "encrypted"
    )
    job, _ = repository.enqueue_slack_action(
        idempotency_key="lifespan-recovery",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=False,
        reviewer="slack:U123:reviewer",
    )

    with TestClient(app):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if repository.get_slack_action_job(account.id, job.id).status == "completed":
                break
            time.sleep(0.01)

    assert repository.get_slack_action_job(account.id, job.id).status == "completed"
