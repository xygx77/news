import asyncio
import json

from src.daily.market.schema import MarketSnapshot
from src.daily.writer.ai_finance import build_ai_morning_draft
from src.models import AIConfig, AIProvider, ContentItem, SourceType
from tests.daily.factories import NOW, fixed_morning_draft

AI_CFG = AIConfig(
    provider=AIProvider.OPENAI,
    model="glm-4-flash",
    base_url="https://open.bigmodel.cn/api/paas/v4",
    api_key_env="ZHIPU_API_KEY",
    temperature=0.2,
    max_tokens=3000,
)


class FakeClient:
    def __init__(self, response: str):
        self.response = response

    async def complete(
        self, system: str, user: str, temperature=None, max_tokens=None
    ) -> str:
        return self.response


def _inputs():
    base = fixed_morning_draft()
    snapshot = MarketSnapshot(
        facts=base.facts,
        calendar=base.calendar,
        missing=base.missing,
        captured_at=NOW,
    )
    news = [
        ContentItem(
            id="rss:ai:1",
            source_type=SourceType.RSS,
            title="央行发布稳市场举措",
            url="https://example.com/news/1",
            content="<p>央行、金融监管总局、证监会联合发布稳市场举措。</p>",
            published_at=NOW,
            metadata={"feed_name": "东方财富要闻"},
        )
    ]
    return snapshot, news, base.trading


def _payload() -> dict:
    return {
        "key_points": [
            "A 股收盘：上证指数 3689.91（+0.10%）。",
            "美股三大指数收涨，道琼斯 53356.12（+3.03%）。",
            "商品与汇率：WTI 原油 88.77（+0.74%）。",
            "央行发布稳市场举措。",
        ],
        "us_market": "美股三大指数收涨，道琼斯 53356.12（+3.03%），标普500 7688.62（+1.58%）。",
        "eu_market": "欧洲主要指数收涨，德国 DAX 26644.27（+0.78%）。",
        "apac_market": "亚太主要指数涨跌不一，恒生指数 24796.98（+3.67%）。",
        "sector_catalysts": [
            {
                "sector": "半导体",
                "catalyst": "稳市场举措提振风险偏好。",
                "source": "东方财富要闻",
            }
        ],
        "policy": [
            {
                "title": "央行发布稳市场举措",
                "summary": "央行联合金融监管总局、证监会发布稳市场举措。",
                "source": "东方财富要闻",
            }
        ],
        "macro": [],
        "company": [],
        "risks": [
            {
                "text": "创业板指下跌 -2.35%，需关注市场情绪。",
                "source": "东方财富要闻",
            }
        ],
        "notes": {
            "ashare_note": "A 股数据来自东方财富。",
            "rate_note": "美债收益率来自 FRED。",
            "commodity_note": "商品数据来自东方财富。",
        },
    }


def test_ai_morning_merges_narrative_and_preserves_facts():
    snapshot, news, trading = _inputs()
    draft = asyncio.run(
        build_ai_morning_draft(
            snapshot,
            news,
            report_day=NOW.date(),
            now=NOW,
            trading=trading,
            ai_config=AI_CFG,
            client=FakeClient(json.dumps(_payload(), ensure_ascii=False)),
        )
    )
    assert draft.ai_used is True
    assert "道琼斯" in draft.us_market
    assert draft.sector_catalysts["半导体"] == "稳市场举措提振风险偏好。"
    assert draft.policy_sections["政策"][0].url == "https://example.com/news/1"
    assert draft.policy_sections["宏观数据与产业"] == []
    assert len(draft.facts) == len(snapshot.facts)
    assert draft.key_points[0].startswith("A 股收盘")


def test_ai_invalid_json_falls_back_to_rule():
    snapshot, news, trading = _inputs()
    draft = asyncio.run(
        build_ai_morning_draft(
            snapshot,
            news,
            report_day=NOW.date(),
            now=NOW,
            trading=trading,
            ai_config=AI_CFG,
            client=FakeClient("not a json response"),
        )
    )
    assert draft.ai_used is False
    assert "99999.99" not in draft.us_market


def test_ai_market_analysis_is_added_to_exact_baseline():
    payload = _payload()
    payload["us_market"] = "科技股领涨，市场情绪回暖。"
    snapshot, news, trading = _inputs()
    draft = asyncio.run(
        build_ai_morning_draft(
            snapshot,
            news,
            report_day=NOW.date(),
            now=NOW,
            trading=trading,
            ai_config=AI_CFG,
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        )
    )
    assert "53356.12" in draft.us_market
    assert "科技股领涨" in draft.us_market


def test_ai_catalyst_without_source_is_marked_missing():
    payload = _payload()
    payload["sector_catalysts"] = [
        {"sector": "半导体", "catalyst": "资金流入。"}
    ]
    snapshot, news, trading = _inputs()
    draft = asyncio.run(
        build_ai_morning_draft(
            snapshot,
            news,
            report_day=NOW.date(),
            now=NOW,
            trading=trading,
            ai_config=AI_CFG,
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        )
    )
    assert draft.sector_catalysts["半导体"] == "催化剂暂缺"


def test_ai_invented_numbers_are_rejected_per_field():
    payload = _payload()
    payload["us_market"] = "道琼斯 99999.99（+9.99%），创历史新高。"
    snapshot, news, trading = _inputs()
    draft = asyncio.run(
        build_ai_morning_draft(
            snapshot,
            news,
            report_day=NOW.date(),
            now=NOW,
            trading=trading,
            ai_config=AI_CFG,
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        )
    )
    assert draft.ai_used is True
    assert "99999.99" not in draft.us_market
