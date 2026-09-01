---
name: startup-x-agent-approval-flow
description: "Change dashboard, API, Telegram, edit, reject, timeout, or approve state transitions."
---

# Approval Flow

## Purpose

Guarantee that only an explicit human approval reaches a publisher.

## Read first

app/services/pipeline.py, app/routes/api.py, app/routes/ui.py, app/routes/telegram.py, tests/test_pipeline.py

## Workflow

1. Write a failing state-transition test.
2. Keep status validation in Pipeline.
3. Re-run safety immediately before publication.
4. Record reviewer, timestamps, feedback, and event-log entries.

## Constraints

- Silence and timeout are never approval.
- Only pending drafts may be reviewed.
- A notification delivery result is never a review decision.

## Verification

```bash
pytest tests/test_pipeline.py tests/test_api.py -q
```
```bash
pytest -q
```
