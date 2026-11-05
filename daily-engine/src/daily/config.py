"""Pydantic configuration for the daily briefing pipeline."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models import RSSSourceConfig

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def expand_env_vars(value: Any) -> Any:
    """Expand ``${VAR}`` references in every string value."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), match.group(0)).strip(),
            value,
        )
    if isinstance(value, list):
        return [expand_env_vars(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_vars(item) for key, item in value.items()}
    return value


class FinanceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_material_items: int = 40


class LaserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    window_hours: int = 48
    fallback_window_hours: int = 72
    min_items: int = 5
    max_items: int = 8


class DailySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Shanghai"
    output_dir: str = "output/daily"
    finance: FinanceSettings = Field(default_factory=FinanceSettings)
    laser: LaserSettings = Field(default_factory=LaserSettings)


class LaserSiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    strategy: Literal["html_list", "sitemap", "custom"]
    list_url: str | None = None
    sitemap_url: str | None = None
    url_pattern: str = ""
    base_url: str = ""
    date_from_url: bool = False
    date_regex: str | None = None
    adapter: str | None = None
    timezone: str = "Asia/Shanghai"
    language: Literal["zh", "en"] = "zh"
    weight: float = 1.0
    enabled: bool = True
    reason: str | None = None

    @model_validator(mode="after")
    def fill_base_url(self) -> "LaserSiteConfig":
        if self.enabled and not self.base_url:
            source = self.list_url or self.sitemap_url
            if source:
                parts = urlsplit(source)
                self.base_url = f"{parts.scheme}://{parts.netloc}"
        return self

    @model_validator(mode="after")
    def validate_enabled_site(self) -> "LaserSiteConfig":
        if not self.enabled:
            return self
        if self.strategy == "html_list" and not self.list_url:
            raise ValueError(f"{self.name}: list_url is required for html_list")
        if self.strategy == "sitemap" and not self.sitemap_url:
            raise ValueError(f"{self.name}: sitemap_url is required for sitemap")
        if self.strategy == "custom" and not (self.list_url or self.sitemap_url):
            raise ValueError(f"{self.name}: list_url or sitemap_url is required for custom")
        if not self.url_pattern:
            raise ValueError(f"{self.name}: url_pattern is required")
        return self


class Jin10Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    limit: int = 100


class WallstreetcnSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    channel: str = "global-channel"
    limit: int = 100


class SourceToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class DailySourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jin10: Jin10Settings = Field(default_factory=Jin10Settings)
    wallstreetcn: WallstreetcnSettings = Field(default_factory=WallstreetcnSettings)
    rss: list[RSSSourceConfig] = Field(default_factory=list)
    cnfin: SourceToggle = Field(default_factory=SourceToggle)
    laser: list[LaserSiteConfig] = Field(default_factory=list)


class SectorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    top_n: int = 5


class MarketSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indices: list[str] = Field(
        default_factory=lambda: ["000001", "399001", "399006", "000688", "000300"]
    )
    sectors: SectorSettings = Field(default_factory=SectorSettings)


class DailyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    writer: Literal["rule"] = "rule"
    daily: DailySettings = Field(default_factory=DailySettings)
    sources: DailySourcesConfig = Field(default_factory=DailySourcesConfig)
    market: MarketSettings = Field(default_factory=MarketSettings)

    @model_validator(mode="before")
    @classmethod
    def reject_ai_writer(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("writer") == "ai":
            raise ValueError("AI writer not implemented in v1")
        return value


def load_daily_config(path: Path) -> DailyConfig:
    """Load and validate a daily briefing configuration file."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return DailyConfig.model_validate(expand_env_vars(data))
