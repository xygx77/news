"""Tencent quote fallback for index rows.

The brief's equity tables come from one upstream (EastMoney). When that endpoint
returns nothing the whole index table disappears, so this module mirrors
``eastmoney.fetch_quotes`` against ``qt.gtimg.cn`` — the same public quote feed
Capital Generation uses as its zero-key channel.

Note: ``usIXIC`` is the Nasdaq Composite, while EastMoney's ``100.NDX`` is the
Nasdaq-100; the fact label stays "纳斯达克" for continuity, and ``source_name``
records which feed answered.
"""

from __future__ import annotations

import asyncio
from typing import Mapping

import httpx

from .schema import Fact

QUOTE_URL = "https://qt.gtimg.cn/q="
SOURCE_NAME = "腾讯财经"
SOURCE_URL = "https://gu.qq.com/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ),
    "Referer": "https://gu.qq.com/",
}
# Groups this feed can actually answer (verified field layout for CN/HK/US).
HK_CODES = {"hkHSI": "恒生指数"}
US_CODES = {"usDJI": "道琼斯", "usINX": "标普500", "usIXIC": "纳斯达克"}
# Field offsets in the '~'-delimited payload: 0 flag, 2 code, 30 quote time.
_NAME, _LAST, _PREV, _CHANGE_PCT = 1, 3, 4, 32


def _direction(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _rows(text: str) -> list[tuple[str, list[str]]]:
    """Split ``v_code="a~b~c";`` records into (code, fields) pairs."""
    parsed: list[tuple[str, list[str]]] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk.startswith("v_") or "=" not in chunk:
            continue
        head, _, tail = chunk.partition("=")
        code = head[2:].strip()
        fields = tail.strip().strip('"').split("~")
        if code and len(fields) > _PREV:
            parsed.append((code, fields))
    return parsed


def parse_quotes(
    text: str,
    *,
    group: str,
    as_of: str,
    labels: Mapping[str, str] | None = None,
) -> list[Fact]:
    """Map a Tencent quote payload into facts.

    When ``labels`` is given, only the requested codes are emitted: the feed can
    echo extra records, and a group must never pick up another group's rows.
    """
    labels = labels or {}
    wanted = set(labels) if labels else None
    facts: list[Fact] = []
    for code, fields in _rows(text):
        if wanted is not None and code not in wanted:
            continue
        label = labels.get(code) or fields[_NAME] or code
        value_num = _to_float(fields[_LAST])
        if value_num is None:
            value = "暂缺"
            status = "missing"
        else:
            value = f"{value_num:.2f}"
            status = "ok"
        change_num = _to_float(fields[_CHANGE_PCT])
        if change_num is None:
            previous = _to_float(fields[_PREV])
            if previous:
                change_num = (value_num - previous) / previous * 100 if value_num is not None else None
        change = f"{change_num:+.2f}%" if change_num is not None else None
        facts.append(
            Fact(
                id=f"{group}:{code}",
                group=group,
                label=label,
                value=value,
                value_num=value_num,
                change=change,
                change_num=change_num,
                change_unit="pct" if change is not None else None,
                direction=_direction(change_num) if change_num is not None else "unknown",
                status=status,
                note="东方财富未返回，已用腾讯财经兜底",
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                as_of=as_of,
            )
        )
    return facts


async def _get_text(
    client: httpx.AsyncClient, codes: list[str], *, attempts: int = 2
) -> str:
    """Fetch the GB18030 payload, retrying transient failures."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(
                f"{QUOTE_URL}{','.join(codes)}",
                headers=HEADERS,
                timeout=10,
                follow_redirects=True,
            )
            response.raise_for_status()
            # No charset in the response header; the feed is GB18030.
            return response.content.decode("gb18030", errors="replace")
        except Exception as exc:  # noqa: BLE001 - retried, then raised
            last_error = exc
            if attempt < attempts - 1:
                await asyncio.sleep(0.5)
    assert last_error is not None
    raise last_error


async def fetch_quotes(
    client: httpx.AsyncClient,
    codes: Mapping[str, str],
    *,
    group: str,
    as_of: str,
) -> list[Fact]:
    """Fetch one group of quotes; an empty result means "not available"."""
    if not codes:
        return []
    text = await _get_text(client, list(codes))
    facts = parse_quotes(text, group=group, as_of=as_of, labels=codes)
    return [fact for fact in facts if fact.status == "ok"]


def codes_for_group(name: str, a_share: Mapping[str, str]) -> dict[str, str]:
    """Return the Tencent codes mirroring an EastMoney quote group."""
    if name == "A 股":
        mirrored: dict[str, str] = {}
        for secid, label in a_share.items():
            market, _, code = secid.partition(".")
            prefix = "sh" if market == "1" else "sz"
            if code:
                mirrored[f"{prefix}{code}"] = label
        return mirrored
    if name == "港股":
        return dict(HK_CODES)
    if name == "美股":
        return dict(US_CODES)
    return {}


def group_id(name: str) -> str | None:
    """Map a snapshot builder name to the fact group EastMoney uses."""
    return {"A 股": "ashare", "港股": "hk", "美股": "us"}.get(name)


def as_of_for(name: str, default: str) -> str:
    """Keep the same 'as of' wording each group already publishes."""
    if name == "美股":
        return "刚结束的美股交易日收盘"
    return default


__all__ = [
    "HK_CODES",
    "US_CODES",
    "as_of_for",
    "codes_for_group",
    "fetch_quotes",
    "group_id",
    "parse_quotes",
]
