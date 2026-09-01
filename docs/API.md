# Prototype API

All management routes except health and the Telegram webhook require HTTP Basic authentication.

## Health

- `GET /health`
- `GET /api/health`

## Configuration

- `GET /api/profile`
- `PUT /api/profile`
- `GET /api/contexts`
- `PUT /api/contexts/{id}`
- `GET /api/schedules`
- `PUT /api/schedules/{id}`

## Draft lifecycle

- `GET /api/drafts`
- `POST /api/drafts/generate`
- `POST /api/drafts/{id}/approve`
- `POST /api/drafts/{id}/reject`
- `POST /api/drafts/{id}/edit`

Example rejection body:

```json
{
  "reason": "too_generic",
  "notes": "Use a concrete founder observation.",
  "reviewer": "founder"
}
```

## Research and memory

- `GET /api/trends`
- `POST /api/trends`
- `GET /api/preferences`
- `GET /api/events`
- `GET /api/state`

## Telegram

- `POST /integrations/telegram/webhook`

When `TELEGRAM_WEBHOOK_SECRET` is configured, the route validates the `X-Telegram-Bot-Api-Secret-Token` header.
