# Multi-Account X Workspaces and Slack Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one trusted operator manage two or three fully independent X accounts with encrypted reusable Buffer and Slack connections, synchronized dashboard/Slack review, and account-safe publication.

**Architecture:** Keep the existing FastAPI, SQLite, repository, Pipeline, scheduler, notifier, and publisher boundaries. Introduce `x_account_id` as an explicit lifecycle key, store provider credentials in reusable encrypted connection records, and make both dashboard and signed Slack actions call the same atomic Pipeline transitions.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, Jinja2, httpx, Pydantic, `cryptography.fernet`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-multi-account-slack-review-design.md`

## Global Constraints

- Never publish without an explicit dashboard or verified Slack approval action.
- Silence and timeout never count as approval.
- `never_reveal`, feedback, similarity history, sources, schedules, notifications, and publication state remain isolated by `x_account_id`.
- Rejected, expired, edited, blocked, and failed originals remain queryable.
- Regeneration must be materially different and stop at the account's attempt limit.
- `BUFFER_LIVE_POSTING=false` remains the deployment default, and each account also defaults to live posting disabled.
- Demo mode must run without Groq, Buffer, Slack, Telegram, or WhatsApp credentials.
- Routes remain transport-only; Pipeline owns lifecycle sequencing and Repository owns persistence.
- Every behavior change begins with a failing test and completes a red-green-refactor cycle.
- WhatsApp, human-user roles, SSO, Postgres, distributed workers, analytics, and media posting are outside this plan.

---

## File map

- `app/services/crypto.py`: authenticated encryption and decryption of provider credential dictionaries.
- `app/services/integrations.py`: connection creation, masking, account binding, connection resolution, and non-publishing connection tests.
- `app/services/csrf.py`: signed dashboard form tokens.
- `app/models.py`: account, connection, binding, publication-attempt, and account-owned model fields.
- `app/db.py`: idempotent migration from the singleton schema and new seed behavior.
- `app/repository.py`: account-scoped persistence, connection records, atomic publish claims, and account-copy operations.
- `app/services/pipeline.py`: account-scoped generation and review sequencing.
- `app/services/feedback.py`, `app/services/trends.py`: account-local retrieval.
- `app/services/publishers.py`: resolved per-account Buffer credentials and dual live-mode gates.
- `app/services/notifiers.py`: dynamic per-account Slack delivery.
- `app/routes/slack.py`: signed interactive Slack callbacks.
- `app/routes/api.py`, `app/routes/ui.py`: account-scoped management and form transport.
- `app/templates/accounts.html`, `app/templates/account_dashboard.html`, `app/templates/account_setup.html`, `app/templates/connections.html`: low-work account UI.
- `app/templates/base.html`, `app/static/app.css`, `app/static/app.js`: account identity, mode, responsive layout, confirmation, and navigation.

---

### Task 1: Credential encryption and account models

**Files:**
- Create: `app/services/crypto.py`
- Create: `tests/test_crypto.py`
- Modify: `app/config.py`
- Modify: `app/models.py`
- Modify: `requirements.txt`
- Modify: `pyproject.toml`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `Settings.app_encryption_key: str`.
- Produces: `CredentialCipher.encrypt(dict[str, str]) -> str`, `CredentialCipher.decrypt(str) -> dict[str, str]`, `XAccount`, `IntegrationConnection`, `AccountIntegration`, `PublishAttempt`.

- [ ] **Step 1: Write failing encryption and model tests**

```python
from cryptography.fernet import Fernet
import pytest

from app.services.crypto import CredentialCipher, CredentialError


def test_credentials_round_trip_without_plaintext_in_token():
    cipher = CredentialCipher(Fernet.generate_key().decode())
    token = cipher.encrypt({"api_key": "buffer-secret", "channel_id": "channel-1"})
    assert "buffer-secret" not in token
    assert cipher.decrypt(token) == {"api_key": "buffer-secret", "channel_id": "channel-1"}


def test_wrong_key_cannot_decrypt_credentials():
    token = CredentialCipher(Fernet.generate_key().decode()).encrypt({"token": "secret"})
    with pytest.raises(CredentialError, match="locked"):
        CredentialCipher(Fernet.generate_key().decode()).decrypt(token)
```

- [ ] **Step 2: Verify the tests fail because the encryption service and models do not exist**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_crypto.py -q`

Expected: collection fails with `ModuleNotFoundError: app.services.crypto`.

