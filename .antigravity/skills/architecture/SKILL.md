---
name: startup-x-agent-architecture
description: "Plan or review changes that cross multiple Startup X Agent components."
---

# Architecture

## Purpose

Protect component boundaries and the end-to-end lifecycle while evolving the prototype.

## Read first

docs/ARCHITECTURE.md, docs/LIFECYCLE.md, app/services/pipeline.py, app/repository.py

## Workflow

1. State the user-visible behavior and affected lifecycle stage.
2. Identify the smallest component boundary that can own the change.
3. Write a failing behavior test before changing implementation.
4. Keep routes thin and put sequencing in Pipeline.
5. Update architecture or lifecycle docs when contracts change.

## Constraints

- Do not create a single all-purpose agent class.
- Do not move dynamic startup data into source constants.
- Do not weaken explicit approval, confidentiality, or retry limits.

## Verification

```bash
pytest -q
```
```bash
python -m compileall app scripts
```
```bash
python scripts/demo_flow.py
```
