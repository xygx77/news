"""Fetch laser article bodies and build short summaries."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Sequence

import httpx
import trafilatura
from bs4 import BeautifulSoup
from dateutil.parser import isoparse

from .scoring import LaserItem

logger = logging.getLogger(__name__)

_SENTENCE = re.compile(r"[^。！？.!?]+[。！？.!?]?")


def extract_summary(text: str, *, min_len: int = 40, max_len: int = 70) -> str:
    """Return one or two sentences, between ``min_len`` and ``max_len``."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    sentences = [part.strip() for part in _SENTENCE.findall(cleaned) if part.strip()]
    summary = ""
    for sentence in sentences:
        summary += sentence
        if len(summary) >= min_len:
            break
    if len(summary) > max_len:
        summary = summary[:max_len].rstrip() + "…"
    return summary


def _jsonld_dates(value: Any) -> list[str]:
    dates: list[str] = []
    if isinstance(value, dict):
        published = value.get("datePublished")
        if isinstance(published, str):
            dates.append(published)
        for child in value.values():
            dates.extend(_jsonld_dates(child))
    elif isinstance(value, list):
        for child in value:
            dates.extend(_jsonld_dates(child))
    return dates


def extract_title(html: str) -> str | None:
    """Read an article title from Open Graph, Twitter, or ``<title>``."""
    soup = BeautifulSoup(html, "html.parser")
    for attributes in (
        {"property": "og:title"},
        {"name": "twitter:title"},
    ):
        meta = soup.find("meta", attrs=attributes)
        if meta and meta.get("content"):
            title = str(meta["content"]).strip()
            if title:
                return title
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        if title:
            return title
    return None


def extract_published_time(html: str) -> datetime | None:
    """Read a publication time from meta tags or JSON-LD."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for attributes in (
        {"property": "article:published_time"},
        {"name": "publishdate"},
        {"name": "pubdate"},
    ):
        meta = soup.find("meta", attrs=attributes)
        if meta and meta.get("content"):
            candidates.append(str(meta["content"]))
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates.extend(_jsonld_dates(payload))

    for value in candidates:
        try:
            parsed = isoparse(value)
        except (ValueError, TypeError, OverflowError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


async def enrich_items(
    items: Sequence[LaserItem], client: httpx.AsyncClient, *, top_n: int = 15
) -> list[LaserItem]:
    """Fetch article bodies for the top items and replace summaries."""
    semaphore = asyncio.Semaphore(8)

    async def enrich(item: LaserItem) -> LaserItem:
        async with semaphore:
            try:
                response = await client.get(
                    item.url, timeout=15, follow_redirects=True
                )
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - keep list-page summary
                logger.warning("Article fetch failed for %s: %s", item.url, exc)
                return item
        html = response.text
        try:
            body = trafilatura.extract(html) or ""
        except Exception as exc:  # noqa: BLE001 - keep list-page summary
            logger.warning("Article extraction failed for %s: %s", item.url, exc)
            body = ""
        summary = extract_summary(body) if body else ""
        discovered_time = extract_published_time(html)
        published_at = item.published_at or discovered_time
        time_inferred = (
            False
            if discovered_time is not None and item.published_at is None
            else item.time_inferred
        )
        title = extract_title(html)
        updated = replace(
            item,
            title=title if title and len(title) >= 6 else item.title,
            summary=summary or item.summary,
            published_at=published_at,
            time_inferred=time_inferred,
        )
        return updated

    head = list(items[:top_n])
    tail = list(items[top_n:])
    enriched = await asyncio.gather(*(enrich(item) for item in head))
    return [*enriched, *tail]
