# Prototype deployment

## Docker Compose

1. Copy `.env.example` to `.env`.
2. Set a strong admin password.
3. Run `docker compose up --build`.
4. Put a TLS reverse proxy or managed HTTPS service in front before enabling Telegram webhooks.

## Hosted container service

Use the included Dockerfile and configure:

- command: the Dockerfile default;
- port: `8000`;
- persistent disk mounted at `/data`;
- `DATABASE_PATH=/data/x_agent.db`;
- `BASE_URL` set to the public HTTPS origin;
- one running application instance while the in-process scheduler is enabled.

Horizontal scaling is outside the prototype design. For multiple instances, move scheduling to a job queue and SQLite to a managed database first.

## Deployment acceptance checks

```bash
curl https://your-host/health
python scripts/set_telegram_webhook.py  # only after Telegram values are set
```

Then use the dashboard to run generate → reject → regenerate → approve while `X_LIVE_POSTING=false`.
