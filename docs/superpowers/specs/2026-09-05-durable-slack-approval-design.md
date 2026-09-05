# Durable Slack Approval and Deployment Design

## Goal

Make an explicit Slack Approve or Reject click reliably reach the account-scoped review workflow, acknowledge Slack promptly, survive a single-process restart before processing, and expose enough status in the dashboard to diagnose delivery. A successful live approval must use the bound Buffer connection and channel for that X account, while invalid, duplicated, silent, or timed-out interactions must never publish.

This design hardens the current FastAPI and SQLite application for a single deployed instance. It does not claim horizontal scalability or eliminate the unavoidable provider-call crash window when Buffer offers no application-level reconciliation guarantee.

## Observed failure and validated boundaries

The reported Slack click did not reach the application:

- the pending draft remained unchanged and no Slack-origin publish attempt existed;
- ngrok recorded no inbound request for the human click;
- a signed diagnostic request sent through the configured public ngrok URL reached the callback, passed signature verification, and returned the expected application response;
- a later dashboard approval used the same Pipeline and Buffer binding successfully and produced a live X post.

These observations isolate the immediate failure to Slack interaction transport configuration. The Slack app that owns the Incoming Webhook must also own the saved Signing Secret and Interactivity configuration. With Socket Mode enabled, Slack sends interactive payloads through WebSocket instead of the HTTP Request URL; the current application has no Socket Mode listener.

The current HTTP callback also performs generation or Buffer publication before responding. Slack requires a valid interaction to be acknowledged within three seconds, so provider latency can make a correctly routed click appear to fail or be retried.

## Chosen transport

Use HTTP Interactivity for the X Agent Slack app:

- Socket Mode is disabled on this Slack app;
- Interactivity is enabled;
- the Request URL is the stable HTTPS application origin followed by `/integrations/slack/<connection-id>/actions`;
- the Incoming Webhook and Signing Secret come from this same Slack app;
- another Slack app in the workspace may continue using Socket Mode independently.

Socket Mode support is not added. Supporting both transports would introduce app-level tokens, persistent WebSocket lifecycle and reconnection handling, duplicate transport paths, and more deployment state without improving this two-to-three-account workflow.

## User-visible flow

```text
Pending draft delivered to Slack
              |
              v
Human clicks Approve or Reject
              |
              v
Signed HTTP callback at stable public URL
              |
      validate and persist action
              |
       immediate Slack HTTP 200
              |
              v
durable single-instance action processor
              |
              v
account-scoped Pipeline and final safety check
       |                         |
     reject                  approve
       |                         |
regenerate within account    Buffer target for account
                                 |
                                 v
                         X publication result
              |
              v
Dashboard audit + Slack result notification
```

The callback acknowledgement means only “the authenticated human action was accepted for processing.” It is not itself an approval result and never bypasses the Pipeline.

## Slack application setup contract

The Connections dashboard shows an exact, copyable callback URL for every saved Slack connection rather than a placeholder. It also displays these mandatory instructions:

1. Open the Slack app that generated this Incoming Webhook.
2. Disable Socket Mode for this app.
3. Enable Interactivity & Shortcuts.
4. Set the Request URL to the exact connection-specific callback URL.
5. Save changes.
6. Use the Signing Secret from the same Slack app.
7. Generate a fresh pending draft and click an action to verify inbound delivery.

The existing Slack Test is relabeled “Test outbound Slack message” because it proves only that the webhook can post into its configured workspace/channel. It cannot prove that Slack will send button actions back to the application.

The dashboard separately reports inbound action health:

- `Not verified`: no authenticated callback has been received for the connection;
- `Received`: a callback was authenticated and durably accepted;
- `Completed`: the action processor reached a terminal result;
- `Failed`: processing ended with a safe actionable error.

No secret, webhook URL, Signing Secret, raw Slack payload, or `response_url` is displayed or stored in plaintext.

## Durable action data model

Add `slack_action_jobs` owned by the repository with:

