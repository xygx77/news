"""Render a local HTML page to PNG with the installed Chrome.

Why a script: the project pages are static HTML, so a screenshot is the only
honest way to check a layout change. Playwright's bundled Chromium is not
installed on this machine; the system Chrome is, so we drive that one through
``channel="chrome"``.

Usage (needs full access: Playwright talks to the browser over a named pipe,
which the DSH file sandbox blocks):

    python scripts/shot.py output/daily/index.html --out output/shot.png
    python scripts/shot.py output/daily/index.html --width 390 --out output/m.png
    python scripts/shot.py output/daily/index.html --click ".archive-more > summary"

``scripts/shot.ps1`` wraps this and finds a Python that has Playwright.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - environment problem, not a page problem
    sys.exit("playwright is not installed for this interpreter: pip install playwright")


def page_url(target: str) -> str:
    if target.startswith(("http://", "https://", "file://")):
        return target
    path = Path(target).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"no such file: {path}")
    return path.as_uri()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="HTML file or http(s) URL")
    parser.add_argument("--out", required=True, help="PNG path to write")
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument(
        "--height",
        type=int,
        default=1200,
        help="viewport height; the capture itself is full page",
    )
    parser.add_argument("--scale", type=float, default=1.0, help="device scale factor")
    parser.add_argument(
        "--click",
        help="CSS selector to click before capturing (e.g. a <details> summary)",
    )
    parser.add_argument(
        "--channel",
        default="chrome",
        help='browser channel, default "chrome" (installed Chrome); "msedge" also works',
    )
    parser.add_argument("--wait", type=int, default=250, help="ms to settle after click")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel)
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.scale,
        )
        page.goto(page_url(args.target))
        if args.click:
            page.click(args.click)
            page.wait_for_timeout(args.wait)
        page.screenshot(path=str(out), full_page=True)
        browser.close()

    print(f"{out}  ({args.width}px, {args.channel})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
