"""Daily briefing orchestration: collect, write, render, log."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Callable, Literal
from zoneinfo import ZoneInfo

import httpx

from ..scrapers.jin10 import Jin10Scraper
from ..scrapers.rss import RSSScraper
from ..scrapers.wallstreetcn import WallstreetcnScraper
from .config import DailyConfig
from .market.calendar import fetch_calendar
from .market.schema import MarketSnapshot
from .market.snapshot import fetch_market_snapshot
from .market.trading_day import (
    fetch_kline_dates,
    load_holidays,
    resolve_trading_day,
)
from .offline import build_offline_client
from .paths import data_dir, templates_dir
from .render.finance import render_morning
from .render.index import render_index
from .render.laser import render_laser
from .sources.laser import LaserCollection, collect_laser, dedup_candidates
from .writer.draft import LaserDraft, MorningDraft
from .writer.enrich import enrich_items
from .writer.ai_finance import build_ai_morning_draft
from .writer.rule_finance import build_morning_draft
from .writer.rule_laser import build_laser_draft
from .writer.scoring import (
    SelectionResult,
    filter_by_window,
    load_keywords,
    select_items,
)

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAILY_HEADERS = {
    "User-Agent": "Horizon/0.1.0 (+https://github.com/Thysrael/Horizon)",
}


@dataclass
class RunReport:
    report_day: date
    output_dir: Path
    finance_ok: bool
    laser_ok: bool
    counts: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    duration_seconds: float = 0.0


def update_source_health(
    state_path: Path, *, source: str, ok: bool, day: date
) -> int:
    """Persist consecutive failure days for one source; return the streak."""
    state_path = Path(state_path)
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    else:
        state = {}
    entry = state.get(source) or {}
    previous = int(entry.get("consecutive_failures", 0))

    if ok:
        streak = 0
        entry["last_ok"] = day.isoformat()
    else:
        if entry.get("last_failure") == day.isoformat():
            streak = previous
        else:
            streak = previous + 1
        entry["last_failure"] = day.isoformat()
    entry["consecutive_failures"] = streak
    state[source] = entry

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return streak


def _short_error(exc: Exception) -> str:
    message = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {message[:200]}"


def _render_atomic(
    render: Callable[[Path], None], final_path: Path
) -> None:
    tmp = final_path.with_name(f".{final_path.name}.tmp")
    try:
        render(tmp)
        tmp.replace(final_path)
    finally:
        if tmp.exists():
            tmp.unlink()


async def _collect_finance(
    config: DailyConfig, client: httpx.AsyncClient, since: datetime
) -> tuple[list, dict[str, str], dict[str, int], dict[str, bool]]:
    scrapers = [
        (
            "jin10",
            Jin10Scraper(
                {
                    "enabled": config.sources.jin10.enabled,
                    "limit": config.sources.jin10.limit,
                },
                client,
            ),
        ),
        (
            "wallstreetcn",
            WallstreetcnScraper(
                {
                    "enabled": config.sources.wallstreetcn.enabled,
                    "channel": config.sources.wallstreetcn.channel,
                    "limit": config.sources.wallstreetcn.limit,
                },
                client,
            ),
        ),
        (
            "rss",
            RSSScraper(config.sources.rss, client, allow_undated=True),
        ),
    ]
    results = await asyncio.gather(
        *(scraper.fetch(since) for _, scraper in scrapers),
        return_exceptions=True,
    )
    items: list = []
    failures: dict[str, str] = {}
    counts: dict[str, int] = {}
    health: dict[str, bool] = {}
    for (name, _), result in zip(scrapers, results):
        if isinstance(result, Exception):
            failures[name] = _short_error(result)
            counts[name] = 0
            health[name] = False
            continue
        items.extend(result.items)
        counts[name] = len(result.items)
        failures.update(result.failures)
        health[name] = result.status != "failure"
    return items, failures, counts, health


async def _collect_laser(
    config: DailyConfig, client: httpx.AsyncClient, now: datetime
) -> tuple[
    SelectionResult,
    dict[str, str],
    dict[str, int],
    dict[str, bool],
    list[str],
]:
    try:
        collection = await asyncio.wait_for(
            collect_laser(client, config.sources.laser, limit=20), timeout=240
        )
    except asyncio.TimeoutError:
        collection = LaserCollection()
        failures = {"laser:timeout": "collection exceeded 240s"}
    else:
        failures = {
            f"laser:{health.name}": (
                (health.error or "empty").splitlines()[0][:200]
            )
            for health in collection.health
            if not health.ok or health.error
        }
    health = {item.name: item.ok for item in collection.health}

    candidates = dedup_candidates(collection.candidates)
    rules = load_keywords(data_dir() / "laser_keywords.json")
    selection = select_items(
        candidates,
        now=now,
        rules=rules,
        window_hours=config.daily.laser.window_hours,
        fallback_window_hours=config.daily.laser.fallback_window_hours,
        min_items=config.daily.laser.min_items,
        max_items=config.daily.laser.max_items,
    )
    enriched = await enrich_items(selection.items, client, top_n=15)
    enriched = filter_by_window(
        enriched, now=now, window_hours=selection.window_hours
    )
    result = SelectionResult(items=enriched, window_hours=selection.window_hours)
    counts = {
        "laser_candidates": len(candidates),
        "laser_selected": len(enriched),
    }
    details = [
        f"{item.name}(listed={item.candidates},parsed={item.parsed},"
        f"elapsed={item.elapsed_ms}ms)"
        for item in collection.health
    ]
    return result, failures, counts, health, details


async def run_daily(
    config: DailyConfig,
    *,
    report_date: date | None = None,
    only: Literal["finance", "laser"] | None = None,
    offline: bool = False,
    out_dir: Path | None = None,
    fixtures_dir: Path | None = None,
) -> RunReport:
    """Run the full daily briefing pipeline."""
    started = time.perf_counter()
    timezone = ZoneInfo(config.daily.timezone)
    if report_date is not None:
        now = datetime.combine(report_date, dt_time(8, 10), tzinfo=timezone)
        report_day = report_date
    else:
        now = datetime.now(timezone)
        report_day = now.date()

    output_dir = Path(out_dir) if out_dir is not None else Path(
        config.daily.output_dir
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    counts: dict[str, int] = {}
    failures: dict[str, str] = {}
    health: dict[str, bool] = {}
    degraded: list[str] = []
    laser_details: list[str] = []
    tracebacks: list[str] = []
    morning: MorningDraft | None = None
    laser_draft: LaserDraft | None = None

    if offline:
        client = build_offline_client(
            fixtures_dir or Path("tests/daily/fixtures")
        )
    else:
        client = httpx.AsyncClient(
            timeout=15, follow_redirects=True, headers=DAILY_HEADERS
        )

    async with client:
        holidays = load_holidays(data_dir() / "cn_market_holidays.json")
        kline_dates = await fetch_kline_dates(client)
        trading = resolve_trading_day(report_day, holidays, kline_dates)
        counts["kline_days"] = len(kline_dates)

        try:
            snapshot = await fetch_market_snapshot(client, config.market)
        except Exception as exc:  # noqa: BLE001 - degrade to empty snapshot
            snapshot = MarketSnapshot(captured_at=now)
            failures["market"] = _short_error(exc)
        calendar = await fetch_calendar(client, report_day=report_day)
        snapshot = snapshot.model_copy(update={"calendar": calendar})
        if not calendar:
            snapshot = snapshot.model_copy(
                update={"missing": [*snapshot.missing, "财经日历"]}
            )
        counts["facts"] = len(snapshot.facts)
        counts["calendar"] = len(calendar)

        if only != "laser" and config.daily.finance.enabled:
            since = datetime.combine(
                trading.previous, dt_time(15, 0), tzinfo=timezone
            )
            finance_items, finance_failures, finance_counts, finance_health = (
                await _collect_finance(config, client, since)
            )
            failures.update(finance_failures)
            counts.update(finance_counts)
            health.update(finance_health)
            try:
                if (
                    config.writer == "ai"
                    and config.ai is not None
                    and not offline
                ):
                    morning = await build_ai_morning_draft(
                        snapshot,
                        finance_items,
                        report_day=report_day,
                        now=now,
                        trading=trading,
                        max_material_items=config.daily.finance.max_material_items,
                        ai_config=config.ai,
                    )
                    counts["ai_finance"] = int(morning.ai_used)
                else:
                    morning = build_morning_draft(
                        snapshot,
                        finance_items,
                        report_day=report_day,
                        now=now,
                        trading=trading,
                        max_material_items=config.daily.finance.max_material_items,
                    )
            except Exception as exc:  # noqa: BLE001 - laser may still render
                failures["writer:finance"] = _short_error(exc)
                tracebacks.append(traceback.format_exc())
                morning = build_morning_draft(
                    snapshot,
                    finance_items,
                    report_day=report_day,
                    now=now,
                    trading=trading,
                    max_material_items=config.daily.finance.max_material_items,
                )

        if only != "finance" and config.daily.laser.enabled:
            try:
                (
                    selection,
                    laser_failures,
                    laser_counts,
                    laser_health,
                    laser_details,
                ) = await _collect_laser(config, client, now)
            except Exception as exc:  # noqa: BLE001 - morning may still render
                failures["laser"] = _short_error(exc)
                tracebacks.append(traceback.format_exc())
            else:
                failures.update(laser_failures)
                counts.update(laser_counts)
                health.update(laser_health)
                try:
                    laser_draft = build_laser_draft(
                        selection, report_day=report_day, now=now
                    )
                except Exception as exc:  # noqa: BLE001
                    failures["writer:laser"] = _short_error(exc)
                    tracebacks.append(traceback.format_exc())

    date_dir = output_dir / report_day.isoformat()
    latest_dir = output_dir / "latest"
    date_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    if morning is not None:
        try:
            _render_atomic(
                lambda path: render_morning(
                    morning,
                    template_path=templates_dir() / "morning.html",
                    output_path=path,
                ),
                date_dir / "morning.html",
            )
            shutil.copy2(
                date_dir / "morning.html", latest_dir / "morning.html"
            )
        except Exception as exc:  # noqa: BLE001 - keep old latest
            failures["render:morning"] = _short_error(exc)
            tracebacks.append(traceback.format_exc())
            morning = None

    if laser_draft is not None:
        try:
            _render_atomic(
                lambda path: render_laser(
                    laser_draft,
                    template_path=templates_dir() / "laser.html",
                    output_path=path,
                ),
                date_dir / "laser.html",
            )
            shutil.copy2(date_dir / "laser.html", latest_dir / "laser.html")
        except Exception as exc:  # noqa: BLE001 - keep old latest
            failures["render:laser"] = _short_error(exc)
            tracebacks.append(traceback.format_exc())
            laser_draft = None

    try:
        render_index(
            output_dir=output_dir,
            report_day=report_day,
            morning=morning,
            laser=laser_draft,
            template_path=templates_dir() / "index.html",
        )
    except Exception as exc:  # noqa: BLE001 - old index remains
        failures["render:index"] = _short_error(exc)
        tracebacks.append(traceback.format_exc())
        fallback = output_dir / "index.html"
        if not fallback.exists():
            fallback.write_text(
                "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
                "<title>每日简报生成失败</title>"
                "<body><h1>每日简报生成失败</h1>"
                "<p>请查看 run.log 获取详情。</p></body></html>",
                encoding="utf-8",
            )

    state_path = output_dir / ".source_health.json"
    for source, ok in health.items():
        streak = update_source_health(
            state_path, source=source, ok=ok, day=report_day
        )
        if streak >= 3:
            degraded.append(source)
    if degraded:
        counts["degraded_sources"] = len(degraded)

    duration = time.perf_counter() - started
    lines = [
        f"[{now:%Y-%m-%d %H:%M:%S}] report={report_day.isoformat()} "
        f"trading={trading.is_trading} previous={trading.previous.isoformat()} "
        f"duration={duration:.1f}s",
        "  counts: " + (", ".join(f"{key}={value}" for key, value in counts.items()) or "none"),
        "  health: "
        + (
            ", ".join(
                f"{key}={'ok' if value else 'fail'}"
                for key, value in health.items()
            )
            or "none"
        ),
    ]
    if snapshot.missing:
        lines.append("  missing: " + ", ".join(snapshot.missing))
    if laser_details:
        lines.append("  laser_sites: " + ", ".join(laser_details))
    if failures:
        lines.append(
            "  failures: "
            + "; ".join(f"{key}: {value}" for key, value in failures.items())
        )
    if degraded:
        lines.append("  degraded: " + ", ".join(degraded))
    if tracebacks:
        lines.append("  tracebacks:\n" + "\n".join(tracebacks))
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    return RunReport(
        report_day=report_day,
        output_dir=output_dir,
        finance_ok=morning is not None,
        laser_ok=laser_draft is not None,
        counts=counts,
        failures=failures,
        duration_seconds=duration,
    )
