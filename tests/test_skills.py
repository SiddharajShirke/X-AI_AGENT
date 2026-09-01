from __future__ import annotations

from pathlib import Path


REQUIRED = {
    "architecture",
    "scheduler",
    "trend-collector",
    "content-generation",
    "safety-guard",
    "feedback-loop",
    "approval-flow",
    "x-publisher",
    "dashboard",
    "testing",
    "deployment",
}


def test_codex_and_antigravity_have_all_focused_skills():
    root = Path(__file__).resolve().parents[1]
    for platform in (".codex", ".antigravity", "codex", "antigravity"):
        skills_root = root / platform / "skills"
        found = {path.parent.name for path in skills_root.glob("*/SKILL.md")}
        assert found == REQUIRED
        for path in skills_root.glob("*/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            assert "## Purpose" in text
            assert "## Workflow" in text
            assert "## Constraints" in text
            assert "## Verification" in text