- [ ] **Step 3: Add the dependency, settings, models, and minimal encryption service**

```python
import json

from cryptography.fernet import Fernet, InvalidToken


class CredentialError(RuntimeError):
    pass


class CredentialCipher:
    def __init__(self, key: str):
        if not key.strip():
            raise CredentialError("Integration credentials are locked: APP_ENCRYPTION_KEY is missing")
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise CredentialError("Integration credentials are locked: APP_ENCRYPTION_KEY is invalid") from exc

    def encrypt(self, credentials: dict[str, str]) -> str:
        payload = json.dumps(credentials, sort_keys=True, separators=(",", ":")).encode()
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, encrypted: str) -> dict[str, str]:
        try:
            value = json.loads(self._fernet.decrypt(encrypted.encode("ascii")))
        except (InvalidToken, ValueError, UnicodeEncodeError, json.JSONDecodeError) as exc:
            raise CredentialError("Integration credentials are locked: encryption key mismatch") from exc
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise CredentialError("Integration credentials are locked: invalid encrypted payload")
        return value
```

Add `app_encryption_key: str = ""` and `app_csrf_secret: str = ""` to `Settings`. Define models with these exact ownership fields:

```python
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
```

Add `x_account_id: int` to `StartupProfile`, `ContentContext`, `ScheduleSlot`, `TrendItem`, `Draft`, and `LearnedPreference`. Move timezone ownership to `XAccount`; remove `timezone` from `StartupProfile` after Task 2 migrates the legacy value.

- [ ] **Step 4: Run focused tests and the existing model tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_crypto.py tests/test_repository.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the encryption foundation**

```bash
git add .env.example pyproject.toml requirements.txt app/config.py app/models.py app/services/crypto.py tests/test_crypto.py
git commit -m "feat: add encrypted integration credential foundation"
```

---

### Task 2: Idempotent account-scoped database migration

**Files:**
- Create: `tests/test_account_migration.py`
- Modify: `app/db.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: legacy tables created by the current `Database.initialize()`.
- Produces: account-scoped schema, initial account ID `1`, `Database.initialize()` idempotency.

- [ ] **Step 1: Write failing fresh-database and legacy-migration tests**

```python
def test_fresh_database_seeds_one_account_with_owned_configuration(database):
    database.initialize()
    with database.connection() as conn:
        account = conn.execute("SELECT * FROM x_accounts").fetchone()
        assert account["id"] == 1
        assert conn.execute("SELECT COUNT(*) FROM startup_profile WHERE x_account_id = 1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM content_contexts WHERE x_account_id = 1").fetchone()[0] == 10


def test_initialize_twice_does_not_duplicate_migrated_data(database):
    database.initialize()
    database.initialize()
    with database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM x_accounts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM schedule_slots WHERE x_account_id = 1").fetchone()[0] == 10
```

Add a fixture that creates the legacy schema and a draft before running the new initializer; assert the draft ID, status, parent link, and text survive under account `1`.

- [ ] **Step 2: Verify migration tests fail on missing account tables and columns**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_account_migration.py -q`

Expected: failure mentioning missing `x_accounts` or `x_account_id`.

- [ ] **Step 3: Implement schema versioning and account migration**

Add `schema_metadata(version INTEGER NOT NULL)` and an idempotent `_migrate_account_scope(conn)` called before seeding. Rebuild tables whose old constraints prevent per-account rows: `startup_profile`, `schedule_slots`, `config_versions`, and `preferences`. Move the legacy profile timezone into the initial `x_accounts` row. Add `x_account_id NOT NULL DEFAULT 1` to other account-owned tables and account-aware indexes.

The rebuilt uniqueness constraints are:

```sql
UNIQUE(x_account_id, slot_number)
UNIQUE(x_account_id, version)
UNIQUE(x_account_id, rule)
```

Add `integration_connections`, `account_integrations`, and `publish_attempts`. Create a partial unique index preventing more than one `publishing` attempt for a draft. Preserve all existing foreign keys and data while rebuilding.

- [ ] **Step 4: Update fixtures to expose the seeded account and run database tests**

