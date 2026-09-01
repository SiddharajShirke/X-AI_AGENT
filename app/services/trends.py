from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from typing import Iterable

import httpx

from app.config import Settings
from app.models import StartupProfile, TrendItem
from app.repository import Repository

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    import re

    return " ".join(re.sub(r"<[^>]+>", " ", unescape(text or "")).split())


class TrendCollector:
    """Collects safe high-level signals; it never returns source text as a draft."""

    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository

    def collect(self, profile: StartupProfile) -> list[TrendItem]:
        items = self.repository.list_trends(limit=8)
        if self.settings.twitter_bearer_token:
            items.extend(self._x_recent_items(profile))
        if profile.rss_feeds:
            items.extend(self._rss_items(profile.rss_feeds[:4]))
        if not items:
            items.extend(self._demo_signals(profile))
        # De-duplicate by title while preserving order and cap prompt size.
        seen: set[str] = set()
        unique: list[TrendItem] = []
        for item in items:
            key = item.title.lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        return unique[:8]


    def _x_recent_items(self, profile: StartupProfile) -> list[TrendItem]:
        """Optional X recent-search adapter.

        The returned snippets are research signals only. The prompt and
        similarity guard explicitly prohibit copying their wording.
        """
        try:
            import tweepy

            terms = [item.strip() for item in profile.trend_keywords if item.strip()][:3]
            competitors = [
                item.strip().lstrip("@") for item in profile.competitor_accounts if item.strip()
            ][:3]
            clauses = [f'"{term}" -is:retweet' for term in terms]
            clauses.extend(f"from:{handle} -is:retweet" for handle in competitors)
            if not clauses:
                return []
            query = " OR ".join(f"({clause})" for clause in clauses)
            client = tweepy.Client(bearer_token=self.settings.twitter_bearer_token)
            response = client.search_recent_tweets(
                query=query,
                max_results=10,
                tweet_fields=["created_at", "public_metrics"],
            )
            items: list[TrendItem] = []
            for post in response.data or []:
                compact = " ".join(str(post.text).split())
                metrics = getattr(post, "public_metrics", None) or {}
                score = 0.7 + min(float(metrics.get("like_count", 0)) / 1000.0, 0.25)
                items.append(
                    TrendItem(
                        title="Recent X discussion in the configured domain",
                        summary=compact[:320],
                        source="x-recent-search",
                        url=f"https://x.com/i/status/{post.id}",
                        score=score,
                        collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    )
                )
            return items
        except Exception as exc:
            logger.warning("X recent-search collection failed: %s", exc)
            return []

    def _demo_signals(self, profile: StartupProfile) -> list[TrendItem]:
        keywords = profile.trend_keywords or profile.content_pillars or [profile.domain]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        signals = []
        for index, keyword in enumerate(keywords[:3]):
            signals.append(
                TrendItem(
                    title=f"Current discussion around {keyword}",
                    summary=(
                        f"Teams in {profile.domain} are comparing practical trade-offs around "
                        f"{keyword}, repeatability, and responsible adoption."
                    ),
                    source="demo-signal",
                    url="",
                    score=1.0 - index * 0.1,
                    active=True,
                    collected_at=now,
                )
            )
        if profile.competitor_accounts:
            signals.append(
                TrendItem(
                    title="Competitor conversation watch",
                    summary=(
                        "Configured competitor accounts are treated only as market signals; "
                        "their wording is never copied and private startup context is never exposed."
                    ),
                    source="competitor-watch-demo",
                    score=0.6,
                    collected_at=now,
                )
            )
        return signals

    def _rss_items(self, urls: Iterable[str]) -> list[TrendItem]:
        collected: list[TrendItem] = []
        with httpx.Client(timeout=self.settings.rss_timeout_seconds, follow_redirects=True) as client:
            for url in urls:
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    root = ET.fromstring(response.content)
                    entries = root.findall(".//item")[:3]
                    if not entries:
                        entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")[:3]
                    for entry in entries:
                        title = self._node_text(entry, ["title", "{http://www.w3.org/2005/Atom}title"])
                        summary = self._node_text(
                            entry,
                            [
                                "description",
                                "summary",
                                "{http://www.w3.org/2005/Atom}summary",
                                "{http://www.w3.org/2005/Atom}content",
                            ],
                        )
                        link = self._node_text(entry, ["link"])
                        if not link:
                            atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
                            link = atom_link.attrib.get("href", "") if atom_link is not None else ""
                        if title:
                            collected.append(
                                TrendItem(
                                    title=_strip_html(title)[:180],
                                    summary=_strip_html(summary)[:500],
                                    source="rss",
                                    url=link,
                                    score=0.8,
                                    collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                )
                            )
                except Exception as exc:  # Prototype degrades to stored/demo signals.
                    logger.warning("RSS source failed: %s (%s)", url, exc)
        return collected

    @staticmethod
    def _node_text(entry: ET.Element, tags: list[str]) -> str:
        for tag in tags:
            node = entry.find(tag)
            if node is not None and node.text:
                return node.text.strip()
        return ""
