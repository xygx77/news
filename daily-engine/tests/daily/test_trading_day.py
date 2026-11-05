import asyncio
from datetime import date

import httpx

from src.daily.market.calendar import parse_calendar_html, parse_calendar_payload
from src.daily.market.trading_day import (
    fetch_kline_dates,
    is_trading_day,
    previous_trading_day,
    resolve_trading_day,
)

HOLIDAYS = {date(2026, 10, 1), date(2026, 10, 2)}


def test_parse_calendar_html_reads_rows():
    html = (
        '<table id="cjlrb"><tbody><tr>'
        "<td>09:30</td><td>中国 10 月 CPI</td><td>高</td>"
        "</tr></tbody></table>"
    )
    events = parse_calendar_html(html)
    assert events[0].time == "09:30"
    assert events[0].title == "中国 10 月 CPI"


def test_weekend_is_not_trading_day():
    assert not is_trading_day(date(2026, 11, 7), HOLIDAYS)


def test_holiday_is_not_trading_day_and_previous_skips_it():
    assert not is_trading_day(date(2026, 10, 1), HOLIDAYS)
    assert previous_trading_day(date(2026, 10, 2), HOLIDAYS) == date(2026, 9, 30)


def test_resolve_marks_unverified_without_kline_data():
    info = resolve_trading_day(date(2026, 11, 5), HOLIDAYS)
    assert info.is_trading is True
    assert info.verified is False


def test_parse_calendar_payload_reads_events():
    payload = {
        "result": {
            "data": [
                {
                    "START_DATE": "2026-11-05 09:30:00",
                    "FE_NAME": "中国 10 月 CPI",
                    "FE_TYPE": "经济数据",
                    "STD_TYPE_CODE": "2",
                }
            ]
        }
    }
    events = parse_calendar_payload(payload, report_day=date(2026, 11, 5))
    assert events[0].time == "09:30"
    assert events[0].title == "中国 10 月 CPI"


def test_resolve_detects_missing_holiday_from_kline():
    info = resolve_trading_day(
        date(2026, 11, 6),
        set(),
        [date(2026, 11, 4)],
    )
    assert info.is_trading is False
    assert info.verified is False


def test_fetch_kline_dates_sends_end_param():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": {"klines": ["2026-11-04,1"]}})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await fetch_kline_dates(client)

    dates = asyncio.run(run())
    assert dates == [date(2026, 11, 4)]
    assert "end=20500101" in seen["url"]
