---
name: startup-x-agent-scheduler
description: "Change the ten-slot scheduler, timezone behavior, or approval timeout processing."
---

# Scheduler

## Purpose

Trigger each enabled slot once per local day and ensure silence never publishes content.

## Read first

app/services/scheduler.py, app/services/pipeline.py, app/repository.py, tests/test_scheduler.py

## Workflow

1. Write a failing test with an explicit timezone-aware datetime.
2. Preserve one-run-per-local-date through last_run_date.
3. Route generation and expiration through Pipeline, never directly to a publisher.
4. Keep the scheduler safe for one prototype process.

## Constraints

- Never interpret elapsed time as approval.
- Never call an external publisher from SchedulerService.
- Document any move toward multi-process scheduling.

## Verification

```bash
pytest tests/test_scheduler.py tests/test_pipeline.py -q
```
```bash
pytest -q
```
