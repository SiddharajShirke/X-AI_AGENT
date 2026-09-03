from __future__ import annotations

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from app.services.integrations import IntegrationError
from app.services.pipeline import PipelineError


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

    pipeline = request.app.state.services.pipeline
    try:
        if action_id == "approve_draft":
            result = pipeline.approve(
                x_account_id,
                draft_id,
                reviewer=reviewer,
                origin="slack",
            )
        elif action_id == "reject_draft":
            result = pipeline.reject_and_regenerate(
                x_account_id,
                draft_id,
                reason="other",
                notes="Rejected from Slack",
                reviewer=reviewer,
                origin="slack",
            )
        else:
            raise HTTPException(status_code=400, detail="Unsupported Slack action")
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "response_type": "ephemeral",
        "text": "Action recorded",
        "draft_id": result.id if result is not None else draft_id,
    }
