from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from app.db import Database, utc_now_iso
from app.models import (
    ContentContext,
    Draft,
    FeedbackRecord,
    LearnedPreference,
    ScheduleSlot,
    StartupProfile,
    TrendItem,
)


_LIST_COLUMNS = {
    "target_audience": "target_audience_json",
    "problems": "problems_json",
    "public_info": "public_info_json",
    "never_reveal": "never_reveal_json",
    "content_pillars": "content_pillars_json",
    "banned_phrases": "banned_phrases_json",
    "trend_keywords": "trend_keywords_json",
    "competitor_accounts": "competitor_accounts_json",
    "rss_feeds": "rss_feeds_json",
}


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                loaded = json.loads(text)
                if isinstance(loaded, list):
                    return [str(item).strip() for item in loaded if str(item).strip()]
            except json.JSONDecodeError:
                pass
        chunks = text.replace(",", "\n").splitlines()
        return [item.strip() for item in chunks if item.strip()]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _profile_from_row(row: Any) -> StartupProfile:
    return StartupProfile(
        id=row["id"],
        name=row["name"],
        domain=row["domain"],
        target_audience=json.loads(row["target_audience_json"]),
        problems=json.loads(row["problems_json"]),
        brand_voice=row["brand_voice"],
        public_info=json.loads(row["public_info_json"]),
        never_reveal=json.loads(row["never_reveal_json"]),
        content_pillars=json.loads(row["content_pillars_json"]),
        banned_phrases=json.loads(row["banned_phrases_json"]),
        trend_keywords=json.loads(row["trend_keywords_json"]),
        competitor_accounts=json.loads(row["competitor_accounts_json"]),
        rss_feeds=json.loads(row["rss_feeds_json"]),
        timezone=row["timezone"],
        max_attempts=row["max_attempts"],
        approval_timeout_minutes=row["approval_timeout_minutes"],
        updated_at=row["updated_at"],
    )


def _context_from_row(row: Any) -> ContentContext:
    return ContentContext(
        id=row["id"],
        name=row["name"],
        purpose=row["purpose"],
        tone=row["tone"],
        live_trends_required=bool(row["live_trends_required"]),
        instructions=row["instructions"],
        enabled=bool(row["enabled"]),
    )


def _schedule_from_row(row: Any) -> ScheduleSlot:
    return ScheduleSlot(
        id=row["id"],
        context_id=row["context_id"],
        slot_number=row["slot_number"],
        time_local=row["time_local"],
        enabled=bool(row["enabled"]),
        last_run_date=row["last_run_date"],
    )


