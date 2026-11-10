import asyncio

import httpx
import pytest

from src.daily.config import MarketSettings
from src.daily.market import sina
from src.daily.market import snapshot as snapshot_mod

PAYLOAD = "\n".join(
    [
        'var hq_str_hf_CL="90.238,,90.340,90.360,92.530,89.070,05:59:59,90.520,91.200,0,11,3,2026-11-07,纽约原油,0";',
        'var hq_str_hf_OIL="97.853,,97.900,97.920,98.500,97.100,05:59:59,98.290,97.500,0,5,1,2026-11-07,布伦特原油,0";',
        'var hq_str_hf_HG="626.288,,626.000,626.150,629.200,618.800,05:59:48,626.000,622.650,0,3,3,2026-11-07,美铜,0";',
        'var hq_str_fx_seurusd="05:59:57,1.114300,1.114500,1.114300,104,1.121700,1.123400,1.113000,1.114300,欧元兑美元即期汇率,-0.670000,-0.007500,0.009272";',
        'var hq_str_fx_susdcnh="05:58:52,6.673600,6.674000,6.673000,120,6.690000,6.695000,6.660000,6.661000,美元兑离岸人民币,0.130000,0.008600,0.0013";',
        'var hq_str_hf_CHA50CFD="12914.750,,12917.000,12918.000,13059.000,12892.000,13:35:29,12906.000,12907.000,1028578,8,13,2026-11-10,富时中国A50期货,264425";',
    ]
)


def test_parse_fx_uses_percent_field():
    facts = sina.parse_quotes(
        PAYLOAD,
        group="fx",
        as_of="抓取时点",
        labels={"fx_seurusd": "欧元/美元"},
    )
    assert len(facts) == 1
    fact = facts[0]
    assert fact.value == "1.1143"
    assert fact.change == "-0.67%"
    assert fact.change_unit == "pct"
    assert fact.direction == "down"
    assert fact.source_name == "新浪财经"
    assert fact.group == "fx"


def test_parse_commodity_computes_percent_from_previous_close():
    facts = sina.parse_quotes(
        PAYLOAD, group="commodity", as_of="抓取时点", labels={"hf_CL": "WTI 原油"}
    )
    fact = facts[0]
    assert fact.value == "90.24"
    assert fact.change_num == pytest.approx(-0.3116, abs=1e-3)
    assert fact.change == "-0.31%"


def test_parse_skips_unrequested_codes():
    facts = sina.parse_quotes(
        PAYLOAD,
        group="commodity",
        as_of="抓取时点",
        labels={"hf_CL": "WTI 原油", "hf_OIL": "布伦特原油"},
    )
    assert [fact.label for fact in facts] == ["WTI 原油", "布伦特原油"]


def test_copper_is_scaled_to_dollars_per_pound():    # Sina quotes copper in cents/lb; EastMoney publishes dollars/lb.
    facts = sina.parse_quotes(
        PAYLOAD, group="commodity", as_of="抓取时点", labels={"hf_HG": "COMEX 铜"}
    )
    fact = facts[0]
    assert fact.value == "6.26"
    assert fact.value_num == pytest.approx(6.26288, abs=1e-5)


def test_parse_a50_futures_uses_previous_settlement():
    facts = sina.parse_quotes(
        PAYLOAD,
        group="a50",
        as_of="抓取时点",
        labels=sina.A50_CODES,
        note=sina.A50_NOTE,
    )
    assert len(facts) == 1
    fact = facts[0]
    assert fact.label == "富时中国A50期指"
    assert fact.value == "12914.75"
    assert fact.change == "+0.07%"
    assert fact.note == "新浪 · 连续合约"
    assert fact.id == "a50:hf_CHA50CFD"


def test_a50_is_not_part_of_the_fx_or_commodity_fallbacks():
    assert "hf_CHA50CFD" not in sina.COMMODITY_CODES
    assert "hf_CHA50CFD" not in sina.FX_CODES
    assert sina.codes_for_group("富时A50", {}) == {}


def test_codes_cover_commodity_and_fx_but_not_indices():
    assert sina.codes_for_group("商品", {}) == sina.COMMODITY_CODES
    assert len(sina.codes_for_group("汇率", {})) == 4
    assert "美元指数" not in sina.codes_for_group("汇率", {}).values()
    assert sina.codes_for_group("A 股", {}) == {}
    assert sina.codes_for_group("欧股", {}) == {}
    assert sina.group_id("汇率") == "fx"
    assert sina.group_id("商品") == "commodity"
    assert sina.group_id("欧股") is None


def test_fx_and_commodity_fall_back_to_sina(monkeypatch):
    """EastMoney dies for two groups; Sina must keep those rows in the brief."""

    async def partial_eastmoney(client, secids, *, group, as_of):
        if group in {"fx", "commodity"}:
            raise RuntimeError("eastmoney down")
        raise RuntimeError("only fx/commodity are exercised here")

    monkeypatch.setattr(snapshot_mod, "fetch_quotes", partial_eastmoney)

    async def no_sectors(*args, **kwargs):
        return [], []

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(snapshot_mod, "fetch_sectors", no_sectors)
    monkeypatch.setattr(snapshot_mod, "fetch_turnover", nothing)
    monkeypatch.setattr(snapshot_mod, "fetch_breadth", nothing)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hq.sinajs.cn":
            return httpx.Response(200, content=PAYLOAD.encode("gb18030"))
        return httpx.Response(500, text="down")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await snapshot_mod.fetch_market_snapshot(
                client, MarketSettings(), as_of="测试口径"
            )

    snapshot = asyncio.run(run())
    by_label = {fact.label: fact for fact in snapshot.facts}

    assert "欧元/美元" in by_label, snapshot.missing
    assert by_label["欧元/美元"].source_name == "新浪财经"
    assert "WTI 原油" in by_label
    assert by_label["WTI 原油"].as_of == "抓取时点"
    assert "汇率" not in snapshot.missing
    assert "商品" not in snapshot.missing
    # No source covers these groups, so they stay explicitly missing.
    assert "欧股" in snapshot.missing
