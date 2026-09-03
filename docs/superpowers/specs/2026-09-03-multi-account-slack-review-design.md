# Multi-Account X Workspaces and Slack Review Design

## Goal

Extend the Startup X Agent prototype so one trusted operator can manage two or three fully independent X accounts from one application. Each account has isolated content configuration, schedules, drafts, feedback, learning, Buffer publication, Slack review, and audit history. The workflow must remain low-effort and must never publish without an explicit human approval action.

## Scope

This remains a single-process FastAPI and SQLite prototype. It does not add multi-user roles, SSO, Postgres, distributed workers, analytics, media posting, or WhatsApp delivery. WhatsApp is a later notifier adapter after the Slack workflow has been validated.

The implementation preserves these invariants:

1. Silence and timeout never count as approval.
2. Only an explicit dashboard or authenticated Slack action can approve a pending draft.
3. `never_reveal` rules are loaded from the draft's X account and override all other context.
4. Rejected, expired, edited, blocked, and failed originals remain in history.
5. Regeneration stays within the originating account, produces a materially different draft, and stops at that account's configured attempt limit.
6. Live publication is disabled by default.
7. Demo mode works without Groq, Buffer, Slack, Telegram, or WhatsApp credentials.
8. No profile, source, draft, feedback, preference, similarity history, notification, or publication state crosses account boundaries.

## Chosen architecture

Use one account-scoped shared application. The existing service boundaries remain: the repository owns persistence, `Pipeline` owns lifecycle sequencing, the scheduler triggers account schedules, notifiers deliver review messages, and publishers normalize Buffer or dry-run results.

All lifecycle entry points receive an explicit `x_account_id`. Routes resolve the selected account and pass it to services; services never infer a global account from mutable UI state. Repository reads and writes that operate on account-owned data require the account ID and include it in their SQL predicates.

```text
Selected X account
        |
        v
Account profile + contexts + schedules + sources + learning
        |
        v
Generation -> safety -> similarity -> pending draft
        |                                  |
        +----------------------+-----------+
                               v
                     Dashboard and Slack
                               |
                 approve / reject / edit link
                               |
                               v
                     Account-scoped Pipeline
                               |
                        final safety check
                               |
                               v
              account Buffer connection + channel
                               |
                     dry-run or live publish
```

## Data model

### X accounts

Add `x_accounts` with:

- `id` integer primary key;
- `name` display label;
- `handle` normalized X handle without secrets;
- `enabled` account-level scheduler switch;
- `live_posting_enabled` account-level live-publication switch, default false;
- `timezone` used for account schedules;
- `created_at` and `updated_at`.

The existing global `BUFFER_LIVE_POSTING` setting remains a deployment-wide kill switch. Live publication requires both the global switch and the account switch.

### Reusable integration connections

Add `integration_connections` with:

- `id` integer primary key;
- `provider` constrained to supported providers such as `buffer` or `slack`;
- `label` chosen by the operator;
- `encrypted_credentials` containing an authenticated encrypted JSON payload;
- `created_at` and `updated_at`.

Add `account_integrations` with:

- `x_account_id`;
- `provider`;
- `connection_id`;
- `target_id`, used for a Buffer channel ID when one Buffer connection serves multiple X accounts;
- `enabled`;
- last test status, error, and timestamp.

An account has at most one active binding per provider. A connection may be reused by several accounts. Separate Buffer accounts use separate saved connections; accounts under one Buffer account reuse the saved Buffer connection and specify different channel IDs. Slack webhook connections may likewise be shared or separate.

### Account-owned content

Change the singleton startup profile into one profile per X account. Add `x_account_id` to content contexts, schedules, trends, drafts, learned preferences, configuration versions, and events. Feedback remains linked to a draft and therefore inherits its account, while repository queries still join or verify the owning account.

All uniqueness and retrieval scopes become account-local:

- profile configuration versions are numbered per account;
- schedule slot numbers are unique per account;
- similarity history contains only that account's drafts;
- approved and rejected examples contain only that account's drafts;
- preferences are unique within an account rather than globally;
- dashboard counts and activity are filtered by account.

### Existing-data migration

Database initialization performs a repeatable schema migration. Existing installations receive one initial X account, and all existing profile, context, schedule, trend, draft, preference, version, and event records are attached to it. Existing draft IDs and parent-child links are preserved. Re-running initialization must not duplicate accounts, contexts, schedules, or history.

## Credential protection

Add `APP_ENCRYPTION_KEY` as a required environment setting only when encrypted integration connections are created or read. It is a Fernet-compatible key used by a dedicated encryption service to serialize provider credentials to JSON and encrypt them with authenticated encryption. Plaintext secrets are used only long enough to call a provider and are never written to the database, returned by APIs, rendered into templates, or included in logs and events.