def _draft_from_row(row: Any) -> Draft:
    return Draft(**dict(row))


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def get_profile(self) -> StartupProfile:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM startup_profile WHERE id = 1").fetchone()
        if row is None:
            raise RuntimeError("Startup profile is not initialized")
        return _profile_from_row(row)

    def update_profile(self, updates: dict[str, Any]) -> StartupProfile:
        current = self.get_profile().model_dump()
        current.update(updates)
        for field in _LIST_COLUMNS:
            current[field] = _coerce_list(current.get(field))
        current["max_attempts"] = max(1, min(int(current.get("max_attempts", 3)), 10))
        current["approval_timeout_minutes"] = max(
            1, min(int(current.get("approval_timeout_minutes", 45)), 1440)
        )
        now = utc_now_iso()
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE startup_profile SET
                    name = ?, domain = ?, target_audience_json = ?, problems_json = ?,
                    brand_voice = ?, public_info_json = ?, never_reveal_json = ?,
                    content_pillars_json = ?, banned_phrases_json = ?, trend_keywords_json = ?,
                    competitor_accounts_json = ?, rss_feeds_json = ?, timezone = ?,
                    max_attempts = ?, approval_timeout_minutes = ?, updated_at = ?
                WHERE id = 1
                """,
                (
                    str(current["name"]).strip(),
                    str(current["domain"]).strip(),
                    json.dumps(current["target_audience"]),
                    json.dumps(current["problems"]),
                    str(current["brand_voice"]).strip(),
                    json.dumps(current["public_info"]),
                    json.dumps(current["never_reveal"]),
                    json.dumps(current["content_pillars"]),
                    json.dumps(current["banned_phrases"]),
                    json.dumps(current["trend_keywords"]),
                    json.dumps(current["competitor_accounts"]),
                    json.dumps(current["rss_feeds"]),
                    str(current["timezone"]).strip(),
                    current["max_attempts"],
                    current["approval_timeout_minutes"],
                    now,
                ),
            )
            version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM config_versions").fetchone()[0]
            row = conn.execute("SELECT * FROM startup_profile WHERE id = 1").fetchone()
            conn.execute(
                "INSERT INTO config_versions (version, snapshot_json, created_at) VALUES (?, ?, ?)",
                (version, json.dumps(dict(row), sort_keys=True), now),
            )
        return self.get_profile()

    def current_config_version(self) -> int:
        with self.database.connection() as conn:
            return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM config_versions").fetchone()[0])

    def list_config_versions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT version, snapshot_json, created_at FROM config_versions ORDER BY version DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_contexts(self, enabled_only: bool = False) -> list[ContentContext]:
        query = "SELECT * FROM content_contexts"
        params: tuple[Any, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_context_from_row(row) for row in rows]

    def get_context(self, context_id: int) -> ContentContext:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM content_contexts WHERE id = ?", (context_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown content context: {context_id}")
        return _context_from_row(row)

    def update_context(self, context_id: int, updates: dict[str, Any]) -> ContentContext:
        current = self.get_context(context_id).model_dump()
        current.update(updates)
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE content_contexts
                SET name = ?, purpose = ?, tone = ?, live_trends_required = ?, instructions = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    current["name"],
                    current["purpose"],
                    current["tone"],
                    int(bool(current["live_trends_required"])),
                    current["instructions"],
                    int(bool(current["enabled"])),
                    context_id,
                ),
            )
        return self.get_context(context_id)

    def list_schedules(self) -> list[ScheduleSlot]:
        with self.database.connection() as conn:
            rows = conn.execute("SELECT * FROM schedule_slots ORDER BY slot_number").fetchall()
        return [_schedule_from_row(row) for row in rows]

    def get_schedule(self, schedule_id: int) -> ScheduleSlot:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM schedule_slots WHERE id = ?", (schedule_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown schedule slot: {schedule_id}")
        return _schedule_from_row(row)

    def update_schedule(self, schedule_id: int, updates: dict[str, Any]) -> ScheduleSlot:
        current = self.get_schedule(schedule_id).model_dump()
        current.update(updates)
        time_local = str(current["time_local"])
        datetime.strptime(time_local, "%H:%M")
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE schedule_slots
                SET context_id = ?, time_local = ?, enabled = ?, last_run_date = ?
                WHERE id = ?
                """,
                (
                    int(current["context_id"]),
                    time_local,
                    int(bool(current["enabled"])),
                    str(current.get("last_run_date", "")),
                    schedule_id,
                ),
            )
        return self.get_schedule(schedule_id)

    def mark_schedule_run(self, schedule_id: int, local_date: str) -> None:
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE schedule_slots SET last_run_date = ? WHERE id = ?",
                (local_date, schedule_id),
            )

    def add_trend(
        self,
        title: str,
        summary: str,
        source: str = "manual",
        url: str = "",
        score: float = 1.0,
    ) -> TrendItem:
        collected_at = utc_now_iso()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trends (title, summary, source, url, score, active, collected_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (title, summary, source, url, score, collected_at),
            )
            trend_id = cursor.lastrowid
        return TrendItem(
            id=trend_id,
            title=title,
            summary=summary,
            source=source,
            url=url,
            score=score,
            active=True,
            collected_at=collected_at,
        )

    def list_trends(self, limit: int = 20, active_only: bool = True) -> list[TrendItem]:
        query = "SELECT * FROM trends"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY score DESC, id DESC LIMIT ?"
        with self.database.connection() as conn:
            rows = conn.execute(query, (limit,)).fetchall()
        return [TrendItem(**{**dict(row), "active": bool(row["active"])}) for row in rows]

    def create_draft(
        self,
        *,
        context_id: int,
        schedule_id: int | None,
        text: str,
        topic: str,
        source_summary: str,
        status: str,
        safety_status: str,
        similarity_score: float,
        attempt: int,
        parent_draft_id: str | None,
        config_version: int,
        expires_at: str | None,
        generator_provider: str,
        prompt_snapshot: str = "",
    ) -> Draft:
        draft_id = str(uuid.uuid4())
        created_at = utc_now_iso()
        with self.database.connection() as conn:
            conn.execute(
                """
                INSERT INTO drafts (
                    id, context_id, schedule_id, text, topic, source_summary, status,
                    safety_status, similarity_score, attempt, parent_draft_id, config_version,
                    expires_at, generator_provider, prompt_snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    context_id,
                    schedule_id,
                    text,
                    topic,
                    source_summary,
                    status,
                    safety_status,
                    similarity_score,
                    attempt,
                    parent_draft_id,
                    config_version,
                    expires_at,
                    generator_provider,
                    prompt_snapshot,
                    created_at,
                ),
            )
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> Draft:
        with self.database.connection() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (str(draft_id),)).fetchone()
        if row is None:
            raise KeyError(f"Unknown draft: {draft_id}")
        return _draft_from_row(row)

    def update_draft(self, draft_id: str, **updates: Any) -> Draft:
        if not updates:
            return self.get_draft(draft_id)
        allowed = {
            "text",
            "topic",
            "source_summary",
            "status",
            "safety_status",
            "similarity_score",
            "expires_at",
            "prompt_snapshot",
            "rejection_reason",
            "reviewer_notes",
            "reviewer",
            "approved_at",
            "published_at",
            "publisher_provider",
            "external_post_id",
            "post_url",
            "error",
        }
        invalid = set(updates) - allowed
        if invalid:
            raise ValueError(f"Unsupported draft fields: {sorted(invalid)}")
        columns = ", ".join(f"{field} = ?" for field in updates)
        params = list(updates.values()) + [str(draft_id)]
        with self.database.connection() as conn:
            conn.execute(f"UPDATE drafts SET {columns} WHERE id = ?", params)
        return self.get_draft(draft_id)

    def list_drafts(self, limit: int = 100, statuses: list[str] | None = None) -> list[Draft]:
        query = "SELECT * FROM drafts"
        params: list[Any] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_draft_from_row(row) for row in rows]

    def pending_expired_before(self, now_iso: str) -> list[Draft]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM drafts
                WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY created_at
                """,
                (now_iso,),
            ).fetchall()
        return [_draft_from_row(row) for row in rows]

    def recent_draft_texts(self, limit: int = 100, exclude_id: str | None = None) -> list[str]:
        query = "SELECT text FROM drafts"
        params: list[Any] = []
        if exclude_id:
            query += " WHERE id != ?"
            params.append(str(exclude_id))
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [row["text"] for row in rows]

    def example_texts(self, statuses: list[str], limit: int = 6) -> list[str]:
        placeholders = ",".join("?" for _ in statuses)
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT text FROM drafts WHERE status IN ({placeholders}) ORDER BY created_at DESC, rowid DESC LIMIT ?",
                [*statuses, limit],
            ).fetchall()
        return [row["text"] for row in rows]

    def create_feedback(
        self,
        *,
        draft_id: str,
        decision: str,
        reason: str,
        notes: str,
        learned_rule: str,
        reviewer: str,
    ) -> FeedbackRecord:
        created_at = utc_now_iso()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feedback (draft_id, decision, reason, notes, learned_rule, reviewer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(draft_id), decision, reason, notes, learned_rule, reviewer, created_at),
            )
            feedback_id = cursor.lastrowid
        return FeedbackRecord(
            id=feedback_id,
            draft_id=str(draft_id),
            decision=decision,
            reason=reason,
            notes=notes,
            learned_rule=learned_rule,
            reviewer=reviewer,
            created_at=created_at,
        )

    def list_feedback(self, limit: int = 50) -> list[FeedbackRecord]:
        with self.database.connection() as conn:
            rows = conn.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [FeedbackRecord(**dict(row)) for row in rows]

    def upsert_preference(
        self,
        *,
        rule: str,
        source_feedback_id: int | None,
        delta: float,
    ) -> LearnedPreference:
        normalized_rule = " ".join(rule.strip().split())
        now = utc_now_iso()
        with self.database.connection() as conn:
            existing = conn.execute("SELECT id FROM preferences WHERE rule = ?", (normalized_rule,)).fetchone()
            if existing:
                preference_id = existing["id"]
                conn.execute(
                    "UPDATE preferences SET weight = weight + ?, source_feedback_id = ?, updated_at = ?, active = 1 WHERE id = ?",
                    (delta, source_feedback_id, now, preference_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO preferences (rule, weight, source_feedback_id, active, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (normalized_rule, delta, source_feedback_id, now, now),
                )
                preference_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM preferences WHERE id = ?", (preference_id,)).fetchone()
        return LearnedPreference(**{**dict(row), "active": bool(row["active"])})

    def list_preferences(self, limit: int = 30, active_only: bool = True) -> list[LearnedPreference]:
        query = "SELECT * FROM preferences"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY weight DESC, updated_at DESC LIMIT ?"
        with self.database.connection() as conn:
            rows = conn.execute(query, (limit,)).fetchall()
        return [LearnedPreference(**{**dict(row), "active": bool(row["active"])}) for row in rows]

    def log_event(self, event_type: str, draft_id: str | None, details: dict[str, Any]) -> None:
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO event_log (event_type, draft_id, details_json, created_at) VALUES (?, ?, ?, ?)",
                (event_type, str(draft_id) if draft_id else None, json.dumps(details, default=str), utc_now_iso()),
            )

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute("SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
