from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from app.services.integrations import IntegrationError


router = APIRouter(tags=["slack"])


@router.post("/integrations/slack/{connection_id}/actions")
async def slack_action(connection_id: int, request: Request):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    integrations = request.app.state.services.integrations

    try:
        target = integrations.get_slack_connection(connection_id)
        integrations.verify_slack_signature(
            target.signing_secret, timestamp, signature, body
        )
    except IntegrationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        encoded_payload = parse_qs(
            body.decode("utf-8"), strict_parsing=True
        )["payload"][0]
        payload = json.loads(encoded_payload)
        action = payload["actions"][0]
        value = json.loads(action["value"])
        action_id = str(action["action_id"])
        x_account_id = int(value["x_account_id"])
        draft_id = str(value["draft_id"])
        user = payload["user"]
        reviewer = f"slack:{user['id']}:{user.get('name', '')}"
    except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Malformed Slack action payload") from exc

    binding = request.app.state.repository.get_account_integration(
        x_account_id, "slack"
    )
    if (
        binding is None
        or not binding.enabled
        or binding.connection_id != connection_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Slack connection is not bound to this X account",
        )

    repository = request.app.state.repository
    try:
        repository.get_draft(x_account_id, draft_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="Draft does not belong to this X account"
        ) from exc

    if action_id not in {"approve_draft", "reject_draft"}:
        raise HTTPException(status_code=400, detail="Unsupported Slack action")
    expected_live = value.get("expected_live")
    if not isinstance(expected_live, bool):
        raise HTTPException(
            status_code=400,
            detail="Approval mode is missing; request a fresh Slack review",
        )

    idempotency_key = hashlib.sha256(
        f"{connection_id}:".encode() + body
    ).hexdigest()
    job, created = repository.enqueue_slack_action(
        idempotency_key=idempotency_key,
        connection_id=connection_id,
        x_account_id=x_account_id,
        draft_id=draft_id,
        action_id=action_id,
        expected_live=expected_live,
        reviewer=reviewer,
    )
    repository.log_event(
        x_account_id,
        "slack_action_received" if created else "slack_action_duplicate",
        draft_id,
        {"job_id": job.id, "action_id": action_id},
    )
    request.app.state.services.slack_actions.wake()

    return {
        "response_type": "ephemeral",
        "replace_original": False,
        "text": (
            "Approval received and queued for processing"
            if action_id == "approve_draft"
            else "Rejection received and queued for processing"
        ),
        "job_id": job.id,
    }
