"""Command line entry point for the daily briefing pipeline."""

from __future__ import annotations

import argparse
import asyncio
import logging
import webbrowser
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .config import load_daily_config
from .pipeline import run_daily


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="horizon-daily",
        description="Generate the daily finance and laser briefings.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="data/config.daily.json",
        help="Path to the daily config file.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory; overrides daily.output_dir.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Report date (YYYY-MM-DD) for backfills or reproducible runs.",
    )
    parser.add_argument(
        "--only",
        choices=["finance", "laser"],
        default=None,
        help="Generate only one of the two reports.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use fixture files instead of the network.",
    )
    parser.add_argument(
        "--fixtures-dir",
        default="tests/daily/fixtures",
        help="Fixture directory for --offline.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated index in the default browser.",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="WARNING",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help="Logging level (default: WARNING).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.WARNING),
        format="%(message)s",
    )

    try:
        config = load_daily_config(Path(args.config))
    except Exception:  # noqa: BLE001 - CLI reports and exits
        logging.exception("Failed to load daily config")
        return 1

    try:
        report_date = date.fromisoformat(args.date) if args.date else None
        report = asyncio.run(
            run_daily(
                config,
                report_date=report_date,
                only=args.only,
                offline=args.offline,
                out_dir=Path(args.out) if args.out else None,
                fixtures_dir=Path(args.fixtures_dir)
                if args.fixtures_dir
                else None,
            )
        )
    except Exception:  # noqa: BLE001 - CLI reports and exits
        logging.exception("Daily briefing failed")
        return 1

    if args.open:
        webbrowser.open(
            (report.output_dir / "index.html").resolve().as_uri()
        )
    if any(name.startswith("render:") for name in report.failures):
        return 1
    return 0 if (report.finance_ok or report.laser_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