```python
@pytest.fixture()
def x_account(repository):
    return repository.list_accounts()[0]
```

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_account_migration.py tests/test_repository.py tests/test_smoke.py -q`

Expected: all selected tests pass and initialization remains repeatable.

- [ ] **Step 5: Commit the migration**

```bash
git add app/db.py tests/conftest.py tests/test_account_migration.py
git commit -m "feat: migrate prototype data to account workspaces"
```

---

### Task 3: Account and integration persistence APIs

**Files:**
- Create: `tests/test_accounts.py`
- Modify: `app/repository.py`
- Modify: `app/models.py`

**Interfaces:**
- Consumes: Task 2 account-scoped schema.
- Produces: account CRUD/copy methods, account-scoped content methods, encrypted connection storage, account bindings.

- [ ] **Step 1: Write failing account-isolation and connection-persistence tests**

```python
def test_two_accounts_have_independent_profiles_contexts_and_schedules(repository):
    first = repository.list_accounts()[0]
    second = repository.create_account(name="Second Brand", handle="second", timezone="UTC", copy_from_id=first.id)
    repository.update_profile(second.id, {"domain": "Second account domain"})
    assert repository.get_profile(first.id).domain != repository.get_profile(second.id).domain
    assert len(repository.list_contexts(first.id)) == 10
    assert len(repository.list_contexts(second.id)) == 10


def test_connection_public_model_never_contains_ciphertext(repository):
    item = repository.create_integration_connection("buffer", "Main Buffer", "encrypted-token")
    assert item.credentials_configured is True
    assert "encrypted" not in item.model_dump()
```

Also test copy independence, handle normalization and uniqueness, disabled accounts, shared connection bindings, different target IDs, and account-scoped draft lookup rejection.

- [ ] **Step 2: Verify failures identify missing account-aware repository signatures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_accounts.py -q`

Expected: failures for missing `list_accounts`, `create_account`, or account arguments.

- [ ] **Step 3: Implement exact repository interfaces**

Add the exact signatures `list_accounts(enabled_only=False)`, `get_account(x_account_id)`, `create_account(name, handle, timezone, copy_from_id=None)`, `update_account(x_account_id, updates)`, `get_profile(x_account_id)`, `update_profile(x_account_id, updates)`, `list_contexts(x_account_id, enabled_only=False)`, `list_schedules(x_account_id)`, `get_draft(x_account_id, draft_id)`, `list_drafts(x_account_id, limit=100, statuses=None)`, `create_integration_connection(provider, label, encrypted_credentials)`, `get_encrypted_credentials(connection_id)`, `bind_account_integration(x_account_id, provider, connection_id, target_id, enabled=True)`, and `get_account_integration(x_account_id, provider)`.

Use account ownership in the SQL itself, never a post-query check:

```python
def get_draft(self, x_account_id: int, draft_id: str) -> Draft:
    with self.database.connection() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE x_account_id = ? AND id = ?",
            (x_account_id, str(draft_id)),
        ).fetchone()
    if row is None:
        raise KeyError(f"Unknown draft for account {x_account_id}: {draft_id}")
    return _draft_from_row(row)
```

Update every existing content method to require and filter by account. When `create_account` receives a non-null `copy_from_id`, it copies profile values, contexts, and schedules in one transaction but copies no drafts, trends, feedback, preferences, versions beyond a new initial snapshot, events, or integration bindings.

- [ ] **Step 4: Run repository and migration coverage**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_accounts.py tests/test_account_migration.py tests/test_repository.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit account persistence**

```bash
git add app/models.py app/repository.py tests/test_accounts.py
git commit -m "feat: scope repository data to X accounts"
```

---

### Task 4: Connection service and provider-safe testing

**Files:**
- Create: `app/services/integrations.py`
- Create: `tests/test_connection_service.py`
- Modify: `app/services/factory.py`

**Interfaces:**
- Consumes: `CredentialCipher`, repository connection/binding methods, `Settings`.
- Produces: `IntegrationService.save_connection`, `bind`, `resolve_buffer`, `resolve_slack`, `get_slack_connection`, `verify_slack_signature`, `test_buffer`, `test_slack`, `disconnect`.

- [ ] **Step 1: Write failing service tests using `httpx.MockTransport`**

```python
def test_shared_buffer_connection_resolves_different_channels(integration_service, accounts):
    connection = integration_service.save_connection("buffer", "Shared", {"api_key": "secret"})
    integration_service.bind(accounts[0].id, "buffer", connection.id, "channel-a")
    integration_service.bind(accounts[1].id, "buffer", connection.id, "channel-b")
    assert integration_service.resolve_buffer(accounts[0].id).channel_id == "channel-a"
    assert integration_service.resolve_buffer(accounts[1].id).channel_id == "channel-b"


def test_buffer_connection_test_never_calls_create_post(integration_service, captured_requests):
    result = integration_service.test_buffer(account_id=1)
    assert result.success is True
    assert all("createPost" not in request.content.decode() for request in captured_requests)
```

