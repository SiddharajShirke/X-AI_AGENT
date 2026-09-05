from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StartupProfile(Model):
    id: int = 1
    x_account_id: int
    name: str
    domain: str
    target_audience: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    brand_voice: str
    public_info: list[str] = Field(default_factory=list)
    never_reveal: list[str] = Field(default_factory=list)
    content_pillars: list[str] = Field(default_factory=list)
    banned_phrases: list[str] = Field(default_factory=list)
    trend_keywords: list[str] = Field(default_factory=list)
    competitor_accounts: list[str] = Field(default_factory=list)
    rss_feeds: list[str] = Field(default_factory=list)
    max_attempts: int = 3
    approval_timeout_minutes: int = 45
    updated_at: str


class ContentContext(Model):
    id: int
    x_account_id: int
    name: str
    purpose: str
    tone: str
    live_trends_required: bool
    instructions: str
    enabled: bool = True


class ScheduleSlot(Model):
    id: int
    x_account_id: int
    context_id: int
    slot_number: int
    time_local: str
    enabled: bool = True
    last_run_date: str = ""


class TrendItem(Model):
    id: int | None = None
    x_account_id: int
    title: str
    summary: str
    source: str
    url: str = ""
    score: float = 1.0
    active: bool = True
    collected_at: str = ""


class Draft(Model):
    id: str
    x_account_id: int
    context_id: int
    schedule_id: int | None = None
    text: str
    topic: str
    source_summary: str
    status: str
    safety_status: str
    similarity_score: float
    attempt: int
    parent_draft_id: str | None = None
    config_version: int
    expires_at: str | None = None
    generator_provider: str
    prompt_snapshot: str = ""
    rejection_reason: str = ""
    reviewer_notes: str = ""
    reviewer: str = ""
    created_at: str
    approved_at: str | None = None
    published_at: str | None = None
    publisher_provider: str = ""
    external_post_id: str = ""
    post_url: str = ""
    error: str = ""


class FeedbackRecord(Model):
    id: int
    x_account_id: int
    draft_id: str
    decision: str
    reason: str
    notes: str
    learned_rule: str
    reviewer: str
    created_at: str


class LearnedPreference(Model):
    id: int
    x_account_id: int
    rule: str
    weight: float
    source_feedback_id: int | None = None
    active: bool = True
    created_at: str
    updated_at: str


class SafetyResult(Model):
    safe: bool
    status: str
    reasons: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)


class SimilarityResult(Model):
    unique: bool
    score: float
    most_similar_text: str = ""


class GenerationResult(Model):
    text: str
    topic: str
    source_summary: str
    provider: str
    rationale: str
    prompt_snapshot: str = ""


class PublishResult(Model):
    success: bool
    provider: str
    external_post_id: str = ""
    post_url: str = ""
    error: str = ""
    raw_response: dict[str, Any] = Field(default_factory=dict)


class NotificationResult(Model):
    success: bool
    provider: str
    error: str = ""


class XAccount(Model):
    id: int
    name: str
    handle: str
    enabled: bool
    live_posting_enabled: bool
    timezone: str
    created_at: str
    updated_at: str


class IntegrationConnection(Model):
    id: int
    provider: str
    label: str
    credentials_configured: bool
    created_at: str
    updated_at: str


class AccountIntegration(Model):
    x_account_id: int
    provider: str
    connection_id: int
    target_id: str
    enabled: bool
    last_test_success: bool | None
    last_test_error: str
    last_tested_at: str | None


class SlackActionJob(Model):
    id: int
    idempotency_key: str
    connection_id: int
    x_account_id: int
    draft_id: str
    action_id: str
    expected_live: bool
    reviewer: str
    status: str
    result_draft_id: str | None = None
    safe_error: str = ""
    created_at: str
    claimed_at: str | None = None
    completed_at: str | None = None


class PublishAttempt(Model):
    id: int
    x_account_id: int
    draft_id: str
    attempt_number: int
    status: str
    origin: str
    reviewer: str
    error: str
    created_at: str
    completed_at: str | None


class ConnectionTestResult(Model):
    success: bool
    provider: str
    error: str = ""


class SchedulerTickResult(Model):
    generated: list[Draft] = Field(default_factory=list)
    expired_replacements: list[Draft] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
