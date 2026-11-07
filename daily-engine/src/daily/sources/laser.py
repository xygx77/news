"""Laser industry news site adapters."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from ..config import LaserSiteConfig
from ..normalize import canonical_url, decode_html, title_similarity

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")
_DATE_IN_URL = (
    re.compile(r"/(\d{4})(\d{2})/(\d{2})/"),
    re.compile(r"/(\d{4})-(\d{2})-(\d{2})"),
    re.compile(r"/(\d{4})-(\d{2})/"),
    re.compile(r"/(\d{4})(\d{2})/"),
)
_DATE_IN_TEXT = (
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),
)
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)")
# A date (and optional "查看详情" tail) printed at the end of a list-page link.
_TRAILING_DATE = re.compile(
    r"[\s·|]*[（(]?\s*"
    r"(?:20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}日?|\d{1,2}[-/]\d{1,2})"
    r"\s*[)）]?\s*(?:查看详情)?\s*$"
)


@dataclass(slots=True)
class LaserCandidate:
    title: str
    url: str
    published_at: datetime | None
    summary: str
    source_name: str
    weight: float
    language: str
    alternates: list[str] = field(default_factory=list)
    time_inferred: bool = False


@dataclass(slots=True)
class SiteHealth:
    name: str
    ok: bool
    candidates: int
    parsed: int
    elapsed_ms: int
    error: str | None = None


@dataclass(slots=True)
class LaserCollection:
    candidates: list[LaserCandidate] = field(default_factory=list)
    health: list[SiteHealth] = field(default_factory=list)


def _make_date(year: str, month: str, day: str) -> datetime | None:
    try:
        if len(year) == 2:
            year = f"20{year}"
        return datetime(int(year), int(month), int(day), tzinfo=SHANGHAI)
    except (TypeError, ValueError):
        return None


def parse_date_from_url(url: str, date_regex: str | None = None) -> datetime | None:
    """Extract a publication date from an article URL.

    A URL that only carries year and month (``/202611/`` or ``/2026-11/``)
    yields ``None`` on purpose: filling in day 1 silently pushed every article
    of such a site outside the 48h window, so those sites never appeared in
    the briefing. Callers fall back to the list-page text and, later, to the
    article page metadata.
    """
    if date_regex:
        match = re.search(date_regex, url)
        if match:
            groups = match.groupdict()
            if groups.get("y") and groups.get("m") and groups.get("d"):
                return _make_date(groups["y"], groups["m"], groups["d"])
    for pattern in _DATE_IN_URL:
        match = pattern.search(url)
        if match is None:
            continue
        groups = match.groups()
        if len(groups) < 3:
            continue
        parsed = _make_date(groups[0], groups[1], groups[2])
        if parsed is not None:
            return parsed
    return None


def parse_date_text(text: str, *, default_year: int | None = None) -> datetime | None:
    """Extract a publication date from nearby list-page text."""
    for pattern in _DATE_IN_TEXT:
        match = pattern.search(text)
        if match:
            year, month, day = match.groups()
            parsed = _make_date(year, month, day)
            if parsed is not None:
                return parsed
    match = _MONTH_DAY.search(text)
    if match:
        year = default_year or datetime.now(SHANGHAI).year
        return _make_date(str(year), match.group(1), match.group(2))
    return None


def _strip_trailing_date(title: str) -> str:
    """Remove a trailing date (and "查看详情") from a list-page link label."""
    cleaned = _TRAILING_DATE.sub("", title).strip()
    return cleaned if len(cleaned) >= 2 else title


def parse_title_date(title: str) -> datetime | None:
    """Extract a date printed in a link label, e.g. ``... 2026-11-03``.

    Stricter than :func:`parse_date_text`: it only accepts a full date anywhere
    in the label or a month-day at the very end, so ranges such as ``增长3-5倍``
    are not mistaken for a publication date.
    """
    for pattern in _DATE_IN_TEXT:
        match = pattern.search(title)
        if match:
            parsed = _make_date(*match.groups())
            if parsed is not None:
                return parsed
    match = re.search(r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)\s*(?:查看详情)?\s*$", title)
    if match:
        return _make_date(str(datetime.now(SHANGHAI).year), match.group(1), match.group(2))
    return None


def _strip_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(fragment=""))


def _context_text(anchor, levels: int = 2) -> tuple[str, object | None]:
    node = anchor
    for _ in range(levels):
        node = node.parent
        if node is None:
            return "", None
        if getattr(node, "name", None) in {"li", "p", "article", "div"}:
            return node.get_text(" ", strip=True), node
    return "", None


def _summary_from_context(anchor, title: str, *, levels: int = 2) -> str:
    text, _ = _context_text(anchor, levels)
    summary = text.replace(title, " ", 1).strip()
    # Drop the leftover date / "查看详情" once the title has been detached.
    summary = _TRAILING_DATE.sub("", summary).strip()
    return summary[:120]


def parse_html_list(html: str, site: LaserSiteConfig) -> list[LaserCandidate]:
    """Parse an HTML list page using the site's link pattern."""
    soup = BeautifulSoup(html, "html.parser")
    base = site.base_url or site.list_url or ""
    pattern = re.compile(site.url_pattern) if site.url_pattern else None
    candidates: list[LaserCandidate] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        url = _strip_fragment(urljoin(base, href))
        if pattern is not None and not pattern.search(url):
            continue
        if url in seen:
            continue
        title = anchor.get_text(" ", strip=True)
        if len(title) < 2:
            continue

        published_at = None
        title_has_date = False
        if site.date_from_url:
            published_at = parse_date_from_url(url, site.date_regex)
        if published_at is None:
            # Many list pages print the date inside the link text. Prefer it over
            # the surrounding container, which often mixes several items and would
            # give every entry the same (first) date.
            published_at = parse_title_date(title)
            title_has_date = published_at is not None
        if published_at is None:
            context, _ = _context_text(anchor)
            published_at = parse_date_text(context)

        if title_has_date:
            title = _strip_trailing_date(title)
            # The date lived in the link, so the enclosing block usually spans
            # several items: read only the immediate parent, and keep the summary
            # empty when that parent holds other links (their text is not ours).
            summary = _summary_from_context(anchor, title, levels=1)
            parent = anchor.parent
            if parent is not None and len(parent.find_all("a", href=True)) > 1:
                summary = ""
        else:
            summary = _summary_from_context(anchor, title)

        seen.add(url)
        candidates.append(
            LaserCandidate(
                title=title,
                url=url,
                published_at=published_at,
                summary=summary,
                source_name=site.name,
                weight=site.weight,
                language=site.language,
                time_inferred=published_at is None,
            )
        )
    return candidates