Test locked keys, disabled bindings, Slack test labeling, secret masking, replacement, and disconnection without deleting history.

- [ ] **Step 2: Verify tests fail on the missing integration service**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_connection_service.py -q`

Expected: collection failure for `app.services.integrations`.

- [ ] **Step 3: Implement the service with immutable public outputs**

```python
@dataclass(frozen=True)
class BufferTarget:
    api_key: str
    channel_id: str

@dataclass(frozen=True)
class SlackTarget:
    webhook_url: str
    signing_secret: str
    connection_id: int
```

Implement exact `IntegrationService` methods named in the Interfaces block. Credential resolution follows this concrete pattern:

```python
def resolve_buffer(self, x_account_id: int) -> BufferTarget | None:
    binding = self.repository.get_account_integration(x_account_id, "buffer")
    if binding is None or not binding.enabled:
        return None
    credentials = self.cipher.decrypt(
        self.repository.get_encrypted_credentials(binding.connection_id)
    )
    api_key = credentials.get("api_key", "").strip()
    if not api_key or not binding.target_id.strip():
        raise IntegrationError("Buffer connection requires an API key and channel ID")
    return BufferTarget(api_key=api_key, channel_id=binding.target_id.strip())
```

Define `IntegrationError(RuntimeError)` in this module. `get_slack_connection(connection_id)` decrypts a Slack connection by its own ID for signed callback verification and rejects other providers. `verify_slack_signature(signing_secret, timestamp, supplied_signature, body)` rejects non-numeric timestamps, timestamps more than 300 seconds from current UTC time, and signatures that fail `secrets.compare_digest` against `v0=` plus the HMAC-SHA256 digest of `v0:{timestamp}:{body}`.

Do not instantiate `CredentialCipher` during application startup when `APP_ENCRYPTION_KEY` is empty. `IntegrationService` stores the optional key and creates the cipher only inside Connect, Test, Replace, or Resolve operations; this preserves credential-free demo startup.

Reject unsupported providers and incomplete credentials. Buffer testing uses a read-only account/channel query. Slack testing sends exactly one message prefixed `Startup X Agent connection test`.

- [ ] **Step 4: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_crypto.py tests/test_connection_service.py -q`

Expected: all selected tests pass with no plaintext secrets in captured logs or model dumps.

- [ ] **Step 5: Commit connection management**

```bash
git add app/services/integrations.py app/services/factory.py tests/test_connection_service.py
git commit -m "feat: manage reusable Buffer and Slack connections"
```

---

### Task 5: Account-local generation, feedback, similarity, and scheduling

**Files:**
- Create: `tests/test_account_pipeline.py`
- Modify: `app/services/pipeline.py`
- Modify: `app/services/feedback.py`
- Modify: `app/services/trends.py`
- Modify: `app/services/scheduler.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_generation.py`
- Modify: `tests/test_guards_and_feedback.py`

**Interfaces:**
- Consumes: account-scoped repository APIs.
- Produces: account-explicit lifecycle signatures and multi-account scheduler tick.

- [ ] **Step 1: Write failing cross-account behavior tests**

```python
def test_generation_memory_and_similarity_are_account_local(pipeline, repository, two_accounts):
    first, second = two_accounts
    rejected = pipeline.generate_draft(first.id, context_id=repository.list_contexts(first.id)[0].id)
    pipeline.reject_and_regenerate(first.id, rejected.id, reason="too_generic")
    second_draft = pipeline.generate_draft(second.id, context_id=repository.list_contexts(second.id)[0].id)
    assert second_draft.x_account_id == second.id
    assert rejected.text not in pipeline.feedback_engine.memory_bundle(second.id)[1]


def test_scheduler_runs_each_account_in_its_own_timezone(settings, repository, pipeline, two_accounts):
    scheduler = SchedulerService(settings, repository, pipeline)
    result = scheduler.tick(datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc))
    assert {draft.x_account_id for draft in result.generated} == {two_accounts[0].id, two_accounts[1].id}
```

Test cross-account parent IDs, rejection, edit, timeout, disabled account behavior, attempt limits, and account-labelled notification inputs.

