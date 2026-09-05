# Durable Slack Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a verified Slack Approve or Reject click durable, immediately acknowledged, account-scoped, idempotent, restart-recoverable, observable, and capable of publishing exactly once through the selected account’s Buffer/X destination.

**Architecture:** Keep the signed HTTP callback as a thin transport adapter. After signature, binding, ownership, and payload validation, persist a sanitized `SlackActionJob`, acknowledge Slack, and let a dedicated single-process worker call the existing Pipeline. Repository compare-and-set operations provide durable claiming and recovery; Pipeline’s existing publication claim remains the final at-most-once publication guard.

**Tech Stack:** Python 3.11/3.12, FastAPI, Pydantic Settings, SQLite, asyncio, httpx, Jinja2, pytest, Docker Compose, Slack HTTP Interactivity, Buffer GraphQL.

**Spec:** `docs/superpowers/specs/2026-09-05-durable-slack-approval-design.md`

## Global Constraints

- Only a verified, explicit human dashboard or Slack action may approve a draft.
- Silence, notification delivery, callback acknowledgement, timeout, restart, or worker wake-up never counts as approval.
- Invalid signatures, stale timestamps, malformed payloads, unknown actions, disabled bindings, and account/draft mismatches create no action job.
- Never persist or log a raw Slack payload, webhook URL, Signing Secret, Slack signature, `response_url`, Buffer key, authorization header, or raw provider response.
- Preserve account isolation in every repository and Pipeline call.
- Preserve Pipeline’s final safety check, mode-drift check, and atomic publication claim.
- Keep `X_LIVE_POSTING=false` and `BUFFER_LIVE_POSTING=false` as safe defaults.
- Demo mode must run without Groq, Buffer, Slack, Telegram, or X credentials.
- This remains a one-instance SQLite deployment; do not claim horizontal or multi-worker safety.
- Add every behavior through a failing test first.

## File and responsibility map

- Modify `app/models.py`: define the public, secret-free `SlackActionJob` model.
- Modify `app/db.py`: create the action-job table and indexes idempotently.
- Modify `app/repository.py`: enqueue, read, claim, complete, fail, recover, and list action jobs using atomic SQLite transactions.
- Create `app/services/slack_actions.py`: process durable jobs through Pipeline and normalize terminal status.
- Modify `app/services/factory.py`: construct and expose the processor without moving lifecycle logic out of Pipeline.
- Modify `app/routes/slack.py`: validate and enqueue callbacks, then acknowledge immediately.
- Modify `app/main.py`: run and stop the recovery worker in the FastAPI lifespan.
- Modify `app/config.py`: define bounded worker poll and lease settings.
- Modify `app/routes/ui.py`: provide exact callback URLs and inbound action health to templates.
- Modify `app/templates/connections.html`: distinguish outbound webhook test from inbound callback health.
- Modify `app/templates/account_setup.html`: show the exact URL and the last callback state for the bound connection.
- Create `app/runtime_validation.py`: produce safe deployment warnings from non-secret settings.
- Modify `README.md`, `.env.example`, `docs/ARCHITECTURE.md`, `docs/LIFECYCLE.md`, `docs/API.md`, `docs/DEPLOYMENT.md`, and `docs/SECURITY_AND_LIMITATIONS.md`: document the implemented transport and one-instance deployment contract.
- Create `tests/test_slack_action_jobs.py`: repository durability, idempotency, claiming, and recovery.
- Modify `tests/test_slack_actions.py`: immediate callback acceptance and processor lifecycle behavior.
- Modify `tests/test_api.py`: callback URL and inbound/outbound dashboard state.
- Create `tests/test_runtime_validation.py`: HTTPS, stable-secret, and default-admin warnings.

---

### Task 0: Preserve the verified provider fixes already in the working tree

**Files:**
- Modify: `app/services/generation.py`
- Modify: `app/services/integrations.py`
- Test: `tests/test_generation.py`
- Test: `tests/test_connection_service.py`

**Interfaces:**
- Consumes: current uncommitted working-tree changes that repair organization-scoped Buffer testing, actionable Slack webhook failures, and GPT-OSS output budgeting.
- Produces: a clean committed baseline before durable Slack work begins.

