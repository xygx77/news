"""China bond yield fallback via FTShare (非凸科技) REST gateway.

The official ChinaBond page is scraped first because it is the primary source,
but its HTML table fails often enough that the rate section silently loses its
China rows. This module mirrors that output from the FTShare endpoint the
Capital Generation plugin documents, and records the curve's own trade date so a
stale upstream shows up as a date, not as a wrong-but-fresh number.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

import httpx

from .schema import Fact

API_URL = "https://market.ft.tech/gateway/api/v1/market/data/chinabond-yield-daily"
SOURCE_NAME = "非凸科技 FTShare"
SOURCE_URL = "https://market.ft.tech/gateway/doc"
CURVE_NAME = "中债国债收益率曲线"
WINDOW_DAYS = 20
PAGE_SIZE = 200


def _direction(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _to_float(raw: Any) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_yields(
    records: list[Mapping[str, Any]], *, curve: str = CURVE_NAME
) -> list[Fact]:
    """Build 10Y/30Y facts from ``chinabond-yield-daily`` records."""
    rows = sorted(
        (row for row in records if str(row.get("curve_name") or "") == curve),
        key=lambda row: str(row.get("trade_date") or ""),
    )
    if not rows:
        return []

    facts: list[Fact] = []
    for tenor, label, fact_id in (
        ("y_10y", "中国 10 年期国债", "rate:cn10y"),
        ("y_30y", "中国 30 年期国债", "rate:cn30y"),
    ):
        series = [
            (str(row.get("trade_date") or ""), _to_float(row.get(tenor)))
            for row in rows
        ]
        series = [(day, value) for day, value in series if day and value is not None]
        if not series:
            continue
        as_of, latest = series[-1]
        change_num: float | None = None
        change: str | None = None
        direction = "unknown"
        if len(series) >= 2:
            change_num = round((latest - series[-2][1]) * 100, 1)
            change = f"{change_num:+.1f}BP"
            direction = _direction(change_num)
        facts.append(
            Fact(
                id=fact_id,
                group="rate",
                label=label,
                value=f"{latest:.2f}%",
                value_num=latest,
                change=change,
                change_num=change_num,
                change_unit="bp" if change_num is not None else None,
                direction=direction,
                status="ok",
                note=f"中债估值中心 · 数据截至 {as_of}",
                source_name=SOURCE_NAME,
                source_url=SOURCE_URL,
                as_of=as_of,
            )
        )
    return facts


async def fetch_china_bond_yields(
    client: httpx.AsyncClient, api_key: str
) -> list[Fact]:
    """Fetch the government-bond curve; ``[]`` when unavailable."""
    if not api_key:
        return []
    today = date.today()
    response = await client.get(
        API_URL,
        params={
            "start_date": (today - timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d"),
            "end_date": today.strftime("%Y%m%d"),
            "curve_name": CURVE_NAME,
            "page": 1,
            "page_size": PAGE_SIZE,
        },
        headers={"FTSHARE_API_KEY": api_key, "Accept": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    records = data.get("records")
    if not isinstance(records, list):
        return []
    return parse_yields(records)
