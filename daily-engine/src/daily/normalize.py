"""Text and URL normalization helpers shared by daily sources."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_NON_WORD = re.compile(r"[^\w一-鿿]+")
_CHARSET_META = re.compile(rb"charset\s*=\s*[\"']?\s*([\w-]+)", re.IGNORECASE)
_GB_ALIASES = {"gbk", "gb2312", "gb_2312", "cp936", "euc-cn"}


def decode_html(raw: bytes, declared: str | None = None) -> str:
    """Decode an HTML document without trusting the transport alone.

    Chinese publisher pages often omit the charset from the HTTP header and only
    declare ``gb2312`` in a meta tag. httpx then falls back to UTF-8 and the
    Chinese text becomes replacement characters, so try UTF-8 first, then the
    declared/meta charset, then GB18030 (a superset of GB2312/GBK).
    """
    candidates: list[str] = ["utf-8", declared or ""]
    match = _CHARSET_META.search(raw[:4096])
    if match:
        candidates.append(match.group(1).decode("ascii", "ignore"))
    candidates.append("gb18030")

    tried: set[str] = set()
    for encoding in candidates:
        if not encoding:
            continue
        name = encoding.strip().lower()
        if name in _GB_ALIASES:
            name = "gb18030"
        if name in tried:
            continue
        tried.add(name)
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_title(title: str) -> str:
    """Fold width, case, punctuation and whitespace before comparing titles."""
    text = unicodedata.normalize("NFKC", title).lower()
    return _NON_WORD.sub("", text)


def title_similarity(left: str, right: str) -> float:
    """Return a 0..1 similarity ratio for two titles."""
    normalized_left = normalize_title(left)
    normalized_right = normalize_title(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def canonical_url(url: str) -> str:
    """Drop fragments, tracking parameters and a trailing slash."""
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))
