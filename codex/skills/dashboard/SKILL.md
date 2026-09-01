---
name: startup-x-agent-dashboard
description: "Change the server-rendered configuration and human review dashboard."
---

# Dashboard

## Purpose

Let a non-technical reviewer change startup context, timers, and review decisions after deployment.

## Read first

app/routes/ui.py, app/templates/dashboard.html, app/static/app.css, tests/test_api.py

## Workflow

1. Add a failing authenticated page or form test.
2. Keep forms thin and call repository or Pipeline methods.
3. Show status, attempt, configuration version, and learning effects clearly.
4. Test mobile-readable markup and no-draft empty states.

## Constraints

- Do not put business rules in Jinja or JavaScript.
- Do not create a UI path that bypasses Pipeline.
- Keep never-reveal fields visually prominent.

## Verification

```bash
pytest tests/test_api.py -q
```
```bash
pytest -q
```
