from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_schedule_and_paths():
    text = (ROOT / "scripts/daily.yml").read_text(encoding="utf-8")
    assert 'cron: "10 0 * * *"' in text
    assert "working-directory: daily-engine" in text
    assert "--out ../docs/daily" in text
    assert "contents: write" in text
    assert "git pull --rebase --autostash" in text
    assert "fetch-depth: 0" in text
    assert "astral-sh/setup-uv@v10.3.3" in text


def test_sync_script_covers_required_paths():
    text = (ROOT / "scripts/sync_news_repo.ps1").read_text(encoding="utf-8")
    for name in (
        "src",
        "profiles",
        "templates",
        "config.daily.json",
        "laser_keywords.json",
        "cn_market_holidays.json",
        "tests",
        "pyproject.toml",
        "uv.lock",
    ):
        assert name in text
    assert "git clone" in text
    assert "if ($LASTEXITCODE -ne 0)" in text
    assert "git diff --cached --quiet" in text
    assert "rev-parse --is-inside-work-tree" in text
