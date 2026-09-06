# Prototype deployment

## Local or Docker setup

Copy `.env.example` to `.env`. Keep `BUFFER_LIVE_POSTING=false` until dry-run review is proven. Set:

- a strong `ADMIN_PASSWORD`;
- a stable Fernet `APP_ENCRYPTION_KEY` before saving Buffer or Slack credentials;
- a separate stable `APP_CSRF_SECRET`;
- `BASE_URL` to the HTTPS public origin when Slack or Telegram links are used.

Generate a Fernet key with:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run locally with `uvicorn app.main:app --host 0.0.0.0 --port 8000`, or:

```bash
docker compose up --build
```

The container expects a persistent volume at `/data` and `DATABASE_PATH=/data/x_agent.db`. Use one application instance while the in-process scheduler is enabled. This prototype does not support horizontally scaled scheduling or SQLite writes.

## Slack HTTP callback operation

Slack review uses the incoming webhook for outbound draft notifications and HTTP Interactivity for inbound human actions. These are separate paths: a successful webhook test proves **outbound posting only**. It does not verify that Slack can deliver an Approve or Reject click to this application.

Use the **same Slack app** for the incoming webhook and Interactivity. In that app, keep **Socket Mode Off** and turn **Interactivity & Shortcuts On**. Socket Mode does not route interactions to this HTTP callback. For every saved Slack connection, copy the exact Interactivity Request URL displayed in the dashboard **Connections** page:

```text
https://your-public-origin/integrations/slack/<connection-id>/actions
```

Changing the public origin (including receiving a new ngrok URL) changes this callback URL. Update the Slack Request URL before relying on Slack actions.

### Local/ngrok

1. Start uvicorn on port 8000:

   ```powershell
   .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000
   ```

2. Start ngrok for `http://localhost:8000`.
3. Set `BASE_URL` to the exact HTTPS ngrok origin and restart uvicorn.
4. In the same Slack app as the webhook, set Socket Mode to Off.
5. Turn Interactivity & Shortcuts On and copy the exact Request URL from **Connections** into Slack.

The changing ngrok origin is appropriate for local testing only. Update Slack every time it changes.

### Deployed single instance

1. Use a stable HTTPS domain and a persistent `/data` volume for the SQLite database.
2. Set stable, private `APP_ENCRYPTION_KEY` and `APP_CSRF_SECRET` values; do not change the encryption key after saving credentials.
3. Run exactly one container/application instance. This prototype is not multi-instance or horizontally safe.
4. Configure the exact deployed callback URL shown in **Connections** in Slack.
5. Verify, in order: outbound webhook test, inbound human click receipt, mocked/dry-run approval, then set `APP_MODE=live` and enable the account live switch and `BUFFER_LIVE_POSTING=true` only when each prior check is successful.

Keep `APP_MODE=demo`, `BUFFER_LIVE_POSTING=false`, and the per-account live switch off until the staged checks are complete. Demo mode remains credential-free.

`APP_MODE` is an operator-facing deployment label, not a publication gate. If `BUFFER_LIVE_POSTING=true`, an account with its LIVE switch enabled can publish even while `APP_MODE=demo`; startup and dashboard warnings call out that mismatch. Set the Buffer switch back to `false` to remove live capability.

## Connection setup

Credentials are entered through `/connections`, encrypted before SQLite storage, and reused from each account's Setup page. They do not belong in `.env`. Changing `APP_ENCRYPTION_KEY` makes existing records visibly locked; replace each connection using the original key or new credentials.

For Slack, configure the exact Interactivity Request URL displayed in **Connections** as `https://host/integrations/slack/<connection-id>/actions`. Put the signing secret and incoming webhook from the same Slack app in the corresponding saved connection. Socket Mode must be Off and Interactivity & Shortcuts must be On.

For live X posting through Buffer, bind the correct Buffer channel on each account, enable the account's live switch, and only then set `BUFFER_LIVE_POSTING=true`. Missing live credentials fail visibly.

## Acceptance checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app scripts
.\.venv\Scripts\python.exe scripts\demo_flow.py
docker compose config
```

Also verify `/health`, authenticated account pages, add/copy account, connection tests, generation, rejection/regeneration, edit, explicit approval, repeated approval, and failed-publication retry. Keep test accounts/channels when enabling live mode.
