# Startup X Agent — Codex-ready prototype

A deployable **prototype** of a human-in-the-loop X content agent for a startup. It creates domain-relevant posts without exposing the product idea, waits for explicit human approval, publishes only approved drafts via the Buffer API, and learns from rejections, edits, approvals, and unanswered review requests.

This repository is intentionally designed for demonstration and continued development with **OpenAI Codex** and Antigravity. It is not presented as production-grade infrastructure.

## What the prototype demonstrates

- A startup profile that can be changed after deployment without editing code.
- Ten editable posting contexts and ten editable daily timers.
- Optional current context from manual signals, RSS feeds, X recent search, trend keywords, and competitor account queries.
- Human-sounding generation through Groq when configured, with a credential-free demo writer as fallback.
- A hard “never reveal” layer and configurable banned wording.
- Exact and near-duplicate prevention against prior drafts.
- Dashboard, Telegram, and Slack review notifications.
- Explicit Approve, Reject + Regenerate, and Edit actions.
- No approval means no publication. Expired drafts are retained and regenerated.
- A self-improving retrieval loop based on positive and negative examples.
- Dry-run publishing by default; optional X publishing through the Buffer API.
- SQLite history, configuration versions, learned preferences, and an event log.
- Docker, Windows PowerShell, Linux/macOS, tests, Codex guidance, and Antigravity skills.

## Workflow

```text
Editable startup profile + 10 timers
                 │
                 ▼
          Scheduled trigger
                 │
                 ▼
       Load latest configuration
                 │
        ┌────────┴────────┐
        ▼                 ▼
Startup context      Current signals
                     X / RSS / manual
        │                 │
        └────────┬────────┘
                 ▼
         Generate one draft
                 │
                 ▼
      Confidentiality + style guard
                 │
                 ▼
         Similarity / history guard
                 │
                 ▼
        Human review notification
                 │
       ┌─────────┼──────────┐
       ▼         ▼          ▼
    Approve    Reject       Edit
       │         │           │
       ▼         ▼           ▼
 Final guard   Save lesson  Re-check
       │         │           │
       ▼         └──► New draft
 Dry-run / X           │
       │               └──► review again
       ▼
 History + future learning
```

## Fastest demonstration: no credentials

### Windows PowerShell

```powershell
Expand-Archive .\startup-x-agent-codex-prototype.zip
cd .\startup-x-agent-codex-prototype
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\start_local.ps1
```

Open `http://localhost:8000` and sign in with:

```text
Username: admin
Password: change-me
```

Change the password in `.env` before sharing a public deployment.

### Linux or macOS

```bash
unzip startup-x-agent-codex-prototype.zip
cd startup-x-agent-codex-prototype
./scripts/start_local.sh
```

### Manual setup

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Five-minute colleague demo

1. Open **Startup profile** and change the domain, audience, voice, public context, and never-reveal rules.
2. Open **10 posting slots** and change one context and timer.
3. Press **Generate a draft**.
4. Reject it with “Too generic” and add a note such as “Use a concrete founder observation.”
5. Observe that the original remains in history, new preference rules appear, and a different attempt is created.
6. Approve the replacement. The default publisher returns a visible dry-run result and never contacts X.
7. Show the activity and configuration-version history.

The same lifecycle can be demonstrated from a terminal:

```bash
python scripts/demo_flow.py
```

## Docker deployment

```bash
cp .env.example .env
# Edit ADMIN_PASSWORD at minimum.
docker compose up --build
```

The dashboard is available on port `8000`. SQLite data persists in the named Docker volume `x_agent_data`.

This single-process SQLite design is suitable for a prototype demonstration. Read `docs/SECURITY_AND_LIMITATIONS.md` before exposing it publicly.

## Dynamic settings available in the dashboard

The agent reads the newest values for every generation:

- startup name and domain;
- target audience;
- problems discussed;
- brand voice;
- public information;
- content pillars;
- things never to reveal;
- banned words and phrases;
- trend keywords;
- competitor X accounts;
- RSS sources;
- timezone;
- human approval timeout;
- maximum regeneration attempts;
- all ten context purposes, tones, and instructions;
- all ten times and enabled/disabled states.

