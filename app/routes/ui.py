from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_pipeline, get_repository, require_admin
from app.repository import Repository
from app.services.pipeline import Pipeline

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _redirect(message: str = "Saved", anchor: str = "top") -> RedirectResponse:
    return RedirectResponse(
        url=f"/?message={quote(message)}#{anchor}",
        status_code=303,
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    profile = repository.get_profile()
    contexts = repository.list_contexts()
    context_map = {context.id: context for context in contexts}
    schedules = repository.list_schedules()
    drafts = repository.list_drafts(limit=80)
    pending = [draft for draft in drafts if draft.status == "pending"]
    published = [draft for draft in drafts if draft.status == "published"]
    rejected = [draft for draft in drafts if draft.status in {"rejected", "expired", "needs_guidance"}]
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "profile": profile,
            "contexts": contexts,
            "context_map": context_map,
            "schedules": schedules,
            "drafts": drafts,
            "pending": pending,
            "published": published,
            "rejected": rejected,
            "trends": repository.list_trends(limit=20),
            "preferences": repository.list_preferences(limit=20),
            "feedback": repository.list_feedback(limit=20),
            "versions": repository.list_config_versions(limit=8),
            "events": repository.list_events(limit=20),
            "settings": request.app.state.settings,
            "message": request.query_params.get("message", ""),
        },
    )


@router.post("/ui/profile")
def save_profile(
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
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
    timezone: str = Form("Asia/Kolkata"),
    max_attempts: int = Form(3),
    approval_timeout_minutes: int = Form(45),
):
    repository.update_profile(
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
            "timezone": timezone,
            "max_attempts": max_attempts,
            "approval_timeout_minutes": approval_timeout_minutes,
        }
    )
    return _redirect("Startup profile updated and versioned.", "configuration")


@router.post("/ui/contexts/{context_id}")
def save_context(
    context_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    name: str = Form(...),
    purpose: str = Form(...),
    tone: str = Form(...),
    instructions: str = Form(...),
    live_trends_required: str | None = Form(default=None),
    enabled: str | None = Form(default=None),
):
    repository.update_context(
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
    return _redirect(f"Context {context_id} updated.", "slots")


@router.post("/ui/schedules/{schedule_id}")
def save_schedule(
    schedule_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    context_id: int = Form(...),
    time_local: str = Form(...),
    enabled: str | None = Form(default=None),
):
    repository.update_schedule(
        schedule_id,
        {"context_id": context_id, "time_local": time_local, "enabled": bool(enabled)},
    )
    return _redirect(f"Schedule slot {schedule_id} updated.", "slots")


@router.post("/ui/trends")
def add_manual_trend(
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
    title: str = Form(...),
    summary: str = Form(...),
    source: str = Form("manual"),
    url: str = Form(""),
):
    repository.add_trend(title=title, summary=summary, source=source, url=url)
    return _redirect("Manual trend signal added.", "learning")


@router.post("/ui/generate")
def generate_ui(
    _: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    context_id: int = Form(...),
):
    draft = pipeline.generate_draft(context_id=context_id)
    return _redirect(f"Draft {draft.id[:8]} generated for review.", "review")


@router.post("/ui/drafts/{draft_id}/approve")
def approve_ui(
    draft_id: str,
    _: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    reviewer: str = Form("dashboard-human"),
):
    draft = pipeline.approve(draft_id, reviewer=reviewer)
    return _redirect(f"Draft is now {draft.status}.", "review")


@router.post("/ui/drafts/{draft_id}/reject")
def reject_ui(
    draft_id: str,
    _: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    reason: str = Form("other"),
    notes: str = Form(""),
    reviewer: str = Form("dashboard-human"),
):
    replacement = pipeline.reject_and_regenerate(
        draft_id,
        reason=reason,
        notes=notes,
        reviewer=reviewer,
    )
    message = "Rejected and a different draft was generated." if replacement else "Rejected; attempt limit reached and human guidance is required."
    return _redirect(message, "review")


@router.post("/ui/drafts/{draft_id}/edit")
def edit_ui(
    draft_id: str,
    _: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
    text: str = Form(...),
    notes: str = Form(""),
    reviewer: str = Form("dashboard-human"),
    approve: str | None = Form(default=None),
):
    draft = pipeline.edit(
        draft_id,
        text,
        reviewer=reviewer,
        notes=notes,
        approve=bool(approve),
    )
    return _redirect(f"Human edit result: {draft.status}.", "review")
