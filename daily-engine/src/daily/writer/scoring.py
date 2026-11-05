"""Score and select laser industry news without an LLM."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from ..sources.laser import LaserCandidate

MAIN_CATEGORIES = ("并购融资", "业绩", "技术突破", "产品发布", "标准规范")
MAX_PER_CATEGORY = 3


@dataclass(slots=True)
class KeywordRule:
    name: str
    keywords: tuple[str, ...]
    score: float


@dataclass(slots=True)
class KeywordRules:
    categories: tuple[KeywordRule, ...]
    noise_keywords: tuple[str, ...]
    noise_score: float


@dataclass(slots=True)
class LaserItem:
    title: str
    url: str
    summary: str
    category: str
    published_at: datetime | None
    score: float
    source_name: str
    weight: float
    language: str
    alternates: list[str] = field(default_factory=list)
    time_inferred: bool = False


@dataclass(slots=True)
class SelectionResult:
    items: list[LaserItem]
    window_hours: int


def load_keywords(path: Path) -> KeywordRules:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    categories = tuple(
        KeywordRule(
            name=item["name"],
            keywords=tuple(item["keywords"]),
            score=float(item["score"]),
        )
        for item in data.get("categories", [])
    )
    return KeywordRules(
        categories=categories,
        noise_keywords=tuple(data.get("noise_keywords", [])),
        noise_score=float(data.get("noise_score", -3)),
    )


def _text(candidate: LaserCandidate) -> str:
    return f"{candidate.title} {candidate.summary}".lower()


def _matches(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def score_candidate(
    candidate: LaserCandidate, *, now: datetime, rules: KeywordRules
) -> float:
    """Score one candidate from keyword hits, source weight and recency."""
    text = _text(candidate)
    score = candidate.weight
    for rule in rules.categories:
        if _matches(text, rule.keywords):
            score += rule.score
    if _matches(text, rules.noise_keywords):
        score += rules.noise_score

    if not candidate.time_inferred and candidate.published_at is not None:
        hours = (now - candidate.published_at).total_seconds() / 3600
        if hours <= 24:
            score += 2
        elif hours <= 48:
            score += 1
    return score


def categorize(candidate: LaserCandidate, rules: KeywordRules) -> str:
    """Pick the highest-scoring matched category."""
    text = _text(candidate)
    matched = [
        (rule.score, rule.name)
        for rule in rules.categories
        if rule.name in MAIN_CATEGORIES and _matches(text, rule.keywords)
    ]
    if not matched:
        return "其他"
    return max(matched, key=lambda pair: pair[0])[1]


def _in_window(candidate: LaserCandidate, now: datetime, hours: int) -> bool:
    if candidate.published_at is None:
        return True
    return candidate.published_at >= now - timedelta(hours=hours)


def filter_by_window(
    items: Sequence[LaserItem], *, now: datetime, window_hours: int
) -> list[LaserItem]:
    """Drop items whose enriched publication time is outside the window."""
    cutoff = now - timedelta(hours=window_hours)
    return [
        item
        for item in items
        if item.published_at is None or item.published_at >= cutoff
    ]


def _to_item(
    candidate: LaserCandidate, score: float, rules: KeywordRules
) -> LaserItem:
    return LaserItem(
        title=candidate.title,
        url=candidate.url,
        summary=candidate.summary,
        category=categorize(candidate, rules),
        published_at=candidate.published_at,
        score=score,
        source_name=candidate.source_name,
        weight=candidate.weight,
        language=candidate.language,
        alternates=list(candidate.alternates),
        time_inferred=candidate.time_inferred or candidate.published_at is None,
    )


def _diversify(
    selected: list[LaserItem],
    scored: list[tuple[float, LaserCandidate]],
    rules: KeywordRules,
    max_items: int,
) -> list[LaserItem]:
    counts = Counter(item.category for item in selected)
    selected_urls = {item.url for item in selected}
    for category in MAIN_CATEGORIES:
        if category in counts:
            continue
        candidate = next(
            (
                (score, item)
                for score, item in scored
                if item.url not in selected_urls
                and categorize(item, rules) == category
            ),
            None,
        )
        if candidate is None:
            continue
        score, raw = candidate
        if len(selected) < max_items:
            selected.append(_to_item(raw, score, rules))
            counts[category] += 1
            selected_urls.add(raw.url)
            continue
        replace_index = next(
            (
                index
                for index, item in enumerate(selected)
                if counts[item.category] > 1
            ),
            None,
        )
        if replace_index is None:
            continue
        old = selected[replace_index]
        counts[old.category] -= 1
        selected[replace_index] = _to_item(raw, score, rules)
        counts[category] += 1
        selected_urls.discard(old.url)
        selected_urls.add(raw.url)
    return selected


def select_items(
    candidates: Sequence[LaserCandidate],
    *,
    now: datetime,
    rules: KeywordRules,
    window_hours: int = 48,
    fallback_window_hours: int = 72,
    min_items: int = 5,
    max_items: int = 8,
) -> SelectionResult:
    """Filter by time window, rank, and keep 5-8 diverse items."""
    effective_window = window_hours
    windowed = [
        candidate for candidate in candidates
        if _in_window(candidate, now, effective_window)
    ]
    if len(windowed) < min_items and fallback_window_hours > effective_window:
        effective_window = fallback_window_hours
        windowed = [
            candidate for candidate in candidates
            if _in_window(candidate, now, effective_window)
        ]

    scored = [
        (score, candidate)
        for candidate in windowed
        if (score := score_candidate(candidate, now=now, rules=rules)) > 0
    ]
    def sort_key(pair: tuple[float, LaserCandidate]):
        score, candidate = pair
        published = (
            candidate.published_at.timestamp()
            if candidate.published_at is not None
            else float("-inf")
        )
        inferred = candidate.time_inferred or candidate.published_at is None
        return (inferred, -score, -published)

    scored.sort(key=sort_key)

    selected: list[LaserItem] = []
    counts: Counter[str] = Counter()
    for score, candidate in scored:
        if len(selected) >= max_items:
            break
        category = categorize(candidate, rules)
        if counts[category] >= MAX_PER_CATEGORY:
            continue
        selected.append(_to_item(candidate, score, rules))
        counts[category] += 1

    selected = _diversify(selected, scored, rules, max_items)
    return SelectionResult(items=selected, window_hours=effective_window)
