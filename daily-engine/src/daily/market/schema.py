"""Structured market facts used by writers and renderers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Fact(BaseModel):
    """One traceable market data point or derived display value."""

    model_config = ConfigDict(extra="forbid")

    id: str
    group: str
    label: str
    value: str
    value_num: float | None = None
    change: str | None = None
    change_num: float | None = None
    change_unit: Literal["pct", "bp", "abs"] | None = None
    direction: Literal["up", "down", "flat", "unknown"] = "unknown"
    status: Literal["ok", "missing", "stale"] = "ok"
    note: str | None = None
    source_name: str
    source_url: str
    as_of: str


class CalendarEvent(BaseModel):
    """One economic calendar entry."""

    model_config = ConfigDict(extra="forbid")

    time: str
    title: str
    importance: str = ""
    source_name: str = "东方财富财经日历"
    source_url: str = "https://data.eastmoney.com/cjrl/"


class MarketSnapshot(BaseModel):
    """All facts and calendar entries collected for one run."""

    model_config = ConfigDict(extra="forbid")

    facts: list[Fact] = Field(default_factory=list)
    calendar: list[CalendarEvent] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    captured_at: datetime