A profile save creates a new configuration snapshot. Each draft records the configuration version that created it.

Use **high-level prohibition categories** in “things never to reveal,” such as “product architecture,” “unreleased features,” or “customer identities.” Do not paste passwords, API keys, source code, raw customer records, or a complete secret invention into the dashboard. When OpenAI generation is enabled, the safe startup context and prohibition rules are included in the model request so the writer can avoid them; the local deterministic guard checks the resulting draft again.

## How the self-improving loop works

This prototype does **retrieval-based learning**, not continual model training.

On rejection it stores:

- the full rejected draft;
- reason code;
- reviewer note;
- context and attempt;
- a reusable preference rule;
- a parent-child link to the replacement.

On human edit it stores the before/after relationship. On approval it stores the post as a positive example. The next prompt receives recent approved examples, rejected examples, and weighted preference rules. A separate similarity check prevents exact or near repetition even if generation ignores the prompt.

Automatic regeneration stops at the configured attempt limit. The chain then becomes `needs_guidance`, rather than looping forever.

## Groq generation

Demo mode works without a key. To use the Groq API for live content generation, set:

```env
GROQ_API_KEY=your-key
GROQ_MODEL=openai/gpt-oss-120b
```

The model ID contains the prefix `openai/`, but it is hosted and called through Groq. Do not create an OpenAI client. If the Groq call fails, the prototype logs a warning and falls back to the demo writer instead of breaking the review workflow.

## Provider activation stages

| Stage | GROQ_API_KEY | BUFFER_LIVE_POSTING | BUFFER_API_KEY + CHANNEL_ID | Active providers |
|---|---|---|---|---|
| 1 | absent | `false` | absent | DemoWriter + DryRunPublisher |
| 2 | present | `false` | absent | GroqWriter + DryRunPublisher |
| 3 | present | `false` | present | GroqWriter + **DryRunPublisher** |
| 4 | present | `true` | present | GroqWriter + BufferPublisher |

Set `BUFFER_LIVE_POSTING=true` only when an intentional test post is ready.

## Current context and competitor awareness

The trend collector can combine:

1. manual research signals entered in the dashboard;
2. configured RSS feeds;
3. configured trend keywords;
4. configured competitor handles;
5. optional X recent search when `TWITTER_BEARER_TOKEN` is present.

The source material is treated as a research signal. Prompts explicitly prohibit copying or close paraphrasing, and the startup’s private context never becomes part of an X search query.
External signal text is marked as untrusted reference data in the prompt, so instructions embedded in a post or feed cannot override the agent’s confidentiality rules.

```env
TWITTER_BEARER_TOKEN=your-x-bearer-token
```

## Telegram approval

