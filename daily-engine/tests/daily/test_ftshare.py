import asyncio

import httpx
import pytest

from src.daily.market import ftshare
from src.daily.market import rates

RECORDS = [
    {
        "curve_name": "中债国债收益率曲线",
        "trade_date": "2026-11-05",
        "y_1y": 1.1710,
        "y_10y": 1.6038,
        "y_30y": 2.0425,
    },
    {
        "curve_name": "中债国债收益率曲线",
        "trade_date": "2026-11-06",
        "y_1y": 1.1683,
        "y_10y": 1.5868,
        "y_30y": 2.0275,
    },
    {
        "curve_name": "中债中短期票据收益率曲线(AAA)",
        "trade_date": "2026-11-06",
        "y_1y": 1.4775,
        "y_10y": 1.8975,
        "y_30y": None,
    },
]


def test_parse_yields_builds_both_tenors_with_change():
    facts = ftshare.parse_yields(RECORDS)
    by_label = {fact.label: fact for fact in facts}

    ten = by_label["中国 10 年期国债"]
    assert ten.value == "1.59%"
    assert ten.change == "-1.7BP"
    assert ten.change_unit == "bp"
    assert ten.direction == "down"
    assert ten.as_of == "2026-11-06"
    assert ten.source_name == "非凸科技 FTShare"
    assert "2026-11-06" in ten.note

    thirty = by_label["中国 30 年期国债"]
    assert thirty.value == "2.03%"
    assert thirty.change == "-1.5BP"


def test_parse_yields_ignores_other_curves():
    others = [row for row in RECORDS if "国债收益率曲线" not in row["curve_name"]]
    assert ftshare.parse_yields(others) == []
    assert ftshare.parse_yields([]) == []


def test_parse_yields_skips_tenor_without_data():
    rows = [dict(row, y_30y=None) for row in RECORDS]
    labels = [fact.label for fact in ftshare.parse_yields(rows)]
    assert labels == ["中国 10 年期国债"]


def test_fetch_returns_empty_without_key():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            return await ftshare.fetch_china_bond_yields(client, "")

    assert asyncio.run(run()) == []


def test_rates_falls_back_to_ftshare(monkeypatch):
    """The official scrape fails; the FTShare curve must fill the rate rows."""

    async def official_down(client):
        return []

    expected = ftshare.parse_yields(RECORDS)

    async def gateway(client, api_key):
        assert api_key == "test-key"
        return expected

    monkeypatch.setattr(rates, "_fetch_chinabond_yields", official_down)
    monkeypatch.setattr(rates.ftshare, "fetch_china_bond_yields", gateway)
    monkeypatch.setenv("FTSHARE_API_KEY", "test-key")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            return await rates.fetch_china_bond_yields(client)

    facts = asyncio.run(run())
    assert [fact.label for fact in facts] == ["中国 10 年期国债", "中国 30 年期国债"]


def test_rates_stays_empty_without_key(monkeypatch):
    async def official_down(client):
        return []

    monkeypatch.setattr(rates, "_fetch_chinabond_yields", official_down)
    monkeypatch.delenv("FTSHARE_API_KEY", raising=False)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            return await rates.fetch_china_bond_yields(client)

    assert asyncio.run(run()) == []