- [ ] **Step 2: Verify the new signatures fail before service changes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_account_pipeline.py -q`

Expected: `TypeError` for unsupported account arguments or missing account fields.

- [ ] **Step 3: Implement account-explicit service contracts**

Use these exact Pipeline signatures: `generate_draft(x_account_id, *, context_id=None, schedule_id=None, parent_draft_id=None, attempt=1)`, `approve(x_account_id, draft_id, *, reviewer, origin="dashboard")`, `reject_and_regenerate(x_account_id, draft_id, *, reason, notes="", reviewer, origin="dashboard")`, `edit(x_account_id, draft_id, new_text, *, reviewer, notes="", approve=False)`, `retry_publish(x_account_id, draft_id, *, reviewer, origin="dashboard")`, and `expire_and_regenerate(now=None, x_account_id=None)`.

Begin every lifecycle operation with account-owned retrieval:

```python
account = self.repository.get_account(x_account_id)
if not account.enabled:
    raise PipelineError(f"X account @{account.handle} is paused")
profile = self.repository.get_profile(x_account_id)
draft = self.repository.get_draft(x_account_id, draft_id) if draft_id else None
```

Change `FeedbackEngine.memory_bundle(x_account_id)`, all feedback recorders, `TrendCollector.collect(x_account_id, profile)`, and scheduler iteration to use enabled accounts. A schedule is resolved only through its owner account.

- [ ] **Step 4: Run all lifecycle and scheduler tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_account_pipeline.py tests/test_pipeline.py tests/test_scheduler.py tests/test_generation.py tests/test_guards_and_feedback.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit account-local lifecycle behavior**

```bash
git add app/services/pipeline.py app/services/feedback.py app/services/trends.py app/services/scheduler.py tests/test_account_pipeline.py tests/test_pipeline.py tests/test_scheduler.py tests/test_generation.py tests/test_guards_and_feedback.py
git commit -m "feat: isolate content lifecycle by X account"
```

---

### Task 6: Atomic per-account Buffer publication

**Files:**
- Create: `tests/test_publish_claims.py`
- Modify: `app/repository.py`
- Modify: `app/services/publishers.py`
- Modify: `app/services/pipeline.py`
- Modify: `tests/test_integrations.py`

**Interfaces:**
- Consumes: `IntegrationService.resolve_buffer`, account dual live switches.
- Produces: atomic publish claim and completion methods, per-account publisher resolution, explicit retry.

- [ ] **Step 1: Write failing dual-account and concurrent approval tests**

```python
def test_two_accounts_publish_to_their_bound_channels(pipeline, fake_buffer, two_accounts):
    first_draft = make_pending(two_accounts[0].id)
    second_draft = make_pending(two_accounts[1].id)
    pipeline.approve(two_accounts[0].id, first_draft.id, reviewer="admin")
    pipeline.approve(two_accounts[1].id, second_draft.id, reviewer="admin")
    assert fake_buffer.channel_ids == ["channel-a", "channel-b"]


def test_repeated_approval_calls_publisher_once(pipeline, counting_publisher, x_account, pending_draft):
    first = pipeline.approve(x_account.id, pending_draft.id, reviewer="admin")
    second = pipeline.approve(x_account.id, pending_draft.id, reviewer="admin")
    assert counting_publisher.calls == 1
    assert second.status == first.status
```

Use a thread barrier test to make two approval calls overlap. Add tests for global-off dry-run, account-off dry-run, live-on missing/locked connection failure, safe retry from `failed`, and refusal to retry `published`.

- [ ] **Step 2: Verify tests expose the current non-atomic global publisher behavior**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_publish_claims.py -q`

Expected: failures show multiple publisher calls or the wrong channel.

- [ ] **Step 3: Implement claim/complete persistence and publisher targets**

Implement `claim_publish(x_account_id, draft_id, *, reviewer, origin, allow_retry=False) -> tuple[Draft, PublishAttempt] | None` and `complete_publish(x_account_id, draft_id, attempt_id, result) -> Draft`.

The conditional state claim uses this predicate inside the same transaction that inserts the attempt:

```sql
UPDATE drafts
SET status = 'publishing', reviewer = ?, approved_at = COALESCE(approved_at, ?), expires_at = NULL
WHERE x_account_id = ?
  AND id = ?
  AND (status = 'pending' OR (? = 1 AND status = 'failed'))
```

`claim_publish` performs one transaction: conditional draft update, next attempt number, and `publish_attempts` insertion. `PublisherManager.publish(draft, account, target)` constructs a Buffer publisher using the decrypted target only for that call. It never stores credentials on the manager or in `PublishResult`.

