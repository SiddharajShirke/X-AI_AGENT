---
name: startup-x-agent-feedback-loop
description: "Change rejection, edit, approval examples, learned preferences, or regeneration memory."
---

# Feedback Loop

## Purpose

Turn human decisions into reusable context while retaining every original draft.

## Read first

app/services/feedback.py, app/services/pipeline.py, app/repository.py, tests/test_pipeline.py

## Workflow

1. Write a failing test for the human decision and expected future lesson.
2. Record the immutable original and a structured feedback event.
3. Upsert concise preference rules with bounded weight changes.
4. Ensure the regenerated child links to its parent and increments attempt.

## Constraints

- Do not fine-tune a model on every rejection.
- Do not delete or overwrite the rejected original.
- Do not create an unbounded regeneration loop.

## Verification

```bash
pytest tests/test_pipeline.py tests/test_guards_and_feedback.py -q
```
```bash
python scripts/demo_flow.py
```
```bash
pytest -q
```
