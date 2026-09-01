from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.config import Settings
from app.models import ContentContext, Draft, NotificationResult, StartupProfile

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def notify_for_review(
        self, draft: Draft, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult: ...

    def notify_status(self, draft: Draft, message: str) -> NotificationResult: ...


class ConsoleNotifier:
    def notify_for_review(
        self, draft: Draft, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult:
        logger.info(
            "Draft ready for review: startup=%s context=%s draft=%s text=%s",
            profile.name,
            context.name,
            draft.id,
            draft.text,
        )
        return NotificationResult(success=True, provider="console")

    def notify_status(self, draft: Draft, message: str) -> NotificationResult:
        logger.info("Draft status: draft=%s status=%s message=%s", draft.id, draft.status, message)
        return NotificationResult(success=True, provider="console")


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        base_url: str,
        client: httpx.Client | None = None,
    ):
        self.token = token
        self.chat_id = chat_id
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10.0)

    def notify_for_review(
        self, draft: Draft, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult:
        text = (
            f"X POST READY FOR REVIEW\n\n"
            f"Startup: {profile.name}\n"
            f"Context: {context.name}\n"
            f"Attempt: {draft.attempt}\n"
            f"Topic: {draft.topic}\n\n"
            f"{draft.text}\n\n"
            f"Nothing is published until Approve is pressed."
        )
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "✅ Approve", "callback_data": f"approve:{draft.id}"},
                        {"text": "❌ Reject + regenerate", "callback_data": f"reject:{draft.id}"},
                    ],
                    [
                        {
                            "text": "✏️ Open review dashboard",
                            "url": f"{self.base_url}/#draft-{draft.id}",
                        }
                    ],
                ]
            },
        }
        try:
            response = self.client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage", json=payload
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("ok", False):
                return NotificationResult(
                    success=False,
                    provider="telegram",
                    error=str(body),
                )
            return NotificationResult(success=True, provider="telegram")
        except Exception as exc:
            return NotificationResult(success=False, provider="telegram", error=str(exc))

    def notify_status(self, draft: Draft, message: str) -> NotificationResult:
        try:
            response = self.client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": f"{message}\nDraft: {draft.id}"},
            )
            response.raise_for_status()
            return NotificationResult(success=True, provider="telegram")
        except Exception as exc:
            return NotificationResult(success=False, provider="telegram", error=str(exc))


class SlackNotifier:
    def __init__(self, webhook_url: str, base_url: str, client: httpx.Client | None = None):
        self.webhook_url = webhook_url
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10.0)

    def notify_for_review(
        self, draft: Draft, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult:
        review_url = f"{self.base_url}/#draft-{draft.id}"
        payload = {
            "text": f"X post ready for review: {review_url}",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "X post ready for review"},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Startup:* {profile.name}\n*Context:* {context.name}\n"
                            f"*Attempt:* {draft.attempt}\n\n{draft.text}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Review draft"},
                            "url": review_url,
                        }
                    ],
                },
            ],
        }
        try:
            response = self.client.post(self.webhook_url, json=payload)
            response.raise_for_status()
            return NotificationResult(success=True, provider="slack")
        except Exception as exc:
            return NotificationResult(success=False, provider="slack", error=str(exc))

    def notify_status(self, draft: Draft, message: str) -> NotificationResult:
        try:
            response = self.client.post(
                self.webhook_url,
                json={"text": f"{message} — draft {draft.id}"},
            )
            response.raise_for_status()
            return NotificationResult(success=True, provider="slack")
        except Exception as exc:
            return NotificationResult(success=False, provider="slack", error=str(exc))


class NotifierManager:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    @classmethod
    def from_settings(cls, settings: Settings) -> "NotifierManager":
        notifiers: list[Notifier] = [ConsoleNotifier()]
        if settings.telegram_bot_token and settings.telegram_chat_id:
            notifiers.append(
                TelegramNotifier(
                    settings.telegram_bot_token,
                    settings.telegram_chat_id,
                    settings.base_url,
                )
            )
        if settings.slack_webhook_url:
            notifiers.append(SlackNotifier(settings.slack_webhook_url, settings.base_url))
        return cls(notifiers)

    def notify_for_review(
        self, draft: Draft, profile: StartupProfile, context: ContentContext
    ) -> list[NotificationResult]:
        results = []
        for notifier in self.notifiers:
            try:
                results.append(notifier.notify_for_review(draft, profile, context))
            except Exception as exc:
                results.append(
                    NotificationResult(
                        success=False,
                        provider=type(notifier).__name__,
                        error=str(exc),
                    )
                )
        return results

    def notify_status(self, draft: Draft, message: str) -> list[NotificationResult]:
        results = []
        for notifier in self.notifiers:
            try:
                results.append(notifier.notify_status(draft, message))
            except Exception as exc:
                results.append(
                    NotificationResult(
                        success=False,
                        provider=type(notifier).__name__,
                        error=str(exc),
                    )
                )
        return results
