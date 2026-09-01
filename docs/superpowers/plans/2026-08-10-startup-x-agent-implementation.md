# Startup X Agent Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable prototype demonstrating dynamic startup configuration, scheduled content creation, trend context, confidentiality and similarity checks, human approval, rejection-driven regeneration, feedback memory, and optional X/Telegram/Slack/OpenAI integrations.

**Architecture:** A single FastAPI service uses SQLite repositories and focused services. It serves a Jinja dashboard, runs a lightweight timezone-aware scheduler, and selects demo or live adapters from environment configuration. The pipeline always stores decisions and publishes only after explicit approval.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, Pydantic Settings, SQLite, httpx, OpenAI Python SDK, optional Tweepy, pytest, Docker Compose.

## Global Constraints

- Prototype-level code, not production-grade distributed infrastructure.
- Demo mode must run without external credentials.
- Live posting must be disabled by default.
- Silence must never count as approval.
- Rejected or expired drafts must be retained and used as negative examples.
- Maximum regeneration attempts must be configurable.
- All startup context and schedules must be editable after deployment.

---

### Task 1: Repository, configuration, and database schema

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `.env.example`, `app/config.py`, `app/db.py`, `app/models.py`, `app/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Produces: `Settings`, `Database.initialize()`, and `Repository` CRUD methods consumed by every service.

- [ ] Write repository tests asserting default profile, ten contexts, ten schedules, profile snapshots, draft persistence, and preference persistence.
- [ ] Run `pytest tests/test_repository.py -q` and verify failure because the application package does not exist.
- [ ] Implement schema creation, deterministic seed data, transaction helpers, typed row conversion, and repository methods.
- [ ] Run the test again and verify it passes.
- [ ] Commit with `git commit -m "feat: add configuration database and repositories"`.

### Task 2: Safety, similarity, feedback, and prompt context

**Files:**
- Create: `app/services/safety.py`, `app/services/similarity.py`, `app/services/feedback.py`, `app/services/prompts.py`
- Test: `tests/test_guards_and_feedback.py`

**Interfaces:**
- Produces: `SafetyGuard.check(text, profile)`, `SimilarityGuard.score(text, history)`, `FeedbackEngine.record_rejection(...)`, and `PromptBuilder.build(...)`.

- [ ] Write tests proving forbidden disclosure is blocked, duplicate content is rejected, rejection creates a learned preference, and prompt context contains approved and rejected examples.
- [ ] Run the focused test and verify missing-module failures.
- [ ] Implement minimal deterministic services with no external calls.
- [ ] Run the focused and repository tests and verify they pass.
- [ ] Commit with `git commit -m "feat: add safety similarity and feedback memory"`.

### Task 3: Trend collection and content generation

**Files:**
- Create: `app/services/trends.py`, `app/services/generation.py`
- Test: `tests/test_generation.py`

**Interfaces:**
- Produces: `TrendCollector.collect(profile)`, `DemoWriter.generate(context)`, `OpenAIWriter.generate(context)`, and `ContentGenerator.generate(...)`.

- [ ] Write tests proving demo generation varies by attempt, respects the chosen context, and never returns a stored rejected draft.
- [ ] Run the focused test and verify failure.
- [ ] Implement manual/RSS trend aggregation, prompt assembly, deterministic demo output, and optional OpenAI Responses API adapter.
- [ ] Run the focused and full unit suite.
- [ ] Commit with `git commit -m "feat: add trend collection and content generation"`.

### Task 4: Notifications, publishing, and the orchestration pipeline

**Files:**
- Create: `app/services/notifiers.py`, `app/services/publishers.py`, `app/services/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `Pipeline.generate_draft()`, `Pipeline.approve()`, `Pipeline.reject_and_regenerate()`, `Pipeline.edit()`, and `Pipeline.expire_and_regenerate()`.

