"""Wallstreetcn live news scraper."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .base import BaseScraper, FetchResult
from ..models import ContentItem, SourceType

logger = logging.getLogger(__name__)

WALLSTREETCN_API_URL = "https://api-one.wallstcn.com/apiv1/content/lives"
WALLSTREETCN_LIST_URL = "https://wallstreetcn.com/live/global"
_SENTENCE_END = re.compile(r"(?<=[。！？!?])")


def _first_sentence(text: str, limit: int = 60) -> str:
    parts = [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]
    title = parts[0] if parts else text.strip()
    if len(title) > limit:
        title = title[:limit].rstrip() + "…"
    return title


def _absolute_uri(uri: str | None) -> str:
    if not uri:
        return WALLSTREETCN_LIST_URL
    if uri.startswith("/"):
        return f"https://wallstreetcn.com{uri}"
    return uri


def parse_wallstreetcn_lives(
    payload: dict[str, Any], since: datetime
) -> list[ContentItem]:
    """Convert a Wallstreetcn lives response into ContentItems."""
    entries = (payload.get("data") or {}).get("items") or []
    items: list[ContentItem] = []
    for entry in entries:
        display_time = entry.get("display_time")
        if display_time is None:
            continue
        try:
            published_at = datetime.fromtimestamp(int(display_time), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            logger.warning(
                "Skipping Wallstreetcn entry with invalid time: %r", entry.get("id")
            )
            continue
        if published_at < since:
            continue
        text = BeautifulSoup(
            str(entry.get("content_text") or ""), "html.parser"
        ).get_text(" ", strip=True)
        if not text:
            continue
        items.append(
            ContentItem(
                id=f"wallstreetcn:live:{entry.get('id', '')}",
                source_type=SourceType.WALLSTREETCN,
                title=_first_sentence(text),
                url=_absolute_uri(entry.get("uri")),
                content=text,
                published_at=published_at,
                metadata={
                    "channel": entry.get("channel"),
                    "score": entry.get("score"),
                },
            )
        )
    return items


class WallstreetcnScraper(BaseScraper):
    """Fetch 7x24 live news from Wallstreetcn."""

    async def fetch(self, since: datetime) -> FetchResult:
        result = FetchResult()
        if not self.config.get("enabled", True):
            return result
        channel = str(self.config.get("channel", "global-channel"))
        limit = int(self.config.get("limit", 100))
        try:
            response = await self.client.get(
                WALLSTREETCN_API_URL,
                params={"channel": channel, "limit": limit},
            )
            response.raise_for_status()
            result.items.extend(parse_wallstreetcn_lives(response.json(), since)[:limit])
            result.successful += 1
        except Exception as exc:  # noqa: BLE001 - reported per source
            result.fail("wallstreetcn", exc)
        return result