- `id`: integer primary key;
- `idempotency_key`: SHA-256 digest of the connection ID and exact signed request body, unique;
- `connection_id`;
- `x_account_id`;
- `draft_id`;
- `action_id`: constrained by service logic to `approve_draft` or `reject_draft`;
- `expected_live`: the publication mode embedded when the review message was created;
- `reviewer`: verified Slack user identity;
- `status`: `pending`, `processing`, `completed`, or `failed`;
- `result_draft_id` where rejection creates a replacement;
- `safe_error` containing no provider secrets or raw payloads;
- `created_at`, `claimed_at`, and `completed_at`.

Only validated fields are persisted. The raw form body can contain Slack bearer-style callback URLs and must not be stored. The digest provides callback deduplication without retaining the payload.

Database initialization creates the table and indexes idempotently. Existing installations require no destructive migration.

## Callback route

`POST /integrations/slack/{connection_id}/actions` remains a transport adapter and performs only bounded work before returning:

1. Read the exact raw body.
2. Resolve the encrypted Slack connection identified by the URL.
3. Verify Slack HMAC and the five-minute timestamp window.
4. Parse the form payload only after verification.
5. Validate action ID, account ID, draft ID, expected publication mode, and verified Slack user.
6. Verify the connection is the account’s enabled Slack binding.
7. Verify the draft belongs to that account.
8. Insert the job using the unique idempotency key, or return the existing job for a repeated delivery.
9. Wake the processor and return HTTP 200 immediately.

Invalid signatures, stale timestamps, malformed payloads, unknown actions, ownership mismatches, and disabled bindings create no job and no lifecycle state change.

The HTTP 200 response states that the action was received for processing and uses `replace_original=false`. It must not report publication success before Buffer has responded.

## Action processor

Add a focused `SlackActionProcessor`; do not put provider work in the route or scheduler.

The processor runs as one application lifespan task alongside the existing scheduler. It polls pending jobs and uses a repository compare-and-set to claim one job. It then calls the same account-scoped Pipeline methods used by the dashboard:

- `approve_draft(..., origin="slack", expected_live_posting=...)`;
- `reject_and_regenerate(..., origin="slack")`.

The Pipeline remains the only lifecycle sequencer. Final safety validation, account state, mode drift, Buffer resolution, publication claim, feedback and event logging remain unchanged.

After processing, the job records `completed` or `failed` with a safe error. The existing account notifier sends a concise terminal status to Slack and the dashboard reads the persisted draft, publication attempt, and action job state.

Pending jobs survive process restart. A `processing` lease older than a conservative recovery interval can be reclaimed only when the related draft is still `pending` for approval or otherwise has an unambiguous terminal state. A draft already in `publishing` is never automatically republished; it is flagged for operator reconciliation to preserve at-most-once external publication.

## Idempotency and safety

Two layers prevent duplicate publication:

1. A repeated Slack delivery maps to one `slack_action_jobs.idempotency_key`.
2. Pipeline approval atomically claims only a `pending` draft and creates one publication attempt.

A second callback or simultaneous dashboard click observes persisted state and cannot call Buffer again. Notification delivery, callback acknowledgement, worker wake-up, timeout, or application restart never counts as a new approval.

If publication mode changed after the Slack review message was sent, the processor fails the job with “Publication mode changed; request a fresh Slack review.” It does not silently switch between dry-run and live publication.

## Buffer publication and result handling

Live publication still requires both `BUFFER_LIVE_POSTING=true` and the account’s live switch. The processor does not receive Buffer credentials from Slack. Pipeline resolves the account’s enabled encrypted Buffer connection and channel at processing time.

Buffer success records provider, external post ID, final URL when present, timestamps and the Slack reviewer. Provider failure leaves the draft `failed` and requires a new explicit Retry action. A successful action result includes the X URL in Slack and the dashboard when Buffer supplies one.

The design preserves the existing crash-safety choice: once a draft is `publishing`, restart recovery does not blindly call Buffer again. Without a verified provider idempotency or reconciliation contract, avoiding an accidental duplicate is safer than automatic replay.

## Observability

Add safe structured events at each boundary:

- `slack_action_received` after signature, binding and ownership validation;
- `slack_action_duplicate` for an already persisted callback;
- `slack_action_processing` when claimed;
- `slack_action_completed` with resulting draft status and provider, excluding raw responses;
- `slack_action_failed` with a normalized safe reason.

