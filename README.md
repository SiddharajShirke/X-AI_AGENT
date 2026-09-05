# Startup X Agent prototype

A small human-in-the-loop workflow for operating two or three independent X accounts. It generates drafts, sends them to the dashboard and an account-specific Slack channel, waits for an explicit human decision, and publishes approved text posts through that account's Buffer channel.

This is intentionally a prototype, not production infrastructure. Its safety rules are still strict:

- no post is published without an explicit authenticated dashboard/API action or verified Slack/Telegram action;
- silence and timeouts never approve;
- `never_reveal` always overrides trends and learned preferences;
- rejected, expired, and edited originals remain in account-local history;
- regeneration is materially different and stops at the configured attempt limit;
- live publishing is off by default.

## Fastest credential-free demo

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts\demo_flow.py
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

Open `http://localhost:8000` and sign in with the `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`. Demo generation, account isolation, review, and dry-run publication work without Groq, Buffer, Slack, Telegram, or X credentials.

## Everyday workflow

1. Open **Accounts** and choose the X account.
2. Review its pending drafts in the focused workspace, or review the same draft in its Slack channel.
3. Explicitly **Approve**, **Edit**, or **Reject + regenerate**.
4. When live publishing is deliberately enabled, approval sends the exact text to the Buffer channel bound to that account.

Ideas, drafts, schedules, feedback, learned preferences, review history, Buffer targets, and Slack delivery are scoped by X account. Copying setup to a new account copies profile, contexts, and schedules only; it does not copy content history, feedback, or connections.

## One-time setup for two or three accounts

1. Set a strong admin password and persistent secrets in `.env`:

   ```powershell
   .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Put that value in `APP_ENCRYPTION_KEY`. Put a separate long random value in `APP_CSRF_SECRET`. Keep both stable and out of version control; changing the encryption key locks previously saved credentials.

2. From **Accounts**, add each X account. Choose **Copy settings from** to reuse another account's profile/context/schedule setup without sharing its learning history.
3. Complete each account's seven-step **Setup** page.
4. Add reusable Buffer and Slack credentials once under **Connections**. The dashboard shows only `Configured ••••••••` after saving.
5. On each account's Setup page, select a saved connection and enter that account's Buffer channel ID or Slack channel label.

One Buffer login can serve different X channels, or each X account can use a different Buffer login. Slack works the same way: reuse one saved Slack connection or bind separate encrypted webhooks.

## Slack review

For each Slack app/connection:

1. Create an incoming webhook for the review channel.
2. Copy the app's signing secret.
3. Save both under **Connections**, then note the saved connection ID.
4. Set Slack's Interactivity Request URL to:

   ```text
   https://your-host.example/integrations/slack/<connection-id>/actions
   ```

5. Bind that saved connection on the relevant X account's Setup page and press **Test**.

Slack messages contain the account handle, draft, Approve, Reject + regenerate, and Edit-in-dashboard actions. Incoming actions must have a valid Slack HMAC signature less than five minutes old and must match the account's bound connection. Repeated approval callbacks cannot publish twice.

Slack uses HTTP Interactivity, not Socket Mode. Use the same Slack app for the incoming webhook and the callback: Socket Mode must be **Off**, and **Interactivity & Shortcuts** must be **On**. Copy the exact per-connection URL shown in **Connections** into Slack. The **Test outbound Slack message** control proves only that the webhook can post outbound; it does not prove that Slack clicks reach this application. See [deployment instructions](docs/DEPLOYMENT.md#slack-http-callback-operation) for local/ngrok and deployed single-instance setup.

## Buffer publishing

The application publishes only through Buffer; the optional X bearer token is used only for trend research.

Live publication needs all of the following:

- `BUFFER_LIVE_POSTING=true` in `.env`;
- **Allow live posting for this account** enabled in that account's Setup page;
- an enabled saved Buffer connection;
- the correct Buffer channel ID bound to that X account;
- an explicit human approval.

If either live switch is off, approval intentionally uses the dry-run publisher. If both are on but credentials are missing or locked, publication becomes visibly `failed`; it never silently falls back to dry-run. A failed publication can be retried only with a separate explicit Retry action.

The prototype handles text-only posts. Media, threads, analytics reconciliation, OAuth onboarding, and background retry queues are out of scope.

## Optional generation and research

Set `GROQ_API_KEY` to use `GROQ_MODEL` for generation. Without it, the deterministic demo writer is used. Set `TWITTER_BEARER_TOKEN` only for optional X recent-search trend collection. Manual and RSS signals also remain account-scoped.

## API

Management endpoints use HTTP Basic authentication and explicit account paths, for example:

```text
GET  /api/accounts
GET  /api/accounts/{id}/state
POST /api/accounts/{id}/drafts/generate
POST /api/accounts/{id}/drafts/{draft_id}/approve
POST /api/accounts/{id}/drafts/{draft_id}/reject
GET  /api/connections
PUT  /api/accounts/{id}/integrations/{provider}
```

See [docs/API.md](docs/API.md). Browser mutation forms additionally require signed CSRF tokens. Reviewer identity comes from authenticated Basic credentials or a verified Slack/Telegram identity, never a submitted reviewer field.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

SQLite persists in the `x_agent_data` volume. Run one application instance while the in-process scheduler is enabled; horizontal scaling is outside this prototype.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app scripts
.\.venv\Scripts\python.exe scripts\demo_flow.py
docker compose config
```

## Future WhatsApp work

WhatsApp is deliberately deferred until the Slack and dashboard workflow is proven. It should become another account-resolved notifier and verified action adapter that calls the same Pipeline transitions; it must not introduce a separate approval state machine.

More detail is in `docs/ARCHITECTURE.md`, `docs/LIFECYCLE.md`, `docs/DEPLOYMENT.md`, and `docs/SECURITY_AND_LIMITATIONS.md`.
