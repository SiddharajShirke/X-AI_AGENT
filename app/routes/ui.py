from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_pipeline, get_repository, require_admin
from app.models import AccountIntegration, Draft, SlackActionJob
from app.repository import Repository
from app.safe_display import validated_public_url
from app.services.integrations import IntegrationError
from app.services.pipeline import Pipeline, PipelineError


router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _redirect(
    x_account_id: int, message: str = "Saved", anchor: str = "top"
) -> RedirectResponse:
    return RedirectResponse(
        url=f"/accounts/{x_account_id}?message={quote(message)}#{anchor}",
        status_code=303,
    )


def _verify_csrf(request: Request, token: str, reviewer: str) -> None:
    if not request.app.state.csrf.verify(token, reviewer):
        raise HTTPException(status_code=403, detail="Invalid or expired CSRF token")


def _form_error(exc: Exception) -> None:
    status_code = 404 if isinstance(exc, KeyError) else 400
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


_SLACK_ACTION_LABELS = {
    "approve_draft": "Approve",
    "reject_draft": "Reject",
}
_DRAFT_STATUS_LABELS = {
    "pending": "Pending",
    "publishing": "Publishing",
    "published": "Published",
    "failed": "Failed",
    "blocked": "Blocked",
    "rejecting": "Rejecting",
    "rejected": "Rejected",
    "editing": "Editing",
    "expiring": "Expiring",
    "expired": "Expired",
    "needs_guidance": "Needs guidance",
}
_FAILED_ACTION_GUIDANCE = {
    "Publication was interrupted; operator reconciliation is required": (
        "Reconcile the interrupted publication with Buffer/X before any new "
        "explicit action."
    ),
    "Rejection was interrupted; operator reconciliation is required": (
        "Review the original and child drafts before taking another explicit action."
    ),
    "Publication mode changed; request a fresh Slack review": (
        "Generate a fresh Slack review message and confirm its displayed mode."
    ),
    "Slack action arrived after the approval deadline; request a fresh review": (
        "Use the current replacement draft or generate a fresh review."
    ),
    "X account is paused": "Re-enable this account before requesting a fresh review.",
    "Draft is no longer pending": (
        "Review this account's current draft state before taking another action."
    ),
    "Unexpected Slack action processing failure": (
        "Inspect this account's safe audit state before taking another explicit action."
    ),
    "Slack action could not be processed": (
        "Review this account's current state and request a fresh Slack review."
    ),
}


def _terminal_guidance(action: SlackActionJob, result: Draft | None) -> str:
    if action.status == "failed":
        return _FAILED_ACTION_GUIDANCE.get(
            action.safe_error,
            "Review this account's safe action state before taking another explicit action.",
        )
    if result is None:
        return "Review this account's current draft state before taking another action."
    return {
        "published": "Publication completed; open the validated post link when available.",
        "failed": "Resolve the Buffer connection, then use the explicit Retry control.",
        "blocked": "Review the blocked draft and this account's safety configuration.",
        "needs_guidance": "Add operator guidance before generating another replacement.",
        "pending": "Review the replacement draft before making a new explicit decision.",
        "publishing": (
            "Reconcile the interrupted publication with Buffer/X before any new "
            "explicit action."
        ),
        "expired": "Use the current replacement draft or generate a fresh review.",
        "rejected": "Review this account's replacement history before continuing.",
    }.get(
        result.status,
        "Review this account's current draft state before taking another action.",
    )


def _slack_inbound_health(
    action: SlackActionJob | None, result: Draft | None = None
) -> dict[str, str]:
    if action is None:
        return {
            "state": "Not verified",
            "details": (
                "No inbound Slack action has reached this connection for this X account yet. "
                "Check the Slack app settings, then use an action from a Slack review."
            ),
        }
    state = {
        "completed": "Completed",
        "failed": "Failed",
        "pending": "Received",
        "processing": "Received",
    }.get(action.status, "Received")
    action_label = _SLACK_ACTION_LABELS.get(action.action_id, "Unknown")
    result_id = action.result_draft_id or action.draft_id
    result_status = (
        _DRAFT_STATUS_LABELS.get(result.status, "Unavailable")
        if result is not None
        else "Unavailable"
    )
    timestamp = (
        action.completed_at
        if state in {"Completed", "Failed"} and action.completed_at
        else action.created_at
    )
    if state == "Received":
        guidance = "The authenticated action is awaiting processing; refresh shortly."
    else:
        guidance = _terminal_guidance(action, result)
    return {
        "state": state,
        "details": (
            f"{state} {timestamp}. Action: {action_label}. "
            f"Source draft: {action.draft_id}. "
            f"Result draft: {result_id} ({result_status}). {guidance}"
        ),
    }


