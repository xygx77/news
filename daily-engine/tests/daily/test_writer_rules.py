import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from src.daily.market.schema import Fact, MarketSnapshot
from src.daily.market.trading_day import TradingDayInfo
from src.daily.sources.laser import LaserCandidate
from src.daily.writer.enrich import (
    enrich_items,
    extract_published_time,
    extract_summary,
    extract_title,
)
from src.daily.writer.rule_finance import build_morning_draft
from src.daily.writer.rule_laser import build_laser_draft
from src.daily.writer.scoring import (
    LaserItem,
    SelectionResult,
    categorize,
    filter_by_window,
    load_keywords,
    score_candidate,
    select_items,
)
from src.models import ContentItem, SourceType

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 5, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_score_rewards_ma_and_recency():
    rules = load_keywords(ROOT / "data/laser_keywords.json")
    item = LaserCandidate(
        "某公司完成 10 亿元并购融资",
        "https://x/1",
        NOW - timedelta(hours=2),
        "",
        "激光制造网",
        1.0,
        "zh",
    )
    assert score_candidate(item, now=NOW, rules=rules) >= 6


def test_select_filters_noise_and_widens_window():
    rules = load_keywords(ROOT / "data/laser_keywords.json")
    old = LaserCandidate(
        "激光新品发布",
        "https://x/2",
        NOW - timedelta(hours=60),
        "",
        "OFweek激光",
        1.0,
        "zh",
    )
    noisy = LaserCandidate(
        "激光展直播报名",
        "https://x/3",
        NOW - timedelta(hours=2),
        "",
        "OFweek激光",
        1.0,
        "zh",
    )
    result = select_items([old, noisy], now=NOW, rules=rules)
    assert result.window_hours == 72
    assert [i.title for i in result.items] == ["激光新品发布"]
    assert not any("直播" in i.title for i in result.items)


def test_extract_summary_truncates_at_sentence_boundary():
    text = "第一句介绍公司业绩。" * 5 + "第二句补充技术细节。"
    summary = extract_summary(text)
    assert 40 <= len(summary) <= 70
    assert summary.endswith("。")


def test_extract_summary_returns_empty_for_blank():
    assert extract_summary("   ") == ""


def test_extract_published_time_reads_meta():
    html = '<meta property="article:published_time" content="2026-11-05T07:30:00+08:00">'
    published = extract_published_time(html)
    assert published.isoformat() == "2026-11-05T07:30:00+08:00"


def _fact(label, value, change, group="us"):
    return Fact(
        id=f"F{label}",
        group=group,
        label=label,
        value=value,
        change=change,
        source_name="东方财富",
        source_url="https://quote.eastmoney.com/",
        as_of="2026-11-04 收盘",
    )


def test_morning_draft_builds_us_sentence_from_facts():
    snapshot = MarketSnapshot(
        facts=[
            _fact("道琼斯", "53356.12", "+3.03%"),
            _fact("标普500", "6120.10", "-0.52%"),
        ],
        captured_at=NOW,
    )
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=True,
    )
    draft = build_morning_draft(
        snapshot, [], report_day=date(2026, 11, 5), now=NOW, trading=trading
    )
    assert "道琼斯" in draft.us_market and "+3.03%" in draft.us_market
    assert "涨跌不一" in draft.us_market
    assert len(draft.key_points) == 4
    assert draft.trading_status.startswith("今日为交易日")


def test_morning_draft_marks_missing_data():
    snapshot = MarketSnapshot(facts=[], missing=["道琼斯"], captured_at=NOW)
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=False,
    )
    draft = build_morning_draft(
        snapshot, [], report_day=date(2026, 11, 5), now=NOW, trading=trading
    )
    assert "暂缺" in draft.us_market


def test_morning_policy_snippet_strips_html():
    snapshot = MarketSnapshot(facts=[], captured_at=NOW)
    news = [
        ContentItem(
            id="rss:html:1",
            source_type=SourceType.RSS,
            title="央行发布稳市场举措",
            url="https://example.com/news/html",
            content=(
                "<p>央行、金融监管总局、证监会联合发布稳市场举措。</p>"
                "<h3>标题</h3><p>第二段内容。</p>"
            ),
            published_at=NOW,
            metadata={"feed_name": "东方财富要闻"},
        )
    ]
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=True,
    )
    draft = build_morning_draft(
        snapshot,
        news,
        report_day=date(2026, 11, 5),
        now=NOW,
        trading=trading,
    )
    assert draft.policy_items
    snippet = draft.policy_items[0].snippet
    assert "<p>" not in snippet
    assert "央行、金融监管总局" in snippet


