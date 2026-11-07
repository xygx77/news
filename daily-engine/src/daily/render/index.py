"""Render the archive index for the daily briefing site."""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path

from ..writer.draft import LaserDraft, MorningDraft
from ..writer.rule_finance import WEEKDAYS_CN
from .template import fill_placeholders, render_conditional_blocks

_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TAG = re.compile(r"<[^>]+>")


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _plain(value: object) -> str:
    """Strip inline markup from feed titles before escaping."""
    return html.escape(_TAG.sub("", str(value if value is not None else "")))


def list_archives(output_dir: Path) -> list[date]:
    """List date-named report directories, newest first."""
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return []
    archives: list[date] = []
    for child in output_dir.iterdir():
        if not child.is_dir() or not _DATE_DIR.fullmatch(child.name):
            continue
        try:
            archives.append(date.fromisoformat(child.name))
        except ValueError:
            continue
    return sorted(archives, reverse=True)


_MORE_LINK = '<span class="more">查看全文 <span class="arrow">→</span></span>'


def _morning_card(report_day: date, morning: MorningDraft | None) -> str:
    head = f'<div class="card-head"><h2>金融市场晨报</h2>{_MORE_LINK}</div>'
    if morning is None:
        return (
            '<a class="card" href="latest/morning.html">'
            f"{head}"
            '<p class="card-empty">本期未生成，见最近一期。</p></a>'
        )
    points = "".join(
        f"<li>{_plain(point)}</li>" for point in morning.key_points[:4]
    )
    return (
        f'<a class="card" href="{report_day.isoformat()}/morning.html">'
        f"{head}"
        '<p class="digest-label">今日导读</p>'
        f'<ul class="digest">{points}</ul></a>'
    )


def _laser_card(report_day: date, laser: LaserDraft | None) -> str:
    head = f'<div class="card-head"><h2>激光产业简报</h2>{_MORE_LINK}</div>'
    if laser is None:
        return (
            '<a class="card" href="latest/laser.html">'
            f"{head}"
            '<p class="card-empty">本期未生成，见最近一期。</p></a>'
        )
    points = "".join(
        f"<li>{_plain(point)}</li>" for point in laser.key_points
    )
    return (
        f'<a class="card" href="{report_day.isoformat()}/laser.html">'
        f"{head}"
        '<p class="digest-label">今日导读</p>'
        f'<ul class="digest">{points}</ul></a>'
    )


def _archive(output_dir: Path) -> str:
    archives = list_archives(output_dir)
    if not archives:
        return '<p class="small">暂无历史归档。</p>'
    groups: dict[str, list[date]] = {}
    for day in archives:
        groups.setdefault(day.strftime("%Y-%m"), []).append(day)
    parts: list[str] = []
    for month in sorted(groups, reverse=True):
        year, month_number = month.split("-")
        entries = "".join(
            f'<li><span class="day">{day.isoformat()}</span>'
            f'<span class="links">'
            f'<a href="{day.isoformat()}/morning.html">晨报</a>'
            f'<a href="{day.isoformat()}/laser.html">激光简报</a></span></li>'
            for day in groups[month]
        )
        parts.append(f"<h3>{year}年{int(month_number)}月</h3><ul>{entries}</ul>")
    return "".join(parts)


def render_index(
    *,
    output_dir: Path,
    report_day: date,
    morning: MorningDraft | None,
    laser: LaserDraft | None,
    template_path: Path,
) -> None:
    """Render the site index with cards and monthly archive."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    template = Path(template_path).read_text(encoding="utf-8")
    template = render_conditional_blocks(template, {})
    values = {
        "DATE_CN": _escape(
            f"{report_day.year}年{report_day.month}月{report_day.day}日"
        ),
        "WEEKDAY_CN": _escape(WEEKDAYS_CN[report_day.weekday()]),
        "MORNING_CARD": _morning_card(report_day, morning),
        "LASER_CARD": _laser_card(report_day, laser),
        "ARCHIVE": _archive(output_dir),
        "DISCLAIMER": _escape(
            "页面由规则自动生成，数据来源见各报告；不构成投资建议。"
        ),
    }
    rendered = fill_placeholders(template, values)
    (output_dir / "index.html").write_text(rendered, encoding="utf-8")
