# Codex development guide

## Start correctly

Open a terminal at the repository root, then start Codex. Root `AGENTS.md` contains repository-wide invariants and commands.

For component work, explicitly load the matching skill:

```text
Read AGENTS.md and codex/skills/safety-guard/SKILL.md. Add a new configurable disclosure rule, write the failing test first, and keep demo mode credential-free.
```

## Skill index

- `architecture`: module boundaries and lifecycle changes.
- `scheduler`: timing, timezones, timeout behavior.
- `trend-collector`: X, RSS, manual, and source handling.
- `content-generation`: prompt and writer changes.
- `safety-guard`: confidentiality and brand constraints.
- `feedback-loop`: rejection, edit, and preference memory.
- `approval-flow`: pending, approval, rejection, edit, timeout.
- `x-publisher`: dry-run and live posting adapters.
- `dashboard`: forms, server-rendered UI, dynamic settings.
- `testing`: behavior-first pytest workflow.
- `deployment`: Docker, environment, packaging, smoke checks.

## Recommended Codex prompt pattern

```text
Task: <one focused change>
Read first: AGENTS.md and codex/skills/<skill>/SKILL.md
Constraints: preserve explicit approval; never weaken never-reveal; demo mode must work without credentials
Verification: run the exact focused tests, then pytest -q and python -m compileall app scripts
Deliver: changed files, test evidence, and any prototype limitation introduced
```

## Repository commands

```bash
pytest -q
python scripts/demo_flow.py
uvicorn app.main:app --reload
python -m compileall app scripts
```

## Do not ask Codex to

- bypass human approval;
- default to live X posting;
- remove rejected history;
- send private startup context to trend searches;
- hard-code startup context that belongs in the dashboard;
- replace the deterministic safety guard with prompt-only safety.
