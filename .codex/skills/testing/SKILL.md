---
name: startup-x-agent-testing
description: "Add or improve tests for the Startup X Agent prototype."
---

# Testing

## Purpose

Prove lifecycle behavior with deterministic, network-free tests.

## Read first

tests/, AGENTS.md, docs/LIFECYCLE.md

## Workflow

1. Name the production behavior that would make the test fail.
2. Write one focused failing test and observe the expected failure.
3. Implement the minimum change, run the focused test, then the full suite.
4. Use temporary SQLite paths and MockTransport for HTTP adapters.

## Constraints

- Do not require live OpenAI, X, Telegram, Slack, or RSS access.
- Do not assert implementation details when public behavior is available.
- Never weaken a test to make unsafe behavior pass.

## Verification

```bash
pytest -q
```
```bash
python -m compileall app scripts
```
