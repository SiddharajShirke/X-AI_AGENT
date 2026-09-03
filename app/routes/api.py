from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.dependencies import get_pipeline, get_repository, require_admin
from app.repository import Repository
from app.services.integrations import IntegrationError
from app.services.pipeline import Pipeline, PipelineError


router = APIRouter(prefix="/api", tags=["api"])


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    handle: str = Field(min_length=1, max_length=50)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    copy_from_id: int | None = None


class AccountUpdate(BaseModel):
    name: str | None = None
    handle: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    live_posting_enabled: bool | None = None


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
    reviewer: str | None = None


class RejectRequest(BaseModel):
    reason: str = "other"
    notes: str = ""
    reviewer: str | None = None


class EditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    notes: str = ""
    reviewer: str | None = None
    approve: bool = False


class TrendRequest(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    summary: str = Field(min_length=2, max_length=1000)
    source: str = "manual"
    url: str = ""


class ConnectionSave(BaseModel):
    provider: Literal["buffer", "slack"]
    label: str = Field(min_length=1, max_length=120)
    credentials: dict[str, str]
    connection_id: int | None = None


class IntegrationBinding(BaseModel):
    provider: Literal["buffer", "slack"]
    connection_id: int
    target_id: str = ""
    enabled: bool = True


def _raise_api_error(exc: Exception):
    code = 404 if isinstance(exc, KeyError) else 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/health")
def api_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/accounts")
def list_accounts(
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    return repository.list_accounts()


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.create_account(
            payload.name,
            payload.handle,
            payload.timezone,
            copy_from_id=payload.copy_from_id,
        )
    except (KeyError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}")
def get_account(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.get_account(x_account_id)
    except KeyError as exc:
        _raise_api_error(exc)


@router.put("/accounts/{x_account_id}")
def update_account(
    x_account_id: int,
    payload: AccountUpdate,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.update_account(
            x_account_id, payload.model_dump(exclude_none=True)
        )
    except (KeyError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}/profile")
def get_profile(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.get_profile(x_account_id)
    except (KeyError, RuntimeError) as exc:
        _raise_api_error(exc)


@router.put("/accounts/{x_account_id}/profile")
def update_profile(
    x_account_id: int,
    payload: ProfileUpdate,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    updates = payload.model_dump(exclude_none=True)
    timezone = updates.pop("timezone", None)
    try:
        repository.get_account(x_account_id)
        if timezone is not None:
            repository.update_account(x_account_id, {"timezone": timezone})
        return repository.update_profile(x_account_id, updates)
    except (KeyError, RuntimeError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}/contexts")
def list_contexts(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        repository.get_account(x_account_id)
        return repository.list_contexts(x_account_id)
    except KeyError as exc:
        _raise_api_error(exc)


@router.put("/accounts/{x_account_id}/contexts/{context_id}")
def update_context(
    x_account_id: int,
    context_id: int,
    payload: ContextUpdate,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.update_context(
            x_account_id, context_id, payload.model_dump(exclude_none=True)
        )
    except (KeyError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}/schedules")
def list_schedules(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        repository.get_account(x_account_id)
        return repository.list_schedules(x_account_id)
    except KeyError as exc:
        _raise_api_error(exc)


@router.put("/accounts/{x_account_id}/schedules/{schedule_id}")
def update_schedule(
    x_account_id: int,
    schedule_id: int,
    payload: ScheduleUpdate,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        return repository.update_schedule(
            x_account_id, schedule_id, payload.model_dump(exclude_none=True)
        )
    except (KeyError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}/drafts")
def list_drafts(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        repository.get_account(x_account_id)
        return repository.list_drafts(x_account_id)
    except KeyError as exc:
        _raise_api_error(exc)


@router.post(
    "/accounts/{x_account_id}/drafts/generate",
    status_code=status.HTTP_201_CREATED,
)
def generate_draft(
    x_account_id: int,
    payload: GenerateRequest,
    _: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        return pipeline.generate_draft(
            x_account_id,
            context_id=payload.context_id,
            schedule_id=payload.schedule_id,
        )
    except (PipelineError, KeyError, ValueError) as exc:
        _raise_api_error(exc)


@router.post("/accounts/{x_account_id}/drafts/{draft_id}/approve")
def approve_draft(
    x_account_id: int,
    draft_id: str,
    payload: ReviewRequest,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        return pipeline.approve(
            x_account_id, draft_id, reviewer=reviewer, origin="api"
        )
    except (PipelineError, KeyError) as exc:
        _raise_api_error(exc)


@router.post("/accounts/{x_account_id}/drafts/{draft_id}/retry")
def retry_draft(
    x_account_id: int,
    draft_id: str,
    payload: ReviewRequest,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        return pipeline.retry_publish(
            x_account_id, draft_id, reviewer=reviewer, origin="api"
        )
    except (PipelineError, KeyError) as exc:
        _raise_api_error(exc)


@router.post("/accounts/{x_account_id}/drafts/{draft_id}/reject")
def reject_draft(
    x_account_id: int,
    draft_id: str,
    payload: RejectRequest,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        pipeline.repository.get_draft(x_account_id, draft_id)
        replacement = pipeline.reject_and_regenerate(
            x_account_id,
            draft_id,
            reason=payload.reason,
            notes=payload.notes,
            reviewer=reviewer,
            origin="api",
        )
        return {
            "original": pipeline.repository.get_draft(x_account_id, draft_id),
            "replacement": replacement,
        }
    except (PipelineError, KeyError) as exc:
        _raise_api_error(exc)


@router.post("/accounts/{x_account_id}/drafts/{draft_id}/edit")
def edit_draft(
    x_account_id: int,
    draft_id: str,
    payload: EditRequest,
    reviewer: str = Depends(require_admin),
    pipeline: Pipeline = Depends(get_pipeline),
):
    try:
        return pipeline.edit(
            x_account_id,
            draft_id,
            payload.text,
            reviewer=reviewer,
            notes=payload.notes,
            approve=payload.approve,
        )
    except (PipelineError, KeyError) as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}/trends")
def list_trends(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        repository.get_account(x_account_id)
        return repository.list_trends(x_account_id)
    except KeyError as exc:
        _raise_api_error(exc)


@router.post("/accounts/{x_account_id}/trends", status_code=201)
def add_trend(
    x_account_id: int,
    payload: TrendRequest,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        repository.get_account(x_account_id)
        return repository.add_trend(
            x_account_id,
            title=payload.title,
            summary=payload.summary,
            source=payload.source,
            url=payload.url,
        )
    except KeyError as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}/preferences")
def list_preferences(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    return repository.list_preferences(x_account_id)


@router.get("/accounts/{x_account_id}/events")
def list_events(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    return repository.list_events(x_account_id)


@router.get("/accounts/{x_account_id}/state")
def get_state(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        return {
            "account": repository.get_account(x_account_id),
            "profile": repository.get_profile(x_account_id),
            "contexts": repository.list_contexts(x_account_id),
            "schedules": repository.list_schedules(x_account_id),
            "drafts": repository.list_drafts(x_account_id),
            "trends": repository.list_trends(x_account_id),
            "preferences": repository.list_preferences(x_account_id),
            "feedback": repository.list_feedback(x_account_id),
            "config_versions": repository.list_config_versions(x_account_id),
            "events": repository.list_events(x_account_id),
            "integrations": {
                provider: repository.get_account_integration(x_account_id, provider)
                for provider in ("buffer", "slack")
            },
        }
    except (KeyError, RuntimeError) as exc:
        _raise_api_error(exc)


@router.get("/connections")
def list_connections(
    provider: str | None = None,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    return repository.list_integration_connections(provider)


@router.post("/connections", status_code=201)
def save_connection(
    payload: ConnectionSave,
    request: Request,
    _: str = Depends(require_admin),
):
    try:
        return request.app.state.services.integrations.save_connection(
            payload.provider,
            payload.label,
            payload.credentials,
            connection_id=payload.connection_id,
        )
    except (IntegrationError, KeyError, ValueError) as exc:
        _raise_api_error(exc)


@router.get("/accounts/{x_account_id}/integrations")
def list_account_integrations(
    x_account_id: int,
    _: str = Depends(require_admin),
    repository: Repository = Depends(get_repository),
):
    try:
        repository.get_account(x_account_id)
        return {
            provider: repository.get_account_integration(x_account_id, provider)
            for provider in ("buffer", "slack")
        }
    except KeyError as exc:
        _raise_api_error(exc)


@router.put("/accounts/{x_account_id}/integrations/{provider}")
def bind_account_integration(
    x_account_id: int,
    provider: Literal["buffer", "slack"],
    payload: IntegrationBinding,
    request: Request,
    _: str = Depends(require_admin),
):
    if payload.provider != provider:
        raise HTTPException(status_code=400, detail="Provider path and payload differ")
    try:
        return request.app.state.services.integrations.bind(
            x_account_id,
            provider,
            payload.connection_id,
            payload.target_id,
            enabled=payload.enabled,
        )
    except (IntegrationError, KeyError, ValueError) as exc:
        _raise_api_error(exc)


@router.post("/accounts/{x_account_id}/integrations/{provider}/test")
def test_account_integration(
    x_account_id: int,
    provider: Literal["buffer", "slack"],
    request: Request,
    _: str = Depends(require_admin),
):
    service = request.app.state.services.integrations
    return (
        service.test_buffer(x_account_id)
        if provider == "buffer"
        else service.test_slack(x_account_id)
    )


@router.delete("/accounts/{x_account_id}/integrations/{provider}")
def disconnect_account_integration(
    x_account_id: int,
    provider: Literal["buffer", "slack"],
    request: Request,
    _: str = Depends(require_admin),
):
    try:
        request.app.state.services.integrations.disconnect(x_account_id, provider)
        return {"disconnected": True, "provider": provider}
    except (IntegrationError, KeyError) as exc:
        _raise_api_error(exc)