The account dashboard shows the last inbound callback time, action, processing state and resulting draft status. The Connections page distinguishes outbound webhook health from inbound callback health.

Logs may include connection ID, account ID, draft ID, action ID and status. They must never include request bodies, signatures, webhook URLs, Signing Secrets, response URLs, Buffer keys or authorization headers.

## Deployment behavior

For ngrok development, `BASE_URL` must exactly match the active HTTPS tunnel origin and Slack must use the connection-specific path. The application and tunnel must remain running.

For a deployed instance:

- use a stable HTTPS origin, not an ephemeral development tunnel;
- persist the SQLite database outside the image;
- preserve `APP_ENCRYPTION_KEY` and `APP_CSRF_SECRET` across restarts;
- run exactly one application instance while SQLite, the in-process scheduler and Slack action processor are enabled;
- keep live posting off until outbound Slack, inbound callback and Buffer tests pass;
- configure Slack Interactivity again whenever the public origin or connection ID changes.

Startup validation emits a prominent warning in live mode when `BASE_URL` is not HTTPS, encryption/CSRF secrets are missing, or default admin credentials remain. It does not print secrets.

Horizontal scaling requires a shared transactional database and external durable queue/worker. This implementation must not claim multi-instance safety.

## Error handling

Slack receives HTTP errors only for requests that cannot be authenticated or parsed safely. Authenticated, valid actions receive HTTP 200 after durable acceptance; processing errors are reported asynchronously through account Slack notification and dashboard status.

Representative safe errors include:

- Slack connection is not bound to this X account;
- draft does not belong to this X account;
- publication mode changed; request a fresh Slack review;
- draft is no longer pending;
- Buffer connection is missing or locked;
- Buffer publication failed; retry requires explicit approval;
- action needs operator reconciliation after an interrupted publication.

## Testing strategy

All behavior changes use red-green-refactor with temporary SQLite databases and mocked providers.

Tests prove:

1. A valid signed action is persisted before the callback returns HTTP 200.
2. Slow Buffer publication does not occur in the request handler.
3. Invalid/stale signatures, malformed bodies, unknown actions and ownership mismatches persist no job.
4. Repeated Slack deliveries create one job and at most one publication attempt.
5. The processor calls Pipeline with the verified account, draft, reviewer, origin and expected mode.
6. Restart processing recovers a pending job.
7. A stale processing lease never republishes a draft already in `publishing` or `published`.
8. Mode drift, safety failure and missing Buffer bindings fail visibly without dry-run fallback.
9. Mocked Buffer success records the external ID/URL and a Slack-origin reviewer.
10. Mocked Buffer failure retains the draft and requires explicit Retry.
11. Slack and Buffer credentials never appear in jobs, events, logs, HTML or API responses.
12. Each account and Slack connection remains isolated.
13. The dashboard renders the exact connection-specific callback URL and separate inbound/outbound health.
14. Demo mode works with no external credentials.
15. Docker configuration, compilation, full tests and the CLI demo remain valid.

Required verification:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app scripts
.\.venv\Scripts\python.exe scripts\demo_flow.py
docker compose config
```

Live acceptance requires a human-created pending draft and a real Slack click. The expected audit sequence is `slack_action_received` → `slack_action_processing` → `draft_published` or `publish_failed` → `slack_action_completed` or `slack_action_failed`. No automated test or diagnostic request substitutes for the human approval action.

## Success criteria

- A human click from the correctly configured Slack app reaches the stable HTTPS callback and is visibly acknowledged.
- The action is durable before acknowledgement and survives a process restart before processing.
- Approval and rejection use the same Pipeline as the dashboard and preserve every safety invariant.
- A valid live approval publishes at most once to the intended account’s Buffer/X destination.
- Slack and the dashboard show the processing result and X link or an actionable safe failure.
- Outbound webhook health and inbound callback health are distinct and understandable.
- The deployment documentation produces a reproducible one-instance setup and does not overstate SQLite or in-process-worker scalability.