_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _find_elements(root: ET.Element, tag: str) -> list[ET.Element]:
    found = root.findall(f".//{_SITEMAP_NS}{tag}")
    if not found:
        found = root.findall(f".//{tag}")
    return found


def _child_text(element: ET.Element, tag: str) -> str:
    child = element.find(f"{_SITEMAP_NS}{tag}")
    if child is None:
        child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def sitemap_children(xml: str) -> list[str]:
    """Return child sitemap URLs from a sitemap index."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    return [
        loc
        for element in _find_elements(root, "sitemap")
        if (loc := _child_text(element, "loc"))
    ]


def _parse_lastmod(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return parse_date_text(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _title_from_url(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(?:html?|jhtml|aspx?)$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug or url


def parse_sitemap(xml: str, site: LaserSiteConfig) -> list[LaserCandidate]:
    """Parse a sitemap urlset into candidates filtered by the site pattern."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    pattern = re.compile(site.url_pattern) if site.url_pattern else None
    candidates: list[LaserCandidate] = []
    seen: set[str] = set()
    for element in _find_elements(root, "url"):
        loc = _child_text(element, "loc")
        if not loc:
            continue
        url = _strip_fragment(loc)
        if pattern is not None and not pattern.search(url):
            continue
        if url in seen:
            continue
        published_at = None
        if site.date_from_url:
            published_at = parse_date_from_url(url, site.date_regex)
        if published_at is None:
            published_at = _parse_lastmod(_child_text(element, "lastmod"))
        seen.add(url)
        candidates.append(
            LaserCandidate(
                title=_title_from_url(url),
                url=url,
                published_at=published_at,
                summary="",
                source_name=site.name,
                weight=site.weight,
                language=site.language,
                time_inferred=published_at is None,
            )
        )
    candidates.sort(
        key=lambda item: item.published_at
        or datetime.min.replace(tzinfo=SHANGHAI),
        reverse=True,
    )
    return candidates