def test_laser_draft_limits_key_points_to_five():
    items = [
        LaserItem(
            f"标题{i}",
            f"https://x/{i}",
            "摘要",
            "技术突破",
            NOW,
            9.0,
            "OFweek激光",
            1.0,
            "zh",
        )
        for i in range(7)
    ]
    draft = build_laser_draft(
        SelectionResult(items=items, window_hours=48),
        report_day=date(2026, 11, 5),
        now=NOW,
    )
    assert len(draft.key_points) == 5
    assert len(draft.items) == 7
    assert draft.insufficient is False


def test_laser_draft_flags_insufficient_items():
    draft = build_laser_draft(
        SelectionResult(items=[], window_hours=72),
        report_day=date(2026, 11, 5),
        now=NOW,
    )
    assert draft.insufficient is True
    assert draft.key_points == []


def test_score_does_not_reward_undated_recency():
    rules = load_keywords(ROOT / "data/laser_keywords.json")
    undated = LaserCandidate(
        "并购融资 激光新品发布",
        "https://x/undated",
        None,
        "",
        "OFweek激光",
        1.0,
        "zh",
    )
    assert score_candidate(undated, now=NOW, rules=rules) == 6.0


def test_headline_company_boost_is_not_a_category():
    rules = load_keywords(ROOT / "data/laser_keywords.json")
    candidate = LaserCandidate(
        "Lumentum 宣布高管任命",
        "https://x/company",
        NOW,
        "",
        "Lumentum",
        0.5,
        "en",
    )
    assert categorize(candidate, rules) == "其他"


def test_undated_candidates_rank_after_dated():
    rules = load_keywords(ROOT / "data/laser_keywords.json")
    dated = LaserCandidate(
        "激光新品发布",
        "https://x/dated",
        NOW - timedelta(hours=30),
        "",
        "OFweek激光",
        1.0,
        "zh",
    )
    undated = LaserCandidate(
        "并购融资 激光新品发布",
        "https://x/undated",
        None,
        "",
        "OFweek激光",
        1.0,
        "zh",
    )
    result = select_items([undated, dated], now=NOW, rules=rules)
    assert result.items[0].url == "https://x/dated"


def test_extract_title_prefers_og_title():
    html = '<meta property="og:title" content="Real Title"><title>Site</title>'
    assert extract_title(html) == "Real Title"


def test_enrich_items_replaces_slug_title():
    item = LaserItem(
        "new-fiber-laser",
        "https://example.com/article",
        "",
        "技术突破",
        None,
        5.0,
        "Coherent",
        0.5,
        "en",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                '<html><head><meta property="og:title" '
                'content="Coherent Ships New Fiber Laser"></head>'
                "<body><p>A long enough article body for trafilatura to "
                "extract a summary from the page.</p></body></html>"
            ),
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await enrich_items([item], client)

    result = asyncio.run(run())
    assert result[0].title == "Coherent Ships New Fiber Laser"


def test_filter_by_window_drops_stale_enriched_items():
    old = LaserItem(
        "old",
        "https://x/old",
        "",
        "技术突破",
        NOW - timedelta(days=20),
        5.0,
        "Lumentum",
        0.5,
        "en",
    )
    fresh = LaserItem(
        "fresh",
        "https://x/new",
        "",
        "技术突破",
        NOW - timedelta(hours=2),
        5.0,
        "激光制造网",
        1.0,
        "zh",
    )
    result = filter_by_window([old, fresh], now=NOW, window_hours=48)
    assert [item.title for item in result] == ["fresh"]


def test_morning_draft_includes_breadth_in_key_point():
    snapshot = MarketSnapshot(
        facts=[
            _fact("上证指数", "3388.81", "+1.23%", group="ashare"),
            _fact(
                "涨跌家数",
                "上涨 860 家、下跌 4418 家、平盘 101 家",
                None,
                group="breadth",
            ),
        ],
        captured_at=NOW,
    )
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=True,
    )
    draft = build_morning_draft(
        snapshot, [], report_day=date(2026, 11, 5), now=NOW, trading=trading
    )
    assert draft.key_points[0] == (
        "A 股收盘：上证指数 3388.81（+1.23%）。"
        "全市场上涨 860 家、下跌 4418 家、平盘 101 家。"
    )


