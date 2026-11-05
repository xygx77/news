"""Offline HTTP client backed by fixture files."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CALENDAR_URL = "https://data.eastmoney.com/cjrl/"
KLINE_PREFIX = "https://push2his.eastmoney.com/"


def build_offline_client(fixtures_dir: Path) -> httpx.AsyncClient:
    """Return an AsyncClient that serves fixtures instead of network calls."""
    fixtures_dir = Path(fixtures_dir)
    prefix_map = json.loads(
        (fixtures_dir / "offline_map.json").read_text(encoding="utf-8")
    )
    quotes = json.loads(
        (fixtures_dir / "eastmoney_quotes.json").read_text(encoding="utf-8")
    )

    def fixture(name: str) -> httpx.Response:
        path = fixtures_dir / name
        if not path.exists():
            return httpx.Response(503, text=f"missing fixture: {name}")
        text = path.read_text(encoding="utf-8")
        if name.endswith(".json"):
            return httpx.Response(200, json=json.loads(text))
        return httpx.Response(200, text=text)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(ULIST_URL):
            secids = request.url.params.get("secids", "")
            diff = [
                quotes[secid]
                for secid in secids.split(",")
                if secid in quotes
            ]
            return httpx.Response(200, json={"data": {"diff": diff}})
        if url.startswith(CLIST_URL):
            return fixture("eastmoney_clist.json")
        if url.startswith(FRED_CSV_URL):
            series_id = request.url.params.get("id", "DGS10")
            return fixture(f"fred_{series_id}.csv")
        if url.startswith(CALENDAR_URL):
            return fixture("calendar.html")
        if url.startswith(KLINE_PREFIX):
            return fixture("kline.json")
        for prefix, name in prefix_map.items():
            if url.startswith(prefix):
                return fixture(name)
        return httpx.Response(503, text=f"offline fixture not found: {url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
