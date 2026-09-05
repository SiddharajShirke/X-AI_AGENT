from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
import time
from typing import Any

import httpx

from app.config import Settings
from app.models import AccountIntegration, ConnectionTestResult, IntegrationConnection
from app.repository import Repository
from app.services.crypto import CredentialCipher, CredentialError


SUPPORTED_PROVIDERS = {"buffer", "slack"}


class IntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BufferTarget:
    api_key: str
    channel_id: str


@dataclass(frozen=True)
class SlackTarget:
    webhook_url: str
    signing_secret: str
    connection_id: int


class IntegrationService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        *,
        client: httpx.Client | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self._encryption_key = settings.app_encryption_key
        self._client = client or httpx.Client(timeout=settings.buffer_timeout_seconds)

    def _cipher(self) -> CredentialCipher:
        try:
            return CredentialCipher(self._encryption_key)
        except CredentialError as exc:
            raise IntegrationError(str(exc)) from exc

    @staticmethod
    def _validate_credentials(provider: str, credentials: dict[str, str]) -> dict[str, str]:
        normalized = provider.strip().lower()
        if normalized not in SUPPORTED_PROVIDERS:
            raise IntegrationError(f"Unsupported integration provider: {provider}")
        clean = {str(key): str(value).strip() for key, value in credentials.items()}
        if normalized == "buffer" and not clean.get("api_key"):
            raise IntegrationError("Buffer connection requires an API key")
        if normalized == "slack" and (
            not clean.get("webhook_url") or not clean.get("signing_secret")
        ):
            raise IntegrationError(
                "Slack connection requires a webhook URL and signing secret"
            )
        return clean

    def _decrypt(self, connection_id: int) -> dict[str, str]:
        try:
            encrypted = self.repository.get_encrypted_credentials(connection_id)
            return self._cipher().decrypt(encrypted)
        except (CredentialError, KeyError) as exc:
            message = str(exc)
            if isinstance(exc, CredentialError):
                raise IntegrationError(message) from exc
            raise IntegrationError("Integration connection was not found") from exc

    def save_connection(
        self,
        provider: str,
        label: str,
        credentials: dict[str, str],
        *,
        connection_id: int | None = None,
    ) -> IntegrationConnection:
        normalized_provider = str(provider).strip().lower()
        normalized_label = str(label).strip()
        if not normalized_label:
            raise IntegrationError("Connection label is required")
        clean = self._validate_credentials(normalized_provider, credentials)
        try:
            encrypted = self._cipher().encrypt(clean)
        except CredentialError as exc:
            raise IntegrationError(str(exc)) from exc
        if connection_id is None:
            return self.repository.create_integration_connection(
                normalized_provider, normalized_label, encrypted
            )
        return self.repository.replace_integration_connection(
            connection_id,
            provider=normalized_provider,
            label=normalized_label,
            encrypted_credentials=encrypted,
        )

    def bind(
        self,
        x_account_id: int,
        provider: str,
        connection_id: int,
        target_id: str,
        *,
        enabled: bool = True,
    ) -> AccountIntegration:
        normalized_provider = str(provider).strip().lower()
        if normalized_provider not in SUPPORTED_PROVIDERS:
            raise IntegrationError(f"Unsupported integration provider: {provider}")
        self.repository.get_account(x_account_id)
        return self.repository.bind_account_integration(
            x_account_id,
            normalized_provider,
            connection_id,
            target_id,
            enabled=enabled,
        )

    def resolve_buffer(self, x_account_id: int) -> BufferTarget | None:
        binding = self.repository.get_account_integration(x_account_id, "buffer")
        if binding is None or not binding.enabled:
            return None
        credentials = self._decrypt(binding.connection_id)
        api_key = credentials.get("api_key", "").strip()
        channel_id = binding.target_id.strip()
        if not api_key or not channel_id:
            raise IntegrationError("Buffer connection requires an API key and channel ID")
        return BufferTarget(api_key=api_key, channel_id=channel_id)

    def resolve_slack(self, x_account_id: int) -> SlackTarget | None:
        binding = self.repository.get_account_integration(x_account_id, "slack")
        if binding is None or not binding.enabled:
            return None
        target = self.get_slack_connection(binding.connection_id)
        return target

    def get_slack_connection(self, connection_id: int) -> SlackTarget:
        try:
            connection = self.repository.get_integration_connection(connection_id)
        except KeyError as exc:
            raise IntegrationError("Slack connection was not found") from exc
        if connection.provider != "slack":
            raise IntegrationError("Connection is not a Slack connection")
        credentials = self._decrypt(connection_id)
        webhook_url = credentials.get("webhook_url", "").strip()
        signing_secret = credentials.get("signing_secret", "").strip()
        if not webhook_url or not signing_secret:
            raise IntegrationError(
                "Slack connection requires a webhook URL and signing secret"
            )
        return SlackTarget(
            webhook_url=webhook_url,
            signing_secret=signing_secret,
            connection_id=connection_id,
        )

    @staticmethod
    def verify_slack_signature(
        signing_secret: str,
        timestamp: str,
        supplied_signature: str,
        body: bytes,
    ) -> None:
        try:
            timestamp_value = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise IntegrationError("Invalid Slack request timestamp") from exc
        if abs(int(time.time()) - timestamp_value) > 300:
            raise IntegrationError("Slack request timestamp is stale")
        base = f"v0:{timestamp}:".encode() + body
        expected = "v0=" + hmac.new(
            signing_secret.encode(), base, hashlib.sha256
        ).hexdigest()
        if not secrets.compare_digest(expected, supplied_signature):
            raise IntegrationError("Invalid Slack request signature")

    def _record_test(
        self, x_account_id: int, provider: str, *, success: bool, error: str = ""
    ) -> ConnectionTestResult:
        if self.repository.get_account_integration(x_account_id, provider) is not None:
            self.repository.record_integration_test(
                x_account_id, provider, success=success, error=error
            )
        return ConnectionTestResult(success=success, provider=provider, error=error)

    def test_buffer(self, account_id: int) -> ConnectionTestResult:
        try:
            target = self.resolve_buffer(account_id)
            if target is None:
                raise IntegrationError("Buffer connection is not enabled")
            endpoint = self.settings.buffer_api_url.rstrip("/")
            headers = {"Authorization": f"Bearer {target.api_key}"}
            organization_response = self._client.post(
                endpoint,
                json={
                    "query": (
                        "query GetOrganizations { "
                        "account { organizations { id } } "
                        "}"
                    )
                },
                headers=headers,
            )
            organization_response.raise_for_status()
            organization_payload: Any = organization_response.json()
            if (organization_payload or {}).get("errors"):
                raise IntegrationError("Buffer organization lookup failed")
            organizations = (
                (((organization_payload or {}).get("data") or {}).get("account") or {})
                .get("organizations")
                or []
            )
            organization_ids = [
                str(item.get("id", "")).strip()
                for item in organizations
                if str(item.get("id", "")).strip()
            ]
            if not organization_ids:
                raise IntegrationError("No Buffer organization is accessible")

            accessible_channel_ids: set[str] = set()
            for organization_id in organization_ids:
                response = self._client.post(
                    endpoint,
                    json={
                        "query": (
                            "query GetChannels($organizationId: OrganizationId!) { "
                            "channels(input: { organizationId: $organizationId }) { id } "
                            "}"
                        ),
                        "variables": {"organizationId": organization_id},
                    },
                    headers=headers,
                )
                response.raise_for_status()
                payload: Any = response.json()
                if (payload or {}).get("errors"):
                    raise IntegrationError("Buffer channel lookup failed")
                channels = ((payload or {}).get("data") or {}).get("channels") or []
                accessible_channel_ids.update(
                    str(item.get("id")) for item in channels if item.get("id")
                )

            if target.channel_id not in accessible_channel_ids:
                raise IntegrationError("Configured Buffer channel is not accessible")
            return self._record_test(account_id, "buffer", success=True)
        except Exception as exc:
            error = str(exc) if isinstance(exc, IntegrationError) else "Buffer connection test failed"
            return self._record_test(
                account_id, "buffer", success=False, error=error
            )

    def test_slack(self, account_id: int) -> ConnectionTestResult:
        try:
            target = self.resolve_slack(account_id)
            if target is None:
                raise IntegrationError("Slack connection is not enabled")
            response = self._client.post(
                target.webhook_url,
                json={"text": "Startup X Agent connection test — no draft was published"},
            )
            if not response.is_success:
                if response.status_code == 404 and response.text.strip() == "no_service":
                    raise IntegrationError(
                        "Slack webhook is inactive or revoked; create a new Incoming "
                        "Webhook and replace this connection"
                    )
                raise IntegrationError(
                    f"Slack webhook returned HTTP {response.status_code}"
                )
            return self._record_test(account_id, "slack", success=True)
        except Exception as exc:
            error = str(exc) if isinstance(exc, IntegrationError) else "Slack connection test failed"
            return self._record_test(account_id, "slack", success=False, error=error)

    def disconnect(self, x_account_id: int, provider: str) -> None:
        normalized_provider = str(provider).strip().lower()
        if normalized_provider not in SUPPORTED_PROVIDERS:
            raise IntegrationError(f"Unsupported integration provider: {provider}")
        self.repository.disconnect_account_integration(x_account_id, normalized_provider)