def _news(title, content, published_at, source_name="东方财富要闻", url=None):
    return ContentItem(
        id=f"test:{title}",
        source_type=SourceType.RSS,
        title=title,
        url=url or f"https://example.com/{abs(hash(title))}",
        content=content,
        published_at=published_at,
        metadata={"feed_name": source_name},
    )


def test_rank_news_puts_market_moving_items_first():
    from src.daily.writer.rule_finance import rank_news

    policy = _news(
        "央行宣布降准 50 个基点",
        "释放长期资金约 1 万亿元，市场流动性明显改善。",
        NOW - timedelta(hours=3),
        "华尔街见闻",
    )
    noise = _news("某科技公司发布新品", "正文内容" * 2000, NOW - timedelta(hours=1))
    ranked = rank_news([noise, policy], now=NOW)
    assert ranked[0].title == "央行宣布降准 50 个基点"


def test_rank_news_prefers_recent_when_scores_tie():
    from src.daily.writer.rule_finance import rank_news

    fresh = _news("市场消息一", "", NOW - timedelta(hours=1))
    stale = _news("市场消息二", "", NOW - timedelta(hours=10))
    ranked = rank_news([stale, fresh], now=NOW)
    assert ranked[0].title == "市场消息一"


def test_rank_news_boosts_configured_sectors():
    from src.daily.writer.rule_finance import rank_news

    sector = _news("半导体设备订单大增", "", NOW - timedelta(hours=2))
    other = _news("某地举办展会", "", NOW - timedelta(hours=2))
    ranked = rank_news([other, sector], now=NOW, sector_labels=("半导体",))
    assert ranked[0].title == "半导体设备订单大增"


def test_morning_draft_selects_important_news_before_cap():
    important = _news("央行宣布降息 25 个基点", "", NOW - timedelta(hours=5))
    noise = _news("某公司举办发布会", "长文" * 3000, NOW - timedelta(hours=1))
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=True,
    )
    draft = build_morning_draft(
        MarketSnapshot(facts=[], captured_at=NOW),
        [noise, important],
        report_day=date(2026, 11, 5),
        now=NOW,
        trading=trading,
        max_material_items=1,
    )
    assert [brief.title for brief in draft.policy_items] == [
        "央行宣布降息 25 个基点"
    ]


def test_morning_draft_lists_each_platform_once():
    first = _news(
        "央行开展 5000 亿元逆回购",
        "",
        NOW - timedelta(hours=1),
        "华尔街见闻",
        url="https://wallstreetcn.com/live/1",
    )
    second = _news(
        "财政部发布专项债通知",
        "",
        NOW - timedelta(hours=2),
        "华尔街见闻",
        url="https://wallstreetcn.com/live/2",
    )
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=True,
    )
    draft = build_morning_draft(
        MarketSnapshot(facts=[], captured_at=NOW),
        [first, second],
        report_day=date(2026, 11, 5),
        now=NOW,
        trading=trading,
    )
    names = [source.name for source in draft.sources]
    assert names.count("华尔街见闻") == 1


def test_rank_news_scores_title_and_lead_only():
    from src.daily.writer.rule_finance import rank_news

    deep = _news(
        "市场综述",
        "无关内容" * 100 + " 央行降息 25 个基点",
        NOW - timedelta(hours=1),
    )
    lead = _news("央行降息 25 个基点", "", NOW - timedelta(hours=1))
    ranked = rank_news([deep, lead], now=NOW)
    assert ranked[0].title == "央行降息 25 个基点"


def test_morning_draft_uses_platform_display_names():
    item = ContentItem(
        id="wallstreetcn:live:9",
        source_type=SourceType.WALLSTREETCN,
        title="央行开展逆回购操作",
        url="https://wallstreetcn.com/live/9",
        content="央行开展逆回购操作。",
        published_at=NOW,
        metadata={},
    )
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=True,
    )
    draft = build_morning_draft(
        MarketSnapshot(facts=[], captured_at=NOW),
        [item],
        report_day=date(2026, 11, 5),
        now=NOW,
        trading=trading,
    )
    names = [source.name for source in draft.sources]
    assert "华尔街见闻" in names
    assert "wallstreetcn" not in names


def test_rank_news_handles_naive_timestamp():
    from src.daily.writer.rule_finance import rank_news

    naive = _news("央行开展逆回购", "", datetime(2026, 11, 5, 7, 0))
    aware = _news("财政部发布通知", "", NOW)
    ranked = rank_news([naive, aware], now=NOW)
    assert len(ranked) == 2
