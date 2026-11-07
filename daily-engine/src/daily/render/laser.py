"""Render the laser industry briefing from a LaserDraft."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from ..writer.draft import LaserDraft
from ..writer.rule_finance import WEEKDAYS_CN
from ..writer.rule_laser import truncate_title
from .template import fill_placeholders, render_conditional_blocks


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else "暂缺"))


def _date_cn(day) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def _safe_href(url: object) -> str | None:
    """Return an escaped href, but only for public http(s) targets."""
    value = str(url or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        return None
    return html.escape(value, quote=True)


def _link_or_text(url: object, label: str) -> str:
    """Render an anchor for public http(s) targets, plain text otherwise."""
    href = _safe_href(url)
    text = html.escape(str(label or ""))
    return f'<a href="{href}">{text}</a>' if href else text


def _key_points(draft: LaserDraft) -> str:
    if not draft.key_points:
        return "<li>暂缺</li>"
    return "".join(f"<li>{html.escape(point)}</li>" for point in draft.key_points)


def _laser_items(draft: LaserDraft) -> str:
    if not draft.items:
        return '<p class="small">今日未采集到有效信息。</p>'
    articles: list[str] = []
    for index, item in enumerate(draft.items, start=1):
        published = (
            item.published_at.strftime("%Y-%m-%d %H:%M")
            if item.published_at
            else "时间暂缺"
        )
        alternates = ""
        if item.alternates:
            links = "、".join(
                _link_or_text(alt, urlsplit(alt).netloc or alt)
                for alt in item.alternates
            )
            alternates = f'<p class="small">其他来源：{links}</p>'
        # The headline is the entry point to the source page, like the finance
        # brief's policy section; unsafe URLs fall back to plain text.
        title_html = _link_or_text(item.url, truncate_title(item.title))
        articles.append(
            '<article class="laser-item">'
            f'<h3><span class="tag">{index}. {html.escape(item.category)}</span>'
            f"{title_html}</h3>"
            f"<p>{html.escape(item.summary or '暂缺')}</p>"
            '<p class="small">来源：'
            f"{_link_or_text(item.url, item.source_name)}"
            f" · {published}</p>"
            f"{alternates}</article>"
        )
    return "\n".join(articles)


def render_laser(
    draft: LaserDraft, *, template_path: Path, output_path: Path
) -> None:
    """Render the laser briefing to an HTML file."""
    template = Path(template_path).read_text(encoding="utf-8")
    template = render_conditional_blocks(
        template,
        {"trend": False, "insufficient": draft.insufficient},
    )
    missing_text = "、".join(draft.missing) if draft.missing else "无"
    captured = draft.captured_at.strftime("%Y-%m-%d %H:%M")
    values = {
        "DATE_CN": _escape(_date_cn(draft.report_day)),
        "WEEKDAY_CN": _escape(WEEKDAYS_CN[draft.report_day.weekday()]),
        "WINDOW_HOURS": _escape(draft.window_hours),
        "WINDOW_NOTE": _escape(
            f"本期覆盖近 {draft.window_hours} 小时公开信息，"
            "按关键词权重排序，最多 8 条。"
        ),
        "INSUFFICIENT_NOTICE": _escape(
            f"今日有效信息较少（{len(draft.items)} 条），按实际条数发布，未补旧闻。"
        ),
        "KEY_POINTS": _key_points(draft),
        "LASER_ITEMS": _laser_items(draft),
        "DISCLAIMER": _escape(
            "本简报由规则自动生成，趋势点评待接入 AI。"
            f"数据窗口：近 {draft.window_hours} 小时。"
            f"数据截至 {captured}（北京时间）。未获取项：{missing_text}。"
        ),
    }
    rendered = fill_placeholders(template, values)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
