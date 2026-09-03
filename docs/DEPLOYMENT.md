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

## Connection setup

Credentials are entered through `/connections`, encrypted before SQLite storage, and reused from each account's Setup page. They do not belong in `.env`. Changing `APP_ENCRYPTION_KEY` makes existing records visibly locked; replace each connection using the original key or new credentials.

For Slack, configure Interactivity Request URL as `https://host/integrations/slack/<connection-id>/actions`. Put the Slack app signing secret and incoming webhook in the corresponding saved connection.

For live X posting through Buffer, bind the correct Buffer channel on each account, enable the account's live switch, and only then set `BUFFER_LIVE_POSTING=true`. Missing live credentials fail visibly.

## Acceptance checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall app scripts
.\.venv\Scripts\python.exe scripts\demo_flow.py
docker compose config
```

Also verify `/health`, authenticated account pages, add/copy account, connection tests, generation, rejection/regeneration, edit, explicit approval, repeated approval, and failed-publication retry. Keep test accounts/channels when enabling live mode.