- [ ] **Step 1: Review the existing diff without modifying it**

Run:

```powershell
git diff -- app/services/generation.py app/services/integrations.py tests/test_generation.py tests/test_connection_service.py
```

Expected: only the previously verified Buffer query, Slack error, GPT-OSS budget, and regression-test changes appear; no credential value appears.

- [ ] **Step 2: Re-run the focused provider tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_generation.py tests/test_connection_service.py
```

Expected: PASS.

- [ ] **Step 3: Commit only the four prerequisite files**

```powershell
git add app/services/generation.py app/services/integrations.py tests/test_generation.py tests/test_connection_service.py
git commit -m "fix: repair provider connection and generation checks"
```

---

### Task 1: Persist sanitized Slack action jobs atomically

**Files:**
- Modify: `app/models.py:166-204`
- Modify: `app/db.py:210-260`
- Modify: `app/db.py:_create_indexes`
- Modify: `app/repository.py:127-170`
- Modify: `app/repository.py` after integration-binding methods
- Create: `tests/test_slack_action_jobs.py`

**Interfaces:**
- Consumes: `Database.connection()`, `utc_now_iso()`, account/draft/connection foreign keys.
- Produces: `SlackActionJob`; `enqueue_slack_action(*, idempotency_key, connection_id, x_account_id, draft_id, action_id, expected_live, reviewer)`; `get_slack_action_job(x_account_id, job_id)`; `claim_next_slack_action(stale_before)`; `complete_slack_action(x_account_id, job_id, *, result_draft_id)`; `fail_slack_action(x_account_id, job_id, safe_error)`; `latest_slack_action(x_account_id, connection_id)`; and `list_slack_actions(x_account_id, limit=20)`.

- [ ] **Step 1: Write the failing model/schema/enqueue test**

Create `tests/test_slack_action_jobs.py` with this first behavior:

```python
from __future__ import annotations

from app.models import SlackActionJob


def test_enqueue_slack_action_is_idempotent_and_secret_free(repository):
    account = repository.list_accounts()[0]
    context = repository.list_contexts(account.id)[0]
    draft = repository.create_draft(
        account.id,
        context_id=context.id,
        schedule_id=None,
        text="A pending post for explicit review.",
        topic="review",
        source_summary="manual",
        status="pending",
        safety_status="safe",
        similarity_score=0.1,
        attempt=1,
        parent_draft_id=None,
        config_version=repository.current_config_version(account.id),
        expires_at=None,
        generator_provider="demo",
        prompt_snapshot="",
    )
    connection = repository.create_integration_connection(
        "slack", "Review", "encrypted-value"
    )

    first, first_created = repository.enqueue_slack_action(
        idempotency_key="digest-1",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )
    second, second_created = repository.enqueue_slack_action(
        idempotency_key="digest-1",
        connection_id=connection.id,
        x_account_id=account.id,
        draft_id=draft.id,
        action_id="approve_draft",
        expected_live=True,
        reviewer="slack:U123:reviewer",
    )

    assert isinstance(first, SlackActionJob)
    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert first.status == "pending"
    assert "encrypted-value" not in first.model_dump_json()
```

- [ ] **Step 2: Run the test and observe the missing model/method failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_action_jobs.py::test_enqueue_slack_action_is_idempotent_and_secret_free
```

Expected: FAIL because `SlackActionJob` or `enqueue_slack_action` does not exist.

- [ ] **Step 3: Add the public job model**

Add to `app/models.py`:

```python
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
```

- [ ] **Step 4: Add the idempotent table and indexes**

Add to `Database._create_schema()` in `app/db.py`:

```sql
CREATE TABLE IF NOT EXISTS slack_action_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    connection_id INTEGER NOT NULL REFERENCES integration_connections(id),
    x_account_id INTEGER NOT NULL REFERENCES x_accounts(id),
    draft_id TEXT NOT NULL,
    action_id TEXT NOT NULL CHECK (action_id IN ('approve_draft', 'reject_draft')),
    expected_live INTEGER NOT NULL,
    reviewer TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    result_draft_id TEXT,
    safe_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (x_account_id, draft_id) REFERENCES drafts(x_account_id, id),
    FOREIGN KEY (x_account_id, result_draft_id) REFERENCES drafts(x_account_id, id)
);
```

