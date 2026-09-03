from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_pipeline, get_repository, require_admin
from app.repository import Repository
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


def _dashboard_context(request: Request, repository: Repository, x_account_id: int, reviewer: str):
    account = repository.get_account(x_account_id)
    profile = repository.get_profile(x_account_id)
    contexts = repository.list_contexts(x_account_id)
    context_map = {context.id: context for context in contexts}
    schedules = repository.list_schedules(x_account_id)
    drafts = repository.list_drafts(x_account_id, limit=80)
    return {
        "account": account,
        "accounts": repository.list_accounts(),
        "profile": profile,
        "contexts": contexts,
        "context_map": context_map,
        "schedules": schedules,
        "drafts": drafts,
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
        "settings": request.app.state.settings,
        "csrf_token": request.app.state.csrf.issue(reviewer),
        "message": request.query_params.get("message", ""),
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    reviewer: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    accounts = repository.list_accounts()
    if not accounts:
        raise HTTPException(status_code=404, detail="No X accounts configured")
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_dashboard_context(request, repository, accounts[0].id, reviewer),
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
        request=request, name="dashboard.html", context=context
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
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        draft = pipeline.approve(
            x_account_id, draft_id, reviewer=reviewer, origin="dashboard"
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
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        draft = pipeline.retry_publish(
            x_account_id, draft_id, reviewer=reviewer, origin="dashboard"
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
):
    _verify_csrf(request, csrf_token, reviewer)
    try:
        draft = pipeline.edit(
            x_account_id,
            draft_id,
            text,
            reviewer=reviewer,
            notes=notes,
            approve=bool(approve),
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