- [ ] **Step 4: Run publication, integration, and pipeline tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_publish_claims.py tests/test_integrations.py tests/test_pipeline.py -q`

Expected: all selected tests pass and every concurrency run records one publisher call.

- [ ] **Step 5: Commit exactly-once account publication**

```bash
git add app/repository.py app/services/publishers.py app/services/pipeline.py tests/test_publish_claims.py tests/test_integrations.py
git commit -m "feat: publish atomically to account Buffer channels"
```

---

### Task 7: Dynamic Slack notifications and signed review actions

**Files:**
- Create: `app/routes/slack.py`
- Create: `tests/test_slack_actions.py`
- Modify: `app/services/notifiers.py`
- Modify: `app/services/integrations.py`
- Modify: `app/main.py`
- Modify: `tests/test_integrations.py`

**Interfaces:**
- Consumes: `IntegrationService.resolve_slack`, account-scoped Pipeline transitions.
- Produces: account-labelled Slack blocks and `POST /integrations/slack/{connection_id}/actions`.

- [ ] **Step 1: Write failing notification and signature tests**

```python
def test_slack_review_message_contains_account_and_actions(slack_notifier, account, draft, profile, context):
    slack_notifier.notify_for_review(draft, account, profile, context)
    payload = json.loads(slack_notifier.captured_requests[0].content)
    assert f"@{account.handle}" in str(payload)
    actions = next(block["elements"] for block in payload["blocks"] if block["type"] == "actions")
    assert {item["action_id"] for item in actions if item.get("action_id") in {"approve_draft", "reject_draft"}} == {"approve_draft", "reject_draft"}


def test_valid_slack_approve_calls_account_pipeline(client, signed_slack_request, draft):
    response = client.post(
        f"/integrations/slack/{signed_slack_request.connection_id}/actions",
        content=signed_slack_request.body,
        headers=signed_slack_request.headers,
    )
    assert response.status_code == 200
    assert repository.get_draft(draft.x_account_id, draft.id).status == "published"
```

The `slack_notifier` fixture uses `httpx.MockTransport`, exposes its captured request list as `captured_requests`, and never contacts Slack. Test invalid signatures, timestamps older than five minutes, connection/account mismatch, draft/account mismatch, repeat callbacks, Slack rejection regeneration, and edit-dashboard URL.

- [ ] **Step 2: Verify tests fail because Slack has notification links only**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_slack_actions.py -q`

Expected: missing route or missing interactive actions.

- [ ] **Step 3: Implement Slack signing and dynamic delivery**

Verify `v0:{timestamp}:{raw_body}` using HMAC-SHA256 and `secrets.compare_digest`. Parse the form-encoded `payload` only after signature validation. Use Slack `user.id` and `user.name` as the reviewer identity and `origin="slack"`.

```python
@router.post("/integrations/slack/{connection_id}/actions")
async def slack_action(connection_id: int, request: Request):
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    target = request.app.state.services.integrations.get_slack_connection(connection_id)
    request.app.state.services.integrations.verify_slack_signature(
        target.signing_secret, timestamp, signature, body
    )
    payload = json.loads(parse_qs(body.decode())["payload"][0])
    action = payload["actions"][0]
    value = json.loads(action["value"])
    reviewer = f"slack:{payload['user']['id']}:{payload['user'].get('name', '')}"
    pipeline = request.app.state.services.pipeline
    if action["action_id"] == "approve_draft":
        draft = pipeline.approve(value["x_account_id"], value["draft_id"], reviewer=reviewer, origin="slack")
    elif action["action_id"] == "reject_draft":
        draft = pipeline.reject_and_regenerate(
            value["x_account_id"], value["draft_id"], reason="other",
            notes="Rejected from Slack", reviewer=reviewer, origin="slack"
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported Slack action")
    return {"response_type": "ephemeral", "text": "Action recorded", "draft_id": draft.id if draft else value["draft_id"]}
```

Change `NotifierManager` to resolve Slack per account on each notification. Console remains always enabled. Telegram remains optional and must include the account ID in callbacks before it can act on account-owned drafts.

- [ ] **Step 4: Run Slack, notifier, pipeline, and Telegram regression tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_slack_actions.py tests/test_integrations.py tests/test_pipeline.py tests/test_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit Slack review actions**

```bash
git add app/routes/slack.py app/services/notifiers.py app/services/integrations.py app/main.py tests/test_slack_actions.py tests/test_integrations.py
git commit -m "feat: review account drafts securely from Slack"
```