Add indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_slack_action_jobs_status_created
    ON slack_action_jobs(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_slack_action_jobs_connection_created
    ON slack_action_jobs(connection_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_slack_action_jobs_account_created
    ON slack_action_jobs(x_account_id, created_at DESC, id DESC);
```

- [ ] **Step 5: Add row conversion and enqueue/read methods**

Add to `app/repository.py`:

```python
def _slack_action_from_row(row: Any) -> SlackActionJob:
    return SlackActionJob(
        **{
            **dict(row),
            "expected_live": bool(row["expected_live"]),
        }
    )


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
    with self.database.connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO slack_action_jobs (
                idempotency_key, connection_id, x_account_id, draft_id,
                action_id, expected_live, reviewer, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                idempotency_key,
                connection_id,
                x_account_id,
                str(draft_id),
                action_id,
                int(expected_live),
                reviewer,
                utc_now_iso(),
            ),
        )
        created = cursor.rowcount == 1
        row = conn.execute(
            "SELECT * FROM slack_action_jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
    return _slack_action_from_row(row), created
```

Implement `get_slack_action_job(x_account_id, job_id)`, `latest_slack_action(x_account_id, connection_id)`, and `list_slack_actions(x_account_id, limit=20)` with these predicates and `_slack_action_from_row`:

```python
def get_slack_action_job(self, x_account_id: int, job_id: int) -> SlackActionJob:
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
```

Every public lookup includes `x_account_id = ?`; `latest_slack_action` also includes `connection_id = ?` so a shared Slack connection never causes one account's inbound status to appear in another account workspace.

- [ ] **Step 6: Run the enqueue test and verify green**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_action_jobs.py::test_enqueue_slack_action_is_idempotent_and_secret_free
```

Expected: PASS.

- [ ] **Step 7: Write failing atomic-claim and terminal-update tests**

Add tests that enqueue two jobs and assert:

```python
first = repository.claim_next_slack_action(stale_before="2000-01-01T00:00:00+00:00")
second = repository.claim_next_slack_action(stale_before="2000-01-01T00:00:00+00:00")

assert first is not None and first.status == "processing"
assert second is not None and second.id != first.id

completed = repository.complete_slack_action(
    first.x_account_id, first.id, result_draft_id=first.draft_id
)
failed = repository.fail_slack_action(
    second.x_account_id, second.id, "Publication mode changed"
)

assert completed.status == "completed"
assert completed.completed_at is not None
assert failed.status == "failed"
assert failed.safe_error == "Publication mode changed"
```

- [ ] **Step 8: Run the new tests and observe missing-method failures**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_action_jobs.py
```

Expected: FAIL because claim/complete/fail are absent.

- [ ] **Step 9: Implement compare-and-set claim and terminal updates**

Implement `claim_next_slack_action(stale_before: str) -> SlackActionJob | None` in one SQLite transaction:

```sql
SELECT * FROM slack_action_jobs
WHERE status = 'pending'
   OR (status = 'processing' AND claimed_at < ?)
ORDER BY created_at, id
LIMIT 1;
```

Then claim with:

```sql
UPDATE slack_action_jobs
SET status = 'processing', claimed_at = ?
WHERE id = ?
  AND (status = 'pending' OR (status = 'processing' AND claimed_at < ?));
```

Return the claimed row only when `rowcount == 1`. Implement terminal methods as account-scoped guarded updates from `processing`:

```python
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
    return self.get_slack_action_job(x_account_id, job_id)
```

Never pass a raw exception object or provider response to either method.

- [ ] **Step 10: Run persistence tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_action_jobs.py tests/test_connection_service.py
```

Expected: PASS.

```powershell
git add app/models.py app/db.py app/repository.py tests/test_slack_action_jobs.py
git commit -m "feat: persist durable Slack action jobs"
```

---

### Task 2: Process durable jobs through the account-scoped Pipeline

**Files:**
- Create: `app/services/slack_actions.py`
- Modify: `app/services/factory.py`
- Modify: `app/repository.py`
- Test: `tests/test_slack_action_jobs.py`

**Interfaces:**
- Consumes: Task 1 repository job methods, `Pipeline.approve`, `Pipeline.reject_and_regenerate`, `NotifierManager.notify_status`.
- Produces: `SlackActionProcessor.tick()`, `run_forever()`, `wake()`, and `stop()`; `Services.slack_actions`.

- [ ] **Step 1: Write a failing processor approval test**

Build a pending draft, enqueue an approval job, and use the real Pipeline with a mocked Buffer transport. Assert the processor produces a Slack-origin publish attempt:

```python
processed = processor.tick()
draft = repository.get_draft(account.id, pending.id)
job = repository.get_slack_action_job(account.id, job.id)

assert processed == 1
assert draft.status == "published"
assert draft.publisher_provider == "buffer"
assert job.status == "completed"
assert job.result_draft_id == draft.id
with repository.database.connection() as conn:
    attempt = conn.execute(
        "SELECT origin, reviewer FROM publish_attempts WHERE draft_id = ?",
        (draft.id,),
    ).fetchone()
assert dict(attempt) == {
    "origin": "slack",
    "reviewer": "slack:U123:reviewer",
}
```

- [ ] **Step 2: Run it and observe the missing processor failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_action_jobs.py -k processor
```

Expected: FAIL because `SlackActionProcessor` does not exist.

- [ ] **Step 3: Implement the processor boundary**

Create `app/services/slack_actions.py` with:

```python
class SlackActionProcessor:
    def __init__(self, settings, repository, pipeline, notifiers):
        self.settings = settings
        self.repository = repository
        self.pipeline = pipeline
        self.notifiers = notifiers
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()

    def tick(self) -> int:
        stale_before = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self.settings.slack_action_lease_seconds)
        ).isoformat(timespec="seconds")
        processed = 0
        while job := self.repository.claim_next_slack_action(stale_before):
            try:
                self._process(job)
            except Exception as exc:
                logger.exception(
                    "Slack action processing failed: job=%s account=%s draft=%s error=%s",
                    job.id,
                    job.x_account_id,
                    job.draft_id,
                    type(exc).__name__,
                )
                self.repository.fail_slack_action(
                    job.x_account_id,
                    job.id,
                    "Unexpected Slack action processing failure",
                )
            processed += 1
        return processed

    def _process(self, job: SlackActionJob) -> None:
        account = self.repository.get_account(job.x_account_id)
        draft = self.repository.get_draft(job.x_account_id, job.draft_id)
        if job.action_id == "approve_draft" and draft.status == "publishing":
            self.repository.fail_slack_action(
                job.x_account_id,
                job.id,
                "Publication was interrupted; operator reconciliation is required",
            )
            return
        if job.action_id == "approve_draft" and draft.status in {
            "published", "failed", "blocked"
        }:
            self.repository.complete_slack_action(
                job.x_account_id, job.id, result_draft_id=draft.id
            )
            return
        if job.action_id == "reject_draft" and draft.status in {
            "rejected", "needs_guidance"
        }:
            self.repository.complete_slack_action(
                job.x_account_id, job.id, result_draft_id=draft.id
            )
            return
        try:
            if job.action_id == "approve_draft":
                result = self.pipeline.approve(
                    job.x_account_id,
                    job.draft_id,
                    reviewer=job.reviewer,
                    origin="slack",
                    expected_live_posting=job.expected_live,
                )
            else:
                result = self.pipeline.reject_and_regenerate(
                    job.x_account_id,
                    job.draft_id,
                    reason="other",
                    notes="Rejected from Slack",
                    reviewer=job.reviewer,
                    origin="slack",
                )
            self.repository.complete_slack_action(
                job.x_account_id,
                job.id,
                result_draft_id=result.id if result is not None else job.draft_id,
            )
        except PipelineError as exc:
            safe_error = str(exc)
            self.repository.fail_slack_action(
                job.x_account_id, job.id, safe_error
            )
            self.notifiers.notify_status(draft, account, safe_error)