The dashboard shows only whether credentials are configured and a non-secret label. It provides Connect, Test, Replace, and Disconnect actions. Replacing or disconnecting a connection does not delete accounts, drafts, or review history.

If encrypted records exist but the master key is missing or incorrect, affected integrations are shown as locked. Live actions fail visibly and retain the draft; the system never downgrades to plaintext storage.

## Account-scoped lifecycle

### Generation

Manual and scheduled generation require an enabled X account. `Pipeline.generate_draft` loads only that account's profile, context, trends, approved examples, rejected examples, learned preferences, history, and current configuration version. The created draft records `x_account_id`.

The safety guard receives the account profile. The similarity guard compares only account-local history. Notification messages include the account name and handle.

### Review synchronization

Every pending draft is visible on the selected account dashboard and is sent to the account's enabled Slack connection. Slack messages contain:

- account name and X handle;
- draft text, topic, attempt, and target provider mode;
- Approve;
- Reject + Regenerate;
- Edit in Dashboard.

Slack actions call a signed form endpoint. The endpoint verifies the raw request body with the connection's signing secret, enforces a short timestamp window, identifies the Slack user, and only then dispatches to `Pipeline`. The action payload carries account and draft identifiers, but neither is trusted until repository ownership validation succeeds.

Dashboard and Slack actions call the same pipeline methods and therefore share status validation, safety checks, feedback recording, attempt limits, and event logging. Editing remains in the dashboard. After an action, Slack receives a concise result message and the dashboard reflects the same persisted state.

### Approval and exactly-once claim

Approval uses an atomic repository compare-and-set from `pending` to `publishing`. Only the request that successfully claims the draft may call a publisher. Later dashboard clicks or Slack callbacks return the recorded state and do not call Buffer again.

Immediately before publication, `Pipeline` reloads the account profile, reruns safety, resolves the account's active Buffer binding, decrypts that connection, and uses the account binding's Buffer channel ID.

Publication mode is explicit:

- global live switch false: intentional dry run;
- account live switch false: intentional dry run;
- both switches true and valid credentials/channel present: Buffer live publication;
- both switches true but the connection is missing, locked, invalid, or disabled: visible failure, never a reported dry run.

Successful results become `published`. Provider failures become `failed` with a safe error message. Safety failures become `blocked`. A failed publication offers Retry Publish, which is a new explicit human action that rechecks account ownership and safety before atomically claiming a new publication attempt. Every terminal state retains the draft and logs its account, origin surface, reviewer identity, and non-secret provider result.

### Rejection, editing, and timeout

Reject and edit operations verify account ownership before changing state. Feedback and learned rules are stored only for the account. Regenerated children inherit the account and schedule, increment the attempt, and compare against account-local history.

Timeout processing never publishes. It retains the expired original and regenerates only within the account until its attempt limit is reached. A disabled account does not generate scheduled or timeout replacement drafts until re-enabled.

## Buffer connection behavior

A Buffer connection stores an encrypted API key. The account binding stores the channel ID. This supports both desired arrangements:

```text
Shared Buffer connection
|- X account A -> Buffer channel A
`- X account B -> Buffer channel B

Separate Buffer connection
`- X account C -> Buffer channel C
```

The Test action validates authentication and access to the configured channel without creating a post. Connection status is persisted without raw provider responses or secrets. Publisher results remain normalized through the existing `PublishResult` contract.

## Slack connection behavior

A Slack connection stores the encrypted webhook URL and signing secret required for interactive callbacks. Accounts may select the same connection or different connections. The Test action sends a clearly labeled test message and records delivery status.

An invalid signature, stale timestamp, unknown connection, mismatched account, mismatched draft, or non-pending review action cannot approve or reject content. Notification delivery by itself never counts as approval.

## Dashboard experience

### First-run wizard

The operator completes seven short steps:

1. Add account name and X handle.
2. Enter the account's startup profile and never-reveal rules.
3. Select or create a Buffer connection.
4. Enter and test the Buffer channel ID.
5. Select or create a Slack connection.
6. Send a Slack test notification.
7. Select a schedule preset or keep schedules paused.

New accounts default to dry-run. The wizard may copy another account's profile, contexts, and schedule as a starting point. Copying creates independent rows; later changes, history, and learning are never linked.

### Everyday dashboard

The landing page presents compact account cards with handle, pending count, next schedule, pause state, Slack status, and Buffer dry-run/live status. Selecting an account opens its workspace. The selected handle and publication mode remain visible in the header and on each draft action.

The account workspace prioritizes:

- today's pending drafts;
- Generate Draft;
- next scheduled generation;
- Approve, Reject + Regenerate, and Edit;
- actionable integration errors;
- recent publication history.