def _slack_outbound_health(binding: AccountIntegration | None) -> dict[str, str]:
    if binding is None or not binding.enabled:
        return {
            "state": "Not connected",
            "details": "Connect this Slack connection to send an outbound webhook test.",
        }
    if binding.last_test_success is True:
        return {
            "state": "Passed",
            "details": f"Last tested {binding.last_tested_at}.",
        }
    if binding.last_test_success is False:
        return {
            "state": "Failed",
            "details": (
                f"Last tested {binding.last_tested_at}. "
                "Review the Slack webhook configuration and test again."
            ),
        }
    return {
        "state": "Not tested",
        "details": "Run an outbound webhook test after connecting this account.",
    }


def _slack_connection_context(
    request: Request,
    repository: Repository,
    x_account_id: int,
    slack_binding: AccountIntegration | None,
) -> dict[str, object]:
    base_url = request.app.state.settings.base_url.rstrip("/")
    slack_connections = repository.list_integration_connections("slack")
    slack_callback_urls = {
        item.id: f"{base_url}/integrations/slack/{item.id}/actions"
        for item in slack_connections
    }
    latest_slack_actions = {
        item.id: repository.latest_slack_action(x_account_id, item.id)
        for item in slack_connections
    }
    latest_slack_results: dict[int, Draft | None] = {}
    for connection_id, action in latest_slack_actions.items():
        result = None
        if action is not None:
            result_id = action.result_draft_id or action.draft_id
            try:
                result = repository.get_draft(x_account_id, result_id)
            except KeyError:
                result = None
        latest_slack_results[connection_id] = result
    return {
        "slack_connections": slack_connections,
        "slack_callback_urls": slack_callback_urls,
        "latest_slack_actions": latest_slack_actions,
        "slack_inbound_health": {
            connection_id: _slack_inbound_health(
                action, latest_slack_results[connection_id]
            )
            for connection_id, action in latest_slack_actions.items()
        },
        "slack_outbound_healths": {
            item.id: _slack_outbound_health(
                slack_binding
                if slack_binding is not None
                and slack_binding.connection_id == item.id
                else None
            )
            for item in slack_connections
        },
    }


def _dashboard_context(request: Request, repository: Repository, x_account_id: int, reviewer: str):
    account = repository.get_account(x_account_id)
    profile = repository.get_profile(x_account_id)
    contexts = repository.list_contexts(x_account_id)
    context_map = {context.id: context for context in contexts}
    schedules = repository.list_schedules(x_account_id)
    drafts = repository.list_drafts(x_account_id, limit=80)
    buffer_binding = repository.get_account_integration(x_account_id, "buffer")
    slack_binding = repository.get_account_integration(x_account_id, "slack")
    buffer_status = _connection_status(request, x_account_id, "buffer")
    slack_status = _connection_status(request, x_account_id, "slack")
    slack_connection_context = _slack_connection_context(
        request, repository, x_account_id, slack_binding
    )
    return {
        "account": account,
        "accounts": repository.list_accounts(),
        "profile": profile,
        "contexts": contexts,
        "context_map": context_map,
        "schedules": schedules,
        "drafts": drafts,
        "safe_post_urls": {
            draft.id: validated_public_url(draft.post_url) for draft in drafts
        },
        "pending": [draft for draft in drafts if draft.status == "pending"],
        "published": [draft for draft in drafts if draft.status == "published"],
        "rejected": [
            draft
            for draft in drafts
            if draft.status in {"rejected", "expired", "needs_guidance"}
        ],
        "trends": repository.list_trends(x_account_id, limit=20),
        "preferences": repository.list_preferences(x_account_id, limit=20),
        "feedback": repository.list_feedback(x_account_id, limit=20),
        "versions": repository.list_config_versions(x_account_id, limit=8),
        "events": repository.list_events(x_account_id, limit=20),
        "buffer_binding": buffer_binding,
        "slack_binding": slack_binding,
        "buffer_status": buffer_status,
        "slack_status": slack_status,
        "buffer_connections": repository.list_integration_connections("buffer"),
        "settings": request.app.state.settings,
        "csrf_token": request.app.state.csrf.issue(reviewer),
        "message": request.query_params.get("message", ""),
        **slack_connection_context,
    }