```

Add safe structured events for claim, completion, and failure using account ID, job ID, action ID, draft ID, result status, and provider only. The broad exception boundary above keeps the worker loop alive while normalizing unexpected failures; its log records only identifiers and the exception type, never `str(exc)`.

- [ ] **Step 4: Implement wakeable worker lifecycle**

Add:

```python
async def run_forever(self) -> None:
    self._stop_event.clear()
    while not self._stop_event.is_set():
        try:
            await asyncio.to_thread(self.tick)
        except Exception as exc:
            logger.exception(
                "Slack action worker tick failed: %s", type(exc).__name__
            )
        self._wake_event.clear()
        try:
            await asyncio.wait_for(
                self._wake_event.wait(),
                timeout=self.settings.slack_action_poll_seconds,
            )
        except TimeoutError:
            continue

def wake(self) -> None:
    self._wake_event.set()

def stop(self) -> None:
    self._stop_event.set()
    self._wake_event.set()
```

- [ ] **Step 5: Wire it into the service factory**

Extend `Services` with `slack_actions: SlackActionProcessor`. Construct it after Pipeline so it consumes Pipeline without creating a cycle:

```python
slack_actions = SlackActionProcessor(settings, repository, pipeline, notifiers)
return Services(
    pipeline=pipeline,
    generator=generator,
    trend_collector=trends,
    safety_guard=safety,
    similarity_guard=similarity,
    feedback_engine=feedback,
    notifiers=notifiers,
    publishers=publishers,
    integrations=integrations,
    slack_actions=slack_actions,
)
```

- [ ] **Step 6: Add recovery and non-republication tests**

Add deterministic tests that:

- claim a job, set `claimed_at` older than the lease, leave its draft pending, then assert `tick()` safely processes it;
- leave its draft `published`, then assert recovery marks the job completed without another publisher request;
- leave its draft `publishing`, then assert recovery marks the job failed with reconciliation required and makes zero publisher requests;
- enqueue the same body twice and assert one publication attempt.

- [ ] **Step 7: Run processor/pipeline tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_action_jobs.py tests/test_pipeline.py tests/test_integrations.py
```

