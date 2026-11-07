import asyncio

import httpx
import pytest

from src.daily.config import MarketSettings
from src.daily.market import snapshot as snapshot_mod
from src.daily.market import tencent


def _record(code: str, name: str, last: str, prev: str, pct: str) -> str:
    """Build one ``v_code="..."`` record with the fields Tencent actually sends."""
    fields = ["1", name, code, last, prev, "0", "0"]
    fields += [""] * (30 - len(fields))
    fields += ["20261106161401", "", pct]
    assert len(fields) == 33
    return f'v_{code}="{"~".join(fields)}";'


PAYLOAD = (
    _record("sh000001", "上证指数", "3649.18", "3689.91", "-1.10")
    + _record("sz399001", "深证成指", "11175.78", "11471.09", "-2.57")
    + _record("hkHSI", "恒生指数", "24481.86", "24796.98", "-1.27")
    + _record("usDJI", "道琼斯", "51285.73", "51523.06", "-0.46")
)


def test_parse_quotes_maps_fields_and_source():
    facts = tencent.parse_quotes(
        PAYLOAD,
        group="ashare",
        as_of="上一交易日收盘",
        labels={"sh000001": "上证指数", "sz399001": "深证成指"},
    )
    index = {fact.label: fact for fact in facts}
    sh = index["上证指数"]
    assert sh.value == "3649.18"
    assert sh.change == "-1.10%"
    assert sh.change_num == pytest.approx(-1.10)
    assert sh.direction == "down"
    assert sh.source_name == "腾讯财经"
    assert sh.group == "ashare"
    assert sh.as_of == "上一交易日收盘"
    assert sh.status == "ok"
    assert index["深证成指"].change_num == pytest.approx(-2.57)


def test_parse_quotes_ignores_garbage():
    assert tencent.parse_quotes("", group="ashare", as_of="x") == []
    assert tencent.parse_quotes("not a payload", group="ashare", as_of="x") == []


def test_codes_for_group_mirrors_eastmoney_secids():
    a_share = {"1.000001": "上证指数", "0.399001": "深证成指"}
    assert tencent.codes_for_group("A 股", a_share) == {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
    }
    assert tencent.codes_for_group("港股", {}) == tencent.HK_CODES
    assert tencent.codes_for_group("美股", {}) == tencent.US_CODES
    assert tencent.codes_for_group("商品", {}) == {}


async def _empty_pair(*args, **kwargs):
    return [], []


async def _none(*args, **kwargs):
    return None


def test_equity_tables_fall_back_to_tencent(monkeypatch):
    """When the primary quote feed dies, the index rows must still be emitted."""

    async def primary_down(*args, **kwargs):
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(snapshot_mod, "fetch_quotes", primary_down)
    monkeypatch.setattr(snapshot_mod, "fetch_sectors", _empty_pair)
    monkeypatch.setattr(snapshot_mod, "fetch_turnover", _none)
    monkeypatch.setattr(snapshot_mod, "fetch_breadth", _none)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "qt.gtimg.cn":
            return httpx.Response(
                200, content=PAYLOAD.encode("gb18030")
            )
        return httpx.Response(500, text="down")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await snapshot_mod.fetch_market_snapshot(
                client, MarketSettings(), as_of="测试口径"
            )

    snapshot = asyncio.run(run())
    labels = {fact.label: fact for fact in snapshot.facts}

    assert "上证指数" in labels, snapshot.missing
    assert labels["上证指数"].source_name == "腾讯财经"
    assert labels["上证指数"].value == "3649.18"
    assert labels["恒生指数"].group == "hk"
    assert labels["恒生指数"].as_of == "测试口径"
    assert labels["道琼斯"].group == "us"
    assert labels["道琼斯"].as_of == "刚结束的美股交易日收盘"

    # Indices are no longer reported missing; the genuine gaps still are.
    assert "A 股" not in snapshot.missing
    assert "港股" not in snapshot.missing
    assert "板块" in snapshot.missing
