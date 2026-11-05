"""Jin10 7x24 flash news scraper."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .base import BaseScraper, FetchResult
from ..models import ContentItem, SourceType

logger = logging.getLogger(__name__)

JIN10_API_URL = "https://flash-api.jin10.com/get_flash_list"
JIN10_LIST_URL = "https://flash.jin10.com/"
JIN10_HEADERS = {
    "User-Agent": "Horizon/0.1.0 (+https://github.com/Thysrael/Horizon)",
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
}
SHANGHAI = ZoneInfo("Asia/Shanghai")
_SENTENCE_END = re.compile(r"(?<=[。！？!?])")


def _html_to_text(value: str) -> str:
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _first_sentence(text: str, limit: int = 60) -> str:
    parts = [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]
    title = parts[0] if parts else text.strip()
    if len(title) > limit:
        title = title[:limit].rstrip() + "…"
    return title


def parse_jin10_items(payload: dict[str, Any], since: datetime) -> list[ContentItem]:
    """Convert a Jin10 flash-list response into ContentItems."""
    items: list[ContentItem] = []
    for entry in payload.get("data") or []:
        entry_data = entry.get("data") or {}
        if entry_data.get("vip"):
            continue
        content = _html_to_text(str(entry_data.get("content") or ""))
        if not content or "VIP" in content:
            continue
        try:
            published_at = datetime.strptime(entry["time"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=SHANGHAI
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping Jin10 entry with invalid time: %r", entry.get("id"))
            continue
        if published_at < since:
            continue
        items.append(
            ContentItem(
                id=f"jin10:flash:{entry.get('id', '')}",
                source_type=SourceType.JIN10,
                title=_first_sentence(content),
                url=JIN10_LIST_URL,
                content=content,
                published_at=published_at,
                metadata={
                    "important": bool(entry.get("important")),
                    "kinds": entry.get("kinds") or [],
                },
            )
        )
    return items


class Jin10Scraper(BaseScraper):
    """Fetch 7x24 flash news from Jin10."""

    async def fetch(self, since: datetime) -> FetchResult:
        result = FetchResult()
        if not self.config.get("enabled", True):
            return result
        limit = int(self.config.get("limit", 100))
        try:
            response = await self.client.get(
                JIN10_API_URL,
                params={"channel": "-8200", "vip": "1"},
                headers=JIN10_HEADERS,
            )
            response.raise_for_status()
            result.items.extend(parse_jin10_items(response.json(), since)[:limit])
            result.successful += 1
        except Exception as exc:  # noqa: BLE001 - reported per source
            result.fail("jin10", exc)
        return result
