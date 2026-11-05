"""Rule-based morning report writer."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Sequence

from bs4 import BeautifulSoup

from ..market.schema import Fact, MarketSnapshot
from ..market.trading_day import TradingDayInfo
from ...models import ContentItem
from ..normalize import canonical_url, title_similarity
from .draft import MorningDraft, NewsBrief, SourceRef

WEEKDAYS_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
US_LABELS = ("道琼斯", "标普500", "纳斯达克")
EU_LABELS = ("德国 DAX", "英国富时100", "法国 CAC40", "欧洲斯托克50")
APAC_LABELS = ("恒生指数", "日经225", "韩国 KOSPI")
POLICY_KEYWORDS = (
    "央行",
    "财政部",
    "证监会",
    "关税",
    "降息",
    "加息",
    "CPI",
    "PMI",
    "GDP",
    "就业",
    "非农",
)
RISK_KEYWORDS = ("暴跌", "违约", "制裁", "冲突", "风险", "下调")


def _direction(fact: Fact) -> str:
    if fact.direction != "unknown":
        return fact.direction
    if fact.change:
        if fact.change.startswith("+"):
            return "up"
        if fact.change.startswith("-"):
            return "down"
    return "unknown"


def _market_subject(labels: Sequence[str]) -> str:
    if {"道琼斯", "标普500", "纳斯达克"} <= set(labels):
        return "美股三大指数"
    if "德国 DAX" in labels:
        return "欧洲主要指数"
    if "恒生指数" in labels:
        return "亚太主要指数"
    return "主要市场"


def describe_market(facts: Sequence[Fact], labels: Sequence[str]) -> str:
    """Describe a market from the facts for the given labels."""
    by_label = {fact.label: fact for fact in facts}
    parts: list[str] = []
    directions: list[str] = []
    for label in labels:
        fact = by_label.get(label)
        if fact is None or fact.status != "ok":
            parts.append(f"{label} 暂缺")
            continue
        if fact.change:
            parts.append(f"{label} {fact.value}（{fact.change}）")
        else:
            parts.append(f"{label} {fact.value}")
        direction = _direction(fact)
        if direction in {"up", "down"}:
            directions.append(direction)

    if directions and all(item == "up" for item in directions):
        trend = "收涨"
    elif directions and all(item == "down" for item in directions):
        trend = "收跌"
    elif directions:
        trend = "涨跌不一"
    else:
        trend = "数据暂缺"
    return f"{_market_subject(labels)}{trend}，{'，'.join(parts)}。"


def _plain(text: str | None) -> str:
    """Strip HTML tags and collapse whitespace."""
    soup = BeautifulSoup(text or "", "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def _snippet(text: str | None, limit: int = 80) -> str:
    cleaned = _plain(text)
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[。！？.!?])", cleaned, maxsplit=1)
    sentence = parts[0] if parts else cleaned
    if len(sentence) > limit:
        return sentence[:limit].rstrip() + "…"
    return sentence


def _source_name(item: ContentItem) -> str:
    metadata = item.metadata or {}
    for key in ("feed_name", "source_name"):
        value = metadata.get(key)
        if value:
            return str(value)
    return item.source_type.value


def dedup_news(
    items: Sequence[ContentItem], *, threshold: float = 0.85
) -> list[ContentItem]:
    """Deduplicate financial news by URL and title similarity."""
    ordered = sorted(
        items,
        key=lambda item: (len(item.content or ""), item.published_at),
        reverse=True,
    )
    kept: list[ContentItem] = []
    seen_urls: set[str] = set()
    for item in ordered:
        key = canonical_url(str(item.url))
        if key in seen_urls:
            continue
        if any(
            title_similarity(item.title, existing.title) >= threshold
            for existing in kept
        ):
            continue
        kept.append(item)
        seen_urls.add(key)
    return kept


def _policy_items(news: Sequence[ContentItem]) -> list[NewsBrief]:
    scored: list[tuple[int, ContentItem]] = []
    for item in news:
        text = f"{item.title} {item.content or ''}"
        hits = sum(1 for keyword in POLICY_KEYWORDS if keyword in text)
        if hits:
            scored.append((hits, item))
    scored.sort(key=lambda pair: (pair[0], pair[1].published_at), reverse=True)
    return [
        NewsBrief(
            title=_plain(item.title),
            url=str(item.url),
            source_name=_source_name(item),
            snippet=_snippet(item.content or item.title),
            published_at=item.published_at,
        )
        for _, item in scored[:4]
    ]


def _risk_items(
    facts: Sequence[Fact], news: Sequence[ContentItem]
) -> list[str]:
    index_moves: list[tuple[float, str]] = []
    fx_risks: list[str] = []
    bond_risks: list[str] = []
    sector_moves: list[tuple[float, str]] = []
    news_risks: list[str] = []

    for fact in facts:
        if (
            fact.group in {"ashare", "us", "eu", "apac"}
            and fact.change_unit == "pct"
            and fact.change_num is not None
        ):
            if fact.change_num <= -2:
                index_moves.append(
                    (
                        abs(fact.change_num),
                        f"{fact.label} 下跌 {fact.change}，需关注市场情绪。",
                    )
                )
            elif abs(fact.change_num) >= 3:
                index_moves.append(
                    (
                        abs(fact.change_num),
                        f"{fact.label} 波动 {fact.change}，市场情绪放大。",
                    )
                )
        if (
            fact.group == "fx"
            and fact.label == "美元指数"
            and fact.change_num is not None
            and abs(fact.change_num) >= 0.5
        ):
            fx_risks.append(
                f"美元指数变动 {fact.change}，关注人民币与风险资产估值压力。"
            )
        if (
            fact.id == "rate:DGS10"
            and fact.change_unit == "bp"
            and fact.change_num is not None
            and abs(fact.change_num) >= 10
        ):
            bond_risks.append(f"美国 10 年期国债单日变动 {fact.change}。")
        if (
            fact.group == "sector"
            and fact.change_unit == "pct"
            and fact.change_num is not None
        ):
            if fact.change_num >= 3:
                sector_moves.append(
                    (
                        abs(fact.change_num),
                        f"{fact.label} 涨幅 {fact.change}，短期拥挤度较高，"
                        "注意回调风险。",
                    )
                )
            elif fact.change_num <= -2:
                sector_moves.append(
                    (
                        abs(fact.change_num),
                        f"{fact.label} 跌幅 {fact.change}，需关注资金流出。",
                    )
                )

    for item in news:
        if any(keyword in item.title for keyword in RISK_KEYWORDS):
            news_risks.append(f"{item.title}（{_source_name(item)}）")

    index_risks = [text for _, text in sorted(index_moves, reverse=True)]
    sector_risks = [text for _, text in sorted(sector_moves, reverse=True)]
    combined = (
        index_risks[:2]
        + fx_risks[:1]
        + bond_risks[:1]
        + sector_risks[:1]
        + news_risks[:1]
    )
    combined = list(dict.fromkeys(combined))
    return combined[:4] if combined else ["暂无明显风险信号（规则判断）"]


def _sector_catalysts(
    facts: Sequence[Fact], news: Sequence[ContentItem]
) -> dict[str, str]:
    """Match a news item to each sector; otherwise mark the catalyst missing."""
    catalysts: dict[str, str] = {}
    for fact in facts:
        if fact.group != "sector":
            continue
        keyword = fact.label[:2] if len(fact.label) > 2 else fact.label
        matched = None
        for item in news:
            text = f"{item.title} {item.content or ''}"
            if fact.label in text or (len(keyword) >= 2 and keyword in text):
                matched = item
                break
        if matched is not None:
            catalysts[fact.label] = (
                f"{_snippet(matched.content or matched.title)}"
                f"（{_source_name(matched)}）"
            )
        else:
            catalysts[fact.label] = "催化剂暂缺"
    return catalysts


COMMODITY_KEYWORDS = {
    "WTI 原油": ("原油", "油价", "OPEC", "减产", "中东"),
    "布伦特原油": ("原油", "油价", "OPEC", "减产", "中东"),
    "COMEX 黄金": ("黄金", "贵金属", "美联储", "降息"),
    "COMEX 白银": ("白银", "贵金属"),
    "COMEX 铜": ("铜", "有色", "LME", "库存"),
}


def _with_commodity_notes(
    facts: Sequence[Fact], news: Sequence[ContentItem]
) -> list[Fact]:
    """Attach a news-derived driver to each commodity row."""
    result: list[Fact] = []
    for fact in facts:
        if fact.group == "commodity" and fact.status == "ok":
            keywords = COMMODITY_KEYWORDS.get(fact.label, ())
            matched = next(
                (
                    item
                    for item in news
                    if any(
                        keyword in f"{item.title} {item.content or ''}"
                        for keyword in keywords
                    )
                ),
                None,
            )
            note = (
                f"{_snippet(matched.content or matched.title)}"
                f"（{_source_name(matched)}）"
                if matched is not None
                else "暂缺"
            )
            fact = fact.model_copy(update={"note": note})
        result.append(fact)
    return result


def _commodity_summary(facts: Sequence[Fact]) -> str:
    by_label = {fact.label: fact for fact in facts}
    parts: list[str] = []
    for label in (
        "WTI 原油",
        "布伦特原油",
        "COMEX 黄金",
        "COMEX 白银",
        "美元指数",
    ):
        fact = by_label.get(label)
        if fact is not None and fact.status == "ok":
            if fact.change:
                parts.append(f"{label} {fact.value}（{fact.change}）")
            else:
                parts.append(f"{label} {fact.value}")
        else:
            parts.append(f"{label} 暂缺")
    return f"商品与汇率：{'，'.join(parts)}。"


def _ashare_summary(facts: Sequence[Fact], trading: TradingDayInfo) -> str:
    if not trading.is_trading:
        return "今日休市，A 股不交易。"
    members = [
        fact for fact in facts if fact.group == "ashare" and fact.status == "ok"
    ]
    if not members:
        return "A 股主要指数数据暂缺。"
    parts = [
        f"{fact.label} {fact.value}（{fact.change}）"
        for fact in members
        if fact.change
    ]
    if not parts:
        return "A 股主要指数数据暂缺。"
    return f"A 股收盘：{'，'.join(parts)}。"


def _notes(facts: Sequence[Fact]) -> dict[str, str]:
    ashare = [fact for fact in facts if fact.group == "ashare" and fact.status == "ok"]
    ashare_note = (
        f"A 股主要指数来自东方财富，数据截至{ashare[0].as_of}。"
        if ashare
        else "A 股数据暂缺。"
    )
    rate_note = (
        "美债 2Y/10Y/30Y 收益率来自 FRED。"
        if any(fact.group == "rate" and fact.status == "ok" for fact in facts)
        else "利率数据暂缺。"
    )
    if any(
        fact.label.startswith("中国") and fact.status == "missing" for fact in facts
    ):
        rate_note += "中国国债收益率暂缺。"
    return {
        "ashare_note": ashare_note,
        "rate_note": rate_note,
        "commodity_note": (
            "WTI 原油、布伦特原油、COMEX 黄金/白银与铜来自东方财富；"
            "缺失项标暂缺。"
        ),
    }


def _sources(facts: Sequence[Fact], policy: Sequence[NewsBrief]) -> list[SourceRef]:
    refs: list[SourceRef] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, url: str, note: str) -> None:
        key = (name, url)
        if key in seen:
            return
        seen.add(key)
        refs.append(SourceRef(name=name, url=url, note=note))

    for fact in facts:
        if fact.source_name == "暂缺":
            continue
        add(fact.source_name, fact.source_url, "行情数据")
    for brief in policy:
        add(brief.source_name, brief.url, "政策与宏观")
    return refs


def _missing(snapshot: MarketSnapshot, facts: Sequence[Fact]) -> list[str]:
    missing = list(snapshot.missing)
    for labels in (US_LABELS, EU_LABELS, APAC_LABELS):
        for label in labels:
            if not any(fact.label == label and fact.status == "ok" for fact in facts):
                missing.append(label)
    return list(dict.fromkeys(missing))


def build_morning_draft(
    snapshot: MarketSnapshot,
    news: Sequence[ContentItem],
    *,
    report_day: date,
    now: datetime,
    trading: TradingDayInfo,
    max_material_items: int = 40,
) -> MorningDraft:
    """Build the morning report draft from facts and news."""
    unique_news = dedup_news(list(news))[:max_material_items]
    facts = _with_commodity_notes(list(snapshot.facts), unique_news)
    us_market = describe_market(facts, US_LABELS)
    eu_market = describe_market(facts, EU_LABELS)
    apac_market = describe_market(facts, APAC_LABELS)
    policy_items = _policy_items(unique_news)
    risk_items = _risk_items(facts, unique_news)
    sector_catalysts = _sector_catalysts(facts, unique_news)

    if trading.is_trading:
        trading_status = (
            f"今日为交易日（北京时间 {report_day.isoformat()}），"
            "A 股数据为上一交易日收盘，海外数据为刚结束的美股交易日。"
        )
    else:
        trading_status = (
            f"今日休市（北京时间 {report_day.isoformat()}），"
            "仅保留海外市场、利率汇率、商品与风险提示。"
        )
    if not trading.verified:
        trading_status += "（节假日未校验）"

    top_news = (
        policy_items[0].title
        if policy_items
        else (unique_news[0].title if unique_news else "暂无重要新闻。")
    )
    key_points = [
        _ashare_summary(facts, trading),
        us_market,
        _commodity_summary(facts),
        top_news,
    ]

    return MorningDraft(
        report_day=report_day,
        weekday_cn=WEEKDAYS_CN[report_day.weekday()],
        trading=trading,
        trading_status=trading_status,
        key_points=key_points,
        us_market=us_market,
        eu_market=eu_market,
        apac_market=apac_market,
        policy_items=policy_items,
        risk_items=risk_items,
        calendar=list(snapshot.calendar),
        notes=_notes(facts),
        facts=facts,
        sources=_sources(facts, policy_items),
        missing=_missing(snapshot, facts),
        captured_at=snapshot.captured_at,
        sector_catalysts=sector_catalysts,
    )