def _connection_status(request: Request, x_account_id: int, provider: str) -> str:
    binding = request.app.state.repository.get_account_integration(
        x_account_id, provider
    )
    if binding is None or not binding.enabled:
        return "not connected"
    try:
        if provider == "buffer":
            target = request.app.state.services.integrations.resolve_buffer(x_account_id)
        else:
            target = request.app.state.services.integrations.resolve_slack(x_account_id)
    except IntegrationError:
        return "locked"
    return "connected" if target is not None else "not connected"


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    accounts = repository.list_accounts()
    if not accounts:
        raise HTTPException(status_code=404, detail="No X accounts configured")
    summaries = []
    for account in accounts:
        drafts = repository.list_drafts(account.id, limit=100)
        enabled_schedules = [
            slot for slot in repository.list_schedules(account.id) if slot.enabled
        ]
        summaries.append(
            {
                "account": account,
                "profile": repository.get_profile(account.id),
                "pending_count": sum(draft.status == "pending" for draft in drafts),
                "published_count": sum(draft.status == "published" for draft in drafts),
                "next_schedule": enabled_schedules[0] if enabled_schedules else None,
                "slack_status": _connection_status(request, account.id, "slack"),
                "buffer_status": _connection_status(request, account.id, "buffer"),
                "effective_live": (
                    request.app.state.settings.buffer_live_posting
                    and account.live_posting_enabled
                ),
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="accounts.html",
        context={
            "accounts": accounts,
            "summaries": summaries,
            "settings": request.app.state.settings,
            "csrf_token": request.app.state.csrf.issue(reviewer),
            "message": request.query_params.get("message", ""),
        },
    )


@router.get("/accounts/{x_account_id}", response_class=HTMLResponse)
def account_dashboard(
    x_account_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        context = _dashboard_context(request, repository, x_account_id, reviewer)
    except (KeyError, RuntimeError) as exc:
        _form_error(exc)
    return templates.TemplateResponse(
        request=request, name="account_dashboard.html", context=context
    )


@router.get("/accounts/{x_account_id}/setup", response_class=HTMLResponse)
def account_setup(
    x_account_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        context = _dashboard_context(request, repository, x_account_id, reviewer)
    except (KeyError, RuntimeError) as exc:
        _form_error(exc)
    return templates.TemplateResponse(
        request=request, name="account_setup.html", context=context
    )


@router.get("/connections", response_class=HTMLResponse)
def connections_dashboard(
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    accounts = repository.list_accounts()
    requested = request.query_params.get("account_id")
    return_account_id = int(requested) if requested and requested.isdigit() else accounts[0].id
    slack_binding = repository.get_account_integration(return_account_id, "slack")
    slack_connection_context = _slack_connection_context(
        request, repository, return_account_id, slack_binding
    )
    return templates.TemplateResponse(
        request=request,
        name="connections.html",
        context={
            "accounts": accounts,
            "connections": repository.list_integration_connections(),
            "return_account_id": return_account_id,
            "slack_binding": slack_binding,
            "settings": request.app.state.settings,
            "csrf_token": request.app.state.csrf.issue(reviewer),
            "message": request.query_params.get("message", ""),
            **slack_connection_context,
        },
    )


@router.post("/ui/accounts")
def create_account_ui(
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    csrf_token: str = Form(""),
    name: str = Form(...),
    handle: str = Form(...),
    timezone: str = Form("UTC"),
    copy_from_id: int | None = Form(default=None),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        account = repository.create_account(
            name, handle, timezone, copy_from_id=copy_from_id
        )
    except (KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(account.id, "X account added.")


@router.post("/ui/accounts/{x_account_id}")
def save_account_ui(
    x_account_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    csrf_token: str = Form(""),
    name: str = Form(...),
    handle: str = Form(...),
    timezone: str = Form(...),
    enabled: str | None = Form(default=None),
    live_posting_enabled: str | None = Form(default=None),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        repository.update_account(
            x_account_id,
            {
                "name": name,
                "handle": handle,
                "timezone": timezone,
                "enabled": bool(enabled),
                "live_posting_enabled": bool(live_posting_enabled),
            },
        )
    except (KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, "X account updated.", "account")


@router.post("/ui/accounts/{x_account_id}/profile")
def save_profile(
    x_account_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    csrf_token: str = Form(""),
    name: str = Form(...),
    domain: str = Form(...),
    target_audience: str = Form(""),
    problems: str = Form(""),
    brand_voice: str = Form(...),
    public_info: str = Form(""),
    never_reveal: str = Form(""),
    content_pillars: str = Form(""),
    banned_phrases: str = Form(""),
    trend_keywords: str = Form(""),
    competitor_accounts: str = Form(""),
    rss_feeds: str = Form(""),
    max_attempts: int = Form(3),
    approval_timeout_minutes: int = Form(45),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        repository.update_profile(
            x_account_id,
            {
                "name": name,
                "domain": domain,
                "target_audience": target_audience,
                "problems": problems,
                "brand_voice": brand_voice,
                "public_info": public_info,
                "never_reveal": never_reveal,
                "content_pillars": content_pillars,
                "banned_phrases": banned_phrases,
                "trend_keywords": trend_keywords,
                "competitor_accounts": competitor_accounts,
                "rss_feeds": rss_feeds,
                "max_attempts": max_attempts,
                "approval_timeout_minutes": approval_timeout_minutes,
            },
        )
    except (KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, "Startup profile updated and versioned.", "configuration")


@router.post("/ui/accounts/{x_account_id}/contexts/{context_id}")
def save_context(
    x_account_id: int,
    context_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    csrf_token: str = Form(""),
    name: str = Form(...),
    purpose: str = Form(...),
    tone: str = Form(...),
    instructions: str = Form(...),
    live_trends_required: str | None = Form(default=None),
    enabled: str | None = Form(default=None),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        repository.update_context(
            x_account_id,
            context_id,
            {
                "name": name,
                "purpose": purpose,
                "tone": tone,
                "instructions": instructions,
                "live_trends_required": bool(live_trends_required),
                "enabled": bool(enabled),
            },
        )
    except (KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"Context {context_id} updated.", "slots")


@router.post("/ui/accounts/{x_account_id}/schedules/{schedule_id}")
def save_schedule(
    x_account_id: int,
    schedule_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    csrf_token: str = Form(""),
    context_id: int = Form(...),
    time_local: str = Form(...),
    enabled: str | None = Form(default=None),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        repository.update_schedule(
            x_account_id,
            schedule_id,
            {
                "context_id": context_id,
                "time_local": time_local,
                "enabled": bool(enabled),
            },
        )
    except (KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"Schedule slot {schedule_id} updated.", "slots")


@router.post("/ui/accounts/{x_account_id}/trends")
def add_manual_trend(
    x_account_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    csrf_token: str = Form(""),
    title: str = Form(...),
    summary: str = Form(...),
    source: str = Form("manual"),
    url: str = Form(""),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        repository.get_account(x_account_id)
        repository.add_trend(
            x_account_id, title=title, summary=summary, source=source, url=url
        )
    except KeyError as exc:
        _form_error(exc)
    return _redirect(x_account_id, "Manual trend signal added.", "learning")


@router.post("/ui/accounts/{x_account_id}/generate")
def generate_ui(
    x_account_id: int,
    request: Request,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    csrf_token: str = Form(""),
    context_id: int = Form(...),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        draft = pipeline.generate_draft(x_account_id, context_id=context_id)
    except (PipelineError, KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"Draft {draft.id[:8]} generated for review.", "review")


@router.post("/ui/accounts/{x_account_id}/drafts/{draft_id}/approve")
def approve_ui(
    x_account_id: int,
    draft_id: str,
    request: Request,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    csrf_token: str = Form(""),
    expected_live: bool | None = Form(default=None),
):
    _verify_csrf(request, csrf_token, reviewer)
    if expected_live is None:
        raise HTTPException(
            status_code=400, detail="Approval mode is missing; refresh and confirm again"
        )
    try:
        draft = pipeline.approve(
            x_account_id,
            draft_id,
            reviewer=reviewer,
            origin="dashboard",
            expected_live_posting=expected_live,
        )
    except (PipelineError, KeyError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"Draft is now {draft.status}.", "review")


@router.post("/ui/accounts/{x_account_id}/drafts/{draft_id}/retry")
def retry_ui(
    x_account_id: int,
    draft_id: str,
    request: Request,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    csrf_token: str = Form(""),
    expected_live: bool | None = Form(default=None),
):
    _verify_csrf(request, csrf_token, reviewer)
    if expected_live is None:
        raise HTTPException(
            status_code=400, detail="Approval mode is missing; refresh and confirm again"
        )
    try:
        draft = pipeline.retry_publish(
            x_account_id,
            draft_id,
            reviewer=reviewer,
            origin="dashboard",
            expected_live_posting=expected_live,
        )
    except (PipelineError, KeyError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"Publish retry is now {draft.status}.", "review")


@router.post("/ui/accounts/{x_account_id}/drafts/{draft_id}/reject")
def reject_ui(
    x_account_id: int,
    draft_id: str,
    request: Request,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    csrf_token: str = Form(""),
    reason: str = Form("other"),
    notes: str = Form(""),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        replacement = pipeline.reject_and_regenerate(
            x_account_id,
            draft_id,
            reason=reason,
            notes=notes,
            reviewer=reviewer,
            origin="dashboard",
        )
    except (PipelineError, KeyError) as exc:
        _form_error(exc)
    message = (
        "Rejected and a different draft was generated."
        if replacement
        else "Rejected; attempt limit reached and human guidance is required."
    )
    return _redirect(x_account_id, message, "review")


@router.post("/ui/accounts/{x_account_id}/drafts/{draft_id}/edit")
def edit_ui(
    x_account_id: int,
    draft_id: str,
    request: Request,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    csrf_token: str = Form(""),
    text: str = Form(...),
    notes: str = Form(""),
    approve: str | None = Form(default=None),
    expected_live: bool | None = Form(default=None),
):
    _verify_csrf(request, csrf_token, reviewer)
    if approve and expected_live is None:
        raise HTTPException(
            status_code=400, detail="Approval mode is missing; refresh and confirm again"
        )
    try:
        draft = pipeline.edit(
            x_account_id,
            draft_id,
            text,
            reviewer=reviewer,
            notes=notes,
            approve=bool(approve),
            expected_live_posting=expected_live,
        )
    except (PipelineError, KeyError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"Human edit result: {draft.status}.", "review")


@router.post("/ui/connections")
def save_connection_ui(
    request: Request,
    reviewer: str = Depends(require_admin),
    csrf_token: str = Form(""),
    provider: str = Form(...),
    label: str = Form(...),
    connection_id: int | None = Form(default=None),
    api_key: str = Form(""),
    webhook_url: str = Form(""),
    signing_secret: str = Form(""),
    return_account_id: int = Form(...),
):
    _verify_csrf(request, csrf_token, reviewer)
    credentials = (
        {"api_key": api_key}
        if provider == "buffer"
        else {"webhook_url": webhook_url, "signing_secret": signing_secret}
    )
    try:
        request.app.state.services.integrations.save_connection(
            provider, label, credentials, connection_id=connection_id
        )
    except (IntegrationError, KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(return_account_id, f"{provider.title()} connection saved.", "connections")


@router.post("/ui/accounts/{x_account_id}/integrations/{provider}")
def bind_integration_ui(
    x_account_id: int,
    provider: str,
    request: Request,
    reviewer: str = Depends(require_admin),
    csrf_token: str = Form(""),
    connection_id: int = Form(...),
    target_id: str = Form(""),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        request.app.state.services.integrations.bind(
            x_account_id, provider, connection_id, target_id
        )
    except (IntegrationError, KeyError, ValueError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"{provider.title()} connected.", "connections")


@router.post("/ui/accounts/{x_account_id}/integrations/{provider}/test")
def test_integration_ui(
    x_account_id: int,
    provider: str,
    request: Request,
    reviewer: str = Depends(require_admin),
    csrf_token: str = Form(""),
):
    _verify_csrf(request, csrf_token, reviewer)
    service = request.app.state.services.integrations
    if provider not in {"buffer", "slack"}:
        raise HTTPException(status_code=400, detail="Unsupported provider")
    result = service.test_buffer(x_account_id) if provider == "buffer" else service.test_slack(x_account_id)
    message = f"{provider.title()} test passed." if result.success else f"{provider.title()} test failed: {result.error}"
    return _redirect(x_account_id, message, "connections")


@router.post("/ui/accounts/{x_account_id}/integrations/{provider}/disconnect")
def disconnect_integration_ui(
    x_account_id: int,
    provider: str,
    request: Request,
    reviewer: str = Depends(require_admin),
    csrf_token: str = Form(""),
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        request.app.state.services.integrations.disconnect(x_account_id, provider)
    except (IntegrationError, KeyError) as exc:
        _form_error(exc)
    return _redirect(x_account_id, f"{provider.title()} disconnected.", "connections")
