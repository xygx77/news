"""EastMoney quote, sector and turnover endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import httpx

from .schema import Fact

ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
QUOTE_SOURCE_URL = "https://quote.eastmoney.com/"
SOURCE_NAME = "东方财富"

A_SHARE = {
    "1.000001": "上证指数",
    "0.399001": "深证成指",
    "0.399006": "创业板指",
    "1.000688": "科创50",
    "1.000300": "沪深300",
}
A_SHARE_CODES = {
    "000001": "1.000001",
    "399001": "0.399001",
    "399006": "0.399006",
    "000688": "1.000688",
    "000300": "1.000300",
}
HK = {"100.HSI": "恒生指数"}
US = {"100.DJIA": "道琼斯", "100.SPX": "标普500", "100.NDX": "纳斯达克"}
EU = {
    "100.GDAXI": "德国 DAX",
    "100.FTSE": "英国富时100",
    "100.FCHI": "法国 CAC40",
    "100.SX5E": "欧洲斯托克50",
}
APAC = {"100.N225": "日经225", "100.KS11": "韩国 KOSPI"}
FX = {
    "100.UDI": "美元指数",
    "133.USDCNH": "美元兑离岸人民币",
    "119.EURUSD": "欧元/美元",
    "119.GBPUSD": "英镑/美元",
    "119.USDJPY": "美元/日元",
}
COMMODITY = {
    "102.CL00Y": "WTI 原油",
    "112.B00Y": "布伦特原油",
    "101.GC00Y": "COMEX 黄金",
    "101.SI00Y": "COMEX 白银",
    "101.HG00Y": "COMEX 铜",
}


def _direction(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _diff_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    diff = (payload.get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        return [row for row in diff.values() if isinstance(row, dict)]
    return [row for row in diff if isinstance(row, dict)]


def _format_change(raw: Any) -> tuple[str | None, float | None, str | None, str]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, None, None, "unknown"
    return f"{value:+.2f}%", value, "pct", _direction(value)


def parse_ulist(
    payload: dict[str, Any],
    *,
    group: str,
    as_of: str,
    labels: Mapping[str, str] | None = None,
) -> list[Fact]:
    """Map an EastMoney ulist response into facts."""
    labels = labels or {}
    facts: list[Fact] = []
    for row in _diff_rows(payload):
        code = str(row.get("f12") or "")
        label = str(labels.get(code) or row.get("f14") or code)
        raw_value = row.get("f2")
        try:
            value_num = float(raw_value)
        except (TypeError, ValueError):
            value_num = None
        if value_num is None:
            value = "暂缺"
            status = "missing"
        else:
            value = f"{value_num:.2f}"
            status = "ok"
        change, change_num, change_unit, direction = _format_change(row.get("f3"))
        facts.append(
            Fact(
                id=f"{group}:{code}",
                group=group,
                label=label,
                value=value,
                value_num=value_num,
                change=change,
                change_num=change_num,
                change_unit=change_unit,
                direction=direction,
                status=status,
                source_name=SOURCE_NAME,
                source_url=QUOTE_SOURCE_URL,
                as_of=as_of,
            )
        )
    return facts


def parse_clist(payload: dict[str, Any], *, as_of: str) -> list[Fact]:
    """Map an EastMoney sector list response into facts."""
    facts: list[Fact] = []
    for row in _diff_rows(payload):
        code = str(row.get("f12") or "")
        label = str(row.get("f14") or code)
        change, change_num, change_unit, direction = _format_change(row.get("f3"))
        facts.append(
            Fact(
                id=f"sector:{code}",
                group="sector",
                label=label,
                value=change or "暂缺",
                value_num=change_num,
                change=change,
                change_num=change_num,
                change_unit=change_unit,
                direction=direction,
                status="ok" if change else "missing",
                source_name=SOURCE_NAME,
                source_url="https://quote.eastmoney.com/center/boardlist.html",
                as_of=as_of,
            )
        )
    return facts


async def _get_json(
    client: httpx.AsyncClient, url: str, *, params: Mapping[str, Any], attempts: int = 3
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(
                url, params=params, timeout=10, follow_redirects=True
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - retried, then raised
            last_error = exc
            if attempt < attempts - 1:
                await asyncio.sleep(0.5)
    assert last_error is not None
    raise last_error


async def fetch_quotes(
    client: httpx.AsyncClient,
    secids: Mapping[str, str],
    *,
    group: str,
    as_of: str,
) -> list[Fact]:
    """Fetch a group of quotes in one ulist request."""
    payload = await _get_json(
        client,
        ULIST_URL,
        params={
            "fltt": 2,
            "fields": "f2,f3,f6,f12,f14",
            "secids": ",".join(secids),
        },
    )
    labels = {
        secid.split(".", 1)[-1]: label for secid, label in secids.items()
    }
    return parse_ulist(
        payload, group=group, as_of=as_of, labels=labels
    )


async def fetch_sectors(
    client: httpx.AsyncClient, *, top_n: int = 5, as_of: str
) -> tuple[list[Fact], list[Fact]]:
    """Fetch top gainers and losers among industry sectors."""
    base_params = {
        "pn": 1,
        "pz": max(top_n, 1),
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:90+t:2+f:!50",
        "fields": "f3,f12,f14",
    }
    gainers_payload = await _get_json(
        client, CLIST_URL, params={**base_params, "po": 1}
    )
    losers_payload = await _get_json(
        client, CLIST_URL, params={**base_params, "po": 0}
    )
    gainers = parse_clist(gainers_payload, as_of=as_of)[:top_n]
    losers = parse_clist(losers_payload, as_of=as_of)[:top_n]
    return gainers, losers


async def fetch_turnover(
    client: httpx.AsyncClient, secids: Mapping[str, str]
) -> Fact | None:
    """Sum Shanghai and Shenzhen turnover from ``f6``."""
    payload = await _get_json(
        client,
        ULIST_URL,
        params={
            "fltt": 2,
            "fields": "f6,f12,f14",
            "secids": ",".join(secids),
        },
    )
    total = 0.0
    for row in _diff_rows(payload):
        try:
            total += float(row.get("f6"))
        except (TypeError, ValueError):
            continue
    if total <= 0:
        return None
    return Fact(
        id="turnover:cn",
        group="turnover",
        label="两市成交额",
        value=f"{total / 1e8:.2f} 亿元",
        value_num=total / 1e8,
        status="ok",
        note="由沪深两市成交额求和",
        source_name=SOURCE_NAME,
        source_url=QUOTE_SOURCE_URL,
        as_of="上一交易日收盘",
    )
