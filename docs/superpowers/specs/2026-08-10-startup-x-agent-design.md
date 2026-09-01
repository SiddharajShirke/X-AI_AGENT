# Startup X Agent Prototype — Design Specification

## Goal

Build a deployable, prototype-level Startup X Agent that creates distinct, human-sounding posts about a configurable startup domain, researches current context, protects confidential product details, requests explicit human approval, publishes only approved drafts, and improves future drafts from approvals, edits, rejections, and timeouts.

## Prototype boundaries

This repository demonstrates the complete workflow rather than production scale. It uses one FastAPI process, SQLite, a lightweight in-process scheduler, a server-rendered admin dashboard, and optional external integrations. Demo mode requires no external credentials and never posts to X.

## Safety invariants

1. Silence is never approval.
2. A draft is published only after an explicit approval action.
3. The latest “never reveal” configuration overrides trends, feedback, and creativity.
4. Rejected or expired drafts are retained and influence regeneration.
5. A rejected draft is not merely paraphrased; the next attempt must change angle and pass similarity checks.
6. Automatic regeneration stops after the configured maximum attempts.
7. Live X posting is disabled by default and requires an explicit environment flag plus credentials.

## Runtime architecture

```text
Editable Admin Configuration
        │
        ▼
SQLite configuration + history
        │
Scheduled slot / manual trigger
        │
        ▼
Trend Collector ──► Content Planner
                         │
Approved examples ───────┤
Rejected examples ───────┤
Learned preferences ─────┤
                         ▼
                  Content Generator
                         │
                 Confidentiality Guard
                         │
                 Similarity Guard
                         │
                         ▼
              Dashboard / Telegram / Slack
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
    Approve            Reject             Edit
       │                 │                 │
Final safety check  Feedback memory   Re-check content
       │                 │                 │
       ▼                 └──── Regenerate ─┘
Dry-run or X publisher
       │
       ▼
Post history + event log
```

## Components

- **Configuration repository:** startup profile, 10 editable contexts, 10 editable schedules, trend sources, confidentiality rules, and version snapshots.
- **Scheduler:** checks enabled schedules in the configured timezone and triggers each slot once per local day.
- **Trend collector:** combines manual trends, configured keywords, competitor handles, and optional RSS items. The prototype never copies source text into a post.
- **Content planner:** chooses a safe angle using the slot purpose, current trend context, recent history, and feedback memory.
- **Content generator:** uses the OpenAI Responses API when configured; otherwise a deterministic demo writer produces usable drafts.
- **Safety guard:** blocks forbidden phrases, suspicious overlap with confidential statements, empty content, and excessive length.
- **Similarity guard:** compares a candidate with recent approved, pending, rejected, and expired drafts using normalized exact match, token overlap, and sequence similarity.
- **Feedback engine:** records decisions, converts reason codes and notes into durable weighted preferences, and provides positive and negative examples to future prompts.
- **Approval manager:** supports dashboard actions, Telegram callback buttons, Slack notification links, editing, rejection, timeout, and bounded regeneration.
- **Publisher:** dry-run by default; optional direct X publishing through Tweepy when explicitly enabled.
- **Dashboard:** updates all runtime context without code changes or redeployment and shows drafts, learned rules, trends, configuration versions, and activity.

## Dynamic configuration

The agent loads the latest configuration at each generation. Administrators can change startup name, domain, audience, problems, brand voice, public information, confidential information, content pillars, banned phrases, trend keywords, competitor accounts, RSS sources, timezone, approval timeout, attempt limit, content contexts, and posting times.

Every profile update creates a configuration snapshot. Each draft records the configuration version used for generation.

## Learning model

This prototype uses retrieval-based learning, not model fine-tuning. It stores:

- approved posts as positive examples;
- rejected and expired posts as negative examples;
- human-edited before/after text;
- reason codes and reviewer notes;
- derived preference rules with weights.

Future prompts retrieve relevant examples and active rules. Similarity checks independently prevent exact or near repetition.

## Deployment

- Local: Python virtual environment and Uvicorn.
- Demonstration: Docker Compose with a persistent `/data` volume.
- External services: OpenAI, Telegram, Slack, and X are enabled independently through `.env` values.

## Success criteria

A colleague can start the app, edit the startup profile and 10 schedules, generate a draft, reject it, observe a different regenerated draft and learned rule, approve the new draft, see a dry-run post result, and optionally connect real OpenAI, Telegram, Slack, and X credentials without changing source code.
