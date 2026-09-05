# Prototype API

`GET /health`, `GET /api/health`, Telegram webhook, and signed Slack callbacks are the only non-Basic-auth paths. All management resources use explicit account IDs.

## Accounts and configuration

- `GET|POST /api/accounts`
- `GET|PUT /api/accounts/{account_id}`
- `GET|PUT /api/accounts/{account_id}/profile`
- `GET /api/accounts/{account_id}/contexts`
- `PUT /api/accounts/{account_id}/contexts/{context_id}`
- `GET /api/accounts/{account_id}/schedules`
- `PUT /api/accounts/{account_id}/schedules/{schedule_id}`
- `GET /api/accounts/{account_id}/state`

Creating an account accepts `name`, `handle`, `timezone`, and optional `copy_from_id`. Copying excludes drafts, feedback, learned preferences, events, and integrations.

## Draft lifecycle

- `GET /api/accounts/{account_id}/drafts`
- `POST /api/accounts/{account_id}/drafts/generate`
- `POST /api/accounts/{account_id}/drafts/{draft_id}/approve`
- `POST /api/accounts/{account_id}/drafts/{draft_id}/reject`
- `POST /api/accounts/{account_id}/drafts/{draft_id}/edit`
- `POST /api/accounts/{account_id}/drafts/{draft_id}/retry`

The authenticated Basic username becomes the reviewer. Any `reviewer` field in a JSON body is ignored.

## Research and history

- `GET|POST /api/accounts/{account_id}/trends`
- `GET /api/accounts/{account_id}/preferences`
- `GET /api/accounts/{account_id}/events`

## Encrypted connections

- `GET|POST /api/connections`
- `GET /api/accounts/{account_id}/integrations`
- `PUT /api/accounts/{account_id}/integrations/{buffer|slack}`
- `POST /api/accounts/{account_id}/integrations/{buffer|slack}/test`
- `DELETE /api/accounts/{account_id}/integrations/{buffer|slack}`

Connection responses include label and `credentials_configured`; they never include plaintext or encrypted credential values. A binding body contains `provider`, `connection_id`, `target_id`, and `enabled`.

## Provider callbacks

- `POST /integrations/slack/{connection_id}/actions`
- `POST /integrations/telegram/webhook`

Slack action bodies are parsed only after signature verification. Slack values contain both `x_account_id` and `draft_id`, and the route verifies that the URL connection is the account's enabled binding.

The Slack callback URL must be copied exactly from **Connections** for the saved Slack connection: `https://your-public-origin/integrations/slack/<connection-id>/actions`. Use the same Slack app as the incoming webhook, with Socket Mode Off and Interactivity & Shortcuts On. An outbound webhook test does not exercise this endpoint; a verified human click does.