Expected: PASS.

```powershell
git add app/services/slack_actions.py app/services/factory.py app/repository.py tests/test_slack_action_jobs.py
git commit -m "feat: process Slack approvals durably"
```

---

### Task 3: Acknowledge Slack immediately after durable acceptance

**Files:**
- Modify: `app/routes/slack.py`
- Modify: `tests/test_slack_actions.py`

**Interfaces:**
- Consumes: `Repository.enqueue_slack_action(*, idempotency_key, connection_id, x_account_id, draft_id, action_id, expected_live, reviewer)` and `SlackActionProcessor.wake()`.
- Produces: HTTP 200 accepted response containing `job_id`, without calling Pipeline or Buffer in the request handler.

- [ ] **Step 1: Replace the synchronous-route expectation with a failing acceptance test**

In `tests/test_slack_actions.py`, construct a signed valid action with the worker disabled. Assert the route returns 200, leaves the draft pending, and persists a pending job:

```python
app = create_app(
    settings=_configured_settings(settings),
    start_scheduler=False,
    start_slack_worker=False,
)
with TestClient(app) as client:
    account = app.state.repository.list_accounts()[0]
    connection = _connect_slack(app, account.id)
    draft = _pending(app, account.id)
    body, headers = _signed_request(
        _slack_payload("approve_draft", account.id, draft.id),
        "signing-secret",
    )

    response = client.post(
        f"/integrations/slack/{connection.id}/actions",
        content=body,
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["text"] == "Approval received and queued for processing"
    assert app.state.repository.get_draft(account.id, draft.id).status == "pending"
    jobs = app.state.repository.list_slack_actions(account.id)
    assert len(jobs) == 1 and jobs[0].status == "pending"
```

