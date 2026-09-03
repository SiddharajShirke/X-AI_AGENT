from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import Database


@pytest.fixture()
def legacy_database(tmp_path: Path) -> Database:
    database = Database(str(tmp_path / "legacy.db"))
    with database.connection() as conn:
        conn.executescript(
            """
            CREATE TABLE startup_profile (
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
            CREATE TABLE content_contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                purpose TEXT NOT NULL,
                tone TEXT NOT NULL,
                live_trends_required INTEGER NOT NULL DEFAULT 0,
                instructions TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE schedule_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_id INTEGER NOT NULL REFERENCES content_contexts(id),
                slot_number INTEGER NOT NULL UNIQUE,
                time_local TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_date TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE config_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL UNIQUE,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                score REAL NOT NULL DEFAULT 1.0,
                active INTEGER NOT NULL DEFAULT 1,
                collected_at TEXT NOT NULL
            );
            CREATE TABLE drafts (
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
            CREATE TABLE feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id TEXT NOT NULL REFERENCES drafts(id),
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                notes TEXT NOT NULL,
                learned_rule TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule TEXT NOT NULL UNIQUE,
                weight REAL NOT NULL DEFAULT 1.0,
                source_feedback_id INTEGER REFERENCES feedback(id),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                draft_id TEXT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            INSERT INTO startup_profile VALUES (
                1, 'Legacy Brand', 'legacy.example', '["founders"]', '["reliability"]',
                'plain', '["public"]', '["secret"]', '["learning"]', '[]', '["agents"]',
                '[]', '[]', 'Europe/London', 4, 90, '2026-01-01T00:00:00+00:00'
            );
            INSERT INTO content_contexts VALUES (
                11, 'Legacy context', 'Preserve it', 'plain', 0, 'Keep this row', 1
            );
            INSERT INTO schedule_slots VALUES (21, 11, 7, '13:45', 1, '2026-08-31');
            INSERT INTO config_versions VALUES (
                31, 4, '{"name":"Legacy Brand"}', '2026-01-01T00:00:00+00:00'
            );
            INSERT INTO trends VALUES (
                41, 'Legacy trend', 'Still relevant', 'manual', '', 2.5, 1,
                '2026-01-02T00:00:00+00:00'
            );
            INSERT INTO drafts (
                id, context_id, schedule_id, text, topic, source_summary, status,
                safety_status, similarity_score, attempt, parent_draft_id, config_version,
                expires_at, generator_provider, prompt_snapshot, rejection_reason,
                reviewer_notes, reviewer, created_at
            ) VALUES
                ('legacy-parent', 11, 21, 'Original legacy text', 'History', 'manual',
                 'rejected', 'safe', 0.1, 1, NULL, 4, NULL, 'demo', 'prompt one',
                 'too_generic', 'Be specific', 'operator', '2026-01-03T00:00:00+00:00'),
                ('legacy-child', 11, 21, 'Materially different child text', 'History', 'manual',
                 'pending', 'safe', 0.2, 2, 'legacy-parent', 4,
                 '2026-01-04T00:00:00+00:00', 'demo', 'prompt two', '', '', '',
                 '2026-01-03T00:01:00+00:00');
            INSERT INTO feedback VALUES (
                51, 'legacy-parent', 'rejected', 'too_generic', 'Be specific',
                'Prefer concrete details', 'operator', '2026-01-03T00:02:00+00:00'
            );
            INSERT INTO preferences VALUES (
                61, 'Prefer concrete details', 2.0, 51, 1,
                '2026-01-03T00:02:00+00:00', '2026-01-03T00:02:00+00:00'
            );
            INSERT INTO event_log VALUES (
                71, 'draft_generated', 'legacy-child', '{"legacy":true}',
                '2026-01-03T00:01:00+00:00'
            );
            """
        )
    return database


def test_fresh_database_seeds_one_account_with_owned_configuration(database):
    database.initialize()

    with database.connection() as conn:
        account = conn.execute("SELECT * FROM x_accounts").fetchone()
        assert account["id"] == 1
        assert account["live_posting_enabled"] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM startup_profile WHERE x_account_id = 1"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM content_contexts WHERE x_account_id = 1"
        ).fetchone()[0] == 10


