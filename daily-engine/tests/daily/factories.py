from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.daily.market.schema import CalendarEvent, Fact, MarketSnapshot
from src.daily.market.trading_day import TradingDayInfo
from src.daily.writer.draft import LaserDraft, MorningDraft
from src.daily.writer.rule_finance import build_morning_draft
from src.daily.writer.rule_laser import build_laser_draft
from src.daily.writer.scoring import LaserItem, SelectionResult
from src.models import ContentItem, SourceType

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 5, 8, 10, tzinfo=SH)


def _fact(
    fact_id: str,
    group: str,
    label: str,
    value: str,
    change: str | None,
    change_num: float | None,
    unit: str,
    direction: str,
    *,
    status: str = "ok",
    note: str | None = None,
    source_name: str = "东方财富",
    source_url: str = "https://quote.eastmoney.com/",
    as_of: str = "2026-11-04 收盘",
) -> Fact:
    return Fact(
        id=fact_id,
        group=group,
        label=label,
        value=value,
        value_num=None,
        change=change,
        change_num=change_num,
        change_unit=unit,
        direction=direction,
        status=status,
        note=note,
        source_name=source_name,
        source_url=source_url,
        as_of=as_of,
    )


def fixed_morning_draft() -> MorningDraft:
    facts = [
        _fact("ashare:1.000001", "ashare", "上证指数", "3388.81", "+1.23%", 1.23, "pct", "up"),
        _fact("ashare:0.399001", "ashare", "深证成指", "10880.12", "+0.86%", 0.86, "pct", "up"),
        _fact("ashare:0.399006", "ashare", "创业板指", "2210.35", "-2.35%", -2.35, "pct", "down"),
        _fact("ashare:1.000688", "ashare", "科创50", "1012.44", "+0.41%", 0.41, "pct", "up"),
        _fact("ashare:1.000300", "ashare", "沪深300", "3988.20", "+0.55%", 0.55, "pct", "up"),
        _fact("us:100.DJIA", "us", "道琼斯", "53356.12", "+3.03%", 3.03, "pct", "up"),
        _fact("us:100.SPX", "us", "标普500", "6120.10", "-0.52%", -0.52, "pct", "down"),
        _fact("us:100.NDX", "us", "纳斯达克", "20150.88", "+0.31%", 0.31, "pct", "up"),
        _fact("eu:100.GDAXI", "eu", "德国 DAX", "24120.40", "+0.22%", 0.22, "pct", "up"),
        _fact("eu:100.FTSE", "eu", "英国富时100", "8456.10", "-0.11%", -0.11, "pct", "down"),
        _fact("eu:100.FCHI", "eu", "法国 CAC40", "8012.30", "+0.18%", 0.18, "pct", "up"),
        _fact("eu:100.SX5E", "eu", "欧洲斯托克50", "5120.60", "+0.09%", 0.09, "pct", "up"),
        _fact("apac:100.HSI", "apac", "恒生指数", "26120.30", "-0.44%", -0.44, "pct", "down"),
        _fact("apac:100.N225", "apac", "日经225", "50210.50", "+0.72%", 0.72, "pct", "up"),
        _fact("apac:100.KS11", "apac", "韩国 KOSPI", "3120.10", "+0.15%", 0.15, "pct", "up"),
        _fact("fx:100.UDI", "fx", "美元指数", "98.42", "+0.12%", 0.12, "pct", "up", as_of="抓取时点"),
        _fact("fx:133.USDCNH", "fx", "美元兑离岸人民币", "7.1180", "-0.08%", -0.08, "pct", "down", as_of="抓取时点"),
        _fact("commodity:102.CL00Y", "commodity", "WTI 原油", "78.40", "+1.15%", 1.15, "pct", "up", as_of="抓取时点"),
        _fact("commodity:101.GC00Y", "commodity", "COMEX 黄金", "4020.50", "+0.63%", 0.63, "pct", "up", as_of="抓取时点"),
        _fact("commodity:101.HG00Y", "commodity", "COMEX 铜", "4.82", "-0.27%", -0.27, "pct", "down", as_of="抓取时点"),
        _fact("rate:DGS10", "rate", "美国 10 年期国债", "4.95%", "+4BP", 4.0, "bp", "up", source_name="FRED", source_url="https://fred.stlouisfed.org/series/DGS10", as_of="2026-11-03"),
        _fact("rate:DGS30", "rate", "美国 30 年期国债", "5.12%", "+2BP", 2.0, "bp", "up", source_name="FRED", source_url="https://fred.stlouisfed.org/series/DGS30", as_of="2026-11-03"),
        _fact("rate:cn10y", "rate", "中国 10 年期国债", "暂缺", "暂缺", 0.0, "bp", "flat", status="missing", source_name="暂缺", source_url="https://www.chinabond.com.cn/", as_of="暂缺"),
        _fact("rate:cn30y", "rate", "中国 30 年期国债", "暂缺", "暂缺", 0.0, "bp", "flat", status="missing", source_name="暂缺", source_url="https://www.chinabond.com.cn/", as_of="暂缺"),
        _fact("sector:BK001", "sector", "半导体", "+3.21%", "+3.21%", 3.21, "pct", "up"),
        _fact("sector:BK002", "sector", "通信设备", "+2.12%", "+2.12%", 2.12, "pct", "up"),
        _fact("sector:BK003", "sector", "房地产", "-1.88%", "-1.88%", -1.88, "pct", "down"),
        _fact("sector:BK004", "sector", "煤炭", "-1.25%", "-1.25%", -1.25, "pct", "down"),
        _fact("turnover:cn", "turnover", "两市成交额", "1.86 亿元", None, 0.0, "abs", "flat", note="由沪深两市成交额求和"),
    ]
    calendar = [
        CalendarEvent(time="09:30", title="中国 10 月 CPI", importance="高"),
        CalendarEvent(time="20:30", title="美国初请失业金人数", importance="中"),
    ]
    news = [
        ContentItem(
            id="rss:test:1",
            source_type=SourceType.RSS,
            title="央行发布政策<script>alert(1)</script>",
            url="https://example.com/news/1",
            content="央行今日发布政策，市场关注流动性变化。",
            published_at=NOW,
            metadata={"feed_name": "东方财富要闻"},
        )
    ]
    snapshot = MarketSnapshot(
        facts=facts,
        calendar=calendar,
        missing=["布伦特原油", "中国 10 年期国债"],
        captured_at=NOW,
    )
    trading = TradingDayInfo(
        day=date(2026, 11, 5),
        is_trading=True,
        previous=date(2026, 11, 4),
        verified=True,
    )
    return build_morning_draft(
        snapshot,
        news,
        report_day=date(2026, 11, 5),
        now=NOW,
        trading=trading,
    )