- [ ] **Step 2: Run the focused test and verify it fails because approval is synchronous**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_actions.py -k queued_for_processing
```

Expected: FAIL because the current route publishes before returning and creates no job.

- [ ] **Step 3: Refactor the route to enqueue only**

Keep the current order of signature, payload, binding, and draft ownership validation. Replace direct Pipeline calls with:

```python
idempotency_key = hashlib.sha256(
    f"{connection_id}:".encode() + body
).hexdigest()
job, created = repository.enqueue_slack_action(
    idempotency_key=idempotency_key,
    connection_id=connection_id,
    x_account_id=x_account_id,
    draft_id=draft_id,
    action_id=action_id,
    expected_live=expected_live,
    reviewer=reviewer,
)
repository.log_event(
    x_account_id,
    "slack_action_received" if created else "slack_action_duplicate",
    draft_id,
    {"job_id": job.id, "action_id": action_id},
)
request.app.state.services.slack_actions.wake()
return {
    "response_type": "ephemeral",
    "replace_original": False,
    "text": (
        "Approval received and queued for processing"
        if action_id == "approve_draft"
        else "Rejection received and queued for processing"
    ),
    "job_id": job.id,
}
```

Validate `expected_live` as a boolean for both actions so the persisted model is complete. Do not store `payload`, `body`, `response_url`, or request headers.

- [ ] **Step 4: Strengthen negative and duplicate callback tests**

For invalid signature, stale signature, malformed body, unsupported action, connection mismatch, and draft mismatch, add:

```python
assert app.state.repository.list_slack_actions(account.id) == []
```

For two identical signed deliveries, assert one job and one shared `job_id`.

- [ ] **Step 5: Run route security tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_actions.py tests/test_csrf.py
```

Expected: PASS.

```powershell
git add app/routes/slack.py tests/test_slack_actions.py
git commit -m "feat: acknowledge Slack actions after durable enqueue"
```

---

### Task 4: Start and recover the Slack action worker in deployment

**Files:**
- Modify: `app/config.py:20-45`
- Modify: `app/main.py:28-58`
- Modify: `.env.example`
- Modify: `tests/test_slack_actions.py`

**Interfaces:**
- Consumes: `Services.slack_actions.run_forever()`, `stop()`.
- Produces: `Settings.slack_action_worker_enabled`, `slack_action_poll_seconds`, `slack_action_lease_seconds`, and `create_app(settings=None, start_scheduler=None, start_slack_worker=None)`.

- [ ] **Step 1: Write a failing lifespan recovery test**

Persist a pending action job before entering `TestClient(app)`, then enable the worker and assert the job reaches `completed` during lifespan polling. Use a short test-only poll interval and poll persisted state with a monotonic deadline rather than sleeping a fixed duration.

```python
deadline = time.monotonic() + 2
while time.monotonic() < deadline:
    if repository.get_slack_action_job(account.id, job.id).status == "completed":
        break
    time.sleep(0.01)
assert repository.get_slack_action_job(account.id, job.id).status == "completed"
```

- [ ] **Step 2: Run it and observe that no worker starts**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_actions.py -k lifespan_recovers
```

Expected: FAIL with the job still pending.

- [ ] **Step 3: Add bounded settings**

Add to `Settings`:

```python
slack_action_worker_enabled: bool = True
slack_action_poll_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
slack_action_lease_seconds: int = Field(default=300, ge=30, le=3600)
```

Add safe defaults to `.env.example`:

```dotenv
SLACK_ACTION_WORKER_ENABLED=true
SLACK_ACTION_POLL_SECONDS=1
SLACK_ACTION_LEASE_SECONDS=300
```

- [ ] **Step 4: Run the processor in FastAPI lifespan**

Extend `create_app`:

```python
def create_app(
    settings: Settings | None = None,
    start_scheduler: bool | None = None,
    start_slack_worker: bool | None = None,
) -> FastAPI:
```

Resolve `should_start_slack_worker` from the explicit argument or setting. In lifespan, create distinct scheduler and Slack worker tasks, stop both services in `finally`, and await both with `asyncio.gather`. Expose the processor as `app.state.slack_actions` for diagnostics.

- [ ] **Step 5: Run lifespan and repeated-delivery tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_actions.py tests/test_slack_action_jobs.py
```

Expected: PASS with one publication attempt for repeated callbacks.

- [ ] **Step 6: Commit worker startup**

```powershell
git add app/config.py app/main.py .env.example tests/test_slack_actions.py
git commit -m "feat: recover Slack actions in app lifespan"
```

---

### Task 5: Show exact callback configuration and inbound health

**Files:**
- Modify: `app/routes/ui.py:38-75`
- Modify: `app/routes/ui.py:174-198`
- Modify: `app/templates/connections.html`
- Modify: `app/templates/account_setup.html`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `Repository.latest_slack_action(x_account_id, connection_id)`, `Settings.base_url`, saved connection IDs.
- Produces: exact `slack_callback_urls` mapping and `latest_slack_actions` mapping in template context.

