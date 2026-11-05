"""Structured drafts produced by rule writers and consumed by renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ..market.schema import CalendarEvent, Fact
from ..market.trading_day import TradingDayInfo
from .scoring import LaserItem


@dataclass(frozen=True)
class SourceRef:
    name: str
    url: str
    note: str


@dataclass(frozen=True)
class NewsBrief:
    title: str
    url: str
    source_name: str
    snippet: str
    published_at: datetime | None


@dataclass
class MorningDraft:
    report_day: date
    weekday_cn: str
    trading: TradingDayInfo
    trading_status: str
    key_points: list[str]
    us_market: str
    eu_market: str
    apac_market: str
    policy_items: list[NewsBrief]
    risk_items: list[str]
    calendar: list[CalendarEvent]
    notes: dict[str, str]
    facts: list[Fact]
    sources: list[SourceRef]
    missing: list[str]
    captured_at: datetime
    policy_sections: dict[str, list[NewsBrief]] = field(default_factory=dict)
    sector_catalysts: dict[str, str] = field(default_factory=dict)
    ai_used: bool = False


@dataclass
class LaserDraft:
    report_day: date
    window_hours: int
    items: list[LaserItem]
    key_points: list[str]
    sources: list[SourceRef]
    insufficient: bool
    missing: list[str]
    captured_at: datetime
