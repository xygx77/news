"""Orchestrate market data collection into one snapshot."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..config import MarketSettings
from . import repo, sina, tencent
from .eastmoney import (
    A_SHARE,
    A_SHARE_CODES,
    APAC,
    COMMODITY,
    EU,
    FX,
    HK,
    US,
    fetch_breadth,
    fetch_quotes,
    fetch_sectors,
    fetch_turnover,
)
from .rates import (
    FRED_SERIES,
    fetch_china_bond_yields,
    fetch_fred_yields,
    fetch_usdcny_fix,
)
from .schema import Fact, MarketSnapshot

SHANGHAI = ZoneInfo("Asia/Shanghai")
# Order matters: the first feed that returns rows for a group wins.
SECONDARY_QUOTE_FEEDS = (tencent, sina)


def _needs_retry(result: object) -> bool:
    """Treat exceptions, ``None``, and empty collections as fetch failures."""
    if isinstance(result, Exception) or result is None:
        return True
    if isinstance(result, tuple):
        return not any(result)
    if isinstance(result, list):
        return not result
    return False


def _a_share_secids(settings: MarketSettings) -> dict[str, str]:
    return {
        A_SHARE_CODES[code]: A_SHARE[A_SHARE_CODES[code]]
        for code in settings.indices
        if code in A_SHARE_CODES
    }


async def fetch_market_snapshot(
    client: httpx.AsyncClient,
    settings: MarketSettings,
    *,
    as_of: str = "上一交易日收盘",
) -> MarketSnapshot:
    """Fetch all market groups independently and degrade per group."""
    captured_at = datetime.now(SHANGHAI)
    a_share = _a_share_secids(settings)

    builders: dict[str, Callable[[], Awaitable[object]]] = {
        "A 股": lambda: fetch_quotes(client, a_share, group="ashare", as_of=as_of),
        "港股": lambda: fetch_quotes(client, HK, group="hk", as_of=as_of),
        "美股": lambda: fetch_quotes(
            client, US, group="us", as_of="刚结束的美股交易日收盘"
        ),
        "欧股": lambda: fetch_quotes(client, EU, group="eu", as_of=as_of),
        "日韩": lambda: fetch_quotes(client, APAC, group="apac", as_of=as_of),
        "富时A50": lambda: sina.fetch_quotes(
            client,
            sina.A50_CODES,
            group="a50",
            as_of="抓取时点",
            note=sina.A50_NOTE,
        ),
        "汇率": lambda: fetch_quotes(client, FX, group="fx", as_of="抓取时点"),
        "商品": lambda: fetch_quotes(
            client, COMMODITY, group="commodity", as_of="抓取时点"
        ),
        "美债": lambda: fetch_fred_yields(client, FRED_SERIES),
        "中国国债": lambda: fetch_china_bond_yields(client),
        "回购定盘": lambda: repo.fetch_repo_fixings(client),
        "人民币中间价": lambda: fetch_usdcny_fix(client),
    }
    if settings.sectors.enabled:
        builders["板块"] = lambda: fetch_sectors(
            client, top_n=settings.sectors.top_n, as_of=as_of
        )
    turnover_secids = {
        secid: label
        for secid, label in a_share.items()
        if secid in {"1.000001", "0.399001"}
    }
    if turnover_secids:
        builders["成交额"] = lambda: fetch_turnover(client, turnover_secids)
    builders["涨跌家数"] = lambda: fetch_breadth(client)

    names = list(builders)
    results = await asyncio.gather(
        *(build() for build in builders.values()), return_exceptions=True
    )
    # Remote endpoints fail intermittently; retry only the groups that came back empty.
    failed = [name for name, result in zip(names, results) if _needs_retry(result)]
    if failed:
        await asyncio.sleep(2)
        retried = await asyncio.gather(
            *(builders[name]() for name in failed), return_exceptions=True
        )
        for name, result in zip(failed, retried):
            results[names.index(name)] = result

    # Each group comes from a single upstream. If it is still empty after the
    # retry, try the secondary feeds instead of dropping those rows: Tencent for
    # the equity indices, Sina for FX and commodities.
    for name, result in zip(names, results):
        if not _needs_retry(result):
            continue
        for provider in SECONDARY_QUOTE_FEEDS:
            codes = provider.codes_for_group(name, a_share)
            group = provider.group_id(name)
            if not codes or group is None:
                continue
            try:
                fallback = await provider.fetch_quotes(
                    client,
                    codes,
                    group=group,
                    as_of=provider.as_of_for(name, as_of),
                )
            except Exception:  # noqa: BLE001 - the group stays missing instead
                continue
            if fallback:
                results[names.index(name)] = fallback
                break

    facts: list[Fact] = []
    missing: list[str] = []
    for name, result in zip(names, results):
        if isinstance(result, Exception) or result is None:
            missing.append(name)
            continue
        if isinstance(result, tuple):
            if not result[0] or not result[1]:
                missing.append(name)
            for group_facts in result:
                facts.extend(group_facts)
        elif isinstance(result, list):
            if not result:
                missing.append(name)
            facts.extend(result)
        elif isinstance(result, Fact):
            facts.append(result)

    for label in ("美国 10 年期国债", "美国 30 年期国债"):
        if not any(
            fact.label == label and fact.status == "ok" for fact in facts
        ):
            missing.append(label)

    if not any(fact.label == "布伦特原油" for fact in facts):
        facts.append(
            Fact(
                id="commodity:brent",
                group="commodity",
                label="布伦特原油",
                value="暂缺",
                status="missing",
                note="暂缺",
                source_name="暂缺",
                source_url="https://www.eia.gov/",
                as_of="暂缺",
            )
        )
        missing.append("布伦特原油")

    return MarketSnapshot(
        facts=facts,
        calendar=[],
        missing=missing,
        captured_at=captured_at,
    )
