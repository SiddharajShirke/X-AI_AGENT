# Colleague demonstration script

## Preparation

1. Copy `.env.example` to `.env`.
2. Change `ADMIN_PASSWORD`.
3. Leave OpenAI, Telegram, Slack, and X credentials blank for the first demo.
4. Start with `docker compose up --build` or `scripts/start_local.ps1`.

## Seven-minute walkthrough

### Minute 1 — Explain the safety boundary

Open **Startup profile**. Show public information and **Things never to reveal**. Explain that the agent talks around the startup’s domain, not about its hidden implementation.

### Minute 2 — Show dynamic configuration

Change the domain, audience, and brand voice. Save. Point out the configuration version increase and explain that no code or redeployment was needed.

### Minute 3 — Show the ten contexts and times

Open **10 posting slots**. Change a time and edit the context instructions. Explain that every slot can use a different content purpose and can be disabled.

### Minute 4 — Generate

Choose a context and press **Generate a draft**. Show the pending state, context, attempt, source summary, and similarity score. Explain that no publication happened.

### Minute 5 — Reject and teach

Choose **Too generic**, add “Use a specific founder observation and avoid corporate language,” then reject. Show:

- the original in history;
- a new child draft;
- attempt increment;
- different wording and angle;
- new learned preference rules.

### Minute 6 — Approve

Approve the replacement. Show the `published` state and dry-run provider. Explain that real X remains locked behind an explicit environment flag and credentials.

### Minute 7 — Show no-response behavior

Explain the configurable timeout: silence expires a draft and produces another candidate; it never becomes implicit approval. Show the event log and lifecycle document.

## Optional integrations

Repeat with OpenAI generation, Telegram buttons, Slack notification, and direct X posting only after the offline workflow has been demonstrated safely.
