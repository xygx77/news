"""Interbank repo fixing rates (FR001/FR007) from ChinaMoney.

The rate table carried government bond yields but nothing about the short end of
the money market, so a liquidity squeeze showed up nowhere. ChinaMoney publishes
the official repo fixing rates as a plain CSV: no key, no pagination, one row per
trading day.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from .schema import Fact

REPO_URL = (
    "https://www.chinamoney.com.cn/r/cms/www/chinamoney/"
    "data/currency/frr-chrt.csv"
)
REPO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.chinamoney.com.cn/chinese/bkfrr/",
}
SOURCE_NAME = "中国外汇交易中心"
SOURCE_URL = "https://www.chinamoney.com.cn/chinese/bkfrr/"
# Column layout of the CSV: 0=date, 6=FR001, 7=FR007, 8=FR014.
_DATE_COL = 0
TERMS = (
    ("fr001", 6, "银行间回购 FR001（隔夜）"),
    ("fr007", 7, "银行间回购 FR007（7 天）"),
)
# A fixing older than this is not a money market signal any more; hide it rather
# than dress up a stale number as today's liquidity.
MAX_AGE_DAYS = 15


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _direction(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def parse_repo_csv(text: str) -> dict[str, list[tuple[str, float]]]:
    """Collect (date, value) series per term from the ChinaMoney CSV."""
    series: dict[str, list[tuple[str, float]]] = {}
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) <= _DATE_COL:
            continue
        day = parts[_DATE_COL].strip()
        if len(day) != 10 or day[4] != "-":
            continue
        for term, column, _label in TERMS:
            if len(parts) <= column:
                continue
            value = _to_float(parts[column].strip())
            if value is None:
                continue
            series.setdefault(term, []).append((day, value))
    for rows in series.values():
        rows.sort()
    return series


def build_repo_facts(
    series: dict[str, list[tuple[str, float]]], *, today: date
) -> list[Fact]:
    """Turn the newest two fixings of each term into rate facts."""
    oldest = (today - timedelta(days=MAX_AGE_DAYS)).isoformat()
    facts: list[Fact] = []
    for term, _column, label in TERMS:
        rows = series.get(term) or []
        if not rows:
            continue
        as_of, latest = rows[-1]
        if as_of < oldest:
            continue
        change_num: float | None = None
        change: str | None = None
        direction = "unknown"
        if len(rows) >= 2:
            change_num = round((latest - rows[-2][1]) * 100, 1)
            change = f"{change_num:+.1f}BP"
            direction = _direction(change_num)
        facts.append(
            Fact(
                id=f"rate:{term}",
                group="rate",
                label=label,
                value=f"{latest:.2f}%",
                value_num=latest,
                change=change,
                change_num=change_num,
                change_unit="bp" if change_num is not None else None,
                direction=direction,
                status="ok",
                note=f"中国货币网 · 数据截至 {as_of}",
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                as_of=as_of,
            )
        )
    return facts


async def fetch_repo_fixings(client: httpx.AsyncClient) -> list[Fact]:
    """Fetch the repo fixings; ``[]`` when the feed is unavailable."""
    try:
        response = await client.get(
            REPO_URL, headers=REPO_HEADERS, timeout=15, follow_redirects=True
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - optional data, hide on failure
        return []
    return build_repo_facts(
        parse_repo_csv(response.text), today=date.today()
    )


__all__ = [
    "MAX_AGE_DAYS",
    "TERMS",
    "build_repo_facts",
    "fetch_repo_fixings",
    "parse_repo_csv",
]
