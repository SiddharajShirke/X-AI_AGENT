from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import SimilarityResult


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def token_jaccard(left: str, right: str) -> float:
    a = set(normalize_text(left).split())
    b = set(normalize_text(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity_score(left: str, right: str) -> float:
    a = normalize_text(left)
    b = normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sequence = SequenceMatcher(None, a, b).ratio()
    jaccard = token_jaccard(a, b)
    containment = 0.0
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if a_tokens and b_tokens:
        containment = len(a_tokens & b_tokens) / min(len(a_tokens), len(b_tokens))
    # Containment is valuable for paraphrase-like short posts; sequence keeps
    # reordered versions high enough to be caught in this prototype.
    return round(max(sequence, jaccard, (sequence + containment) / 2), 4)


class SimilarityGuard:
    def __init__(self, threshold: float = 0.78):
        self.threshold = threshold

    def check(self, text: str, history: list[str]) -> SimilarityResult:
        best_score = 0.0
        best_text = ""
        for previous in history:
            score = similarity_score(text, previous)
            if score > best_score:
                best_score = score
                best_text = previous
        return SimilarityResult(
            unique=best_score < self.threshold,
            score=best_score,
            most_similar_text=best_text,
        )
