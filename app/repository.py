from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Iterable

from app.db import Database, utc_now_iso
from app.models import (
    AccountIntegration,
    ContentContext,
    Draft,
    FeedbackRecord,
    IntegrationConnection,
    LearnedPreference,
    PublishAttempt,
    PublishResult,
    ScheduleSlot,
    SlackActionJob,
    StartupProfile,
    TrendItem,
    XAccount,
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
_ACCOUNT_UPDATE_FIELDS = {"name", "handle", "enabled", "live_posting_enabled", "timezone"}
_HANDLE_PATTERN = re.compile(r"[a-z0-9_]{1,15}")


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


def _normalize_handle(handle: str) -> str:
    normalized = str(handle).strip().removeprefix("@").strip().lower()
    if not _HANDLE_PATTERN.fullmatch(normalized):
        raise ValueError("X account handle must contain 1-15 letters, numbers, or underscores")
    return normalized


def _account_from_row(row: Any) -> XAccount:
    return XAccount(
        **{
            **dict(row),
            "enabled": bool(row["enabled"]),
            "live_posting_enabled": bool(row["live_posting_enabled"]),
        }
    )


def _profile_from_row(row: Any) -> StartupProfile:
    return StartupProfile(
        id=row["id"],
        x_account_id=row["x_account_id"],
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
        max_attempts=row["max_attempts"],
        approval_timeout_minutes=row["approval_timeout_minutes"],
        updated_at=row["updated_at"],
    )


def _context_from_row(row: Any) -> ContentContext:
    return ContentContext(
        id=row["id"],
        x_account_id=row["x_account_id"],
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
        x_account_id=row["x_account_id"],
        context_id=row["context_id"],
        slot_number=row["slot_number"],
        time_local=row["time_local"],
        enabled=bool(row["enabled"]),
        last_run_date=row["last_run_date"],
    )


def _draft_from_row(row: Any) -> Draft:
    return Draft(**dict(row))


def _connection_from_row(row: Any) -> IntegrationConnection:
    return IntegrationConnection(
        id=row["id"],
        provider=row["provider"],
        label=row["label"],
        credentials_configured=bool(row["credentials_configured"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _binding_from_row(row: Any) -> AccountIntegration:
    return AccountIntegration(
        x_account_id=row["x_account_id"],
        provider=row["provider"],
        connection_id=row["connection_id"],
        target_id=row["target_id"],
        enabled=bool(row["enabled"]),
        last_test_success=(
            None if row["last_test_success"] is None else bool(row["last_test_success"])
        ),
        last_test_error=row["last_test_error"],
        last_tested_at=row["last_tested_at"],
    )


def _slack_action_from_row(row: Any) -> SlackActionJob:
    return SlackActionJob(
        **{
            **dict(row),
            "expected_live": bool(row["expected_live"]),
        }
    )


def _publish_attempt_from_row(row: Any) -> PublishAttempt:
    return PublishAttempt(**dict(row))


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def list_accounts(self, enabled_only: bool = False) -> list[XAccount]:
        query = "SELECT * FROM x_accounts"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        with self.database.connection() as conn:
            rows = conn.execute(query).fetchall()
        return [_account_from_row(row) for row in rows]

    def get_account(self, x_account_id: int) -> XAccount:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM x_accounts WHERE id = ?", (x_account_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown X account: {x_account_id}")
        return _account_from_row(row)

    def create_account(
        self,
        name: str,
        handle: str,
        timezone: str,
        copy_from_id: int | None = None,
    ) -> XAccount:
        normalized_name = str(name).strip()
        normalized_handle = _normalize_handle(handle)
        normalized_timezone = str(timezone).strip()
        if not normalized_name:
            raise ValueError("X account name is required")
        if not normalized_timezone:
            raise ValueError("X account timezone is required")
        now = utc_now_iso()
        try:
            with self.database.connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO x_accounts (
                        name, handle, enabled, live_posting_enabled, timezone, created_at, updated_at
                    ) VALUES (?, ?, 1, 0, ?, ?, ?)
                    """,
                    (normalized_name, normalized_handle, normalized_timezone, now, now),
                )
                x_account_id = int(cursor.lastrowid)
                source_account_id = copy_from_id
                if source_account_id is None:
                    source = conn.execute(
                        "SELECT id FROM x_accounts WHERE id != ? ORDER BY id LIMIT 1",
                        (x_account_id,),
                    ).fetchone()
                    if source is None:
                        raise RuntimeError("No default account configuration is available")
                    source_account_id = int(source["id"])
                self._copy_account_configuration(
                    conn,
                    source_account_id=source_account_id,
                    target_account_id=x_account_id,
                    target_timezone=normalized_timezone,
                    created_at=now,
                )
        except sqlite3.IntegrityError as exc:
            if "x_accounts.handle" in str(exc):
                raise ValueError(f"X account handle already exists: {normalized_handle}") from exc
            raise
        return self.get_account(x_account_id)

    @staticmethod
    def _copy_account_configuration(
        conn: sqlite3.Connection,
        *,
        source_account_id: int,
        target_account_id: int,
        target_timezone: str,
        created_at: str,
    ) -> None:
        source_profile = conn.execute(
            "SELECT * FROM startup_profile WHERE x_account_id = ?", (source_account_id,)
        ).fetchone()
        if source_profile is None:
            raise KeyError(f"Unknown source account configuration: {source_account_id}")
        conn.execute(
            """
            INSERT INTO startup_profile (
                x_account_id, name, domain, target_audience_json, problems_json, brand_voice,
                public_info_json, never_reveal_json, content_pillars_json, banned_phrases_json,
                trend_keywords_json, competitor_accounts_json, rss_feeds_json, timezone,
                max_attempts, approval_timeout_minutes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_account_id,
                source_profile["name"],
                source_profile["domain"],
                source_profile["target_audience_json"],
                source_profile["problems_json"],
                source_profile["brand_voice"],
                source_profile["public_info_json"],
                source_profile["never_reveal_json"],
                source_profile["content_pillars_json"],
                source_profile["banned_phrases_json"],
                source_profile["trend_keywords_json"],
                source_profile["competitor_accounts_json"],
                source_profile["rss_feeds_json"],
                target_timezone,
                source_profile["max_attempts"],
                source_profile["approval_timeout_minutes"],
                created_at,
            ),
        )

        context_ids: dict[int, int] = {}
        source_contexts = conn.execute(
            "SELECT * FROM content_contexts WHERE x_account_id = ? ORDER BY id",
            (source_account_id,),
        ).fetchall()
        for context in source_contexts:
            cursor = conn.execute(
                """
                INSERT INTO content_contexts (
                    x_account_id, name, purpose, tone, live_trends_required, instructions, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_account_id,
                    context["name"],
                    context["purpose"],
                    context["tone"],
                    context["live_trends_required"],
                    context["instructions"],
                    context["enabled"],
                ),
            )
            context_ids[int(context["id"])] = int(cursor.lastrowid)

        source_schedules = conn.execute(
            "SELECT * FROM schedule_slots WHERE x_account_id = ? ORDER BY slot_number",
            (source_account_id,),
        ).fetchall()
        for schedule in source_schedules:
            conn.execute(
                """
                INSERT INTO schedule_slots (
                    x_account_id, context_id, slot_number, time_local, enabled, last_run_date
                ) VALUES (?, ?, ?, ?, ?, '')
                """,
                (
                    target_account_id,
                    context_ids[int(schedule["context_id"])],
                    schedule["slot_number"],
                    schedule["time_local"],
                    schedule["enabled"],
                ),
            )

        copied_profile = conn.execute(
            "SELECT * FROM startup_profile WHERE x_account_id = ?", (target_account_id,)
        ).fetchone()
        snapshot = dict(copied_profile)
        snapshot.pop("timezone", None)
        conn.execute(
            """
            INSERT INTO config_versions (x_account_id, version, snapshot_json, created_at)
            VALUES (?, 1, ?, ?)
            """,
            (target_account_id, json.dumps(snapshot, sort_keys=True), created_at),
        )

    def update_account(self, x_account_id: int, updates: dict[str, Any]) -> XAccount:
        if not updates:
            return self.get_account(x_account_id)
        invalid = set(updates) - _ACCOUNT_UPDATE_FIELDS
        if invalid:
            raise ValueError(f"Unsupported X account fields: {sorted(invalid)}")
        normalized = dict(updates)
        if "name" in normalized:
            normalized["name"] = str(normalized["name"]).strip()
            if not normalized["name"]:
                raise ValueError("X account name is required")
        if "handle" in normalized:
            normalized["handle"] = _normalize_handle(normalized["handle"])
        if "timezone" in normalized:
            normalized["timezone"] = str(normalized["timezone"]).strip()
            if not normalized["timezone"]:
                raise ValueError("X account timezone is required")
        for field in ("enabled", "live_posting_enabled"):
            if field in normalized:
                normalized[field] = int(bool(normalized[field]))
        normalized["updated_at"] = utc_now_iso()
        columns = ", ".join(f"{field} = ?" for field in normalized)
        params = [*normalized.values(), x_account_id]
        try:
            with self.database.connection() as conn:
                conn.execute(f"UPDATE x_accounts SET {columns} WHERE id = ?", params)
        except sqlite3.IntegrityError as exc:
            if "x_accounts.handle" in str(exc):
                raise ValueError("X account handle already exists") from exc
            raise
        return self.get_account(x_account_id)

    def get_profile(self, x_account_id: int) -> StartupProfile:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM startup_profile WHERE x_account_id = ?", (x_account_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Startup profile is not initialized for account {x_account_id}")
        return _profile_from_row(row)

    def update_profile(self, x_account_id: int, updates: dict[str, Any]) -> StartupProfile:
        current = self.get_profile(x_account_id).model_dump()
        if "timezone" in updates:
            raise ValueError("Timezone belongs to the X account, not the startup profile")
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
                    competitor_accounts_json = ?, rss_feeds_json = ?, max_attempts = ?,
                    approval_timeout_minutes = ?, updated_at = ?
                WHERE x_account_id = ?
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
                    current["max_attempts"],
                    current["approval_timeout_minutes"],
                    now,
                    x_account_id,
                ),
            )
            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM config_versions WHERE x_account_id = ?",
                (x_account_id,),
            ).fetchone()[0]
            row = conn.execute(
                "SELECT * FROM startup_profile WHERE x_account_id = ?", (x_account_id,)
            ).fetchone()
            snapshot = dict(row)
            snapshot.pop("timezone", None)
            conn.execute(
                """
                INSERT INTO config_versions (x_account_id, version, snapshot_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (x_account_id, version, json.dumps(snapshot, sort_keys=True), now),
            )
        return self.get_profile(x_account_id)

    def current_config_version(self, x_account_id: int) -> int:
        with self.database.connection() as conn:
            return int(
                conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM config_versions WHERE x_account_id = ?",
                    (x_account_id,),
                ).fetchone()[0]
            )

    def list_config_versions(
        self, x_account_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT version, snapshot_json, created_at FROM config_versions
                WHERE x_account_id = ? ORDER BY version DESC LIMIT ?
                """,
                (x_account_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_contexts(
        self, x_account_id: int, enabled_only: bool = False
    ) -> list[ContentContext]:
        query = "SELECT * FROM content_contexts WHERE x_account_id = ?"
        params: list[Any] = [x_account_id]
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY id"
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_context_from_row(row) for row in rows]

    def get_context(self, x_account_id: int, context_id: int) -> ContentContext:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM content_contexts WHERE x_account_id = ? AND id = ?",
                (x_account_id, context_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown content context for account {x_account_id}: {context_id}")
        return _context_from_row(row)

    def update_context(
        self, x_account_id: int, context_id: int, updates: dict[str, Any]
    ) -> ContentContext:
        current = self.get_context(x_account_id, context_id).model_dump()
        current.update(updates)
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE content_contexts
                SET name = ?, purpose = ?, tone = ?, live_trends_required = ?, instructions = ?, enabled = ?
                WHERE x_account_id = ? AND id = ?
                """,
                (
                    current["name"],
                    current["purpose"],
                    current["tone"],
                    int(bool(current["live_trends_required"])),
                    current["instructions"],
                    int(bool(current["enabled"])),
                    x_account_id,
                    context_id,
                ),
            )
        return self.get_context(x_account_id, context_id)

    def list_schedules(self, x_account_id: int) -> list[ScheduleSlot]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM schedule_slots WHERE x_account_id = ? ORDER BY slot_number",
                (x_account_id,),
            ).fetchall()
        return [_schedule_from_row(row) for row in rows]

    def get_schedule(self, x_account_id: int, schedule_id: int) -> ScheduleSlot:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_slots WHERE x_account_id = ? AND id = ?",
                (x_account_id, schedule_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown schedule slot for account {x_account_id}: {schedule_id}")
        return _schedule_from_row(row)

    def update_schedule(
        self, x_account_id: int, schedule_id: int, updates: dict[str, Any]
    ) -> ScheduleSlot:
        current = self.get_schedule(x_account_id, schedule_id).model_dump()
        current.update(updates)
        time_local = str(current["time_local"])
        datetime.strptime(time_local, "%H:%M")
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE schedule_slots
                SET context_id = ?, time_local = ?, enabled = ?, last_run_date = ?
                WHERE x_account_id = ? AND id = ?
                """,
                (
                    int(current["context_id"]),
                    time_local,
                    int(bool(current["enabled"])),
                    str(current.get("last_run_date", "")),
                    x_account_id,
                    schedule_id,
                ),
            )
        return self.get_schedule(x_account_id, schedule_id)

    def mark_schedule_run(self, x_account_id: int, schedule_id: int, local_date: str) -> None:
        with self.database.connection() as conn:
            conn.execute(
                "UPDATE schedule_slots SET last_run_date = ? WHERE x_account_id = ? AND id = ?",
                (local_date, x_account_id, schedule_id),
            )

    def add_trend(
        self,
        x_account_id: int,
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
                INSERT INTO trends (
                    x_account_id, title, summary, source, url, score, active, collected_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (x_account_id, title, summary, source, url, score, collected_at),
            )
            trend_id = cursor.lastrowid
        return TrendItem(
            id=trend_id,
            x_account_id=x_account_id,
            title=title,
            summary=summary,
            source=source,
            url=url,
            score=score,
            active=True,
            collected_at=collected_at,
        )

    def list_trends(
        self, x_account_id: int, limit: int = 20, active_only: bool = True
    ) -> list[TrendItem]:
        query = "SELECT * FROM trends WHERE x_account_id = ?"
        params: list[Any] = [x_account_id]
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY score DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [TrendItem(**{**dict(row), "active": bool(row["active"])}) for row in rows]

    def create_draft(
        self,
        x_account_id: int,
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
                    id, x_account_id, context_id, schedule_id, text, topic, source_summary,
                    status, safety_status, similarity_score, attempt, parent_draft_id,
                    config_version, expires_at, generator_provider, prompt_snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    x_account_id,
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
        return self.get_draft(x_account_id, draft_id)

    def get_draft(self, x_account_id: int, draft_id: str) -> Draft:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE x_account_id = ? AND id = ?",
                (x_account_id, str(draft_id)),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown draft for account {x_account_id}: {draft_id}")
        return _draft_from_row(row)

    def update_draft(self, x_account_id: int, draft_id: str, **updates: Any) -> Draft:
        if not updates:
            return self.get_draft(x_account_id, draft_id)
        allowed = {
            "text", "topic", "source_summary", "status", "safety_status",
            "similarity_score", "expires_at", "prompt_snapshot", "rejection_reason",
            "reviewer_notes", "reviewer", "approved_at", "published_at",
            "publisher_provider", "external_post_id", "post_url", "error",
        }
        invalid = set(updates) - allowed
        if invalid:
            raise ValueError(f"Unsupported draft fields: {sorted(invalid)}")
        columns = ", ".join(f"{field} = ?" for field in updates)
        params = [*updates.values(), x_account_id, str(draft_id)]
        with self.database.connection() as conn:
            conn.execute(
                f"UPDATE drafts SET {columns} WHERE x_account_id = ? AND id = ?", params
            )
        return self.get_draft(x_account_id, draft_id)

    def transition_draft(
        self,
        x_account_id: int,
        draft_id: str,
        *,
        from_status: str,
        to_status: str,
        **updates: Any,
    ) -> Draft | None:
        allowed = {
            "text", "topic", "source_summary", "safety_status",
            "similarity_score", "expires_at", "prompt_snapshot",
            "rejection_reason", "reviewer_notes", "reviewer", "approved_at",
            "published_at", "publisher_provider", "external_post_id",
            "post_url", "error",
        }
        invalid = set(updates) - allowed
        if invalid:
            raise ValueError(f"Unsupported draft fields: {sorted(invalid)}")
        assignments = ["status = ?", *(f"{field} = ?" for field in updates)]
        params = [to_status, *updates.values(), x_account_id, str(draft_id), from_status]
        with self.database.connection() as conn:
            cursor = conn.execute(
                f"UPDATE drafts SET {', '.join(assignments)} "
                "WHERE x_account_id = ? AND id = ? AND status = ?",
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_draft(x_account_id, draft_id)

    def list_drafts(
        self, x_account_id: int, limit: int = 100, statuses: list[str] | None = None
    ) -> list[Draft]:
        query = "SELECT * FROM drafts WHERE x_account_id = ?"
        params: list[Any] = [x_account_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" AND status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_draft_from_row(row) for row in rows]

    def pending_expired_before(self, x_account_id: int, now_iso: str) -> list[Draft]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM drafts
                WHERE x_account_id = ? AND status = 'pending'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY created_at
                """,
                (x_account_id, now_iso),
            ).fetchall()
        return [_draft_from_row(row) for row in rows]

    def recent_draft_texts(
        self, x_account_id: int, limit: int = 100, exclude_id: str | None = None
    ) -> list[str]:
        query = "SELECT text FROM drafts WHERE x_account_id = ?"
        params: list[Any] = [x_account_id]
        if exclude_id:
            query += " AND id != ?"
            params.append(str(exclude_id))
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [row["text"] for row in rows]

    def example_texts(self, x_account_id: int, statuses: list[str], limit: int = 6) -> list[str]:
        placeholders = ",".join("?" for _ in statuses)
        with self.database.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT text FROM drafts
                WHERE x_account_id = ? AND status IN ({placeholders})
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                [x_account_id, *statuses, limit],
            ).fetchall()
        return [row["text"] for row in rows]

    def create_feedback(
        self,
        x_account_id: int,
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
                INSERT INTO feedback (
                    x_account_id, draft_id, decision, reason, notes, learned_rule, reviewer, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    x_account_id, str(draft_id), decision, reason, notes,
                    learned_rule, reviewer, created_at,
                ),
            )
            feedback_id = cursor.lastrowid
        return FeedbackRecord(
            id=feedback_id,
            x_account_id=x_account_id,
            draft_id=str(draft_id),
            decision=decision,
            reason=reason,
            notes=notes,
            learned_rule=learned_rule,
            reviewer=reviewer,
            created_at=created_at,
        )

    def list_feedback(self, x_account_id: int, limit: int = 50) -> list[FeedbackRecord]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE x_account_id = ? ORDER BY id DESC LIMIT ?",
                (x_account_id, limit),
            ).fetchall()
        return [FeedbackRecord(**dict(row)) for row in rows]

    def upsert_preference(
        self,
        x_account_id: int,
        *,
        rule: str,
        source_feedback_id: int | None,
        delta: float,
    ) -> LearnedPreference:
        normalized_rule = " ".join(rule.strip().split())
        now = utc_now_iso()
        with self.database.connection() as conn:
            existing = conn.execute(
                "SELECT id FROM preferences WHERE x_account_id = ? AND rule = ?",
                (x_account_id, normalized_rule),
            ).fetchone()
            if existing:
                preference_id = existing["id"]
                conn.execute(
                    """
                    UPDATE preferences SET
                        weight = weight + ?, source_feedback_id = ?, updated_at = ?, active = 1
                    WHERE x_account_id = ? AND id = ?
                    """,
                    (delta, source_feedback_id, now, x_account_id, preference_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO preferences (
                        x_account_id, rule, weight, source_feedback_id, active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (x_account_id, normalized_rule, delta, source_feedback_id, now, now),
                )
                preference_id = cursor.lastrowid
            row = conn.execute(
                "SELECT * FROM preferences WHERE x_account_id = ? AND id = ?",
                (x_account_id, preference_id),
            ).fetchone()
        return LearnedPreference(**{**dict(row), "active": bool(row["active"])})

    def list_preferences(
        self, x_account_id: int, limit: int = 30, active_only: bool = True
    ) -> list[LearnedPreference]:
        query = "SELECT * FROM preferences WHERE x_account_id = ?"
        params: list[Any] = [x_account_id]
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY weight DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            LearnedPreference(**{**dict(row), "active": bool(row["active"])}) for row in rows
        ]

    def log_event(
        self,
        x_account_id: int,
        event_type: str,
        draft_id: str | None,
        details: dict[str, Any],
    ) -> None:
        with self.database.connection() as conn:
            conn.execute(
                """
                INSERT INTO event_log (
                    x_account_id, event_type, draft_id, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    x_account_id,
                    event_type,
                    str(draft_id) if draft_id else None,
                    json.dumps(details, default=str),
                    utc_now_iso(),
                ),
            )

    def list_events(self, x_account_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM event_log WHERE x_account_id = ? ORDER BY id DESC LIMIT ?",
                (x_account_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_publish(
        self,
        x_account_id: int,
        draft_id: str,
        *,
        reviewer: str,
        origin: str,
        allow_retry: bool = False,
    ) -> tuple[Draft, PublishAttempt] | None:
        now = utc_now_iso()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE drafts
                SET status = 'publishing', reviewer = ?,
                    approved_at = COALESCE(approved_at, ?), expires_at = NULL
                WHERE x_account_id = ?
                  AND id = ?
                  AND (status = 'pending' OR (? = 1 AND status = 'failed'))
                """,
                (reviewer, now, x_account_id, str(draft_id), int(allow_retry)),
            )
            if cursor.rowcount != 1:
                return None
            attempt_number = int(
                conn.execute(
                    """
                    SELECT COALESCE(MAX(attempt_number), 0) + 1
                    FROM publish_attempts
                    WHERE x_account_id = ? AND draft_id = ?
                    """,
                    (x_account_id, str(draft_id)),
                ).fetchone()[0]
            )
            attempt_cursor = conn.execute(
                """
                INSERT INTO publish_attempts (
                    x_account_id, draft_id, attempt_number, status, origin,
                    reviewer, error, created_at, completed_at
                ) VALUES (?, ?, ?, 'publishing', ?, ?, '', ?, NULL)
                """,
                (
                    x_account_id,
                    str(draft_id),
                    attempt_number,
                    origin,
                    reviewer,
                    now,
                ),
            )
            draft_row = conn.execute(
                "SELECT * FROM drafts WHERE x_account_id = ? AND id = ?",
                (x_account_id, str(draft_id)),
            ).fetchone()
            attempt_row = conn.execute(
                "SELECT * FROM publish_attempts WHERE id = ?",
                (attempt_cursor.lastrowid,),
            ).fetchone()
        return _draft_from_row(draft_row), _publish_attempt_from_row(attempt_row)

    def complete_publish(
        self,
        x_account_id: int,
        draft_id: str,
        attempt_id: int,
        result: PublishResult,
    ) -> Draft:
        completed_at = utc_now_iso()
        final_status = "published" if result.success else "failed"
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE publish_attempts
                SET status = ?, error = ?, completed_at = ?
                WHERE id = ? AND x_account_id = ? AND draft_id = ?
                  AND status = 'publishing'
                """,
                (
                    final_status,
                    result.error,
                    completed_at,
                    attempt_id,
                    x_account_id,
                    str(draft_id),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Publication attempt is not active for this account draft")
            draft_cursor = conn.execute(
                """
                UPDATE drafts
                SET status = ?, published_at = ?, publisher_provider = ?,
                    external_post_id = ?, post_url = ?, error = ?
                WHERE x_account_id = ? AND id = ? AND status = 'publishing'
                """,
                (
                    final_status,
                    completed_at if result.success else None,
                    result.provider,
                    result.external_post_id,
                    result.post_url,
                    result.error,
                    x_account_id,
                    str(draft_id),
                ),
            )
            if draft_cursor.rowcount != 1:
                raise RuntimeError("Draft is not awaiting publication completion")
            row = conn.execute(
                "SELECT * FROM drafts WHERE x_account_id = ? AND id = ?",
                (x_account_id, str(draft_id)),
            ).fetchone()
        return _draft_from_row(row)

    def create_integration_connection(
        self, provider: str, label: str, encrypted_credentials: str
    ) -> IntegrationConnection:
        normalized_provider = str(provider).strip().lower()
        now = utc_now_iso()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO integration_connections (
                    provider, label, encrypted_credentials, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (normalized_provider, str(label).strip(), str(encrypted_credentials), now, now),
            )
            row = conn.execute(
                """
                SELECT id, provider, label,
                       encrypted_credentials != '' AS credentials_configured,
                       created_at, updated_at
                FROM integration_connections WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return _connection_from_row(row)

    def get_encrypted_credentials(self, connection_id: int) -> str:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT encrypted_credentials FROM integration_connections WHERE id = ?",
                (connection_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown integration connection: {connection_id}")
        return str(row["encrypted_credentials"])

    def get_integration_connection(self, connection_id: int) -> IntegrationConnection:
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT id, provider, label,
                       encrypted_credentials != '' AS credentials_configured,
                       created_at, updated_at
                FROM integration_connections WHERE id = ?
                """,
                (connection_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown integration connection: {connection_id}")
        return _connection_from_row(row)

    def list_integration_connections(
        self, provider: str | None = None
    ) -> list[IntegrationConnection]:
        query = """
            SELECT id, provider, label,
                   encrypted_credentials != '' AS credentials_configured,
                   created_at, updated_at
            FROM integration_connections
        """
        params: list[Any] = []
        if provider is not None:
            query += " WHERE provider = ?"
            params.append(str(provider).strip().lower())
        query += " ORDER BY provider, label, id"
        with self.database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_connection_from_row(row) for row in rows]

    def replace_integration_connection(
        self,
        connection_id: int,
        *,
        provider: str,
        label: str,
        encrypted_credentials: str,
    ) -> IntegrationConnection:
        now = utc_now_iso()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE integration_connections
                SET label = ?, encrypted_credentials = ?, updated_at = ?
                WHERE id = ? AND provider = ?
                """,
                (
                    str(label).strip(),
                    str(encrypted_credentials),
                    now,
                    connection_id,
                    str(provider).strip().lower(),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown {provider} integration connection: {connection_id}")
        return self.get_integration_connection(connection_id)

    def bind_account_integration(
        self,
        x_account_id: int,
        provider: str,
        connection_id: int,
        target_id: str,
        enabled: bool = True,
    ) -> AccountIntegration:
        normalized_provider = str(provider).strip().lower()
        with self.database.connection() as conn:
            connection = conn.execute(
                "SELECT id FROM integration_connections WHERE id = ? AND provider = ?",
                (connection_id, normalized_provider),
            ).fetchone()
            if connection is None:
                raise KeyError(
                    f"Unknown {normalized_provider} integration connection: {connection_id}"
                )
            conn.execute(
                """
                INSERT INTO account_integrations (
                    x_account_id, provider, connection_id, target_id, enabled,
                    last_test_success, last_test_error, last_tested_at
                ) VALUES (?, ?, ?, ?, ?, NULL, '', NULL)
                ON CONFLICT(x_account_id, provider) DO UPDATE SET
                    connection_id = excluded.connection_id,
                    target_id = excluded.target_id,
                    enabled = excluded.enabled,
                    last_test_success = NULL,
                    last_test_error = '',
                    last_tested_at = NULL
                """,
                (
                    x_account_id,
                    normalized_provider,
                    connection_id,
                    str(target_id).strip(),
                    int(bool(enabled)),
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM account_integrations WHERE x_account_id = ? AND provider = ?
                """,
                (x_account_id, normalized_provider),
            ).fetchone()
        return _binding_from_row(row)

    def get_account_integration(
        self, x_account_id: int, provider: str
    ) -> AccountIntegration | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM account_integrations WHERE x_account_id = ? AND provider = ?",
                (x_account_id, str(provider).strip().lower()),
            ).fetchone()
        return _binding_from_row(row) if row is not None else None

    def enqueue_slack_action(
        self,
        *,
        idempotency_key: str,
        connection_id: int,
        x_account_id: int,
        draft_id: str,
        action_id: str,
        expected_live: bool,
        reviewer: str,
    ) -> tuple[SlackActionJob, bool]:
        if action_id not in {"approve_draft", "reject_draft"}:
            raise ValueError("Unsupported Slack action")
        normalized_connection_id = int(connection_id)
        normalized_account_id = int(x_account_id)
        normalized_draft_id = str(draft_id)
        normalized_expected_live = bool(expected_live)
        normalized_reviewer = str(reviewer)
        with self.database.connection() as conn:
            draft = conn.execute(
                "SELECT id FROM drafts WHERE x_account_id = ? AND id = ?",
                (normalized_account_id, normalized_draft_id),
            ).fetchone()
            if draft is None:
                raise ValueError("Draft does not belong to the X account")
            cursor = conn.execute(
                """
                INSERT INTO slack_action_jobs (
                    idempotency_key, connection_id, x_account_id, draft_id,
                    action_id, expected_live, reviewer, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    idempotency_key,
                    normalized_connection_id,
                    normalized_account_id,
                    normalized_draft_id,
                    action_id,
                    int(normalized_expected_live),
                    normalized_reviewer,
                    utc_now_iso(),
                ),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                """
                SELECT * FROM slack_action_jobs
                WHERE x_account_id = ? AND idempotency_key = ?
                """,
                (normalized_account_id, idempotency_key),
            ).fetchone()
        if row is None:
            raise ValueError("Slack action idempotency key conflicts with another account")
        job = _slack_action_from_row(row)
        if (
            job.connection_id != normalized_connection_id
            or job.draft_id != normalized_draft_id
            or job.action_id != action_id
            or job.expected_live != normalized_expected_live
            or job.reviewer != normalized_reviewer
        ):
            raise ValueError("Slack action idempotency key conflicts with a different request")
        return job, created

    def get_slack_action_job(
        self, x_account_id: int, job_id: int
    ) -> SlackActionJob:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM slack_action_jobs WHERE x_account_id = ? AND id = ?",
                (x_account_id, job_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Slack action job for account {x_account_id}: {job_id}")
        return _slack_action_from_row(row)

    def latest_slack_action(
        self, x_account_id: int, connection_id: int
    ) -> SlackActionJob | None:
        with self.database.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM slack_action_jobs
                WHERE x_account_id = ? AND connection_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (x_account_id, connection_id),
            ).fetchone()
        return _slack_action_from_row(row) if row is not None else None

    def list_slack_actions(
        self, x_account_id: int, limit: int = 20
    ) -> list[SlackActionJob]:
        with self.database.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM slack_action_jobs
                WHERE x_account_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (x_account_id, limit),
            ).fetchall()
        return [_slack_action_from_row(row) for row in rows]

    def claim_next_slack_action(self, stale_before: str) -> SlackActionJob | None:
        claimed_at = utc_now_iso()
        with self.database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute(
                """
                SELECT * FROM slack_action_jobs
                WHERE status = 'pending'
                   OR (status = 'processing' AND claimed_at < ?)
                ORDER BY created_at, id
                LIMIT 1
                """,
                (stale_before,),
            ).fetchone()
            if candidate is None:
                return None
            cursor = conn.execute(
                """
                UPDATE slack_action_jobs
                SET status = 'processing', claimed_at = ?
                WHERE id = ?
                  AND (status = 'pending' OR (status = 'processing' AND claimed_at < ?))
                """,
                (claimed_at, candidate["id"], stale_before),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM slack_action_jobs WHERE id = ?", (candidate["id"],)
            ).fetchone()
            self._log_slack_action_event(
                conn,
                "slack_action_processing",
                row,
                result_status="processing",
                provider="",
            )
        return _slack_action_from_row(row)

    def complete_slack_action(
        self,
        x_account_id: int,
        job_id: int,
        *,
        result_draft_id: str,
    ) -> SlackActionJob:
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE slack_action_jobs
                SET status = 'completed', result_draft_id = ?, safe_error = '',
                    completed_at = ?
                WHERE x_account_id = ? AND id = ? AND status = 'processing'
                """,
                (str(result_draft_id), utc_now_iso(), x_account_id, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Slack action job is not processing: {job_id}")
            row = conn.execute(
                "SELECT * FROM slack_action_jobs WHERE x_account_id = ? AND id = ?",
                (x_account_id, job_id),
            ).fetchone()
            result = conn.execute(
                """
                SELECT status, publisher_provider FROM drafts
                WHERE x_account_id = ? AND id = ?
                """,
                (x_account_id, str(result_draft_id)),
            ).fetchone()
            self._log_slack_action_event(
                conn,
                "slack_action_completed",
                row,
                result_status=str(result["status"]) if result is not None else "",
                provider=(
                    str(result["publisher_provider"] or "")
                    if result is not None
                    else ""
                ),
            )
        return self.get_slack_action_job(x_account_id, job_id)

    def fail_slack_action(
        self, x_account_id: int, job_id: int, safe_error: str
    ) -> SlackActionJob:
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE slack_action_jobs
                SET status = 'failed', safe_error = ?, completed_at = ?
                WHERE x_account_id = ? AND id = ? AND status = 'processing'
                """,
                (str(safe_error), utc_now_iso(), x_account_id, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Slack action job is not processing: {job_id}")
            row = conn.execute(
                "SELECT * FROM slack_action_jobs WHERE x_account_id = ? AND id = ?",
                (x_account_id, job_id),
            ).fetchone()
            self._log_slack_action_event(
                conn,
                "slack_action_failed",
                row,
                result_status="failed",
                provider="",
            )
        return self.get_slack_action_job(x_account_id, job_id)

    @staticmethod
    def _log_slack_action_event(
        conn,
        event_type: str,
        job_row,
        *,
        result_status: str,
        provider: str,
    ) -> None:
        details = {
            "x_account_id": int(job_row["x_account_id"]),
            "job_id": int(job_row["id"]),
            "action_id": str(job_row["action_id"]),
            "draft_id": str(job_row["draft_id"]),
            "result_status": str(result_status),
            "provider": str(provider),
        }
        conn.execute(
            """
            INSERT INTO event_log (
                x_account_id, event_type, draft_id, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                details["x_account_id"],
                event_type,
                details["draft_id"],
                json.dumps(details),
                utc_now_iso(),
            ),
        )

    def record_integration_test(
        self,
        x_account_id: int,
        provider: str,
        *,
        success: bool,
        error: str = "",
    ) -> AccountIntegration:
        normalized_provider = str(provider).strip().lower()
        with self.database.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE account_integrations
                SET last_test_success = ?, last_test_error = ?, last_tested_at = ?
                WHERE x_account_id = ? AND provider = ?
                """,
                (
                    int(bool(success)),
                    str(error),
                    utc_now_iso(),
                    x_account_id,
                    normalized_provider,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(
                    f"Unknown {normalized_provider} integration for account {x_account_id}"
                )
        binding = self.get_account_integration(x_account_id, normalized_provider)
        if binding is None:
            raise KeyError(
                f"Unknown {normalized_provider} integration for account {x_account_id}"
            )
        return binding

    def disconnect_account_integration(
        self, x_account_id: int, provider: str
    ) -> AccountIntegration | None:
        normalized_provider = str(provider).strip().lower()
        with self.database.connection() as conn:
            conn.execute(
                """
                UPDATE account_integrations SET enabled = 0
                WHERE x_account_id = ? AND provider = ?
                """,
                (x_account_id, normalized_provider),
            )
        return self.get_account_integration(x_account_id, normalized_provider)
