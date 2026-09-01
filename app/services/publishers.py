from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.models import Draft, PublishResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GraphQL mutation used to create a post via the Buffer API.
# Variables carry all dynamic data so no user text is interpolated into
# the query string itself.
# ---------------------------------------------------------------------------
_CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename

    ... on PostActionSuccess {
      post {
        id
        text
        status
        externalLink
        channelId
        dueAt
        shareMode
      }
    }

    ... on MutationError {
      message
    }
  }
}
""".strip()


class DryRunPublisher:
    """Default offline publisher. Never contacts any external service."""

    def publish(self, draft: Draft) -> PublishResult:
        short_id = draft.id.split("-")[0]
        return PublishResult(
            success=True,
            provider="dry_run",
            external_post_id=f"demo-{short_id}",
            post_url=f"https://example.com/demo-x/{short_id}",
            raw_response={"dry_run": True, "text": draft.text},
        )


class BufferPublisher:
    """Live publisher that sends approved posts to Buffer via the GraphQL API.

    Buffer then handles final publication to the connected X channel.

    An injectable httpx.Client allows tests to supply a mock transport
    without making real network requests.
    """

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
    ):
        self.settings = settings
        self._client = client or httpx.Client(
            timeout=settings.buffer_timeout_seconds,
        )

    def publish(self, draft: Draft) -> PublishResult:
        variables = {
            "input": {
                "text": draft.text,
                "channelId": self.settings.buffer_channel_id,
                "schedulingType": "automatic",
                "mode": self.settings.buffer_share_mode,
                "aiAssisted": True,
                "assets": [],
                "source": "startup-x-agent-prototype",
            }
        }
        payload = {
            "query": _CREATE_POST_MUTATION,
            "variables": variables,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.buffer_api_key}",
            "Content-Type": "application/json",
        }
        url = self.settings.buffer_api_url.rstrip("/")

        try:
            response = self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Buffer HTTP error: %s", exc.response.status_code)
            return PublishResult(
                success=False,
                provider="buffer",
                error=f"HTTP {exc.response.status_code} from Buffer API",
            )
        except Exception as exc:
            logger.warning("Buffer network error: %s", type(exc).__name__)
            return PublishResult(
                success=False,
                provider="buffer",
                error="Network error reaching Buffer API",
            )

        # Parse GraphQL envelope
        try:
            body = response.json()
        except Exception:
            return PublishResult(
                success=False,
                provider="buffer",
                error="Buffer returned invalid JSON",
            )

        # Check for top-level GraphQL errors
        if body.get("errors"):
            messages = "; ".join(e.get("message", "unknown") for e in body["errors"])
            return PublishResult(
                success=False,
                provider="buffer",
                error=f"GraphQL errors: {messages}",
            )

        # Navigate into the typed response
        create_post = (body.get("data") or {}).get("createPost")
        if create_post is None:
            return PublishResult(
                success=False,
                provider="buffer",
                error="Buffer response missing data.createPost",
            )

        typename = create_post.get("__typename")

        if typename == "MutationError":
            message = create_post.get("message") or "Buffer refused the post"
            return PublishResult(
                success=False,
                provider="buffer",
                error=f"Buffer MutationError: {message}",
            )

        if typename != "PostActionSuccess":
            return PublishResult(
                success=False,
                provider="buffer",
                error=f"Unexpected Buffer response type: {typename!r}",
            )

        post = create_post.get("post")
        if not post:
            return PublishResult(
                success=False,
                provider="buffer",
                error="Buffer PostActionSuccess contained no post object",
            )

        post_id = post.get("id")
        if not post_id:
            return PublishResult(
                success=False,
                provider="buffer",
                error="Buffer post object missing id",
            )

        # externalLink may be null while Buffer is still processing.
        # Do not fabricate a URL.
        external_link: str = post.get("externalLink") or ""

        # Sanitize the raw response before storing: exclude auth headers,
        # keep only the post object which contains no secrets.
        sanitized = {
            "provider": "buffer",
            "post": {
                "id": str(post_id),
                "status": post.get("status"),
                "shareMode": post.get("shareMode"),
                "channelId": post.get("channelId"),
            },
        }

        return PublishResult(
            success=True,
            provider="buffer",
            external_post_id=str(post_id),
            post_url=external_link,
            raw_response=sanitized,
        )


class PublisherManager:
    """Selects the appropriate publisher based on runtime settings.

    Selection logic:
    - buffer_live_posting=True AND has_buffer_credentials → BufferPublisher
    - All other cases → DryRunPublisher (safe default)

    There is no direct X publishing path in this module. Publishing is handled
    exclusively via the Buffer API when live mode is enabled.
    TWITTER_BEARER_TOKEN is unrelated and used only for trend research
    in app/services/trends.py.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.buffer_live_posting and settings.has_buffer_credentials:
            self.publisher: DryRunPublisher | BufferPublisher = BufferPublisher(settings)
        else:
            self.publisher = DryRunPublisher()

    def publish(self, draft: Draft) -> PublishResult:
        return self.publisher.publish(draft)
