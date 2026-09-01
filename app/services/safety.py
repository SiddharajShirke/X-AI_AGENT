from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import SafetyResult, StartupProfile


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _token_overlap(left: str, right: str) -> float:
    a = set(_normalize(left).split())
    b = set(_normalize(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class SafetyGuard:
    """Deterministic last-line guard for prototype demonstrations.

    It does not replace a production DLP system. It makes the safety invariant
    visible and testable even when no language-model API is configured.
    """

    def __init__(self, max_chars: int = 280):
        self.max_chars = max_chars

    def check(self, text: str, profile: StartupProfile) -> SafetyResult:
        reasons: list[str] = []
        matched: list[str] = []
        normalized = _normalize(text)

        if not text.strip():
            reasons.append("Post text is empty.")
        if len(text) > self.max_chars:
            reasons.append(f"Post exceeds the prototype limit of {self.max_chars} characters.")

        for phrase in profile.never_reveal:
            phrase_normalized = _normalize(phrase)
            if not phrase_normalized:
                continue
            direct = phrase_normalized in normalized
            overlap = _token_overlap(text, phrase)
            sequence = SequenceMatcher(None, normalized, phrase_normalized).ratio()
            if direct or (len(phrase_normalized.split()) >= 3 and max(overlap, sequence) >= 0.72):
                matched.append(phrase)
                reasons.append(f"Confidential information matched the never-reveal rule: {phrase}")

        for phrase in profile.banned_phrases:
            phrase_normalized = _normalize(phrase)
            if phrase_normalized and phrase_normalized in normalized:
                matched.append(phrase)
                reasons.append(f"Brand-language rule blocked the phrase: {phrase}")

        safe = not reasons
        return SafetyResult(
            safe=safe,
            status="safe" if safe else "blocked",
            reasons=reasons,
            matched_terms=matched,
        )
