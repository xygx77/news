"""Rule-based laser industry briefing writer."""

from __future__ import annotations

from datetime import date, datetime

from .draft import LaserDraft, SourceRef
from .scoring import LaserItem, SelectionResult


def truncate_title(title: str, limit: int = 30) -> str:
    has_chinese = any("一" <= char <= "鿿" for char in title)
    if has_chinese and len(title) > limit:
        return title[:limit].rstrip() + "…"
    return title


def build_laser_draft(
    selection: SelectionResult, *, report_day: date, now: datetime
) -> LaserDraft:
    """Build the laser briefing draft from selected items."""
    items = list(selection.items)
    key_points = [
        f"{truncate_title(item.title)}（{item.source_name}）"
        for item in items[:5]
    ]

    sources: list[SourceRef] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.source_name, item.url)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            SourceRef(
                name=item.source_name,
                url=item.url,
                note="激光简报信息源",
            )
        )

    return LaserDraft(
        report_day=report_day,
        window_hours=selection.window_hours,
        items=items,
        key_points=key_points,
        sources=sources,
        insufficient=len(items) < 5,
        missing=[] if items else ["今日未采集到有效激光信息"],
        captured_at=now,
    )