---

### Task 8: Account-scoped API and CSRF-protected dashboard routes

**Files:**
- Create: `app/services/csrf.py`
- Create: `tests/test_csrf.py`
- Modify: `app/routes/api.py`
- Modify: `app/routes/ui.py`
- Modify: `app/routes/telegram.py`
- Modify: `app/main.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: account repository, integration service, Pipeline account methods.
- Produces: `/api/accounts`, `/api/accounts/{id}/profile`, `/api/accounts/{id}/drafts`, `/api/accounts/{id}/contexts`, `/api/accounts/{id}/schedules`, `/api/connections`, `/api/accounts/{id}/integrations`, and protected matching `/ui/accounts/{id}` forms.

- [ ] **Step 1: Write failing API isolation and CSRF tests**

```python
def test_account_api_never_returns_another_accounts_drafts(client, auth, two_accounts):
    response = client.get(f"/api/accounts/{two_accounts[0].id}/drafts", headers=auth)
    assert all(item["x_account_id"] == two_accounts[0].id for item in response.json())


def test_approval_form_without_csrf_token_changes_nothing(client, auth, pending_draft):
    response = client.post(
        f"/ui/accounts/{pending_draft.x_account_id}/drafts/{pending_draft.id}/approve",
        headers=auth,
    )
    assert response.status_code == 403
    assert repository.get_draft(pending_draft.x_account_id, pending_draft.id).status == "pending"
```

Test all mutating UI forms, account CRUD, connection save/bind/test/disconnect without credential echo, reviewer identity from Basic auth, and account mismatch errors.

- [ ] **Step 2: Verify routes fail because account scopes and CSRF are absent**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_csrf.py tests/test_api.py -q`

Expected: 404 for account routes or state changes succeeding without a token.

- [ ] **Step 3: Implement signed CSRF tokens and thin account routes**

```python
import base64
import hashlib
import hmac
import time


class CsrfProtector:
    def __init__(self, secret: str):
        self.secret = secret.encode()

    def issue(self, subject: str) -> str:
        timestamp = str(int(time.time()))
        message = f"{subject}:{timestamp}".encode()
        digest = hmac.new(self.secret, message, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{timestamp}:{digest}".encode()).decode()

    def verify(self, token: str, subject: str, max_age_seconds: int = 43200) -> bool:
        try:
            timestamp_text, supplied = base64.urlsafe_b64decode(token.encode()).decode().split(":", 1)
            timestamp = int(timestamp_text)
        except (ValueError, UnicodeDecodeError):
            return False
        if abs(int(time.time()) - timestamp) > max_age_seconds:
            return False
        message = f"{subject}:{timestamp_text}".encode()
        expected = hmac.new(self.secret, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, supplied)
```

Use `APP_CSRF_SECRET` when configured and an application-startup random secret in credential-free demo mode. Derive reviewer identity exclusively from `require_admin` or verified Slack/Telegram identity. Add account-scoped API paths while returning deprecation-safe redirects or wrappers only where existing tests require compatibility.

- [ ] **Step 4: Run route and security tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_csrf.py tests/test_api.py tests/test_slack_actions.py tests/test_pipeline.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit account routes and CSRF**

```bash
git add app/services/csrf.py app/routes/api.py app/routes/ui.py app/routes/telegram.py app/main.py tests/test_csrf.py tests/test_api.py
git commit -m "feat: add protected account management routes"
```

---

### Task 9: Low-work multi-account dashboard

**Files:**
- Create: `app/templates/accounts.html`
- Create: `app/templates/account_dashboard.html`
- Create: `app/templates/account_setup.html`
- Create: `app/templates/connections.html`
- Modify: `app/templates/base.html`
- Modify: `app/templates/dashboard.html`
- Modify: `app/static/app.css`
- Modify: `app/static/app.js`
- Modify: `app/routes/ui.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 8 routes and CSRF context.
- Produces: account cards, account selector, setup wizard, focused review workspace, connection UI, responsive states.

- [ ] **Step 1: Write failing rendered-page tests**

```python
def test_accounts_page_shows_status_cards(client, auth, two_accounts):
    html = client.get("/", headers=auth).text
    assert "@account_one" in html
    assert "@account_two" in html
    assert "Slack connected" in html
    assert "Buffer dry-run" in html


