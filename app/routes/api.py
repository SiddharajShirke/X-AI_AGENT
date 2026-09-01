from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import get_pipeline, get_repository, require_admin
from app.repository import Repository
from app.services.pipeline import Pipeline, PipelineError

router = APIRouter(prefix="/api", tags=["api"])


class ProfileUpdate(BaseModel):
    name: str | None = None
    domain: str | None = None
    target_audience: list[str] | str | None = None
    problems: list[str] | str | None = None
    brand_voice: str | None = None
    public_info: list[str] | str | None = None
    never_reveal: list[str] | str | None = None
    content_pillars: list[str] | str | None = None
    banned_phrases: list[str] | str | None = None
    trend_keywords: list[str] | str | None = None
    competitor_accounts: list[str] | str | None = None
    rss_feeds: list[str] | str | None = None
    timezone: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    approval_timeout_minutes: int | None = Field(default=None, ge=1, le=1440)


class ContextUpdate(BaseModel):
    name: str | None = None
    purpose: str | None = None
    tone: str | None = None
    live_trends_required: bool | None = None
    instructions: str | None = None
    enabled: bool | None = None


class ScheduleUpdate(BaseModel):
    context_id: int | None = None
    time_local: str | None = None
    enabled: bool | None = None


class GenerateRequest(BaseModel):
    context_id: int | None = None
    schedule_id: int | None = None


class ReviewRequest(BaseModel):
    reviewer: str = "human"


class RejectRequest(BaseModel):
    reason: str = "other"
    notes: str = ""
    reviewer: str = "human"


class EditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    notes: str = ""
    reviewer: str = "human"
    approve: bool = False


class TrendRequest(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    summary: str = Field(min_length=2, max_length=1000)
    source: str = "manual"
    url: str = ""


@router.get("/health")
def api_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/profile", dependencies=[Depends(require_admin)])
def get_profile(repository: Repository = Depends(get_repository)):
    return repository.get_profile()


@router.put("/profile", dependencies=[Depends(require_admin)])
def update_profile(payload: ProfileUpdate, repository: Repository = Depends(get_repository)):
    updates = payload.model_dump(exclude_none=True)
    return repository.update_profile(updates)


@router.get("/contexts", dependencies=[Depends(require_admin)])
def list_contexts(repository: Repository = Depends(get_repository)):
    return repository.list_contexts()


@router.put("/contexts/{context_id}", dependencies=[Depends(require_admin)])
def update_context(
    context_id: int,
    payload: ContextUpdate,
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.update_context(context_id, payload.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/schedules", dependencies=[Depends(require_admin)])
def list_schedules(repository: Repository = Depends(get_repository)):
    return repository.list_schedules()


@router.put("/schedules/{schedule_id}", dependencies=[Depends(require_admin)])
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.update_schedule(schedule_id, payload.model_dump(exclude_none=True))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/drafts", dependencies=[Depends(require_admin)])
def list_drafts(repository: Repository = Depends(get_repository)):
    return repository.list_drafts()


@router.post(
    "/drafts/generate",
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_201_CREATED,
)
def generate_draft(
    payload: GenerateRequest,
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        return pipeline.generate_draft(
            context_id=payload.context_id,
            schedule_id=payload.schedule_id,
        )
    except (PipelineError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/approve", dependencies=[Depends(require_admin)])
def approve_draft(
    draft_id: str,
    payload: ReviewRequest,
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        return pipeline.approve(draft_id, reviewer=payload.reviewer)
    except (PipelineError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/reject", dependencies=[Depends(require_admin)])
def reject_draft(
    draft_id: str,
    payload: RejectRequest,
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        original = pipeline.repository.get_draft(draft_id)
        replacement = pipeline.reject_and_regenerate(
            draft_id,
            reason=payload.reason,
            notes=payload.notes,
            reviewer=payload.reviewer,
        )
        return {
            "original": pipeline.repository.get_draft(original.id),
            "replacement": replacement,
        }
    except (PipelineError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/edit", dependencies=[Depends(require_admin)])
def edit_draft(
    draft_id: str,
    payload: EditRequest,
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        return pipeline.edit(
            draft_id,
            payload.text,
            reviewer=payload.reviewer,
            notes=payload.notes,
            approve=payload.approve,
        )
    except (PipelineError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/trends", dependencies=[Depends(require_admin)])
def list_trends(repository: Repository = Depends(get_repository)):
    return repository.list_trends()


@router.post("/trends", dependencies=[Depends(require_admin)], status_code=201)
def add_trend(payload: TrendRequest, repository: Repository = Depends(get_repository)):
    return repository.add_trend(
        title=payload.title,
        summary=payload.summary,
        source=payload.source,
        url=payload.url,
    )


@router.get("/preferences", dependencies=[Depends(require_admin)])
def list_preferences(repository: Repository = Depends(get_repository)):
    return repository.list_preferences()


@router.get("/events", dependencies=[Depends(require_admin)])
def list_events(repository: Repository = Depends(get_repository)):
    return repository.list_events()


@router.get("/state", dependencies=[Depends(require_admin)])
def get_state(repository: Repository = Depends(get_repository)) -> dict[str, Any]:
    return {
        "profile": repository.get_profile(),
        "contexts": repository.list_contexts(),
        "schedules": repository.list_schedules(),
        "drafts": repository.list_drafts(),
        "trends": repository.list_trends(),
        "preferences": repository.list_preferences(),
        "feedback": repository.list_feedback(),
        "config_versions": repository.list_config_versions(),
        "events": repository.list_events(),
    }
