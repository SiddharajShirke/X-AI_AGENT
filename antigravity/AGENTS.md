# AGENTS.md — Startup X Agent prototype

## Mission

Maintain a clear, demonstrable human-in-the-loop X workflow for a dynamically configured startup. This is prototype code, but its safety invariants are mandatory.

## Non-negotiable invariants

1. Never publish without an explicit human approval action.
2. Silence or timeout never counts as approval.
3. `never_reveal` overrides trends, learned preferences, and generation creativity.
4. Keep rejected, expired, and edited originals as learning history.
5. Regeneration must create a materially different draft and pass similarity checks.
6. Stop automatic regeneration at the configured attempt limit.
7. Keep `X_LIVE_POSTING=false` as the default.
8. Demo mode must run without OpenAI, X, Telegram, or Slack credentials.
9. Startup context, contexts, and schedules belong in the database/dashboard, not hard-coded feature logic.
10. Add or change behavior with a failing test first.

## Architecture boundaries

- `app/repository.py`: persistence only.
- `app/services/pipeline.py`: lifecycle sequencing.
- `app/services/safety.py`: deterministic confidentiality and style guard.
- `app/services/similarity.py`: duplicate prevention.
- `app/services/feedback.py`: human-learning memory.
- `app/services/generation.py`: writer adapters.
- `app/services/trends.py`: external/current signal adapters.
- `app/services/notifiers.py`: review notifications.
- `app/services/publishers.py`: dry-run/live publication.
- `app/routes/`: transport and forms, not business rules.

## Required verification

```bash
pytest -q
python -m compileall app scripts
python scripts/demo_flow.py
```

For web changes, also start the app and verify `/health`, authenticated `/`, generation, rejection, regeneration, and approval.

## Focused skills

Read the relevant file under `codex/skills/` (Codex) or `antigravity/skills/` before changing a component. Mirrored hidden folders are included for workspace compatibility.
