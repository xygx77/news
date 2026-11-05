"""US Treasury yields from FRED and China bond fallbacks."""

from __future__ import annotations

from typing import Mapping

import httpx

from .schema import Fact

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES = {
    "DGS2": "美国 2 年期国债",
    "DGS10": "美国 10 年期国债",
    "DGS30": "美国 30 年期国债",
}


def _direction(value: float) -> str:
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def parse_fred_csv(text: str, *, label: str, series_id: str) -> Fact | None:
    """Build a yield fact from FRED's two-column CSV."""
    observations: list[tuple[str, float]] = []
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 2:
            continue
        date_text, value_text = parts[0].strip(), parts[1].strip()
        if not date_text or date_text.lower().startswith("observation"):
            continue
        if value_text in {"", "."}:
            continue
        try:
            observations.append((date_text, float(value_text)))
        except ValueError:
            continue
    if not observations:
        return None

    latest_date, latest = observations[-1]
    change_num: int | None = None
    change: str | None = None
    direction = "unknown"
    if len(observations) >= 2:
        change_num = round((latest - observations[-2][1]) * 100)
        change = f"{change_num:+d}BP"
        direction = _direction(float(change_num))
    return Fact(
        id=f"rate:{series_id}",
        group="rate",
        label=label,
        value=f"{latest}%",
        value_num=latest,
        change=change,
        change_num=float(change_num) if change_num is not None else None,
        change_unit="bp" if change_num is not None else None,
        direction=direction,
        status="ok",
        source_name="FRED",
        source_url=f"https://fred.stlouisfed.org/series/{series_id}",
        as_of=latest_date,
    )


async def fetch_fred_yields(
    client: httpx.AsyncClient, series: Mapping[str, str]
) -> list[Fact]:
    """Fetch each FRED series independently; failures skip that series."""
    facts: list[Fact] = []
    for series_id, label in series.items():
        try:
            response = await client.get(
                FRED_CSV_URL, params={"id": series_id}, timeout=10
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - other series may succeed
            continue
        fact = parse_fred_csv(response.text, label=label, series_id=series_id)
        if fact is not None:
            facts.append(fact)
    return facts


async def fetch_china_bond_yields(client: httpx.AsyncClient) -> list[Fact]:
    """China bond yields have no stable public endpoint in v1; mark missing."""
    return [
        Fact(
            id="rate:cn10y",
            group="rate",
            label="中国 10 年期国债",
            value="暂缺",
            status="missing",
            note="v1 无稳定公开接口",
            source_name="暂缺",
            source_url="https://www.chinabond.com.cn/",
            as_of="暂缺",
        ),
        Fact(
            id="rate:cn30y",
            group="rate",
            label="中国 30 年期国债",
            value="暂缺",
            status="missing",
            note="v1 无稳定公开接口",
            source_name="暂缺",
            source_url="https://www.chinabond.com.cn/",
            as_of="暂缺",
        ),
    ]