def test_account_workspace_names_publish_destination(client, auth, live_account, pending_draft):
    html = client.get(f"/accounts/{live_account.id}", headers=auth).text
    assert f"Approve and publish to @{live_account.handle}" in html
    assert "LIVE" in html
```

Add tests for setup incomplete, no drafts, paused account, locked connection, failed publication retry, account switching, hidden CSRF inputs, mobile viewport metadata, and absence of credential values.

- [ ] **Step 2: Verify tests fail against the global single-page dashboard**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q`

Expected: missing account cards, account URLs, or destination copy.

- [ ] **Step 3: Implement templates and presentation-only JavaScript**

The root lists accounts and an Add X Account action. `/accounts/{id}` prioritizes pending review, next schedule, connection health, and recent publications. `/accounts/{id}/setup` presents the seven approved steps and Copy Settings action. `/connections` manages masked reusable Buffer and Slack connections.

JavaScript may provide confirmation, character counts, and progressive disclosure only. It must not contain lifecycle decisions. Live approval confirmation repeats the final text and X handle. Replace the obsolete footer flag with `BUFFER_LIVE_POSTING` and display the effective per-account mode.

- [ ] **Step 4: Run dashboard tests and manually inspect generated HTML**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_csrf.py -q`

Expected: all selected tests pass; HTML contains no API keys, webhook URLs, signing secrets, or encrypted tokens.

- [ ] **Step 5: Commit the dashboard**

```bash
git add app/templates app/static/app.css app/static/app.js app/routes/ui.py tests/test_api.py
git commit -m "feat: add low-work multi-account dashboard"
```

---

### Task 10: Demo flow, documentation, and full verification

**Files:**
- Modify: `scripts/demo_flow.py`
- Modify: `scripts/seed_demo.py`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LIFECYCLE.md`
- Modify: `docs/API.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `docs/SECURITY_AND_LIMITATIONS.md`
- Modify: `.env.example`
- Modify: `tests/test_smoke.py`
- Modify: `tests/test_skills.py`

**Interfaces:**
- Consumes: completed account, integration, Slack, pipeline, scheduler, API, and dashboard behavior.
- Produces: credential-free two-account demonstration and accurate operator documentation.

- [ ] **Step 1: Write a failing smoke expectation for two isolated demo accounts**

```python
def test_documented_demo_script_runs_two_independent_accounts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/demo_flow.py"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "Account 1" in result.stdout
    assert "Account 2" in result.stdout
    assert "isolation verified" in result.stdout
    assert "status=published" in result.stdout
```

- [ ] **Step 2: Verify the old single-account demo fails the new expectation**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_smoke.py::test_documented_demo_script_runs_two_independent_accounts -q`

Expected: failure because the current demo has no second account or isolation output.

- [ ] **Step 3: Update scripts and documentation to the implemented contracts**

The demo creates or uses two accounts, gives them different profile domains, generates and rejects under one, proves the other has no inherited feedback, and approves a dry-run draft for each. Documentation explains `APP_ENCRYPTION_KEY`, `APP_CSRF_SECRET`, reusable connections, per-account Buffer channels, Slack interactivity request URLs, account copying, dual live switches, migration, and future WhatsApp work.

Remove obsolete `OPENAI_API_KEY`, direct-X publisher, and `X_LIVE_POSTING` descriptions. Document Groq generation and Buffer publication only.

- [ ] **Step 4: Run required verification**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app scripts
.\.venv\Scripts\python.exe scripts\demo_flow.py
```

Expected: every command exits `0`; the demo reports two accounts, isolated learning, explicit approval, and dry-run publication.

- [ ] **Step 5: Run web acceptance with external integrations mocked**

Start the app against a temporary SQLite database with scheduler disabled. Verify `/health`, authenticated `/`, add/copy account, account switch, setup forms, Buffer test, Slack test, generation, Slack reject/regenerate, dashboard edit, dashboard approval, repeated approval, failed-publication retry, and account-local histories. Confirm no real Buffer or Slack request is sent.

- [ ] **Step 6: Review the final diff for invariants and secret leakage**

Run:

```powershell
git diff --check
rg -n "api_key|webhook_url|signing_secret|encrypted_credentials" app/templates app/routes README.md docs
git status --short
```

Expected: no whitespace errors; every secret-related match is validation, masking, or documentation rather than a rendered/stored plaintext value; only intended files are modified.

- [ ] **Step 7: Commit the completed documentation and demo**

```bash
git add .env.example README.md docs app scripts tests
git commit -m "docs: complete multi-account operator workflow"
```
