import asyncio
from datetime import date

import httpx

from src.daily.market import repo

CSV_TEXT = "\n".join(
    [
        "",
        "日期,备注,国家,币种,期限,品种,FR001,FR007,FR014",
        "2026-11-10,,,,,,1.39,1.42,1.42",
        "2026-11-09,,,,,,1.37,1.405,1.41",
        "2026-11-06,,,,,,1.34,1.4,1.4",
    ]
)


def test_parse_reads_columns_and_sorts_by_date():
    series = repo.parse_repo_csv(CSV_TEXT)
    assert [day for day, _ in series["fr001"]] == [
        "2026-11-06",
        "2026-11-09",
        "2026-11-10",
    ]
    assert series["fr001"][-1] == ("2026-11-10", 1.39)


def test_parse_skips_header_and_ragged_rows():
    series = repo.parse_repo_csv("日期,FR001\n2026-11-10,,,,")
    assert series == {}


def test_build_repo_facts_reports_bp_change_and_as_of():
    facts = repo.build_repo_facts(
        repo.parse_repo_csv(CSV_TEXT), today=date(2026, 11, 10)
    )
    by_label = {fact.label: fact for fact in facts}
    overnight = by_label["银行间回购 FR001（隔夜）"]
    assert overnight.value == "1.39%"
    assert overnight.change == "+2.0BP"
    assert overnight.change_unit == "bp"
    assert overnight.direction == "up"
    assert overnight.as_of == "2026-11-10"
    assert overnight.note == "中国货币网 · 数据截至 2026-11-10"
    assert overnight.source_name == repo.SOURCE_NAME
    assert overnight.id == "rate:fr001"
    assert by_label["银行间回购 FR007（7 天）"].change == "+1.5BP"


def test_build_repo_facts_hides_stale_fixings():
    facts = repo.build_repo_facts(
        repo.parse_repo_csv(CSV_TEXT), today=date(2027, 1, 20)
    )
    assert facts == []


def test_fetch_repo_fixings_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.chinamoney.com.cn"
        return httpx.Response(200, text=CSV_TEXT)

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await repo.fetch_repo_fixings(client)

    facts = asyncio.run(run())
    assert {fact.id for fact in facts} == {"rate:fr001", "rate:fr007"}


def test_fetch_repo_fixings_returns_empty_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await repo.fetch_repo_fixings(client)

    assert asyncio.run(run()) == []