async def fetch_sitemap(
    client: httpx.AsyncClient, site: LaserSiteConfig
) -> list[LaserCandidate]:
    """Fetch a sitemap, following up to three child sitemaps."""
    response = await _get_with_retry(client, site.sitemap_url or "")
    candidates = list(parse_sitemap(decode_html(response.content, response.charset_encoding), site))
    for child in sitemap_children(decode_html(response.content, response.charset_encoding))[:3]:
        try:
            child_response = await _get_with_retry(client, child)
        except Exception as exc:  # noqa: BLE001 - one child may fail
            logger.warning("Sitemap child %s failed: %s", child, exc)
            continue
        candidates.extend(parse_sitemap(decode_html(child_response.content, child_response.charset_encoding), site))

    result: list[LaserCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = canonical_url(candidate.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    result.sort(
        key=lambda item: item.published_at
        or datetime.min.replace(tzinfo=SHANGHAI),
        reverse=True,
    )
    return result


async def fetch_lumentum(
    client: httpx.AsyncClient, site: LaserSiteConfig
) -> list[LaserCandidate]:
    """Lumentum's newsroom links out to investor-relations detail pages."""
    response = await _get_with_retry(client, site.list_url or "")
    soup = BeautifulSoup(decode_html(response.content, response.charset_encoding), "html.parser")
    candidates: list[LaserCandidate] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if "news-details" not in href:
            continue
        url = _strip_fragment(urljoin(site.base_url, href))
        if url in seen:
            continue
        title = anchor.get_text(" ", strip=True)
        if len(title) < 2:
            continue
        context = anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
        published_at = parse_date_text(context)
        seen.add(url)
        candidates.append(
            LaserCandidate(
                title=title,
                url=url,
                published_at=published_at,
                summary=context[:120],
                source_name=site.name,
                weight=site.weight,
                language=site.language,
                time_inferred=published_at is None,
            )
        )
    return candidates


CUSTOM_FETCHERS: dict[
    str, Callable[[httpx.AsyncClient, LaserSiteConfig], Awaitable[list[LaserCandidate]]]
] = {"lumentum": fetch_lumentum}


def dedup_candidates(
    candidates: Sequence[LaserCandidate], *, threshold: float = 0.85
) -> list[LaserCandidate]:
    """Merge the same story reported by multiple laser sources."""
    ordered = sorted(
        candidates, key=lambda item: (-item.weight, -len(item.summary))
    )
    kept: list[LaserCandidate] = []
    seen_urls: set[str] = set()
    for candidate in ordered:
        key = canonical_url(candidate.url)
        if key in seen_urls:
            continue
        match = next(
            (
                existing
                for existing in kept
                if title_similarity(existing.title, candidate.title) >= threshold
            ),
            None,
        )
        if match is None:
            kept.append(candidate)
            seen_urls.add(key)
            continue
        if (candidate.weight, len(candidate.summary)) > (
            match.weight,
            len(match.summary),
        ):
            old_url = match.url
            match.title = candidate.title
            match.url = candidate.url
            match.summary = candidate.summary or match.summary
            match.published_at = match.published_at or candidate.published_at
            match.source_name = candidate.source_name
            match.weight = candidate.weight
            match.language = candidate.language
            match.time_inferred = match.time_inferred and candidate.time_inferred
            match.alternates.append(old_url)
        else:
            match.alternates.append(candidate.url)
        seen_urls.add(key)
    return kept


async def _get_with_retry(client: httpx.AsyncClient, url: str):
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = await client.get(url, timeout=15, follow_redirects=True)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.5)
    assert last_error is not None
    raise last_error


async def fetch_site(
    client: httpx.AsyncClient, site: LaserSiteConfig, *, limit: int = 20
) -> tuple[list[LaserCandidate], SiteHealth]:
    """Fetch and parse one HTML-list laser site."""
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(2)
    note: str | None = None
    try:
        async with semaphore:
            if site.strategy == "sitemap":
                parsed = await fetch_sitemap(client, site)
            elif site.strategy == "custom":
                fetcher = CUSTOM_FETCHERS.get(site.adapter or "")
                if fetcher is None:
                    note = "unknown adapter"
                    response = await _get_with_retry(client, site.list_url or "")
                    parsed = parse_html_list(decode_html(response.content, response.charset_encoding), site)
                else:
                    parsed = await fetcher(client, site)
            else:
                response = await _get_with_retry(client, site.list_url or "")
                parsed = parse_html_list(decode_html(response.content, response.charset_encoding), site)
        candidates = parsed[:limit]
        elapsed = int((time.perf_counter() - started) * 1000)
        return candidates, SiteHealth(
            name=site.name,
            ok=True,
            candidates=len(candidates),
            parsed=len(parsed),
            elapsed_ms=elapsed,
            error=note,
        )
    except Exception as exc:  # noqa: BLE001 - per-site health
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.warning("Laser site %s failed: %s", site.name, exc)
        return [], SiteHealth(
            name=site.name,
            ok=False,
            candidates=0,
            parsed=0,
            elapsed_ms=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


async def collect_laser(
    client: httpx.AsyncClient,
    sites: Sequence[LaserSiteConfig],
    *,
    limit: int = 20,
) -> LaserCollection:
    """Fetch all enabled laser sites with global and per-site concurrency caps."""
    global_semaphore = asyncio.Semaphore(8)
    enabled = [site for site in sites if site.enabled]

    async def run(site: LaserSiteConfig):
        async with global_semaphore:
            return await fetch_site(client, site, limit=limit)

    results = await asyncio.gather(*(run(site) for site in enabled))
    candidates = [candidate for items, _ in results for candidate in items]
    health = [health for _, health in results]
    return LaserCollection(candidates=candidates, health=health)
