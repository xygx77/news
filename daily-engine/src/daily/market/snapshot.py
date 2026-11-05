"""Orchestrate market data collection into one snapshot."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..config import MarketSettings
from .eastmoney import (
    A_SHARE,
    A_SHARE_CODES,
    APAC,
    COMMODITY,
    EU,
    FX,
    HK,
    US,
    fetch_quotes,
    fetch_sectors,
    fetch_turnover,
)
from .rates import FRED_SERIES, fetch_china_bond_yields, fetch_fred_yields
from .schema import Fact, MarketSnapshot

SHANGHAI = ZoneInfo("Asia/Shanghai")


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

    tasks = {
        "A 股": fetch_quotes(client, a_share, group="ashare", as_of=as_of),
        "港股": fetch_quotes(client, HK, group="hk", as_of=as_of),
        "美股": fetch_quotes(client, US, group="us", as_of="刚结束的美股交易日收盘"),
        "欧股": fetch_quotes(client, EU, group="eu", as_of=as_of),
        "日韩": fetch_quotes(client, APAC, group="apac", as_of=as_of),
        "汇率": fetch_quotes(client, FX, group="fx", as_of="抓取时点"),
        "商品": fetch_quotes(client, COMMODITY, group="commodity", as_of="抓取时点"),
        "美债": fetch_fred_yields(client, FRED_SERIES),
        "中国国债": fetch_china_bond_yields(client),
    }
    if settings.sectors.enabled:
        tasks["板块"] = fetch_sectors(
            client, top_n=settings.sectors.top_n, as_of=as_of
        )
    turnover_secids = {
        secid: label
        for secid, label in a_share.items()
        if secid in {"1.000001", "0.399001"}
    }
    if turnover_secids:
        tasks["成交额"] = fetch_turnover(client, turnover_secids)

    names = list(tasks)
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
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
    if not any(
        fact.group == "rate"
        and fact.label.startswith("中国")
        and fact.status == "ok"
        for fact in facts
    ):
        missing.append("中国国债")

    facts.append(
        Fact(
            id="commodity:brent",
            group="commodity",
            label="布伦特原油",
            value="暂缺",
            status="missing",
            note="v1 无稳定公开接口",
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