Create a Telegram bot, add it to the intended private chat/channel, and set:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_WEBHOOK_SECRET=a-long-random-value
BASE_URL=https://your-public-domain.example
```

Register the webhook after deployment:

```bash
python scripts/set_telegram_webhook.py
```

The Telegram notification contains:

- the draft and context;
- an **Approve** callback;
- a **Reject + regenerate** callback;
- a dashboard link for editing.

Telegram rejection uses a generic rejection lesson. Richer written feedback is best entered through the dashboard.

## Slack review channel

Create a Slack incoming webhook and set:

```env
SLACK_WEBHOOK_URL=
```

Slack receives the draft and a dashboard review link. The prototype intentionally keeps the actual approval state in the application instead of treating a Slack message as approval.

## Live X publishing via Buffer

Publishing goes through the Buffer GraphQL API. Buffer then publishes to the connected X channel.
The application **never calls the X API directly** when publishing.

Live posting is deliberately locked behind two conditions:

```env
BUFFER_LIVE_POSTING=true
BUFFER_API_KEY=
BUFFER_CHANNEL_ID=
```

Without all values, the publisher remains in dry-run mode.

`TWITTER_BEARER_TOKEN`, if configured, is **only** used for X recent-search trend research — not for publishing.

This prototype publishes text-only posts. Media upload, Buffer analytics polling, publication reconciliation, retry queues, and OAuth account onboarding are outside this prototype's scope.

### Finding your Buffer channel ID

Use the Buffer API Explorer or these GraphQL queries:

**Step 1** — Retrieve organization IDs:

```graphql
query GetOrganizations {
  account {
    organizations {
      id
      name
    }
  }
}
```

**Step 2** — Retrieve connected channels:

```graphql
query GetChannels($organizationId: OrganizationId!) {
  channels(input: {organizationId: $organizationId}) {
    id
    name
    service
    isDisconnected
    isLocked
  }
}
```

Use the channel whose `service` is `twitter`. Set its `id` as `BUFFER_CHANNEL_ID`.

Note: `BUFFER_API_KEY` alone is not enough to target a post — `BUFFER_CHANNEL_ID` is also required.

## API and dashboard security

Dashboard and management API endpoints use HTTP Basic authentication from `.env`. The health endpoint is public. Telegram callbacks use the Telegram webhook secret header when configured.

This is enough for a controlled prototype, not a complete public-internet security model. For real use add a proper identity provider, CSRF protection, role-based access, secret management, encrypted backups, audit retention, and an external job queue.

## Tests

```bash
pytest -q
python -m compileall app scripts
```

Tests cover:

- seed data and ten slots;
- dynamic configuration versioning;
- confidentiality and banned wording;
- near-duplicate detection;
- feedback memory;
- human approval and dry-run publishing;
- rejection and regeneration;
- timeout behavior;
- edit safety;
- attempt limits;
- scheduler once-per-day behavior;
- API authentication and lifecycle;
- dashboard rendering;
- Codex and Antigravity skill files.

## Repository map

```text
app/
  config.py                 Environment settings
  db.py                     SQLite schema and seed data
  repository.py             Persistence API
  models.py                 Typed domain models
  services/
    generation.py           Demo + Groq writers
    trends.py               Manual, RSS, X, and demo signals
    safety.py               Never-reveal and brand-language guard
    similarity.py           Duplicate / near-duplicate guard
    feedback.py             Durable preference learning
    pipeline.py             End-to-end orchestration
    scheduler.py            Ten-slot timer loop
    notifiers.py            Console, Telegram, Slack
    publishers.py           Dry-run and Buffer publishers
  routes/                    JSON API, dashboard, Telegram webhook
  templates/                Server-rendered admin UI
  static/                   Dashboard CSS and JavaScript
codex/ and .codex/           Codex guidance and task skills
antigravity/ and .antigravity/ Antigravity guidance and task skills
docs/                       Architecture, lifecycle, demo, deployment
tests/                      Behavior-first test suite
scripts/                    Seed, demo, webhook, startup, packaging
```

## Continue development with Codex

Start Codex at the repository root so it automatically reads `AGENTS.md`. For a focused task, instruct it to read one skill first:

```text
Read codex/skills/feedback-loop/SKILL.md and AGENTS.md, then add reviewer analytics without weakening the approval invariant.
```

The skill set covers architecture, scheduler, trends, generation, safety, feedback, approvals, X publishing, dashboard, testing, and deployment. See `docs/CODEX_GUIDE.md`.

## Antigravity

The same component skills are mirrored under `antigravity/skills/` and `.antigravity/skills/`. See `docs/ANTIGRAVITY_GUIDE.md`.

## Important prototype limitations

- One application process should own the scheduler.
- SQLite is not intended here for horizontally scaled writes.
- Basic authentication is demonstration-level.
- The deterministic guard cannot guarantee protection against every indirect disclosure.
- Generated content still requires a responsible human reviewer.
- Buffer API access depends on your Buffer account and connected X channel.
- Ten posts every day can overwhelm an audience; the dashboard makes slots individually disableable.
- Live Buffer reconciliation (confirming actual X publication) and media upload remain out of scope.

Read the detailed limitations and the production upgrade checklist in `docs/SECURITY_AND_LIMITATIONS.md`.
