from datetime import datetime
from zoneinfo import ZoneInfo

from src.daily.config import LaserSiteConfig
from src.daily.sources.laser import (
    LaserCandidate,
    dedup_candidates,
    parse_date_from_url,
    parse_html_list,
    parse_sitemap,
)
from src.scrapers.jin10 import parse_jin10_items
from src.scrapers.wallstreetcn import parse_wallstreetcn_lives

SH = ZoneInfo("Asia/Shanghai")


def test_jin10_filters_vip_and_window():
    payload = {
        "data": [
            {
                "id": "1",
                "time": "2026-11-05 07:30:00",
                "important": 1,
                "data": {"content": "央行开展 5000 亿元逆回购操作", "vip": 0},
            },
            {
                "id": "2",
                "time": "2026-11-04 10:00:00",
                "data": {"content": "旧闻", "vip": 0},
            },
            {
                "id": "3",
                "time": "2026-11-05 07:40:00",
                "data": {"content": "VIP 专享", "vip": 1},
            },
        ]
    }
    items = parse_jin10_items(payload, since=datetime(2026, 11, 4, 15, 0, tzinfo=SH))
    assert [item.title for item in items] == ["央行开展 5000 亿元逆回购操作"]
    assert items[0].metadata["important"] is True
    assert items[0].source_type.value == "jin10"


def test_wallstreetcn_parses_epoch_and_strips_html():
    payload = {
        "data": {
            "items": [
                {
                    "id": 11,
                    "display_time": 1793836000,
                    "uri": "https://wallstreetcn.com/livenews/11",
                    "content_text": "<p>美股三大指数收涨</p>",
                },
                {
                    "id": 12,
                    "display_time": 1793749600,
                    "uri": "https://wallstreetcn.com/livenews/12",
                    "content_text": "旧闻",
                },
            ]
        }
    }
    items = parse_wallstreetcn_lives(
        payload, since=datetime(2026, 11, 4, 15, 0, tzinfo=SH)
    )
    assert [item.title for item in items] == ["美股三大指数收涨"]
    assert "<p>" not in items[0].title


HTML = """
<ul>
  <li><a href="/202611/05/91624.html">大族激光发布新一代光纤激光器</a></li>
  <li><a href="/202610/30/91600.html">旧闻</a></li>
  <li><a href="/about/">关于我们</a></li>
</ul>
"""


def test_html_list_extracts_matching_links_and_date():
    site = LaserSiteConfig(
        name="激光制造网",
        strategy="html_list",
        base_url="https://www.laserfair.com",
        list_url="https://www.laserfair.com/news/",
        url_pattern=r"/\d{6}/\d{2}/\d+\.html",
        date_from_url=True,
    )
    items = parse_html_list(HTML, site)
    assert [i.title for i in items] == ["大族激光发布新一代光纤激光器", "旧闻"]
    assert items[0].url == "https://www.laserfair.com/202611/05/91624.html"
    assert items[0].published_at.date().isoformat() == "2026-11-05"


def test_html_list_resolves_relative_links_against_the_list_page():
    # 激光制造网 emits "202611/10/91645.html"; the article only exists under
    # /news/. Joining to base_url dropped the /news/ segment and the published
    # link 404'd.
    html = '<ul><li><a href="202611/10/91645.html">激光焊接新工艺</a></li></ul>'
    site = LaserSiteConfig(
        name="激光制造网",
        strategy="html_list",
        base_url="https://www.laserfair.com",
        list_url="https://www.laserfair.com/news/",
        url_pattern=r"/\d{6}/\d{2}/\d+\.html",
        date_from_url=True,
    )
    items = parse_html_list(html, site)
    assert [i.url for i in items] == [
        "https://www.laserfair.com/news/202611/10/91645.html"
    ]


def test_parse_date_from_url_rejects_unparseable():
    assert parse_date_from_url("https://example.com/about/") is None


def test_parse_date_from_url_does_not_guess_the_day():
    # Year-month URLs used to be read as day 1, which pushed every article of
    # such a site outside the 48h window. They are now reported as unknown so
    # the list-page text or the article page can supply the real date.
    assert parse_date_from_url("https://example.com/2026-11/news") is None
    assert parse_date_from_url("https://example.com/202611/news") is None


def test_parse_date_from_url_keeps_full_dates():
    assert (
        parse_date_from_url("https://example.com/202611/05/news")
        .date()
        .isoformat()
        == "2026-11-05"
    )
    assert (
        parse_date_from_url(
            "https://example.com/2026-11-05/news",
            r"/(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})/",
        )
        .date()
        .isoformat()
        == "2026-11-05"
    )


def test_html_list_reads_date_printed_in_link_text():
    # 大族激光 prints the date at the end of each link and repeats stale dates
    # in the surrounding container; the link text must win.
    html = """
    <ul>
      <li><div class="item"><a href="/news-status/413.html">冬藏蓄势！大族激光助力年度冲刺 2026-11-03 查看详情</a></div></li>
      <li><div class="item"><a href="/news-status/412.html">三十而砺，光启新程 2026-01-15 查看详情</a></div></li>
      <li><div class="item"><a href="/news-status/382.html">2025大族激光年报速读 2026-04-17 查看详情</a></div></li>
    </ul>
    """
    site = LaserSiteConfig(
        name="大族激光",
        strategy="html_list",
        list_url="https://www.hanslaser.com/news-status.html",
        base_url="https://www.hanslaser.com",
        url_pattern=r"/news-status/\d+\.html$",
    )
    items = parse_html_list(html, site)
    assert [i.title for i in items] == [
        "冬藏蓄势！大族激光助力年度冲刺",
        "三十而砺，光启新程",
        "2025大族激光年报速读",
    ]
    assert [i.published_at.date().isoformat() for i in items] == [
        "2026-11-03",
        "2026-01-15",
        "2026-04-17",
    ]


SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.coherent.com/news/press-releases/new-laser</loc><lastmod>2026-11-05</lastmod></url>
  <url><loc>https://www.coherent.com/products/x</loc><lastmod>2026-11-05</lastmod></url>
</urlset>"""


def test_sitemap_filters_and_sorts_by_lastmod():
    site = LaserSiteConfig(
        name="Coherent",
        strategy="sitemap",
        base_url="https://www.coherent.com",
        sitemap_url="https://www.coherent.com/sitemap.xml",
        url_pattern=r"/news/press-releases/[^/]+$",
    )
    items = parse_sitemap(SITEMAP, site)
    assert [i.url for i in items] == [
        "https://www.coherent.com/news/press-releases/new-laser"
    ]
    assert items[0].published_at.date().isoformat() == "2026-11-05"


def test_dedup_keeps_higher_weight_and_records_alternates():
    a = LaserCandidate(
        "Marvell 收购 Lumentum 部分业务", "https://a/1", None, "", "A", 1.0, "zh"
    )
    b = LaserCandidate(
        "Marvell收购Lumentum部分业务", "https://b/2", None, "更完整摘要", "B", 0.5, "zh"
    )
    kept = dedup_candidates([a, b])
    assert len(kept) == 1
    assert kept[0].alternates == ["https://b/2"]
