"""Writer protocol and context objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from ..market.schema import MarketSnapshot
from ..market.trading_day import TradingDayInfo
from ...models import ContentItem
from .draft import LaserDraft, MorningDraft
from .scoring import SelectionResult


@dataclass
class MorningContext:
    report_day: date
    now: datetime
    trading: TradingDayInfo
    snapshot: MarketSnapshot
    news: list[ContentItem] = field(default_factory=list)
    max_material_items: int = 40


@dataclass
class LaserContext:
    report_day: date
    now: datetime
    selection: SelectionResult
    missing: list[str] = field(default_factory=list)


class Writer(Protocol):
    """Rule or AI writer for both daily reports."""

    name: str

    def write_morning(self, ctx: MorningContext) -> MorningDraft: ...

    def write_laser(self, ctx: LaserContext) -> LaserDraft: ...
