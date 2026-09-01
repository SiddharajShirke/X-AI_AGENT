# Antigravity development guide

The Antigravity instructions mirror the Codex component boundaries so both environments make compatible changes.

1. Open the repository root.
2. Keep root `AGENTS.md` in context.
3. Select the task skill under `antigravity/skills/<component>/SKILL.md` or `.antigravity/skills/`.
4. Make one focused change.
5. Run the skill’s verification commands.

Example instruction:

```text
Read AGENTS.md and antigravity/skills/feedback-loop/SKILL.md. Add an admin control to deactivate a learned preference. Preserve rejection history and add tests first.
```

The visible `antigravity/` folder and hidden `.antigravity/` folder contain the same skills for compatibility with different workspace conventions.
