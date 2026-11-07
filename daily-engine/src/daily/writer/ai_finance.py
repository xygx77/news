"""AI-enriched morning writer with strict fact/source guardrails."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Sequence

from ...ai.client import create_ai_client
from ...models import AIConfig, ContentItem
from ..market.schema import MarketSnapshot
from ..market.trading_day import TradingDayInfo
from ..normalize import title_similarity
from .draft import MorningDraft, NewsBrief, SourceRef
from .rule_finance import (
    _plain,
    _snippet,
    _source_name,
    build_morning_draft,
    dedup_news,
    diversify_by_source,
    rank_news,
)

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?(?:%|BP|bp)")
_SECTION_LABELS = (
    ("政策", "policy"),
    ("宏观数据与产业", "macro"),
    ("公司公告与业绩预告", "company"),
)

POLICY_KEYWORDS = (
    "央行",
    "财政部",
    "证监会",
    "国务院",
    "监管",
    "政策",
    "关税",
    "退税",
    "降准",
    "降息",
    "补贴",
    "发改委",
)
MACRO_KEYWORDS = (
    "CPI",
    "PMI",
    "GDP",
    "进出口",
    "工业",
    "消费",
    "投资",
    "销量",
    "产量",
    "同比",
    "环比",
    "就业",
    "非农",
    "数据",
)
COMPANY_KEYWORDS = (
    "公告",
    "业绩",
    "净利",
    "营收",
    "回购",
    "增持",
    "减持",
    "中标",
    "合同",
    "上市",
    "并购",
    "融资",
    "财报",
)

AI_SYSTEM_PROMPT = """你是一名严谨的中文财经编辑，为北京时间早晨的《开盘前晨报》撰写分析段落。

硬性规则：
1. 只能使用用户提供的“市场数据”和“新闻素材”。不得新增任何数字、机构、事件或预测。
2. 市场段落（us_market / eu_market / apac_market）不要写任何数字；数字由程序在段首给出，你只写驱动因素、板块轮动和市场情绪。
3. 新闻摘要中的数字必须与所引用新闻素材完全一致；素材没有的写“暂缺”。
4. 每条新闻摘要必须来自素材；source 必须是素材中的来源名。
5. 不写投资建议，不写“预计/有望/将上涨”等预测；只描述已发生事实及其直接背景。
6. 板块催化剂必须来自新闻素材；不能只重复涨跌幅；没有依据就写“催化剂暂缺”。
7. 风险提示只写素材中已发生的事件，并为每条标注 source；没有来源的风险不要写。
8. title 必须逐字复制素材标题，不得改写或缩写。
9. 市场段落不要出现百分比、点位、BP；不要写“涨超/约/近”等近似数字。
10. 只输出一个 JSON 对象，不要 Markdown 代码块，不要额外文字。