Account profile, contexts, schedules, sources, learning, integrations, and activity remain available in secondary sections. Account creation, pause, connection selection, and testing require no source-code or `.env` edits beyond the one-time master encryption key.

Approval copy names the destination, for example `Approve and publish to @account_one`. Live publication requires a confirmation that repeats the handle and final post text. Dry-run and live results are displayed separately.

### Scheduling

Each account owns its timezone and schedule slots. Presets include once daily, weekdays only, and custom slots. Scheduled generation creates a pending draft and delivers it to Slack and the dashboard; it never publishes. Pausing one account leaves all other accounts active.

The scheduler remains single-process. Its tick iterates enabled account schedules, resolves each account timezone, and preserves once-per-local-day behavior per account and slot.

## Error handling

Provider, encryption, validation, notification, and publication errors are converted to safe operator-facing messages. Routes display errors within the selected account workspace rather than returning an unhandled page. External failures never delete a draft or trigger publication through another account or provider.

Connection tests are explicit actions. A Slack test may send only a labeled test message. A Buffer test may not create, queue, or publish a post.

## API and route contracts

Management endpoints are account-scoped under `/api/accounts/{x_account_id}/...`. Dashboard routes either carry the account ID or resolve it from the account selector and then verify it server-side. Review requests do not accept an arbitrary reviewer string; the dashboard uses the authenticated admin identity and Slack uses the verified Slack user identity.

Add management endpoints for accounts, reusable connections, account bindings, connection testing, and account copying. Responses expose connection IDs, labels, provider, enabled state, and test status, but never encrypted or decrypted credential fields.

Routes remain transport-only. They validate forms and authentication, then call repository methods for configuration or `Pipeline` methods for lifecycle transitions. Every state-changing dashboard form carries and validates a CSRF token; invalid or missing tokens make no state change. Demo mode may use an ephemeral signing secret created at application startup, while configured deployments use a persistent secret.

## Testing strategy

All behavior changes follow red-green-refactor. Required tests prove:

1. Account A generation uses only profile, trends, history, feedback, preferences, and schedules from Account A.
2. Account B cannot read, review, edit, reject, or publish Account A's draft through account-scoped routes or pipeline methods.
3. Shared Buffer credentials can publish to two different configured channel IDs.
4. Separate Buffer credentials are selected for their bound accounts.
5. Buffer and Slack secrets round-trip through encryption and never appear in API, HTML, logs, or events.
6. Missing or incorrect `APP_ENCRYPTION_KEY` locks integrations without losing account content.
7. Shared and separate Slack connections send the correct account-labelled draft.
8. Valid Slack actions and dashboard actions produce the same state transitions.
9. Invalid Slack signatures, stale timestamps, and account/draft mismatches make no state change.
10. Concurrent or repeated approvals call the publisher at most once.
11. Live-enabled accounts with invalid Buffer configuration fail visibly instead of reporting dry-run success.
12. Rejection, edit, timeout, regeneration, similarity, and attempt limits remain account-local.
13. Pausing one account does not pause another.
14. Migration assigns existing data once and remains idempotent.
15. Mobile-readable dashboard markup includes account selection, mode, empty states, and review controls.
16. The credential-free demo workflow remains functional.
17. Failed publication can be retried only by an explicit human action and cannot overlap another publication attempt.
18. Missing or invalid dashboard CSRF tokens cannot generate, approve, reject, edit, configure, test, disconnect, or publish anything.

Required verification remains:

```bash
pytest -q
python -m compileall app scripts
python scripts/demo_flow.py
```

Web acceptance additionally exercises account creation, account switching, connection forms, generation, Slack review callbacks, rejection/regeneration, edit, approval, dry-run publication, and account isolation.

## Documentation changes

Update architecture, lifecycle, API, deployment, environment, demo, and security documentation to describe account scoping, `APP_ENCRYPTION_KEY`, reusable integrations, Slack interactivity configuration, dual live-posting switches, and the migration path. Remove outdated `OPENAI_API_KEY`, direct-X, and `X_LIVE_POSTING` references in favor of the implemented Groq and Buffer configuration.

## Future WhatsApp extension

WhatsApp is deliberately outside this implementation. The provider-neutral reusable connection model and notifier manager allow a future WhatsApp adapter to bind per account, deliver review messages, and route authenticated actions through the same account-scoped `Pipeline` without changing generation or publication rules.

## Success criteria

A trusted operator can add two or three X accounts, give each an independent profile and schedule, reuse or separately configure encrypted Buffer and Slack connections, receive account-labelled drafts in Slack and the dashboard, approve or reject from either review surface, and publish only to the intended account's Buffer channel. No content or learning crosses accounts, repeated approvals do not duplicate publication, and demo mode remains credential-free.
