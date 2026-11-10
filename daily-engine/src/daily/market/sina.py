"""Sina quotes: FX and commodity fallbacks, plus the A50 futures line.

EastMoney serves these groups; when it fails the brief loses every commodity and
FX row at once. Tencent's international feed is empty here, but Sina's
``hq.sinajs.cn`` answers with the same instruments, so it backs those two groups.

Sina is also the only feed for the FTSE China A50 futures used as the pre-open
read on foreign appetite, which no other group covers.

Not covered: 欧股 / 日韩 (no source among Sina, Tencent, Wind EDB) and 美元指数
(no Sina code), which keep EastMoney as their only feed.
"""

from __future__ import annotations

import asyncio
from typing import Mapping

import httpx

from .schema import Fact

QUOTE_URL = "https://hq.sinajs.cn/list="
SOURCE_NAME = "新浪财经"
SOURCE_URL = "https://finance.sina.com.cn/quote/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn",
}
# Sina layouts differ per instrument family.
#   fx_*: 1=last, 9=name, 10=change percent, 11=change amount
#   hf_*: 0=last, 7=previous close, 13=name (no percent field)
_FX_LAST, _FX_PCT = 1, 10
_HF_LAST, _HF_PREV = 0, 7
FX_CODES = {
    "fx_susdcnh": "美元兑离岸人民币",
    "fx_seurusd": "欧元/美元",
    "fx_sgbpusd": "英镑/美元",
    "fx_susdjpy": "美元/日元",
}
COMMODITY_CODES = {
    "hf_CL": "WTI 原油",
    "hf_OIL": "布伦特原油",
    "hf_GC": "COMEX 黄金",
    "hf_SI": "COMEX 白银",
    "hf_HG": "COMEX 铜",
}
# Sina quotes copper in cents per pound while EastMoney publishes dollars per
# pound; scale it so a fallback row keeps the same unit as the primary feed.
COMMODITY_SCALE = {"hf_HG": 0.01}
# FTSE China A50 futures: the pre-open read on foreign appetite for A shares.
A50_CODES = {"hf_CHA50CFD": "富时中国A50期指"}
A50_NOTE = "新浪 · 连续合约"
FALLBACK_NOTE = "东方财富未返回，已用新浪财经兜底"


def _direction(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _to_float(raw: str | None) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _records(text: str) -> list[tuple[str, list[str]]]:
    parsed: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        marker = 'var hq_str_'
        if marker not in line:
            continue
        head, _, tail = line.partition("=")
        code = head.strip()[len(marker):]
        payload = tail.strip().strip(';').strip('"')
        if code and payload:
            parsed.append((code, payload.split(",")))
    return parsed


def _change_for(code: str, fields: list[str]) -> float | None:
    if code.startswith("fx_"):
        return _to_float(fields[_FX_PCT]) if len(fields) > _FX_PCT else None
    last = _to_float(fields[_HF_LAST]) if len(fields) > _HF_LAST else None
    previous = _to_float(fields[_HF_PREV]) if len(fields) > _HF_PREV else None
    if last is None or not previous:
        return None
    return (last - previous) / previous * 100


def parse_quotes(
    text: str,
    *,
    group: str,
    as_of: str,
    labels: Mapping[str, str] | None = None,
    note: str | None = None,
) -> list[Fact]:
    """Map a Sina quote payload into facts (requested codes only)."""
    labels = labels or {}
    wanted = set(labels) if labels else None
    facts: list[Fact] = []
    for code, fields in _records(text):
        if wanted is not None and code not in wanted:
            continue
        raw_last = _to_float(fields[_FX_LAST] if code.startswith("fx_") else fields[_HF_LAST])
        if raw_last is None:
            continue
        scale = COMMODITY_SCALE.get(code, 1.0)
        last = raw_last * scale
        change_num = _change_for(code, fields)
        change = f"{change_num:+.2f}%" if change_num is not None else None
        facts.append(
            Fact(
                id=f"{group}:{code}",
                group=group,
                label=labels.get(code) or code,
                value=f"{last:.4f}" if code.startswith("fx_") else f"{last:.2f}",
                value_num=last,
                change=change,
                change_num=change_num,
                change_unit="pct" if change is not None else None,
                direction=_direction(change_num) if change_num is not None else "unknown",
                status="ok",
                note=note or FALLBACK_NOTE,
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                as_of=as_of,
            )
        )
    return facts


async def _get_text(
    client: httpx.AsyncClient, codes: list[str], *, attempts: int = 2
) -> str:
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
    note: str | None = None,
) -> list[Fact]:
    """Fetch one group of quotes; an empty result means "not available"."""
    if not codes:
        return []
    text = await _get_text(client, list(codes))
    return parse_quotes(text, group=group, as_of=as_of, labels=codes, note=note)


def codes_for_group(name: str, a_share: Mapping[str, str]) -> dict[str, str]:
    """Return the Sina codes mirroring an EastMoney quote group."""
    if name == "汇率":
        return dict(FX_CODES)
    if name == "商品":
        return dict(COMMODITY_CODES)
    return {}


def group_id(name: str) -> str | None:
    return {"汇率": "fx", "商品": "commodity"}.get(name)


def as_of_for(name: str, default: str) -> str:
    return "抓取时点"


__all__ = [
    "A50_CODES",
    "A50_NOTE",
    "COMMODITY_CODES",
    "FX_CODES",
    "as_of_for",
    "codes_for_group",
    "fetch_quotes",
    "group_id",
    "parse_quotes",
]