- [ ] **Step 1: Write failing authenticated-page tests**

Add tests that save Slack connection ID 1 and assert:

```python
assert (
    "https://review.example/integrations/slack/1/actions"
    in response.text
)
assert "Disable Socket Mode" in response.text
assert "Test outbound Slack message" in response.text
assert "Inbound actions: Not verified" in response.text
```

After inserting a completed job, assert the account setup page contains `Inbound actions: Completed` and the job’s safe timestamp, but not encrypted credentials or the idempotency digest.

- [ ] **Step 2: Run the focused page tests and observe missing content**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_api.py -k "callback_url or inbound_action_health"
```

Expected: FAIL because templates show a generic `<connection-id>` URL and no inbound state.

- [ ] **Step 3: Add exact URL and job-health context**

In UI context construction:

```python
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
```

Pass both mappings to the account setup and connections templates.

- [ ] **Step 4: Render separate outbound and inbound health**

For each Slack connection render:

- exact copyable callback URL;
- “Socket Mode must be Off for this app”;
- “Interactivity & Shortcuts must be On”;
- outbound webhook test state from `AccountIntegration.last_test_*`;
- inbound job state from `latest_slack_actions[connection.id]`;
- no raw exception or credential data.

Rename the button to `Test outbound Slack message` so a successful webhook POST is not mistaken for callback readiness.

- [ ] **Step 5: Run dashboard tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_api.py tests/test_connection_service.py
```

Expected: PASS.

```powershell
git add app/routes/ui.py app/templates/connections.html app/templates/account_setup.html tests/test_api.py
git commit -m "feat: expose Slack callback readiness"
```

---

### Task 6: Add safe deployment validation and exact operating instructions

**Files:**
- Create: `app/runtime_validation.py`
- Modify: `app/main.py`
- Create: `tests/test_runtime_validation.py`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LIFECYCLE.md`
- Modify: `docs/API.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/SECURITY_AND_LIMITATIONS.md`

**Interfaces:**
- Consumes: non-secret `Settings` values only.
- Produces: `deployment_warnings(settings: Settings) -> list[str]` and operator documentation for HTTP callbacks.

- [ ] **Step 1: Write failing warning tests**

Create `tests/test_runtime_validation.py`:

```python
from app.config import Settings
from app.runtime_validation import deployment_warnings


def test_live_mode_warns_about_insecure_public_configuration():
    settings = Settings(
        app_mode="live",
        base_url="http://localhost:8000",
        admin_username="admin",
        admin_password="change-me",
        app_encryption_key="",
        app_csrf_secret="",
        database_path=":memory:",
        scheduler_enabled=False,
    )

    warnings = deployment_warnings(settings)

    assert any("HTTPS" in item for item in warnings)
    assert any("ADMIN_PASSWORD" in item for item in warnings)
    assert any("APP_ENCRYPTION_KEY" in item for item in warnings)
    assert any("APP_CSRF_SECRET" in item for item in warnings)


def test_demo_defaults_remain_credential_free():
    settings = Settings(
        app_mode="demo",
        database_path=":memory:",
        scheduler_enabled=False,
    )
    assert deployment_warnings(settings) == []
```

- [ ] **Step 2: Run them and observe the missing module failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_validation.py
```

Expected: FAIL because `app.runtime_validation` does not exist.

- [ ] **Step 3: Implement non-secret warnings**

Create `app/runtime_validation.py`:

```python
from app.config import Settings


def deployment_warnings(settings: Settings) -> list[str]:
    if settings.demo_mode:
        return []
    warnings: list[str] = []
    if not settings.base_url.lower().startswith("https://"):
        warnings.append("BASE_URL must use stable HTTPS for Slack callbacks")
    if settings.admin_password == "change-me":
        warnings.append("ADMIN_PASSWORD still uses the unsafe default")
    if not settings.app_encryption_key:
        warnings.append("APP_ENCRYPTION_KEY is missing")
    if not settings.app_csrf_secret:
        warnings.append("APP_CSRF_SECRET is missing")
    return warnings
```

Log each warning at startup without dumping the Settings object.

- [ ] **Step 4: Update deployment documentation with exact Slack steps**