- [ ] Write tests proving approval publishes, rejection stores learning and creates a different pending draft, timeout never publishes, unsafe edits are blocked, and attempt limits stop regeneration.
- [ ] Run the focused test and verify failure.
- [ ] Implement dry-run/X publishers, console/Telegram/Slack notifications, and transactional orchestration.
- [ ] Run the focused and full test suite.
- [ ] Commit with `git commit -m "feat: add approval feedback and publishing pipeline"`.

### Task 5: Scheduler and FastAPI endpoints

**Files:**
- Create: `app/services/scheduler.py`, `app/dependencies.py`, `app/routes/api.py`, `app/routes/telegram.py`, `app/main.py`
- Test: `tests/test_api.py`, `tests/test_scheduler.py`

**Interfaces:**
- Produces: FastAPI app, authenticated configuration/draft endpoints, Telegram callback webhook, public health endpoint, and background schedule polling.

- [ ] Write API and scheduler tests for health, authentication, profile updates, generation, rejection, approval, one-run-per-day scheduling, and timeout regeneration.
- [ ] Run focused tests and verify failure.
- [ ] Implement the application factory, HTTP Basic guard, JSON/form routes, Telegram webhook validation, and lifecycle-managed scheduler.
- [ ] Run the focused and full test suite.
- [ ] Commit with `git commit -m "feat: add api and dynamic scheduler"`.

### Task 6: Demonstration dashboard

**Files:**
- Create: `app/routes/ui.py`, `app/templates/base.html`, `app/templates/dashboard.html`, `app/static/app.css`, `app/static/app.js`
- Test: extend `tests/test_api.py`

**Interfaces:**
- Produces: a responsive server-rendered dashboard for configuration, schedules, trends, drafts, feedback, and review actions.

- [ ] Add a test asserting the authenticated dashboard renders startup configuration and draft review controls.
- [ ] Run it and verify failure.
- [ ] Implement dashboard routes, forms, templates, and minimal JavaScript for tabs and confirmation prompts.
- [ ] Run all tests.
- [ ] Commit with `git commit -m "feat: add prototype admin and review dashboard"`.

### Task 7: Codex and Antigravity agent guidance

**Files:**
- Create: `AGENTS.md`, `.codex/README.md`, `.antigravity/README.md`
- Create one `SKILL.md` under both `.codex/skills/` and `.antigravity/skills/` for architecture, scheduler, trends, generation, safety, feedback, approval, publisher, dashboard, testing, and deployment.

**Interfaces:**
- Produces: task-specific instructions that future Codex or Antigravity sessions can activate without re-learning the repository.

- [ ] Write a structure test that checks every required skill exists and contains purpose, workflow, constraints, and verification sections.
- [ ] Run it and verify failure.
- [ ] Add root instructions and mirrored focused skills.
- [ ] Run the structure and full suites.
- [ ] Commit with `git commit -m "docs: add codex and antigravity agent skills"`.

### Task 8: Documentation, deployment, and packaging

**Files:**
- Create: `README.md`, `docs/ARCHITECTURE.md`, `docs/DEMO_SCRIPT.md`, `docs/LIFECYCLE.md`, `docs/CODEX_GUIDE.md`, `docs/ANTIGRAVITY_GUIDE.md`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `scripts/seed_demo.py`, `scripts/set_telegram_webhook.py`, `.gitignore`, `LICENSE`

**Interfaces:**
- Produces: local and Docker run paths, demo walkthrough, integration instructions, lifecycle documentation, and a distributable ZIP.

- [ ] Add smoke tests for application import, database seeding, and documentation file presence.
- [ ] Run them and verify failure.
- [ ] Add deployment files, scripts, end-to-end documentation, and troubleshooting guidance.
- [ ] Run `pytest -q`, `python -m compileall app scripts`, and an HTTP smoke test against a temporary database.
- [ ] Commit with `git commit -m "docs: complete deployment and demo package"`.
- [ ] Create the ZIP while excluding `.git`, virtual environments, caches, local databases, and secrets.