def test_initialize_twice_does_not_duplicate_migrated_data(database):
    database.initialize()
    database.initialize()

    with database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM x_accounts").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schedule_slots WHERE x_account_id = 1"
        ).fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM schema_metadata").fetchone()[0] == 1


def test_legacy_rows_and_draft_lineage_survive_under_initial_account(legacy_database):
    legacy_database.initialize()

    with legacy_database.connection() as conn:
        account = conn.execute("SELECT * FROM x_accounts WHERE id = 1").fetchone()
        child = conn.execute("SELECT * FROM drafts WHERE id = 'legacy-child'").fetchone()
        parent = conn.execute("SELECT * FROM drafts WHERE id = 'legacy-parent'").fetchone()

        assert account["name"] == "Legacy Brand"
        assert account["timezone"] == "Europe/London"
        assert (parent["id"], parent["status"], parent["text"], parent["x_account_id"]) == (
            "legacy-parent",
            "rejected",
            "Original legacy text",
            1,
        )
        assert (child["id"], child["status"], child["parent_draft_id"], child["text"]) == (
            "legacy-child",
            "pending",
            "legacy-parent",
            "Materially different child text",
        )
        assert child["x_account_id"] == 1

        expected_rows = {
            "startup_profile": (1, 1),
            "content_contexts": (11, 1),
            "schedule_slots": (21, 1),
            "config_versions": (31, 1),
            "trends": (41, 1),
            "preferences": (61, 1),
            "event_log": (71, 1),
        }
        for table, expected in expected_rows.items():
            row = conn.execute(f"SELECT id, x_account_id FROM {table}").fetchone()
            assert tuple(row) == expected

        feedback = conn.execute("SELECT * FROM feedback WHERE id = 51").fetchone()
        assert feedback["draft_id"] == "legacy-parent"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_account_local_uniqueness_and_single_publishing_claim(database):
    database.initialize()

    with database.connection() as conn:
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO x_accounts
                (id, name, handle, enabled, live_posting_enabled, timezone, created_at, updated_at)
            VALUES (2, 'Second', 'second', 1, 0, 'UTC', ?, ?)
            """,
            (now, now),
        )
        context_id = conn.execute(
            """
            INSERT INTO content_contexts
                (x_account_id, name, purpose, tone, live_trends_required, instructions, enabled)
            VALUES (2, 'Second context', 'Test', 'plain', 0, '', 1)
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO schedule_slots
                (x_account_id, context_id, slot_number, time_local, enabled, last_run_date)
            VALUES (2, ?, 1, '09:00', 1, '')
            """,
            (context_id,),
        )
        conn.execute(
            "INSERT INTO config_versions (x_account_id, version, snapshot_json, created_at) VALUES (2, 1, '{}', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO preferences (x_account_id, rule, created_at, updated_at) VALUES (2, 'same rule', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO preferences (x_account_id, rule, created_at, updated_at) VALUES (1, 'same rule', ?, ?)",
            (now, now),
        )
        draft_id = "publishing-draft"
        conn.execute(
            """
            INSERT INTO drafts (
                id, x_account_id, context_id, text, topic, source_summary, status,
                safety_status, similarity_score, attempt, config_version,
                generator_provider, created_at
            ) VALUES (?, 1, 1, 'Safe draft', 'Test', 'manual', 'publishing',
                      'safe', 0.0, 1, 1, 'demo', ?)
            """,
            (draft_id, now),
        )
        conn.execute(
            """
            INSERT INTO publish_attempts
                (x_account_id, draft_id, attempt_number, status, origin, reviewer, created_at)
            VALUES (1, ?, 1, 'publishing', 'dashboard', 'admin', ?)
            """,
            (draft_id, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO publish_attempts
                    (x_account_id, draft_id, attempt_number, status, origin, reviewer, created_at)
                VALUES (1, ?, 2, 'publishing', 'slack', 'reviewer', ?)
                """,
                (draft_id, now),
            )
