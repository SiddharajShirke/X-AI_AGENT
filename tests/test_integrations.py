from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.notifiers import NotifierManager, SlackNotifier, TelegramNotifier
from app.services.publishers import DryRunPublisher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capturing_client(responses: list[dict], requests: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = responses.pop(0) if responses else {"ok": True}
        return httpx.Response(200, json=body, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _buffer_success_response(
    post_id: str = "buf123",
    external_link: str | None = "https://x.com/i/status/123",
) -> dict:
    post = {
        "id": post_id,
        "text": "A startup post about reliable AI.",
        "status": "sent",
        "externalLink": external_link,
        "channelId": "chan-abc",
        "dueAt": None,
        "shareMode": "shareNow",
    }
    return {
        "data": {
            "createPost": {
                "__typename": "PostActionSuccess",
                "post": post,
            }
        }
    }


def _buffer_settings(**overrides):
    from app.config import Settings

    base = dict(
        app_mode="demo",
        database_path=":memory:",
        scheduler_enabled=False,
        groq_api_key="",
        buffer_live_posting=True,
        buffer_api_key="test-buffer-api-key",
        buffer_channel_id="chan-abc",
        buffer_api_url="https://api.buffer.com",
        buffer_share_mode="shareNow",
    )
    base.update(overrides)
    return Settings(**base)


def _make_draft(repository) -> Any:
    return repository.create_draft(
        context_id=1,
        schedule_id=None,
        text="A startup post about reliable AI.",
        topic="reliability",
        source_summary="manual",
        status="approved",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )


# ---------------------------------------------------------------------------
# Existing notifier tests (unchanged)
# ---------------------------------------------------------------------------

def test_notifier_manager_wires_telegram_settings_in_correct_order(settings):
    configured = settings.model_copy(
        update={
            "telegram_bot_token": "token-123",
            "telegram_chat_id": "chat-456",
            "base_url": "https://review.example",
        }
    )

    manager = NotifierManager.from_settings(configured)
    telegram = next(item for item in manager.notifiers if isinstance(item, TelegramNotifier))

    assert telegram.token == "token-123"
    assert telegram.chat_id == "chat-456"
    assert telegram.base_url == "https://review.example"
    assert isinstance(telegram.client, httpx.Client)


def test_telegram_review_message_contains_explicit_approve_and_reject_actions(repository):
    requests: list[httpx.Request] = []
    client = _capturing_client([{"ok": True}], requests)
    draft = repository.create_draft(
        context_id=1,
        schedule_id=None,
        text="A specific founder observation.",
        topic="reliability",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    notifier = TelegramNotifier("token", "chat", "https://review.example", client=client)

    result = notifier.notify_for_review(
        draft,
        repository.get_profile(),
        repository.get_context(1),
    )

    assert result.success is True
    payload = json.loads(requests[0].content)
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == f"approve:{draft.id}"
    assert buttons[1]["callback_data"] == f"reject:{draft.id}"
    assert "Nothing is published until Approve is pressed" in payload["text"]


def test_slack_review_message_points_to_human_dashboard(repository):
    requests: list[httpx.Request] = []
    client = _capturing_client([{}], requests)
    draft = repository.create_draft(
        context_id=2,
        schedule_id=None,
        text="A market observation.",
        topic="market",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    notifier = SlackNotifier("https://hooks.slack.test/demo", "https://review.example", client=client)

    result = notifier.notify_for_review(
        draft,
        repository.get_profile(),
        repository.get_context(2),
    )

    assert result.success is True
    payload = json.loads(requests[0].content)
    assert payload["blocks"][-1]["elements"][0]["url"] == (
        f"https://review.example/#draft-{draft.id}"
    )


def test_dry_run_publisher_never_calls_external_provider(repository):
    """Renamed from test_dry_run_publisher_never_calls_x to be provider-neutral."""
    draft = repository.create_draft(
        context_id=3,
        schedule_id=None,
        text="A safe prototype post.",
        topic="prototype",
        source_summary="manual",
        status="approved",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )

    result = DryRunPublisher().publish(draft)

    assert result.success is True
    assert result.provider == "dry_run"
    assert result.raw_response == {"dry_run": True, "text": draft.text}


# ---------------------------------------------------------------------------
# BufferPublisher tests — all offline, no live network requests
# ---------------------------------------------------------------------------

def test_buffer_publisher_sends_post_to_configured_api_url(repository):
    """BufferPublisher must POST to the configured buffer_api_url."""
    from app.services.publishers import BufferPublisher

    requests: list[httpx.Request] = []
    settings = _buffer_settings()
    draft = _make_draft(repository)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_buffer_success_response(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = BufferPublisher(settings, client=client).publish(draft)

    assert len(requests) == 1
    assert "api.buffer.com" in str(requests[0].url)


def test_buffer_publisher_sends_authorization_bearer_header(repository):
    """BufferPublisher must send Authorization: Bearer <api_key>."""
    from app.services.publishers import BufferPublisher

    requests: list[httpx.Request] = []
    settings = _buffer_settings(buffer_api_key="test-buffer-api-key")
    draft = _make_draft(repository)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_buffer_success_response(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    BufferPublisher(settings, client=client).publish(draft)

    assert requests[0].headers["authorization"] == "Bearer test-buffer-api-key"


def test_buffer_publisher_sends_graphql_create_post_mutation(repository):
    """BufferPublisher must send a createPost GraphQL mutation."""
    from app.services.publishers import BufferPublisher

    requests: list[httpx.Request] = []
    settings = _buffer_settings()
    draft = _make_draft(repository)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_buffer_success_response(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    BufferPublisher(settings, client=client).publish(draft)

    body = json.loads(requests[0].content)
    assert "createPost" in body["query"]
    assert "input" in body["variables"]


def test_buffer_publisher_sends_correct_variables(repository):
    """BufferPublisher variables must include text, channelId, schedulingType, mode, aiAssisted, assets, source."""
    from app.services.publishers import BufferPublisher

    requests: list[httpx.Request] = []
    settings = _buffer_settings(buffer_channel_id="chan-abc", buffer_share_mode="shareNow")
    draft = _make_draft(repository)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_buffer_success_response(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    BufferPublisher(settings, client=client).publish(draft)

    body = json.loads(requests[0].content)
    inp = body["variables"]["input"]
    assert inp["text"] == draft.text
    assert inp["channelId"] == "chan-abc"
    assert inp["schedulingType"] == "automatic"
    assert inp["mode"] == "shareNow"
    assert inp["aiAssisted"] is True
    assert inp["assets"] == []
    assert inp["source"] == "startup-x-agent-prototype"


def test_buffer_publisher_parses_post_action_success(repository):
    """BufferPublisher correctly parses a PostActionSuccess response."""
    from app.services.publishers import BufferPublisher

    settings = _buffer_settings()
    draft = _make_draft(repository)
    response_body = _buffer_success_response(post_id="buf999", external_link="https://x.com/i/status/999")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = BufferPublisher(settings, client=client).publish(draft)

    assert result.success is True
    assert result.provider == "buffer"
    assert result.external_post_id == "buf999"
    assert result.post_url == "https://x.com/i/status/999"


def test_buffer_publisher_null_external_link_does_not_create_fake_url(repository):
    """When externalLink is null, post_url must be empty string, not a fabricated URL."""
    from app.services.publishers import BufferPublisher

    settings = _buffer_settings()
    draft = _make_draft(repository)
    response_body = _buffer_success_response(post_id="bufnull", external_link=None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = BufferPublisher(settings, client=client).publish(draft)

    assert result.success is True
    assert result.post_url == ""
    # Must not invent an X URL
    assert "x.com" not in (result.post_url or "")
    assert "buffer.com" not in (result.post_url or "")


def test_buffer_publisher_typed_mutation_error_returns_failure(repository):
    """A MutationError __typename must result in success=False."""
    from app.services.publishers import BufferPublisher

    settings = _buffer_settings()
    draft = _make_draft(repository)
    error_body = {
        "data": {
            "createPost": {
                "__typename": "MutationError",
                "message": "Channel not found",
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=error_body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = BufferPublisher(settings, client=client).publish(draft)

    assert result.success is False
    assert result.provider == "buffer"
    assert result.error is not None


def test_buffer_publisher_top_level_graphql_errors_return_failure(repository):
    """Top-level errors[] in the GraphQL envelope must result in success=False."""
    from app.services.publishers import BufferPublisher

    settings = _buffer_settings()
    draft = _make_draft(repository)
    error_body = {"errors": [{"message": "Unauthorized"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=error_body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = BufferPublisher(settings, client=client).publish(draft)

    assert result.success is False
    assert result.provider == "buffer"


def test_buffer_publisher_http_failure_returns_failure(repository):
    """A non-2xx HTTP response must result in success=False."""
    from app.services.publishers import BufferPublisher

    settings = _buffer_settings()
    draft = _make_draft(repository)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = BufferPublisher(settings, client=client).publish(draft)

    assert result.success is False
    assert result.provider == "buffer"


def test_publisher_manager_selects_dry_run_when_live_posting_disabled(settings, repository):
    """PublisherManager must use DryRunPublisher when buffer_live_posting=False."""
    from app.services.publishers import DryRunPublisher, PublisherManager

    mgr = PublisherManager(settings)
    assert isinstance(mgr.publisher, DryRunPublisher)


def test_publisher_manager_selects_dry_run_when_api_key_missing(repository):
    """PublisherManager must use DryRunPublisher when buffer_api_key is missing."""
    from app.config import Settings
    from app.services.publishers import DryRunPublisher, PublisherManager

    s = Settings(
        app_mode="demo",
        database_path=":memory:",
        scheduler_enabled=False,
        buffer_live_posting=True,
        buffer_api_key="",
        buffer_channel_id="chan-abc",
    )
    assert isinstance(PublisherManager(s).publisher, DryRunPublisher)


def test_publisher_manager_selects_dry_run_when_channel_id_missing(repository):
    """PublisherManager must use DryRunPublisher when buffer_channel_id is missing."""
    from app.config import Settings
    from app.services.publishers import DryRunPublisher, PublisherManager

    s = Settings(
        app_mode="demo",
        database_path=":memory:",
        scheduler_enabled=False,
        buffer_live_posting=True,
        buffer_api_key="test-buffer-api-key",
        buffer_channel_id="",
    )
    assert isinstance(PublisherManager(s).publisher, DryRunPublisher)


def test_publisher_manager_selects_buffer_publisher_with_full_credentials(repository):
    """PublisherManager must select BufferPublisher when live posting and both credentials are set."""
    from app.services.publishers import BufferPublisher, PublisherManager

    s = _buffer_settings()
    assert isinstance(PublisherManager(s).publisher, BufferPublisher)
