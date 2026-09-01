---
name: startup-x-agent-deployment
description: "Change Docker, environment, startup scripts, packaging, or hosted prototype instructions."
---

# Deployment

## Purpose

Make the prototype reproducible while keeping demo defaults safe.

## Read first

Dockerfile, docker-compose.yml, .env.example, docs/DEPLOYMENT.md, docs/SECURITY_AND_LIMITATIONS.md

## Workflow

1. Preserve a no-credential local path.
2. Keep runtime data outside the image and secrets outside version control.
3. Run tests, compile checks, CLI demo, and an HTTP health smoke test.
4. Update README and deployment docs with any new variable or command.

## Constraints

- Do not bake .env or credentials into images or ZIP files.
- Do not enable live X posting by default.
- Do not claim the single-process SQLite scheduler is horizontally scalable.

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
```bash
docker compose config
```
