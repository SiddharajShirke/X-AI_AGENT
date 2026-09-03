from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.notifiers import NotifierManager, SlackNotifier, TelegramNotifier
from app.services.integrations import BufferTarget
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


def _buffer_target(
    *, api_key: str = "test-buffer-api-key", channel_id: str = "chan-abc"
) -> BufferTarget:
    return BufferTarget(api_key=api_key, channel_id=channel_id)


def _make_draft(repository) -> Any:
    account = repository.list_accounts()[0]
    context = repository.list_contexts(account.id)[0]
    return repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="A startup post about reliable AI.",
        topic="reliability",
        source_summary="manual",
        status="approved",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(account.id),
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
    account = repository.list_accounts()[0]
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
    notifier = TelegramNotifier("token", "chat", "https://review.example", client=client)

    result = notifier.notify_for_review(
        draft,
        account,
        repository.get_profile(account.id),
        context,
    )

    assert result.success is True
    payload = json.loads(requests[0].content)
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == f"approve:{account.id}:{draft.id}"
    assert buttons[1]["callback_data"] == f"reject:{account.id}:{draft.id}"
    assert "Nothing is published until Approve is pressed" in payload["text"]


def test_slack_review_message_points_to_human_dashboard(repository):
    requests: list[httpx.Request] = []
    client = _capturing_client([{}], requests)
    account = repository.list_accounts()[0]
    context = repository.list_contexts(account.id)[1]
    draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="A market observation.",
        topic="market",
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
    notifier = SlackNotifier("https://hooks.slack.test/demo", "https://review.example", client=client)

    result = notifier.notify_for_review(
        draft,
        account,
        repository.get_profile(account.id),
        context,
    )

    assert result.success is True
    payload = json.loads(requests[0].content)
    dashboard_button = next(
        item for item in payload["blocks"][-1]["elements"] if item.get("url")
    )
    assert dashboard_button["url"] == (
        f"https://review.example/accounts/{account.id}#draft-{draft.id}"
    )


def test_dry_run_publisher_never_calls_external_provider(repository):
    """Renamed from test_dry_run_publisher_never_calls_x to be provider-neutral."""
    account = repository.list_accounts()[0]
    context = repository.list_contexts(account.id)[2]
    draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="A safe prototype post.",
        topic="prototype",
        source_summary="manual",
        status="approved",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(account.id),
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
    result = BufferPublisher(settings, _buffer_target(), client=client).publish(draft)

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
    BufferPublisher(
        settings, _buffer_target(api_key="account-specific-key"), client=client
    ).publish(draft)

    assert requests[0].headers["authorization"] == "Bearer account-specific-key"


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
    BufferPublisher(settings, _buffer_target(), client=client).publish(draft)

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
    BufferPublisher(
        settings, _buffer_target(channel_id="account-channel"), client=client
    ).publish(draft)

    body = json.loads(requests[0].content)
    inp = body["variables"]["input"]
    assert inp["text"] == draft.text
    assert inp["channelId"] == "account-channel"
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
    result = BufferPublisher(settings, _buffer_target(), client=client).publish(draft)

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
    result = BufferPublisher(settings, _buffer_target(), client=client).publish(draft)

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
    result = BufferPublisher(settings, _buffer_target(), client=client).publish(draft)

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
    result = BufferPublisher(settings, _buffer_target(), client=client).publish(draft)

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
    result = BufferPublisher(settings, _buffer_target(), client=client).publish(draft)

    assert result.success is False
    assert result.provider == "buffer"


def test_publisher_manager_selects_dry_run_when_live_posting_disabled(settings, repository):
    """The global switch keeps every account in dry-run mode."""
    from app.services.publishers import PublisherManager

    account = repository.list_accounts()[0]
    account = repository.update_account(account.id, {"live_posting_enabled": True})
    mgr = PublisherManager(settings)
    result = mgr.publish(_make_draft(repository), account, _buffer_target())

    assert result.success is True
    assert result.provider == "dry_run"


def test_publisher_manager_fails_when_live_account_has_no_target(repository):
    """Missing live credentials are visible failures, never silent dry runs."""
    from app.services.publishers import PublisherManager

    account = repository.list_accounts()[0]
    account = repository.update_account(account.id, {"live_posting_enabled": True})
    result = PublisherManager(_buffer_settings()).publish(
        _make_draft(repository), account, None
    )

    assert result.success is False
    assert result.provider == "buffer"
    assert "missing" in result.error.lower()


def test_publisher_manager_uses_per_call_buffer_target(repository):
    """Live publication uses the account target supplied for this call."""
    from app.services.publishers import PublisherManager

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_buffer_success_response(), request=request)

    account = repository.list_accounts()[0]
    account = repository.update_account(account.id, {"live_posting_enabled": True})
    manager = PublisherManager(
        _buffer_settings(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = manager.publish(
        _make_draft(repository),
        account,
        _buffer_target(api_key="dynamic-key", channel_id="dynamic-channel"),
    )

    assert result.success is True
    assert requests[0].headers["authorization"] == "Bearer dynamic-key"
    assert json.loads(requests[0].content)["variables"]["input"]["channelId"] == "dynamic-channel"
