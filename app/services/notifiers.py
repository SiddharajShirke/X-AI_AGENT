from __future__ import annotations

import json
import logging
from typing import Protocol

import httpx

from app.config import Settings
from app.models import ContentContext, Draft, NotificationResult, StartupProfile, XAccount
from app.services.integrations import IntegrationError, IntegrationService

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


class Notifier(Protocol):
    def notify_for_review(
        self, draft: Draft, account: XAccount, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult: ...

    def notify_status(self, draft: Draft, account: XAccount, message: str) -> NotificationResult: ...


class ConsoleNotifier:
    def notify_for_review(
        self, draft: Draft, account: XAccount, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult:
        logger.info(
            "Draft ready for review: account=%s startup=%s context=%s draft=%s text=%s",
            account.handle,
            profile.name,
            context.name,
            draft.id,
            draft.text,
        )
        return NotificationResult(success=True, provider="console")

    def notify_status(self, draft: Draft, account: XAccount, message: str) -> NotificationResult:
        logger.info(
            "Draft status: account=%s draft=%s status=%s message=%s",
            account.handle, draft.id, draft.status, message,
        )
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
        self, draft: Draft, account: XAccount, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult:
        text = (
            f"X POST READY FOR REVIEW\n\n"
            f"Account: {profile.name} (@{account.handle})\n"
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
                        {
                            "text": "✅ Approve",
                            "callback_data": f"approve:{account.id}:{draft.id}",
                        },
                        {
                            "text": "❌ Reject + regenerate",
                            "callback_data": f"reject:{account.id}:{draft.id}",
                        },
                    ],
                    [
                        {
                            "text": "✏️ Open review dashboard",
                            "url": f"{self.base_url}/accounts/{account.id}#draft-{draft.id}",
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
            logger.warning("Telegram review notification failed: %s", type(exc).__name__)
            return NotificationResult(
                success=False, provider="telegram", error="Telegram notification failed"
            )

    def notify_status(self, draft: Draft, account: XAccount, message: str) -> NotificationResult:
        try:
            response = self.client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": f"@{account.handle}: {message}\nDraft: {draft.id}",
                },
            )
            response.raise_for_status()
            return NotificationResult(success=True, provider="telegram")
        except Exception as exc:
            logger.warning("Telegram status notification failed: %s", type(exc).__name__)
            return NotificationResult(
                success=False, provider="telegram", error="Telegram notification failed"
            )


class SlackNotifier:
    def __init__(
        self,
        webhook_url: str,
        base_url: str,
        client: httpx.Client | None = None,
        *,
        live_posting: bool = False,
    ):
        self.webhook_url = webhook_url
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=10.0)
        self.live_posting = live_posting

    def notify_for_review(
        self, draft: Draft, account: XAccount, profile: StartupProfile, context: ContentContext
    ) -> NotificationResult:
        review_url = f"{self.base_url}/accounts/{account.id}#draft-{draft.id}"
        mode = "LIVE via Buffer" if self.live_posting else "DRY-RUN"
        approve_label = (
            f"Approve & publish to @{account.handle}"
            if self.live_posting
            else "Approve in dry-run"
        )
        action_value = json.dumps(
            {
                "x_account_id": account.id,
                "draft_id": draft.id,
                "expected_live": self.live_posting,
            },
            separators=(",", ":"),
        )
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
                            f"*Account:* {profile.name} (@{account.handle})\n"
                            f"*Context:* {context.name}\n"
                            f"*Topic:* {draft.topic}\n"
                            f"*Attempt:* {draft.attempt}\n\n{draft.text}"
                            f"\n\n*Mode:* {mode} · *Destination:* @{account.handle}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": approve_label},
                            "style": "primary",
                            "action_id": "approve_draft",
                            "value": action_value,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject + regenerate"},
                            "style": "danger",
                            "action_id": "reject_draft",
                            "value": action_value,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Edit in dashboard"},
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
            logger.warning("Slack review notification failed: %s", type(exc).__name__)
            return NotificationResult(
                success=False, provider="slack", error="Slack notification failed"
            )

    def notify_status(self, draft: Draft, account: XAccount, message: str) -> NotificationResult:
        try:
            response = self.client.post(
                self.webhook_url,
                json={"text": f"@{account.handle}: {message} — draft {draft.id}"},
            )
            response.raise_for_status()
            return NotificationResult(success=True, provider="slack")
        except Exception as exc:
            logger.warning("Slack status notification failed: %s", type(exc).__name__)
            return NotificationResult(
                success=False, provider="slack", error="Slack notification failed"
            )


class NotifierManager:
    def __init__(
        self,
        notifiers: list[Notifier],
        *,
        integrations: IntegrationService | None = None,
        base_url: str = "",
        slack_client: httpx.Client | None = None,
        buffer_live_posting: bool = False,
    ):
        self.notifiers = notifiers
        self.integrations = integrations
        self.base_url = base_url
        self.slack_client = slack_client
        self.buffer_live_posting = buffer_live_posting

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        integrations: IntegrationService | None = None,
    ) -> "NotifierManager":
        notifiers: list[Notifier] = [ConsoleNotifier()]
        if settings.telegram_bot_token and settings.telegram_chat_id:
            notifiers.append(
                TelegramNotifier(
                    settings.telegram_bot_token,
                    settings.telegram_chat_id,
                    settings.base_url,
                )
            )
        return cls(
            notifiers,
            integrations=integrations,
            base_url=settings.base_url,
            buffer_live_posting=settings.buffer_live_posting,
        )

    def _account_notifiers(self, account: XAccount) -> list[Notifier]:
        resolved = list(self.notifiers)
        if self.integrations is None:
            return resolved
        try:
            target = self.integrations.resolve_slack(account.id)
        except IntegrationError as exc:
            logger.warning("Slack connection unavailable for account %s: %s", account.id, exc)
            return resolved
        if target is not None:
            resolved.append(
                SlackNotifier(
                    target.webhook_url,
                    self.base_url,
                    client=self.slack_client,
                    live_posting=(
                        self.buffer_live_posting and account.live_posting_enabled
                    ),
                )
            )
        return resolved

    def notify_for_review(
        self, draft: Draft, account: XAccount, profile: StartupProfile, context: ContentContext
    ) -> list[NotificationResult]:
        results = []
        for notifier in self._account_notifiers(account):
            try:
                results.append(notifier.notify_for_review(draft, account, profile, context))
            except Exception as exc:
                results.append(
                    NotificationResult(
                        success=False,
                        provider=type(notifier).__name__,
                        error=str(exc),
                    )
                )
        return results

    def notify_status(self, draft: Draft, account: XAccount, message: str) -> list[NotificationResult]:
        results = []
        for notifier in self._account_notifiers(account):
            try:
                results.append(notifier.notify_status(draft, account, message))
            except Exception as exc:
                results.append(
                    NotificationResult(
                        success=False,
                        provider=type(notifier).__name__,
                        error=str(exc),
                    )
                )
        return results
