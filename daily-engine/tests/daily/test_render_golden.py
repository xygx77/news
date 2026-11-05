from datetime import date
from pathlib import Path

import pytest

from src.daily.market.trading_day import TradingDayInfo
from src.daily.render.finance import render_morning
from src.daily.render.index import list_archives, render_index
from src.daily.render.laser import render_laser
from src.daily.render.template import fill_placeholders, render_conditional_blocks

ROOT = Path(__file__).resolve().parents[2]


def test_conditional_blocks_remove_only_when_false():
    template = "A<!-- if:trading_day -->B<!-- endif -->C"
    assert render_conditional_blocks(template, {"trading_day": True}) == "ABC"
    assert render_conditional_blocks(template, {"trading_day": False}) == "AC"


def test_fill_placeholders_escapes_nothing_but_rejects_leftovers():
    assert fill_placeholders("{{X}}", {"X": "<b>ok</b>"}) == "<b>ok</b>"
    with pytest.raises(ValueError, match="unfilled placeholder"):
        fill_placeholders("{{X}}{{Y}}", {"X": "1"})


def test_morning_template_has_condition_markers():
    text = (ROOT / "templates/daily/morning.html").read_text(encoding="utf-8")
    assert text.count("<!-- if:trading_day -->") == 2
    assert text.count("<!-- endif -->") == 2
    assert "{{ASHARE_ROWS}}" in text and "{{SECTOR_BOXES}}" in text


def test_render_morning_golden(tmp_path):
    from tests.daily.factories import fixed_morning_draft

    draft = fixed_morning_draft()
    out = tmp_path / "morning.html"
    render_morning(
        draft,
        template_path=ROOT / "templates/daily/morning.html",
        output_path=out,
    )
    actual = out.read_text(encoding="utf-8")
    expected = (ROOT / "tests/daily/golden/morning.html").read_text(encoding="utf-8")
    assert actual == expected
    assert "<script>alert(1)</script>" not in actual
    assert "{{" not in actual and "{%" not in actual


def test_render_morning_omits_ashare_on_holiday(tmp_path):
    from tests.daily.factories import fixed_morning_draft

    draft = fixed_morning_draft()
    draft.trading = TradingDayInfo(
        day=draft.report_day,
        is_trading=False,
        previous=date(2026, 11, 4),
        verified=True,
    )
    draft.trading_status = (
        f"今日休市（北京时间 {draft.report_day.isoformat()}），"
        "仅保留海外市场、利率汇率、商品与风险提示。"
    )
    out = tmp_path / "morning.html"
    render_morning(
        draft,
        template_path=ROOT / "templates/daily/morning.html",
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "一、A股主要指数" not in text
    assert "五、热点板块与异动解析" not in text
    assert "四、隔夜海外市场" in text
    assert "今日休市" in text


def test_render_morning_includes_fx_rows(tmp_path):
    from tests.daily.factories import fixed_morning_draft

    out = tmp_path / "morning.html"
    render_morning(
        fixed_morning_draft(),
        template_path=ROOT / "templates/daily/morning.html",
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "美元兑离岸人民币" in text
    assert "美元兑人民币中间价" in text


def test_render_sector_boxes_limit_to_two_each(tmp_path):
    from tests.daily.factories import fixed_morning_draft

    draft = fixed_morning_draft()
    draft.facts.append(
        type(draft.facts[0])(
            id="sector:BK999",
            group="sector",
            label="第三涨幅板块",
            value="+1.00%",
            change="+1.00%",
            change_num=1.0,
            change_unit="pct",
            direction="up",
            status="ok",
            source_name="东方财富",
            source_url="https://quote.eastmoney.com/",
            as_of="2026-11-04 收盘",
        )
    )
    out = tmp_path / "morning.html"
    render_morning(
        draft,
        template_path=ROOT / "templates/daily/morning.html",
        output_path=out,
    )
    assert "第三涨幅板块" not in out.read_text(encoding="utf-8")


def test_render_morning_shows_placeholder_rows_for_missing_facts(tmp_path):
    from tests.daily.factories import fixed_morning_draft

    draft = fixed_morning_draft()
    draft.facts = [fact for fact in draft.facts if fact.label != "上证指数"]
    out = tmp_path / "morning.html"
    render_morning(
        draft,
        template_path=ROOT / "templates/daily/morning.html",
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "<td>上证指数</td><td>暂缺</td>" in text


def test_render_laser_and_index(tmp_path):
    from tests.daily.factories import fixed_laser_draft, fixed_morning_draft

    output_dir = tmp_path
    render_laser(
        fixed_laser_draft(),
        template_path=ROOT / "templates/daily/laser.html",
        output_path=output_dir / "2026-11-05" / "laser.html",
    )
    render_index(
        output_dir=output_dir,
        report_day=date(2026, 11, 5),
        morning=fixed_morning_draft(),
        laser=fixed_laser_draft(),
        template_path=ROOT / "templates/daily/index.html",
    )
    index = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "开盘前晨报" in index and "激光行业简报" in index
    assert "2026-11-05/morning.html" in index
    assert "2026-11-05/laser.html" in index
    assert "2026-11-05：" in index


def test_render_laser_renders_alternate_sources(tmp_path):
    from tests.daily.factories import fixed_laser_draft

    draft = fixed_laser_draft()
    draft.items[0].alternates = ["https://alt.example.com/story"]
    out = tmp_path / "laser.html"
    render_laser(
        draft,
        template_path=ROOT / "templates/daily/laser.html",
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "其他来源" in text
    assert 'href="https://alt.example.com/story"' in text


def test_render_laser_escapes_title_markup(tmp_path):
    from tests.daily.factories import fixed_laser_draft

    out = tmp_path / "laser.html"
    render_laser(
        fixed_laser_draft(),
        template_path=ROOT / "templates/daily/laser.html",
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "<b>大族激光</b>" not in text
    assert "&lt;b&gt;大族激光&lt;/b&gt;" in text


def test_index_escapes_source_text(tmp_path):
    from tests.daily.factories import fixed_laser_draft, fixed_morning_draft

    render_index(
        output_dir=tmp_path,
        report_day=date(2026, 11, 5),
        morning=fixed_morning_draft(),
        laser=fixed_laser_draft(),
        template_path=ROOT / "templates/daily/index.html",
    )
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "&amp;" in text
    assert "&quot;引号&quot;" in text
    assert "<b>大族激光</b>" not in text


def test_list_archives_ignores_non_date_dirs(tmp_path):
    (tmp_path / "2026-11-04").mkdir()
    (tmp_path / "latest").mkdir()
    assert list_archives(tmp_path) == [date(2026, 11, 4)]


def test_render_laser_truncates_long_chinese_title(tmp_path):
    from tests.daily.factories import fixed_laser_draft

    draft = fixed_laser_draft()
    draft.items[0].title = "激" * 40
    out = tmp_path / "laser.html"
    render_laser(
        draft,
        template_path=ROOT / "templates/daily/laser.html",
        output_path=out,
    )
    text = out.read_text(encoding="utf-8")
    assert "激" * 40 not in text
    assert "激" * 30 + "…" in text
