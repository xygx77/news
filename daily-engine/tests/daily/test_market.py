import asyncio

import httpx

from src.daily.market.eastmoney import (
    fetch_breadth,
    parse_breadth,
    parse_clist,
    parse_ulist,
)
from src.daily.market.rates import parse_fred_csv


def test_parse_ulist_maps_fields():
    payload = {
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f14": "上证指数",
                    "f2": 3388.81,
                    "f3": 1.23,
                    "f6": 512345678900,
                },
            ]
        }
    }
    facts = parse_ulist(payload, group="ashare", as_of="2026-11-04 收盘")
    assert facts[0].value == "3388.81"
    assert facts[0].change == "+1.23%"
    assert facts[0].change_num == 1.23
    assert facts[0].change_unit == "pct"
    assert facts[0].direction == "up"
    assert facts[0].status == "ok"
    assert facts[0].label == "上证指数"


def test_parse_ulist_uses_configured_label():
    payload = {
        "data": {
            "diff": [
                {"f12": "CL00Y", "f14": "WTI原油", "f2": 78.40, "f3": 1.15}
            ]
        }
    }
    facts = parse_ulist(
        payload,
        group="commodity",
        as_of="抓取时点",
        labels={"CL00Y": "WTI 原油"},
    )
    assert facts[0].label == "WTI 原油"


def test_parse_ulist_marks_missing_value():
    payload = {
        "data": {"diff": [{"f12": "000001", "f14": "上证指数", "f2": "-", "f3": None}]}
    }
    fact = parse_ulist(payload, group="ashare", as_of="x")[0]
    assert fact.value == "暂缺"
    assert fact.status == "missing"
    assert fact.value_num is None


def test_parse_fred_csv_uses_last_two_observations():
    text = "observation_date,DGS10\n2026-11-03,4.91\n2026-11-04,4.95\n2026-11-05,.\n"
    fact = parse_fred_csv(text, label="美国 10 年期国债", series_id="DGS10")
    assert fact.value == "4.95%"
    assert fact.change == "+4BP"
    assert fact.change_num == 4.0
    assert fact.change_unit == "bp"


def test_parse_clist_formats_sector_rows():
    payload = {"data": {"diff": [{"f12": "BK001", "f14": "半导体", "f3": 3.21}]}}
    facts = parse_clist(payload, as_of="2026-11-04 收盘")
    assert facts[0].group == "sector"
    assert facts[0].change == "+3.21%"


def test_parse_breadth_sums_buckets():
    payload = {
        "data": {
            "qdate": 20261106,
            "fenbu": [
                {"-11": 1},
                {"-1": 1251},
                {"0": 101},
                {"1": 470},
                {"2": 196},
            ],
        }
    }
    fact = parse_breadth(payload)
    assert fact is not None
    assert fact.group == "breadth"
    assert fact.label == "涨跌家数"
    assert fact.value == "上涨 666 家、下跌 1252 家、平盘 101 家"
    assert fact.direction == "down"
    assert fact.note == "含北交所，样本 2019 只，数据截至 2026-11-06"
    assert fact.source_name == "东方财富"


def test_parse_breadth_returns_none_without_samples():
    assert parse_breadth({"data": {"fenbu": []}}) is None
    assert parse_breadth({"data": {}}) is None
    assert parse_breadth({}) is None


def test_fetch_breadth_requests_distribution_endpoint():
    calls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(
            200,
            json={"data": {"qdate": 20261106, "fenbu": [{"1": 7}, {"-1": 3}, {"0": 1}]}},
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await fetch_breadth(client)

    fact = asyncio.run(run())
    assert fact is not None
    assert calls[0].host == "push2ex.eastmoney.com"
    assert calls[0].path == "/getTopicZDFenBu"
    assert calls[0].params["dpt"] == "wz.ztzt"
    assert fact.value == "上涨 7 家、下跌 3 家、平盘 1 家"


def test_snapshot_includes_breadth_fact():
    from src.daily.config import MarketSettings
    from src.daily.market.snapshot import fetch_market_snapshot

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/getTopicZDFenBu":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "qdate": 20261106,
                        "fenbu": [{"1": 860}, {"-1": 4418}, {"0": 101}],
                    }
                },
            )
        return httpx.Response(200, json={"data": {"diff": []}})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await fetch_market_snapshot(client, MarketSettings())

    snapshot = asyncio.run(run())
    breadth = [fact for fact in snapshot.facts if fact.id == "breadth:cn"]
    assert len(breadth) == 1
    assert breadth[0].value == "上涨 860 家、下跌 4418 家、平盘 101 家"
