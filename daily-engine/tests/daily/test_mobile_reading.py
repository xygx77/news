"""Guards for the phone reading mode shared by the two daily subpage templates.

The subpages render a fixed A4 canvas that JS scales down; on a 390px phone the
scale clamps at .58, so body text lands near 8px and the frame is cropped ~35px
per side. A `@media screen and (max-width: 820px)` block reflows `#source` into a
normal article instead. These tests keep the block identical across templates and
keep the CSS breakpoint in sync with the JS one.
"""

import re
from pathlib import Path

import pytest

from src.daily.render.finance import render_morning
from src.daily.render.laser import render_laser

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ("morning.html", "laser.html")

BLOCK_BEGIN = "    /* ===== mobile-read: begin"
BLOCK_END = "    /* ===== mobile-read: end ===== */\n"


def _template(name):
    return (ROOT / "templates/daily" / name).read_text(encoding="utf-8")


def _mobile_block(name):
    text = _template(name)
    start = text.index(BLOCK_BEGIN)
    stop = text.index(BLOCK_END, start) + len(BLOCK_END)
    return text[start:stop]


def test_mobile_block_is_byte_identical_in_both_templates():
    blocks = {name: _mobile_block(name) for name in TEMPLATES}
    assert blocks["morning.html"], "mobile-read block not found"
    assert blocks["morning.html"] == blocks["laser.html"]


def test_mobile_block_ends_the_stylesheet():
    # Later rules would win over the phone overrides on equal specificity.
    for name in TEMPLATES:
        text = _template(name)
        assert text.index(BLOCK_END) < text.index("  </style>")


@pytest.mark.parametrize("name", TEMPLATES)
def test_mobile_block_reflows_the_a4_canvas(name):
    block = _mobile_block(name)
    assert "@media screen and (max-width: 820px)" in block
    assert "font-size: 16px" in block
    assert "#pages, .viewer-tools, .folio { display: none; }" in block
    # Cells must be free to wrap so a wide table cannot widen the article,
    # while the percent/bp badge stays on one line.
    assert "th, td { padding: 8px 6px; overflow-wrap: anywhere; }" in block
    assert "table .tag { white-space: nowrap; }" in block
    assert "font-size: 14px" in block
    # #source must stop being the off-screen measuring copy.
    assert re.search(r"#source \{[^}]*position: static;", block, re.S)
    assert re.search(r"#source \{[^}]*visibility: visible;", block, re.S)


@pytest.mark.parametrize("name", TEMPLATES)
def test_mobile_breakpoint_matches_the_js_constant(name):
    block = _mobile_block(name)
    css_px = re.search(r"@media screen and \(max-width: (\d+)px\)", block)
    js_px = re.search(r"MOBILE_MAX_WIDTH = (\d+);", _template(name))
    assert css_px and js_px
    assert css_px.group(1) == js_px.group(1)


@pytest.mark.parametrize("name", TEMPLATES)
def test_mobile_mode_skips_pagination_and_zoom(name):
    text = _template(name)
    assert "function syncLayout() {" in text
    assert re.search(r"function syncLayout\(\) \{\s*if \(mobileQuery\.matches\) \{", text)
    assert "mobileQuery.addEventListener" in text
    assert "function zoomBy(delta) {\n        if (mobileQuery.matches) return;" in text


def test_rendered_subpages_carry_the_mobile_topbar(tmp_path):
    from tests.daily.factories import fixed_laser_draft, fixed_morning_draft

    morning = tmp_path / "morning.html"
    render_morning(
        fixed_morning_draft(),
        template_path=ROOT / "templates/daily/morning.html",
        output_path=morning,
    )
    laser = tmp_path / "laser.html"
    render_laser(
        fixed_laser_draft(),
        template_path=ROOT / "templates/daily/laser.html",
        output_path=laser,
    )
    for path in (morning, laser):
        text = path.read_text(encoding="utf-8")
        assert 'class="doc-topbar"' in text
        assert 'href="../index.html"' in text
        assert "{{" not in text
        assert "mobile-read: begin" in text
