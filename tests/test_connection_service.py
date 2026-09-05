from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.services.integrations import IntegrationError, IntegrationService


@pytest.fixture()
def accounts(repository):
    first = repository.list_accounts()[0]
    second = repository.create_account(
        name="Second Brand",
        handle="second_brand",
        timezone="UTC",
        copy_from_id=first.id,
    )
    return first, second


@pytest.fixture()
def captured_requests():
    return []


@pytest.fixture()
def integration_service(settings, repository, captured_requests):
    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if "hooks.slack.test" in str(request.url):
            return httpx.Response(200, text="ok")
        body = json.loads(request.content)
        if "organizations" in body.get("query", ""):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "account": {
                            "organizations": [{"id": "organization-a"}]
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={"data": {"channels": [{"id": "channel-a"}, {"id": "channel-b"}]}},
        )

    configured = settings.model_copy(
        update={
            "app_encryption_key": Fernet.generate_key().decode(),
            "buffer_api_url": "https://buffer.test/graphql",
        }
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return IntegrationService(configured, repository, client=client)


def test_shared_buffer_connection_resolves_different_channels(
    integration_service, accounts
):
    connection = integration_service.save_connection(
        "buffer", "Shared", {"api_key": "secret"}
    )
    integration_service.bind(accounts[0].id, "buffer", connection.id, "channel-a")
    integration_service.bind(accounts[1].id, "buffer", connection.id, "channel-b")

    assert integration_service.resolve_buffer(accounts[0].id).channel_id == "channel-a"
    assert integration_service.resolve_buffer(accounts[1].id).channel_id == "channel-b"


def test_buffer_connection_test_is_read_only(
    integration_service, accounts, captured_requests
):
    connection = integration_service.save_connection(
        "buffer", "Shared", {"api_key": "buffer-secret"}
    )
    integration_service.bind(accounts[0].id, "buffer", connection.id, "channel-a")

    result = integration_service.test_buffer(accounts[0].id)

    assert result.success is True
    assert len(captured_requests) == 2
    assert all("createPost" not in request.content.decode() for request in captured_requests)
    assert all("buffer-secret" not in request.content.decode() for request in captured_requests)
    assert json.loads(captured_requests[1].content)["variables"] == {
        "organizationId": "organization-a"
    }


def test_buffer_test_accepts_documented_account_id_keyword(
    integration_service, accounts
):
    connection = integration_service.save_connection(
        "buffer", "Shared", {"api_key": "secret"}
    )
    integration_service.bind(accounts[0].id, "buffer", connection.id, "channel-a")

    assert integration_service.test_buffer(account_id=accounts[0].id).success is True


def test_buffer_test_uses_organization_scoped_channels_api(settings, repository, accounts):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "organizations" in body.get("query", ""):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "account": {
                            "organizations": [{"id": "organization-a"}]
                        }
                    }
                },
            )
        if body.get("variables") == {"organizationId": "organization-a"}:
            return httpx.Response(
                200,
                json={"data": {"channels": [{"id": "channel-a"}]}},
            )
        return httpx.Response(
            200,
            json={
                "errors": [
                    {"message": "Field 'channels' argument 'input' is required"}
                ]
            },
        )

    configured = settings.model_copy(
        update={
            "app_encryption_key": Fernet.generate_key().decode(),
            "buffer_api_url": "https://buffer.test/graphql",
        }
    )
    service = IntegrationService(
        configured,
        repository,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    connection = service.save_connection("buffer", "Shared", {"api_key": "secret"})
    service.bind(accounts[0].id, "buffer", connection.id, "channel-a")

    result = service.test_buffer(accounts[0].id)

    assert result.success is True
    assert len(requests) == 2
    assert requests[1]["variables"] == {"organizationId": "organization-a"}
    assert all("createPost" not in request["query"] for request in requests)


def test_missing_master_key_locks_connection_operations(settings, repository):
    service = IntegrationService(settings.model_copy(update={"app_encryption_key": ""}), repository)

    with pytest.raises(IntegrationError, match="locked"):
        service.save_connection("buffer", "Locked", {"api_key": "secret"})


def test_wrong_master_key_locks_existing_credentials(
    settings, repository, accounts
):
    first = IntegrationService(
        settings.model_copy(update={"app_encryption_key": Fernet.generate_key().decode()}),
        repository,
    )
    connection = first.save_connection("buffer", "Shared", {"api_key": "secret"})
    first.bind(accounts[0].id, "buffer", connection.id, "channel-a")
    wrong_key = IntegrationService(
        settings.model_copy(update={"app_encryption_key": Fernet.generate_key().decode()}),
        repository,
    )

    with pytest.raises(IntegrationError, match="locked"):
        wrong_key.resolve_buffer(accounts[0].id)


def test_connection_test_without_binding_returns_safe_failure(
    integration_service, accounts
):
    result = integration_service.test_buffer(accounts[0].id)

    assert result.success is False
    assert result.provider == "buffer"
    assert "enabled" in result.error


def test_disabled_binding_resolves_to_none(integration_service, accounts):
    connection = integration_service.save_connection(
        "buffer", "Shared", {"api_key": "secret"}
    )
    integration_service.bind(
        accounts[0].id, "buffer", connection.id, "channel-a", enabled=False
    )

    assert integration_service.resolve_buffer(accounts[0].id) is None


def test_slack_test_is_labeled_and_credentials_are_replaceable(
    integration_service, accounts, captured_requests
):
    connection = integration_service.save_connection(
        "slack",
        "Review Slack",
        {"webhook_url": "https://hooks.slack.test/one", "signing_secret": "old-secret"},
    )
    integration_service.bind(accounts[0].id, "slack", connection.id, "")

    result = integration_service.test_slack(accounts[0].id)
    replaced = integration_service.save_connection(
        "slack",
        "Review Slack",
        {"webhook_url": "https://hooks.slack.test/two", "signing_secret": "new-secret"},
        connection_id=connection.id,
    )

    assert result.success is True
    assert json.loads(captured_requests[0].content)["text"].startswith(
        "Startup X Agent connection test"
    )
    assert replaced.id == connection.id
    assert "secret" not in replaced.model_dump_json()
    assert integration_service.get_slack_connection(connection.id).signing_secret == "new-secret"


def test_slack_test_explains_inactive_webhook_without_leaking_it(
    settings, repository, accounts
):
    webhook_url = "https://hooks.slack.test/services/secret/path"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no_service")

    configured = settings.model_copy(
        update={"app_encryption_key": Fernet.generate_key().decode()}
    )
    service = IntegrationService(
        configured,
        repository,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    connection = service.save_connection(
        "slack",
        "Review Slack",
        {"webhook_url": webhook_url, "signing_secret": "signing-secret"},
    )
    service.bind(accounts[0].id, "slack", connection.id, "")

    result = service.test_slack(accounts[0].id)

    assert result.success is False
    assert result.error == (
        "Slack webhook is inactive or revoked; create a new Incoming Webhook "
        "and replace this connection"
    )
    assert webhook_url not in result.error


def test_disconnect_disables_binding_without_deleting_connection(
    integration_service, repository, accounts
):
    connection = integration_service.save_connection(
        "buffer", "Shared", {"api_key": "secret"}
    )
    integration_service.bind(accounts[0].id, "buffer", connection.id, "channel-a")

    integration_service.disconnect(accounts[0].id, "buffer")

    binding = repository.get_account_integration(accounts[0].id, "buffer")
    assert binding is not None
    assert binding.enabled is False
    assert repository.get_encrypted_credentials(connection.id)


def test_slack_signature_accepts_valid_and_rejects_stale_or_invalid(
    integration_service,
):
    body = b"payload=%7B%22actions%22%3A%5B%5D%7D"
    timestamp = str(int(time.time()))
    secret = "signing-secret"
    digest = hmac.new(
        secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256
    ).hexdigest()

    integration_service.verify_slack_signature(secret, timestamp, f"v0={digest}", body)

    with pytest.raises(IntegrationError, match="stale"):
        integration_service.verify_slack_signature(
            secret, str(int(timestamp) - 301), f"v0={digest}", body
        )
    with pytest.raises(IntegrationError, match="signature"):
        integration_service.verify_slack_signature(secret, timestamp, "v0=wrong", body)
    with pytest.raises(IntegrationError, match="timestamp"):
        integration_service.verify_slack_signature(secret, "not-a-number", "v0=wrong", body)


@pytest.mark.parametrize(
    ("provider", "credentials"),
    [
        ("buffer", {}),
        ("slack", {"webhook_url": "https://hooks.slack.test/one"}),
        ("unknown", {"token": "secret"}),
    ],
)
def test_unsupported_or_incomplete_connections_are_rejected(
    integration_service, provider, credentials
):
    with pytest.raises(IntegrationError):
        integration_service.save_connection(provider, "Invalid", credentials)