Document two explicit environments:

```text
Local/ngrok:
1. Start uvicorn on port 8000.
2. Start ngrok for http://localhost:8000.
3. Set BASE_URL to the exact HTTPS ngrok origin and restart uvicorn.
4. In the same Slack app as the webhook: Socket Mode Off.
5. Interactivity On; Request URL copied from Connections.

Deployed single instance:
1. Stable HTTPS domain and persistent /data volume.
2. Stable APP_ENCRYPTION_KEY and APP_CSRF_SECRET.
3. One container/application instance.
4. Configure the exact deployed callback URL in Slack.
5. Verify outbound test, inbound click receipt, mocked/dry-run approval, then enable both live switches.
```

State that a webhook test proves outbound posting only, that changing the public origin requires updating Slack, and that Socket Mode routes no interactions to HTTP.

- [ ] **Step 5: Run validation tests and documentation-sensitive API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_runtime_validation.py tests/test_api.py tests/test_slack_actions.py
```

Expected: PASS.

- [ ] **Step 6: Commit deployment hardening**

```powershell
git add app/runtime_validation.py app/main.py tests/test_runtime_validation.py README.md .env.example docs/ARCHITECTURE.md docs/LIFECYCLE.md docs/API.md docs/DEPLOYMENT.md docs/SECURITY_AND_LIMITATIONS.md
git commit -m "docs: harden Slack callback deployment"
```

---

### Task 7: Verify the complete lifecycle and deployment artifact

**Files:**
- Modify only if a verification failure reveals a specific regression.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: evidence that callbacks, publication safety, demo mode, compilation, and Docker configuration are valid.

- [ ] **Step 1: Run focused Slack, Pipeline, publisher, and security tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_slack_action_jobs.py tests/test_slack_actions.py tests/test_pipeline.py tests/test_integrations.py tests/test_connection_service.py tests/test_csrf.py
```

Expected: PASS.

- [ ] **Step 2: Run the entire test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS with zero failures.

- [ ] **Step 3: Compile application and scripts**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall app scripts
```

Expected: exit code 0.

- [ ] **Step 4: Run the credential-free safety demo**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\demo_flow.py
```

Expected: two independent accounts publish through `dry_run`, and output states that both required explicit human approval.

- [ ] **Step 5: Validate Docker Compose**

Run:

```powershell
docker compose config
```

Expected: exit code 0, one application service, persistent data volume, no rendered secret values committed to source.

- [ ] **Step 6: Run local HTTP smoke checks**

Start with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify `/health` returns HTTP 200, authenticated `/` and `/connections` render, the exact connection callback URL is visible, generation creates a pending draft, and an invalid signed callback creates no job.

- [ ] **Step 7: Perform human live acceptance without synthetic approval**

With the operator present:

1. Confirm the X Agent Slack app has Socket Mode Off.
2. Set Interactivity Request URL to the exact dashboard URL.
3. Generate a fresh pending draft.
4. Confirm the Slack review message names the correct X handle and LIVE/DRY-RUN mode.
5. The human clicks Approve once.
6. Confirm dashboard audit order: `slack_action_received`, `slack_action_processing`, `draft_published` or `publish_failed`, then `slack_action_completed` or `slack_action_failed`.
7. For live mode, confirm the persisted Buffer external ID and X URL; do not issue a second approval as a test.

- [ ] **Step 8: Review the diff for secret leakage and architecture drift**

Run:

```powershell
git diff --check
git status --short
git log --oneline -8
```

Search tracked files for known credential prefixes without printing `.env`:

```powershell
rg -n "hooks\.slack\.com/services/[A-Za-z0-9]|xapp-|xox[baprs]-|Bearer [A-Za-z0-9]" app tests docs README.md .env.example
```

Expected: no real credential value; test-only domains and literal documentation placeholders are acceptable after inspection.

- [ ] **Step 9: Commit any verification-only correction, otherwise leave history unchanged**

If no source changed, do not create an empty commit. If a focused regression fix was required, commit only its implementation and regression test with a message naming that behavior.

## Completion gate

Do not call the work complete until all automated verification passes and the human live acceptance click reaches the callback. Automated signed diagnostics may verify routing and security, but they never substitute for the explicit human approval required to publish.