def fixed_laser_draft() -> LaserDraft:
    items = [
        LaserItem(
            "<b>大族激光</b>完成 10 亿元并购融资",
            "https://example.com/laser/1",
            "大族激光宣布完成一笔 10 亿元并购融资，用于扩充激光器产能。",
            "并购融资",
            NOW,
            9.0,
            "激光制造网",
            1.0,
            "zh",
        ),
        LaserItem(
            "某激光企业发布 Q3 财报，营收同比增长",
            "https://example.com/laser/2",
            "该企业第三季度营收同比增长 18%，净利润同比提升。",
            "业绩",
            NOW,
            8.5,
            "OFweek激光",
            1.0,
            "zh",
        ),
        LaserItem(
            "研究团队在光纤激光器效率上刷新纪录",
            "https://example.com/laser/3",
            "研究团队首次将光纤激光器电光转换效率提升到新纪录。",
            "技术突破",
            NOW,
            8.0,
            "Optics.org",
            1.0,
            "en",
        ),
        LaserItem(
            "华工科技推出新一代激光切割设备",
            "https://example.com/laser/4",
            "华工科技发布新一代高功率激光切割设备并开始量产交付。",
            "产品发布",
            NOW,
            7.5,
            "华工科技",
            0.5,
            "zh",
        ),
        LaserItem(
            "行业标准《激光安全规范》通过认证",
            "https://example.com/laser/5",
            "《激光安全规范》通过国标认证，将于明年实施。",
            "标准规范",
            NOW,
            7.0,
            "中国光学光电子行业协会",
            1.0,
            "zh",
        ),
        LaserItem(
            "IPG 发布新型光纤激光器",
            "https://example.com/laser/6",
            "IPG Photonics 推出新型高功率光纤激光器产品。",
            "产品发布",
            NOW,
            6.5,
            "IPG Photonics",
            0.5,
            "en",
        ),
        LaserItem(
            "Lumentum 交付高功率泵浦源",
            "https://example.com/laser/7",
            "Lumentum 宣布向主要客户交付高功率泵浦源产品。",
            "产品发布",
            NOW,
            6.0,
            "Lumentum",
            0.5,
            "en",
        ),
    ]
    return build_laser_draft(
        SelectionResult(items=items, window_hours=48),
        report_day=date(2026, 11, 5),
        now=NOW,
    )
