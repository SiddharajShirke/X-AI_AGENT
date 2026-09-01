from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from app.services.pipeline import PipelineError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["telegram"])


@router.post("/integrations/telegram/webhook")
def telegram_webhook(
    request: Request,
    payload: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    settings = request.app.state.settings
    if settings.telegram_webhook_secret and (
        x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")

    callback = payload.get("callback_query") or {}
    callback_id = callback.get("id", "")
    data = callback.get("data", "")
    if not data or ":" not in data:
        return {"ok": True, "ignored": True}
    action, draft_id = data.split(":", 1)
    pipeline = request.app.state.services.pipeline
    message = "Action ignored"
    try:
        if action == "approve":
            draft = pipeline.approve(draft_id, reviewer="telegram-human")
            message = f"Draft {draft.status}."
        elif action in {"reject", "regenerate"}:
            replacement = pipeline.reject_and_regenerate(
                draft_id,
                reason="other",
                notes="Rejected from Telegram; generate a materially different post.",
                reviewer="telegram-human",
            )
            message = "Rejected and regenerated." if replacement else "Rejected; guidance required."
    except (PipelineError, KeyError) as exc:
        message = str(exc)

    if callback_id and settings.telegram_bot_token:
        try:
            httpx.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": message[:180]},
                timeout=5.0,
            )
        except Exception:
            logger.exception("Could not acknowledge Telegram callback")
    return {"ok": True, "message": message}
