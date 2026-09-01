---
name: startup-x-agent-trend-collector
description: "Add or change manual, RSS, X, competitor, or news research signals."
---

# Trend Collector

## Purpose

Provide current domain context without copying competitors or exposing private startup information.

## Read first

app/services/trends.py, app/services/prompts.py, docs/LIFECYCLE.md

## Workflow

1. Define the new source as a TrendItem adapter.
2. Test failure handling and source labeling without requiring a live network.
3. Limit text, URLs, result count, and request timeout.
4. Keep source material separate from generated output.

## Constraints

- Never send never_reveal or private product text in a search query.
- Never copy a competitor post into generated content.
- External source failure must degrade to stored or demo signals.

## Verification

```bash
pytest tests/test_generation.py tests/test_pipeline.py -q
```
```bash
pytest -q
```
