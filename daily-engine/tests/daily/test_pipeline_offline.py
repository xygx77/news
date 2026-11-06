from datetime import date
from pathlib import Path

from src.daily.cli import main

ROOT = Path(__file__).resolve().parents[2]


def test_offline_pipeline_generates_all_pages(tmp_path, monkeypatch):
    import socket

    dns_calls: list[tuple] = []

    def no_dns(*args, **kwargs):
        dns_calls.append(args)
        raise RuntimeError("network disabled")

    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    exit_code = main(
        [
            "--config",
            "data/config.daily.json",
            "--out",
            str(tmp_path),
            "--offline",
            "--fixtures-dir",
            "tests/daily/fixtures",
            "--date",
            "2026-11-05",
        ]
    )
    assert exit_code == 0
    assert dns_calls == []
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "latest/morning.html").exists()
    assert (tmp_path / "latest/laser.html").exists()
    assert (tmp_path / "2026-11-05/morning.html").exists()
    assert (tmp_path / "2026-11-05/laser.html").exists()
    assert (tmp_path / "run.log").exists()

    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert "calendar=2" in log
    assert "jin10=2" in log
    assert "laser_selected=" in log
    assert "503" in log

    morning = (tmp_path / "2026-11-05/morning.html").read_text(encoding="utf-8")
    assert "央行开展 5000 亿元逆回购操作" in morning
    assert "全市场上涨" in morning
    laser = (tmp_path / "2026-11-05/laser.html").read_text(encoding="utf-8")
    assert "大族激光完成 10 亿元并购融资" in laser


def test_offline_weekend_omits_ashare_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    exit_code = main(
        [
            "--config",
            "data/config.daily.json",
            "--out",
            str(tmp_path),
            "--offline",
            "--fixtures-dir",
            "tests/daily/fixtures",
            "--date",
            "2026-11-07",
        ]
    )
    assert exit_code == 0
    text = (tmp_path / "2026-11-07/morning.html").read_text(encoding="utf-8")
    assert "一、A股主要指数" not in text
    assert "五、热点板块与异动解析" not in text
    assert "今日休市" in text


def test_offline_index_failure_returns_nonzero(tmp_path, monkeypatch):
    import src.daily.pipeline as pipeline

    def boom(**kwargs):
        raise RuntimeError("index boom")

    monkeypatch.setattr(pipeline, "render_index", boom)
    exit_code = main(
        [
            "--config",
            "data/config.daily.json",
            "--out",
            str(tmp_path),
            "--offline",
            "--fixtures-dir",
            "tests/daily/fixtures",
            "--date",
            "2026-11-05",
        ]
    )
    assert exit_code == 1
    fallback = tmp_path / "index.html"
    assert fallback.exists()
    assert "生成失败" in fallback.read_text(encoding="utf-8")
    assert "Traceback" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_source_health_marks_degraded_after_three_failures(tmp_path):
    from src.daily.pipeline import update_source_health

    state = tmp_path / ".source_health.json"
    for day in (date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)):
        streak = update_source_health(
            state, source="Revolve", ok=False, day=day
        )
    assert streak == 3
    assert (
        update_source_health(
            state, source="Revolve", ok=True, day=date(2026, 11, 6)
        )
        == 0
    )
