"""Text and URL normalization helpers shared by daily sources."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_NON_WORD = re.compile(r"[^\w一-鿿]+")


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
