"""China A-share trading day resolution."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import httpx

logger = logging.getLogger(__name__)

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


@dataclass(frozen=True)
class TradingDayInfo:
    day: date
    is_trading: bool
    previous: date
    verified: bool


def load_holidays(path: Path) -> set[date]:
    """Load holiday dates from a year-keyed JSON file."""
    path = Path(path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Holiday file %s could not be read: %s", path, exc)
        return set()

    holidays: set[date] = set()
    for value in data.values() if isinstance(data, dict) else []:
        if not isinstance(value, list):
            continue
        for item in value:
            try:
                holidays.add(date.fromisoformat(str(item)))
            except (TypeError, ValueError):
                continue
    return holidays


def is_trading_day(day: date, holidays: set[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def previous_trading_day(day: date, holidays: set[date]) -> date:
    current = day - timedelta(days=1)
    for _ in range(30):
        if is_trading_day(current, holidays):
            return current
        current -= timedelta(days=1)
    return current


def resolve_trading_day(
    day: date,
    holidays: set[date],
    kline_dates: Sequence[date] | None = None,
) -> TradingDayInfo:
    """Resolve trading status and the previous trading day."""
    holiday_trading = is_trading_day(day, holidays)
    if kline_dates:
        past = [item for item in kline_dates if item < day]
        expected_previous = previous_trading_day(day, holidays)
        previous = max(past) if past else expected_previous
        if holiday_trading and previous < expected_previous:
            # K-line data shows an earlier close than the local rule expects:
            # a holiday is missing from the JSON, so treat today as closed.
            is_trading = False
            verified = False
        else:
            is_trading = holiday_trading
            verified = True
    else:
        is_trading = holiday_trading
        previous = previous_trading_day(day, holidays)
        verified = False
    return TradingDayInfo(
        day=day, is_trading=is_trading, previous=previous, verified=verified
    )


async def fetch_kline_dates(
    client: httpx.AsyncClient, *, limit: int = 10
) -> list[date]:
    """Fetch recent Shanghai Composite daily bars for cross-checking."""
    try:
        response = await client.get(
            KLINE_URL,
            params={
                "secid": "1.000001",
                "klt": 101,
                "fqt": 1,
                "lmt": limit,
                "end": "20500101",
                "fields1": "f1",
                "fields2": "f51",
            },
            timeout=10,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - optional verification
        logger.warning("Kline fetch failed: %s", exc)
        return []

    dates: list[date] = []
    for line in (payload.get("data") or {}).get("klines") or []:
        token = str(line).split(",", 1)[0].strip()
        try:
            dates.append(date.fromisoformat(token))
        except ValueError:
            continue
    return dates
