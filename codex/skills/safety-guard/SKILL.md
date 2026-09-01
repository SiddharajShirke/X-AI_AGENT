---
name: startup-x-agent-safety-guard
description: "Change never-reveal, banned language, length, or disclosure detection."
---

# Safety Guard

## Purpose

Block drafts and edits that reveal protected information or violate configured public wording.

## Read first

app/services/safety.py, app/services/pipeline.py, tests/test_guards_and_feedback.py, tests/test_pipeline.py

## Workflow

1. Add a failing test for the exact unsafe and safe contrast.
2. Implement deterministic matching that is explainable in the dashboard/event log.
3. Re-run the final pre-publish check path.
4. Keep blocked content stored for audit but never published.

## Constraints

- Never replace deterministic safety with prompt-only instructions.
- Never let learned preferences override never_reveal.
- Avoid broad rules that block ordinary domain discussion without evidence.

## Verification

```bash
pytest tests/test_guards_and_feedback.py tests/test_pipeline.py -q
```
```bash
pytest -q
```