JSON schema：
{
  "us_market": "3-5句，分析驱动因素/板块轮动/市场情绪；不要写任何数字",
  "eu_market": "2-4句；不要写任何数字",
  "apac_market": "2-4句；不要写任何数字",
  "sector_catalysts": [
    {"sector": "板块名，必须来自市场数据", "catalyst": "1-2句，依据新闻素材", "source": "素材来源名"}
  ],
  "policy": [{"title": "素材标题", "summary": "不超过80字", "source": "素材来源名"}],
  "macro": [{"title": "素材标题", "summary": "不超过80字", "source": "素材来源名"}],
  "company": [{"title": "素材标题", "summary": "不超过80字", "source": "素材来源名"}],
  "risks": [{"text": "不超过80字，只描述素材中已发生事件", "source": "素材来源名"}],
  "notes": {
    "ashare_note": "A股数据口径一句话",
    "rate_note": "利率汇率口径一句话",
    "commodity_note": "商品口径一句话"
  }
}"""


def _facts_text(facts: Sequence) -> str:
    lines = []
    for fact in facts:
        change = fact.change or "—"
        lines.append(
            f"- [{fact.group}] {fact.label}: {fact.value}"
            f"（{change}，{fact.as_of}，来源：{fact.source_name}）"
        )
    return "\n".join(lines)


def _calendar_text(calendar: Sequence) -> str:
    if not calendar:
        return "暂缺"
    return "\n".join(
        f"- {event.time} {event.title}（{event.importance or '类型未标注'}）"
        for event in calendar
    )


def _news_text(news: Sequence[ContentItem]) -> str:
    lines = []
    for index, item in enumerate(news, start=1):
        lines.append(
            f"[{index}] 来源：{_source_name(item)}"
            f" | 标题：{_plain(item.title)}"
            f" | 摘要：{_snippet(item.content or item.title, limit=120)}"
        )
    return "\n".join(lines) if lines else "暂缺"


def _user_prompt(
    report_day: date,
    trading: TradingDayInfo,
    snapshot: MarketSnapshot,
    news: Sequence[ContentItem],
) -> str:
    return "\n".join(
        [
            f"报告日期：{report_day.isoformat()}",
            f"交易状态：{'交易日' if trading.is_trading else '休市'}"
            f"，上一交易日：{trading.previous.isoformat()}",
            "",
            "市场数据（只能引用这些数字）：",
            _facts_text(snapshot.facts),
            "",
            "财经日历（只能引用这些事件）：",
            _calendar_text(snapshot.calendar),
            "",
            "新闻素材（只能引用这些标题与来源）：",
            _news_text(news),
            "",
            "请按 system 中的 JSON schema 输出。",
        ]
    )


def _parse_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI response did not contain a JSON object")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("AI response JSON is not an object")
    return payload


def _allowed_numbers(facts: Sequence) -> set[str]:
    allowed: set[str] = set()
    for fact in facts:
        for value in (fact.value, fact.change):
            if value:
                allowed.update(_NUMBER.findall(value))
    return allowed


def _article_numbers(item: ContentItem) -> set[str]:
    return set(_NUMBER.findall(f"{item.title} {item.content or ''}"))


def _numbers_ok(text: str, allowed: set[str]) -> bool:
    return all(number in allowed for number in _NUMBER.findall(text))


def _qualitative_sentences(text: str) -> str:
    """Keep only sentences without percentages/BP so exact data stays in the baseline."""
    parts = re.split(r"(?<=[。！？!?])", text)
    kept = [
        part.strip()
        for part in parts
        if part.strip() and not _NUMBER.search(part)
    ]
    return " ".join(kept)


def _market_text(
    payload: dict[str, Any],
    key: str,
    baseline: str,
    allowed: set[str],
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        return baseline
    cleaned = _plain(value)[:1200]
    qualitative = _qualitative_sentences(cleaned)
    if not qualitative:
        return baseline
    # Keep the exact baseline sentence, then add the qualitative analysis.
    return f"{baseline} {qualitative}".strip()


def _match_news(
    title: str, source: str, news: Sequence[ContentItem]
) -> ContentItem | None:
    candidates = [
        item for item in news if not source or _source_name(item) == source
    ]
    if not candidates:
        candidates = list(news)
    if not candidates:
        return None
    best = max(
        candidates, key=lambda item: title_similarity(item.title, title)
    )
    ratio = title_similarity(best.title, title)
    normalized_best = _plain(best.title)
    normalized_title = _plain(title)
    if (
        ratio < 0.3
        and normalized_title not in normalized_best
        and normalized_best not in normalized_title
    ):
        return None
    return best


def _brief_from_ai(
    item: Any, news: Sequence[ContentItem]
) -> NewsBrief | None:
    if not isinstance(item, dict):
        return None
    title = _plain(str(item.get("title") or ""))
    summary = _plain(str(item.get("summary") or ""))
    source = str(item.get("source") or "").strip()
    if not title:
        return None
    matched = _match_news(title, source, news)
    if matched is None:
        return None
    if summary and not _numbers_ok(summary, _article_numbers(matched)):
        summary = ""
    return NewsBrief(
        title=_plain(matched.title),
        url=str(matched.url),
        source_name=_source_name(matched),
        snippet=(summary or _snippet(matched.content or matched.title))[:120],
        published_at=matched.published_at,
    )


def _rule_briefs(
    news: Sequence[ContentItem],
    keywords: Sequence[str],
    used_urls: set[str],
    limit: int,
) -> list[NewsBrief]:
    matches = []
    for item in news:
        url = str(item.url)
        if url in used_urls:
            continue
        text = f"{_plain(item.title)} {item.content or ''}"
        if any(keyword in text for keyword in keywords):
            matches.append(item)
    briefs: list[NewsBrief] = []
    for item in matches[:limit]:
        briefs.append(
            NewsBrief(
                title=_plain(item.title),
                url=str(item.url),
                source_name=_source_name(item),
                snippet=_snippet(item.content or item.title),
                published_at=item.published_at,
            )
        )
        used_urls.add(str(item.url))
    return briefs


def _policy_sections(
    payload: dict[str, Any], news: Sequence[ContentItem]
) -> dict[str, list[NewsBrief]]:
    keyword_map = {
        "政策": POLICY_KEYWORDS,
        "宏观数据与产业": MACRO_KEYWORDS,
        "公司公告与业绩预告": COMPANY_KEYWORDS,
    }
    result: dict[str, list[NewsBrief]] = {}
    used_urls: set[str] = set()
    rank_order = {str(item.url): index for index, item in enumerate(news)}
    for label, key in _SECTION_LABELS:
        items = payload.get(key) or []
        briefs: list[NewsBrief] = []
        if isinstance(items, list):
            for item in items[:8]:
                brief = _brief_from_ai(item, news)
                if brief is not None and brief.url not in used_urls:
                    briefs.append(brief)
                    used_urls.add(brief.url)
        remaining = max(0, 5 - len(briefs))
        briefs.extend(
            _rule_briefs(news, keyword_map[label], used_urls, remaining)
        )
        briefs.sort(key=lambda brief: rank_order.get(str(brief.url), len(news)))
        result[label] = briefs
    return result if any(result.values()) else {}


def _sector_catalysts(
    payload: dict[str, Any],
    facts: Sequence,
    news: Sequence[ContentItem],
) -> dict[str, str]:
    allowed = {
        fact.label for fact in facts if fact.group == "sector"
    }
    sources = {_source_name(item) for item in news}
    result: dict[str, str] = {}
    items = payload.get("sector_catalysts") or []
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        sector = _plain(str(item.get("sector") or ""))
        catalyst = _plain(str(item.get("catalyst") or ""))
        source = str(item.get("source") or "").strip()
        if sector not in allowed:
            continue
        if catalyst and source in sources:
            result[sector] = catalyst[:160]
        else:
            result[sector] = "催化剂暂缺"
    return result


def _risks(
    payload: dict[str, Any],
    baseline: MorningDraft,
    news: Sequence[ContentItem],
) -> list[str]:
    allowed = _allowed_numbers(baseline.facts)
    risks: list[str] = []
    items = payload.get("risks") or []
    if isinstance(items, list):
        for item in items[:6]:
            if not isinstance(item, dict):
                continue
            text = _plain(str(item.get("text") or ""))
            source = str(item.get("source") or "").strip()
            if not text:
                continue
            matched = _match_news(text, source, news)
            if matched is None:
                continue
            if not _numbers_ok(text, allowed | _article_numbers(matched)):
                continue
            risks.append(text[:160])
    merged = list(dict.fromkeys([*baseline.risk_items, *risks]))
    return merged[:4]


def _merge(
    baseline: MorningDraft,
    payload: dict[str, Any],
    news: Sequence[ContentItem],
) -> MorningDraft:
    allowed = _allowed_numbers(baseline.facts)
    sections = _policy_sections(payload, news)
    catalysts = dict(baseline.sector_catalysts)
    for sector, value in _sector_catalysts(
        payload, baseline.facts, news
    ).items():
        if value != "催化剂暂缺" or sector not in catalysts:
            catalysts[sector] = value
    risks = _risks(payload, baseline, news)

    notes = dict(baseline.notes)
    candidate_notes = payload.get("notes")
    if isinstance(candidate_notes, dict):
        for key in notes:
            value = candidate_notes.get(key)
            if (
                isinstance(value, str)
                and value.strip()
                and _numbers_ok(_plain(value), allowed)
            ):
                notes[key] = _plain(value)[:300]

    sources = list(baseline.sources)
    seen = {source.name for source in sources}
    for briefs in sections.values():
        for brief in briefs:
            if brief.source_name not in seen:
                seen.add(brief.source_name)
                sources.append(
                    SourceRef(
                        name=brief.source_name,
                        url=brief.url,
                        note="政策与宏观",
                    )
                )

    return replace(
        baseline,
        key_points=list(baseline.key_points),
        us_market=_market_text(
            payload, "us_market", baseline.us_market, allowed
        ),
        eu_market=_market_text(
            payload, "eu_market", baseline.eu_market, allowed
        ),
        apac_market=_market_text(
            payload, "apac_market", baseline.apac_market, allowed
        ),
        policy_sections=sections,
        sector_catalysts=catalysts,
        risk_items=risks,
        notes=notes,
        sources=sources,
        ai_used=True,
    )


async def build_ai_morning_draft(
    snapshot: MarketSnapshot,
    news: Sequence[ContentItem],
    *,
    report_day: date,
    now: datetime,
    trading: TradingDayInfo,
    max_material_items: int = 40,
    ai_config: AIConfig,
    client: Any | None = None,
) -> MorningDraft:
    """Build a morning draft, enriching narrative fields with AI when possible."""
    sector_labels = [
        fact.label
        for fact in snapshot.facts
        if fact.group == "sector" and fact.status == "ok"
    ]
    unique_news = diversify_by_source(
        rank_news(dedup_news(list(news)), now=now, sector_labels=sector_labels)
    )[:max_material_items]
    baseline = build_morning_draft(
        snapshot,
        unique_news,
        report_day=report_day,
        now=now,
        trading=trading,
        max_material_items=max_material_items,
    )
    if client is None:
        client = create_ai_client(ai_config)

    prompt = _user_prompt(report_day, trading, snapshot, unique_news)
    try:
        text = await asyncio.wait_for(
            client.complete(
                AI_SYSTEM_PROMPT,
                prompt,
                temperature=0.2,
                max_tokens=3000,
            ),
            timeout=300,
        )
        logger.debug("AI morning raw response: %s", text)
        payload = _parse_payload(text)
        return _merge(baseline, payload, unique_news)
    except Exception as exc:  # noqa: BLE001 - fall back to rule writer
        logger.warning("AI morning writer failed; using rule fallback: %s", exc)
        return baseline
