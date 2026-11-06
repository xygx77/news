"""Render the morning report from a MorningDraft."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Mapping, Sequence

from ..market.schema import Fact
from ..writer.draft import MorningDraft
from .template import fill_placeholders, render_conditional_blocks

ASHARE_LABELS = ("上证指数", "深证成指", "创业板指", "科创50", "沪深300")
RATE_LABELS = (
    "美国 2 年期国债",
    "美国 10 年期国债",
    "美国 30 年期国债",
    "美元指数",
    "美元兑离岸人民币",
    "欧元/美元",
    "英镑/美元",
    "美元/日元",
    "美元兑人民币中间价",
)
COMMODITY_LABELS = (
    "WTI 原油",
    "布伦特原油",
    "COMEX 黄金",
    "COMEX 白银",
    "COMEX 铜",
)


def _with_placeholders(
    facts: Sequence[Fact], group: str, labels: Sequence[str]
) -> list[Fact]:
    """Add explicit 暂缺 rows for expected labels missing from a group."""
    existing = {fact.label for fact in facts}
    placeholders = [
        Fact(
            id=f"{group}:missing:{label}",
            group=group,
            label=label,
            value="暂缺",
            status="missing",
            source_name="暂缺",
            source_url="",
            as_of="暂缺",
        )
        for label in labels
        if label not in existing
    ]
    return [*facts, *placeholders]


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else "暂缺"))


def _tag_class(fact: Fact) -> str:
    if fact.direction in {"up", "down"}:
        return f"tag-{fact.direction}"
    return "tag-flat"


def render_fact_rows(facts: Sequence[Fact], columns: Sequence[str]) -> str:
    """Render table rows from facts; text cells are always escaped."""
    rows: list[str] = []
    for fact in facts:
        cells: list[str] = []
        for column in columns:
            if column == "change":
                text = _escape(fact.change or "暂缺")
                cells.append(
                    f'<td><span class="tag {_tag_class(fact)}">{text}</span></td>'
                )
            elif column == "note":
                cells.append(f"<td>{_escape(fact.note or '')}</td>")
            else:
                cells.append(f"<td>{_escape(getattr(fact, column, None))}</td>")
        rows.append("          <tr>" + "".join(cells) + "</tr>")
    return "\n".join(rows) if rows else '          <tr><td colspan="4">暂缺</td></tr>'


def _source_links(facts: Sequence[Fact]) -> str:
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for fact in facts:
        if fact.source_name == "暂缺":
            continue
        key = (fact.source_name, fact.source_url)
        if key in seen:
            continue
        seen.add(key)
        parts.append(
            f'<a href="{html.escape(fact.source_url, quote=True)}">'
            f"{html.escape(fact.source_name)}</a>"
        )
    return "、".join(parts) if parts else "暂缺"


def _sector_boxes(draft: MorningDraft) -> str:
    sectors = [
        fact
        for fact in draft.facts
        if fact.group == "sector" and fact.status == "ok"
    ]
    gainers = [fact for fact in sectors if fact.direction == "up"][:2]
    losers = [fact for fact in sectors if fact.direction == "down"][:2]

    def box(title: str, items: Sequence[Fact], extra_class: str = "") -> str:
        if not items:
            body = '<p class="small">暂缺</p>'
        else:
            entries = []
            for fact in items:
                catalyst = draft.sector_catalysts.get(fact.label, "")
                catalyst_html = (
                    f'<div class="small">催化剂：{html.escape(catalyst)}</div>'
                    if catalyst
                    else ""
                )
                entries.append(
                    f"<li>{_escape(fact.label)} "
                    f'<span class="tag tag-{fact.direction}">{_escape(fact.change)}</span>'
                    f"{catalyst_html}</li>"
                )
            body = f"<ul>{''.join(entries)}</ul>"
        classes = f"callout {extra_class}".strip()
        return (
            f'<div class="{classes}"><div class="callout-title">{_escape(title)}</div>'
            f"{body}</div>"
        )

    return box("涨幅居前", gainers) + box("跌幅居前", losers, "warn")


def _policy_content(draft: MorningDraft) -> str:
    if draft.policy_sections:
        parts: list[str] = []
        for title, briefs in draft.policy_sections.items():
            parts.append(f"<h3>{html.escape(title)}</h3>")
            if not briefs:
                parts.append('<p class="small">暂缺</p>')
                continue
            entries = "".join(
                f'<li><a href="{html.escape(brief.url, quote=True)}">'
                f"{html.escape(brief.title)}</a><br>"
                f'<span class="small">{html.escape(brief.snippet)}'
                f"（{html.escape(brief.source_name)}）</span></li>"
                for brief in briefs
            )
            parts.append(f"<ul>{entries}</ul>")
        return "".join(parts)

    if not draft.policy_items:
        return '<p class="small">暂缺</p>'
    entries = "".join(
        f'<li><a href="{html.escape(brief.url, quote=True)}">'
        f"{html.escape(brief.title)}</a><br>"
        f'<span class="small">{html.escape(brief.snippet)}'
        f"（{html.escape(brief.source_name)}）</span></li>"
        for brief in draft.policy_items
    )
    return f"<ul>{entries}</ul>"


def _calendar_rows(draft: MorningDraft) -> str:
    if not draft.calendar:
        return '          <tr><td colspan="3">暂缺</td></tr>'
    return "\n".join(
        "          <tr>"
        f"<td>{html.escape(event.time)}</td>"
        f"<td>{html.escape(event.title)}</td>"
        f"<td>{html.escape(event.importance)}</td>"
        "</tr>"
        for event in draft.calendar
    )


def _calendar_sources(draft: MorningDraft) -> str:
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for event in draft.calendar:
        key = (event.source_name, event.source_url)
        if key in seen:
            continue
        seen.add(key)
        parts.append(
            f'<a href="{html.escape(event.source_url, quote=True)}">'
            f"{html.escape(event.source_name)}</a>"
        )
    return "、".join(parts) if parts else "暂缺"


def _values(draft: MorningDraft) -> Mapping[str, str]:
    ashare = _with_placeholders(
        [fact for fact in draft.facts if fact.group == "ashare"],
        "ashare",
        ASHARE_LABELS,
    )
    rates = _with_placeholders(
        [fact for fact in draft.facts if fact.group in {"rate", "fx"}],
        "rate",
        RATE_LABELS,
    )
    commodities = _with_placeholders(
        [fact for fact in draft.facts if fact.group == "commodity"],
        "commodity",
        COMMODITY_LABELS,
    )
    turnover = next(
        (fact.value for fact in draft.facts if fact.group == "turnover"),
        "暂缺",
    )
    breadth = next(
        (
            fact.value
            for fact in draft.facts
            if fact.group == "breadth" and fact.status == "ok"
        ),
        None,
    )
    missing_text = "、".join(draft.missing) if draft.missing else "无"
    captured = draft.captured_at.strftime("%Y-%m-%d %H:%M")

    values: dict[str, str] = {
        "DATE_CN": _escape(
            f"{draft.report_day.year}年{draft.report_day.month}月{draft.report_day.day}日"
        ),
        "WEEKDAY_CN": _escape(draft.weekday_cn),
        "DATE_ISO": _escape(draft.report_day.isoformat()),
        "TRADING_STATUS": _escape(draft.trading_status),
        "ASHARE_LABEL": _escape(
            "上一交易日收盘" if draft.trading.is_trading else "今日休市"
        ),
        "ASHARE_ROWS": render_fact_rows(
            ashare, ("label", "value", "change", "note")
        ),
        "ASHARE_SOURCES": _source_links(ashare),
        "TURNOVER": _escape(turnover),
        "BREADTH": _escape(
            f"全市场{breadth}" if breadth else "全市场涨跌家数暂缺"
        ),
        "ASHARE_NOTE": _escape(draft.notes.get("ashare_note", "")),
        "RATE_ROWS": render_fact_rows(rates, ("label", "value", "change", "note")),
        "RATE_SOURCES": _source_links(rates),
        "RATE_NOTE": _escape(draft.notes.get("rate_note", "")),
        "COMMODITY_ROWS": render_fact_rows(
            commodities, ("label", "value", "change", "note")
        ),
        "COMMODITY_SOURCES": _source_links(commodities),
        "COMMODITY_NOTE": _escape(draft.notes.get("commodity_note", "")),
        "US_MARKET": _escape(draft.us_market),
        "EU_MARKET": _escape(draft.eu_market),
        "APAC_MARKET": _escape(draft.apac_market),
        "SECTOR_BOXES": _sector_boxes(draft),
        "POLICY_CONTENT": _policy_content(draft),
        "CALENDAR_ROWS": _calendar_rows(draft),
        "CALENDAR_SOURCES": _calendar_sources(draft),
        "RISK_ITEMS": "".join(
            f"<li>{html.escape(item)}</li>" for item in draft.risk_items
        ),
        "DISCLAIMER": _escape(
            "机构观点，不构成投资建议。"
            f"数据截至 {captured}（北京时间）。未获取项：{missing_text}。"
        ),
    }

    for index in range(4):
        point = (
            draft.key_points[index]
            if index < len(draft.key_points)
            else "暂缺"
        )
        values[f"KEY_POINT_{index + 1}"] = _escape(point)
    return values


def render_morning(
    draft: MorningDraft, *, template_path: Path, output_path: Path
) -> None:
    """Render the morning report to an HTML file."""
    template = Path(template_path).read_text(encoding="utf-8")
    template = render_conditional_blocks(
        template, {"trading_day": draft.trading.is_trading}
    )
    rendered = fill_placeholders(template, _values(draft))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
