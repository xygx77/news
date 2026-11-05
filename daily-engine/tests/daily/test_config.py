from pathlib import Path

from src.daily.config import load_daily_config

ROOT = Path(__file__).resolve().parents[2]


def test_load_daily_config_real_file():
    cfg = load_daily_config(ROOT / "data/config.daily.json")
    assert cfg.writer == "rule"
    assert cfg.daily.timezone == "Asia/Shanghai"
    assert cfg.daily.laser.window_hours == 48
    assert cfg.daily.laser.fallback_window_hours == 72
    assert sum(site.enabled for site in cfg.sources.laser) == 14
    assert all(site.reason for site in cfg.sources.laser if not site.enabled)


def test_project_root_contains_pyproject():
    from src.daily.paths import project_root

    assert (project_root() / "pyproject.toml").exists()


def test_ai_writer_is_rejected(tmp_path):
    import pytest

    path = tmp_path / "config.json"
    path.write_text(
        '{"writer":"ai","daily":{},"sources":{},"market":{}}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="AI writer not implemented"):
        load_daily_config(path)


def test_env_vars_are_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_OUT", "output/from-env")
    path = tmp_path / "config.json"
    path.write_text(
        '{"writer":"rule","daily":{"timezone":"Asia/Shanghai","output_dir":"${TEST_OUT}",'
        '"finance":{"enabled":true,"max_material_items":40},'
        '"laser":{"enabled":true,"window_hours":48,"fallback_window_hours":72,"min_items":5,"max_items":8}},'
        '"sources":{"jin10":{"enabled":true,"limit":100},'
        '"wallstreetcn":{"enabled":true,"channel":"global-channel","limit":100},'
        '"rss":[],"cnfin":{"enabled":false},"laser":[]},'
        '"market":{"indices":["000001"],"sectors":{"enabled":true,"top_n":5}}}',
        encoding="utf-8",
    )
    assert load_daily_config(path).daily.output_dir == "output/from-env"
