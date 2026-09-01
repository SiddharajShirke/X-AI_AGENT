from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


DEFAULT_CONTEXTS = [
    ("Founder Perspective", "Share an honest founder observation.", "thoughtful and direct", False, "Use a specific observation, not a company announcement."),
    ("Industry Insight", "Explain one useful market pattern.", "clear and analytical", True, "Connect a current signal to the startup domain without copying anyone."),
    ("Industry Problem", "Name a real problem the audience experiences.", "empathetic and practical", False, "Focus on the problem, not the hidden product solution."),
    ("Educational Note", "Teach one compact idea.", "helpful and concrete", False, "Use plain language and one memorable takeaway."),
    ("Technical Perspective", "Discuss a technical principle safely.", "technical but accessible", True, "Discuss trade-offs without revealing architecture or proprietary methods."),
    ("Market Observation", "Describe how the market is changing.", "measured and evidence-minded", True, "Avoid predictions presented as facts."),
    ("Contrarian Take", "Challenge a common assumption.", "confident but respectful", True, "State one defensible disagreement and explain it briefly."),
    ("Building Lesson", "Share a lesson from startup building.", "human and reflective", False, "Do not invent metrics, customers, launches, or product details."),
    ("Community Question", "Invite a useful audience discussion.", "curious and conversational", True, "Ask one specific question that practitioners can answer."),
    ("Future Perspective", "Explore where the domain may go.", "optimistic but grounded", True, "Use uncertainty language and avoid hype."),
]

DEFAULT_TIMES = ["09:00", "10:15", "11:30", "12:45", "14:00", "15:30", "17:00", "18:30", "20:00", "21:30"]


class Database:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS startup_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    target_audience_json TEXT NOT NULL,
                    problems_json TEXT NOT NULL,
                    brand_voice TEXT NOT NULL,
                    public_info_json TEXT NOT NULL,
                    never_reveal_json TEXT NOT NULL,
                    content_pillars_json TEXT NOT NULL,
                    banned_phrases_json TEXT NOT NULL,
                    trend_keywords_json TEXT NOT NULL,
                    competitor_accounts_json TEXT NOT NULL,
                    rss_feeds_json TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    approval_timeout_minutes INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS content_contexts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    tone TEXT NOT NULL,
                    live_trends_required INTEGER NOT NULL DEFAULT 0,
                    instructions TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS schedule_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    context_id INTEGER NOT NULL REFERENCES content_contexts(id),
                    slot_number INTEGER NOT NULL UNIQUE,
                    time_local TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_date TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS config_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version INTEGER NOT NULL UNIQUE,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 1.0,
                    active INTEGER NOT NULL DEFAULT 1,
                    collected_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    context_id INTEGER NOT NULL REFERENCES content_contexts(id),
                    schedule_id INTEGER REFERENCES schedule_slots(id),
                    text TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    source_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    safety_status TEXT NOT NULL,
                    similarity_score REAL NOT NULL,
                    attempt INTEGER NOT NULL,
                    parent_draft_id TEXT REFERENCES drafts(id),
                    config_version INTEGER NOT NULL,
                    expires_at TEXT,
                    generator_provider TEXT NOT NULL,
                    prompt_snapshot TEXT NOT NULL DEFAULT '',
                    rejection_reason TEXT NOT NULL DEFAULT '',
                    reviewer_notes TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    published_at TEXT,
                    publisher_provider TEXT NOT NULL DEFAULT '',
                    external_post_id TEXT NOT NULL DEFAULT '',
                    post_url TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_id TEXT NOT NULL REFERENCES drafts(id),
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    learned_rule TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule TEXT NOT NULL UNIQUE,
                    weight REAL NOT NULL DEFAULT 1.0,
                    source_feedback_id INTEGER REFERENCES feedback(id),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    draft_id TEXT,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._seed(conn)

    def _seed(self, conn: sqlite3.Connection) -> None:
        now = utc_now_iso()
        if conn.execute("SELECT COUNT(*) FROM startup_profile").fetchone()[0] == 0:
            profile = {
                "name": "Stealth Startup",
                "domain": "AI agent reliability and workflow automation",
                "target_audience": ["startup founders", "engineering teams", "product leaders"],
                "problems": ["reliable automation", "human oversight", "repeatable evaluation"],
                "brand_voice": "Founder-like, specific, calm, curious, and technically credible",
                "public_info": ["We study dependable AI-assisted workflows."],
                "never_reveal": [
                    "exact product idea",
                    "private architecture",
                    "unreleased features",
                    "customer names",
                    "internal metrics",
                    "proprietary prompts",
                ],
                "content_pillars": ["agent reliability", "evaluation", "human oversight", "startup building"],
                "banned_phrases": ["game-changer", "revolutionary", "unlock the power", "the future is here"],
                "trend_keywords": ["AI agents", "agent reliability", "workflow automation"],
                "competitor_accounts": ["@example_competitor"],
                "rss_feeds": [],
                "timezone": "Asia/Kolkata",
                "max_attempts": 3,
                "approval_timeout_minutes": 45,
            }
            conn.execute(
                """
                INSERT INTO startup_profile (
                    id, name, domain, target_audience_json, problems_json, brand_voice,
                    public_info_json, never_reveal_json, content_pillars_json,
                    banned_phrases_json, trend_keywords_json, competitor_accounts_json,
                    rss_feeds_json, timezone, max_attempts, approval_timeout_minutes, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["name"],
                    profile["domain"],
                    json.dumps(profile["target_audience"]),
                    json.dumps(profile["problems"]),
                    profile["brand_voice"],
                    json.dumps(profile["public_info"]),
                    json.dumps(profile["never_reveal"]),
                    json.dumps(profile["content_pillars"]),
                    json.dumps(profile["banned_phrases"]),
                    json.dumps(profile["trend_keywords"]),
                    json.dumps(profile["competitor_accounts"]),
                    json.dumps(profile["rss_feeds"]),
                    profile["timezone"],
                    profile["max_attempts"],
                    profile["approval_timeout_minutes"],
                    now,
                ),
            )

        if conn.execute("SELECT COUNT(*) FROM content_contexts").fetchone()[0] == 0:
            conn.executemany(
                """
                INSERT INTO content_contexts
                (name, purpose, tone, live_trends_required, instructions, enabled)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                [(name, purpose, tone, int(live), instructions) for name, purpose, tone, live, instructions in DEFAULT_CONTEXTS],
            )

        if conn.execute("SELECT COUNT(*) FROM schedule_slots").fetchone()[0] == 0:
            context_ids = [row[0] for row in conn.execute("SELECT id FROM content_contexts ORDER BY id LIMIT 10")]
            conn.executemany(
                """
                INSERT INTO schedule_slots (context_id, slot_number, time_local, enabled, last_run_date)
                VALUES (?, ?, ?, 1, '')
                """,
                [(context_id, index + 1, DEFAULT_TIMES[index]) for index, context_id in enumerate(context_ids)],
            )

        if conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0] == 0:
            row = conn.execute("SELECT * FROM startup_profile WHERE id = 1").fetchone()
            snapshot = dict(row)
            conn.execute(
                "INSERT INTO config_versions (version, snapshot_json, created_at) VALUES (1, ?, ?)",
                (json.dumps(snapshot, sort_keys=True), now),
            )
