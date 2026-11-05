"""EastMoney economic calendar parsing."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .schema import CalendarEvent

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://data.eastmoney.com/cjrl/"
CALENDAR_API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CALENDAR_COLUMNS = (
    "START_DATE,END_DATE,FE_CODE,FE_NAME,FE_TYPE,CONTENT,"
    "STD_TYPE_CODE,SPONSOR_NAME,CITY"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_calendar_payload(
    payload: dict, *, report_day: date | None = None
) -> list[CalendarEvent]:
    """Parse the EastMoney calendar JSON API response."""
    rows = (payload.get("result") or {}).get("data") or []
    events: list[CalendarEvent] = []
    for row in rows:
        title = str(row.get("FE_NAME") or "").strip()
        if not title:
            continue
        start = str(row.get("START_DATE") or "")
        time_text = start[11:16] if len(start) >= 16 else "全天"
        importance = str(row.get("FE_TYPE") or row.get("STD_TYPE_CODE") or "")
        events.append(
            CalendarEvent(
                time=time_text,
                title=title,
                importance=importance,
                source_name="东方财富财经日历",
                source_url=CALENDAR_URL,
            )
        )
    return events


def parse_calendar_html(html: str) -> list[CalendarEvent]:
    """Parse calendar rows from the EastMoney page."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table#cjlrb tbody tr") or soup.select("#cjlrb tr")
    events: list[CalendarEvent] = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        time_text = cells[0].get_text(" ", strip=True)
        title = cells[1].get_text(" ", strip=True)
        importance = (
            cells[2].get_text(" ", strip=True) if len(cells) >= 3 else ""
        )
        if not time_text or not title:
            continue
        events.append(
            CalendarEvent(
                time=time_text,
                title=title,
                importance=importance,
                source_name="东方财富财经日历",
                source_url=CALENDAR_URL,
            )
        )
    return events


async def fetch_calendar(
    client: httpx.AsyncClient,
    *,
    source_url: str = CALENDAR_URL,
    report_day: date | None = None,
) -> list[CalendarEvent]:
    """Fetch the calendar JSON API, falling back to the HTML page."""
    day = report_day or datetime.now(SHANGHAI).date()
    next_day = day + timedelta(days=1)
    params = {
        "reportName": "RPT_CPH_FECALENDAR",
        "pageSize": 50,
        "sortColumns": "START_DATE",
        "sortTypes": "1",
        "filter": (
            f"(END_DATE>='{day.isoformat()}')"
            f"(START_DATE<'{next_day.isoformat()}')"
        ),
        "source": "WEB",
        "client": "WEB",
        "columns": CALENDAR_COLUMNS,
    }
    try:
        response = await client.get(
            CALENDAR_API_URL, params=params, timeout=10, follow_redirects=True
        )
        response.raise_for_status()
        events = parse_calendar_payload(
            response.json(), report_day=report_day
        )
        if events:
            return events
    except Exception as exc:  # noqa: BLE001 - fall back to HTML
        logger.warning("Calendar API failed: %s", exc)

    try:
        response = await client.get(source_url, timeout=10, follow_redirects=True)
        response.raise_for_status()
        return parse_calendar_html(response.text)
    except Exception as exc:  # noqa: BLE001 - caller records the missing group
        logger.warning("Calendar fetch failed: %s", exc)
        return []
