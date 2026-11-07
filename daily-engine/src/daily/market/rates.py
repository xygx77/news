"""US Treasury yields from FRED and China bond yields from ChinaBond."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .schema import Fact

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CHINABOND_URL = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery"
CHINABOND_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ),
    "Referer": "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN",
}
CHINAMONEY_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
CHINAMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/",
}
SHANGHAI = ZoneInfo("Asia/Shanghai")
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


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_china_bond_yields(html: str) -> list[Fact]:
    """Read the latest 10Y/30Y China government bond yields from the curve table."""
    soup = BeautifulSoup(html.replace("&nbsp", ""), "html.parser")
    observations: dict[str, list[tuple[str, float]]] = {}
    for table in soup.find_all("table"):
        header: list[str] | None = None
        for tr in table.find_all("tr"):
            cells = [
                "".join(cell.get_text(" ", strip=True).split())
                for cell in tr.find_all(["th", "td"])
            ]
            if not cells:
                continue
            if header is None:
                if "日期" in cells and "10年" in cells:
                    header = cells
                continue
            try:
                date_col = header.index("日期")
            except ValueError:
                continue
            if "国债收益率曲线" not in cells[0]:
                continue
            if len(cells) <= date_col:
                continue
            for term in ("10年", "30年"):
                try:
                    column = header.index(term)
                except ValueError:
                    continue
                if len(cells) <= column:
                    continue
                value = _to_float(cells[column])
                if value is None:
                    continue
                observations.setdefault(term, []).append(
                    (cells[date_col], value)
                )

    facts: list[Fact] = []
    for term, label, fact_id in (
        ("10年", "中国 10 年期国债", "rate:cn10y"),
        ("30年", "中国 30 年期国债", "rate:cn30y"),
    ):
        rows = sorted(observations.get(term, []), key=lambda row: row[0])
        if not rows:
            continue
        as_of, latest = rows[-1]
        change_num: float | None = None
        change: str | None = None
        direction = "unknown"
        if len(rows) >= 2:
            change_num = round((latest - rows[-2][1]) * 100, 1)
            change = f"{change_num:+.1f}BP"
            direction = _direction(change_num)
        facts.append(
            Fact(
                id=fact_id,
                group="rate",
                label=label,
                value=f"{latest:.2f}%",
                value_num=latest,
                change=change,
                change_num=change_num,
                change_unit="bp" if change_num is not None else None,
                direction=direction,
                status="ok",
                source_name="中国债券信息网",
                source_url="https://yield.chinabond.com.cn/",
                as_of=as_of,
            )
        )
    return facts


async def fetch_china_bond_yields(client: httpx.AsyncClient) -> list[Fact]:
    """Fetch China government bond yields; return [] when unavailable."""
    today = date.today()
    params = {
        "startDate": (today - timedelta(days=20)).isoformat(),
        "endDate": today.isoformat(),
        "gjqx": "0",
        "qxId": "ycqx",
        "locale": "cn_ZH",
    }
    try:
        response = await client.get(
            CHINABOND_URL,
            params=params,
            headers=CHINABOND_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - optional data, hide on failure
        return []
    return parse_china_bond_yields(response.text)


def parse_usdcny_fix(payload: Mapping[str, Any]) -> Fact | None:
    """Parse the PBOC USD/CNY central parity fixing from ChinaMoney."""
    records = payload.get("records") or []
    if not records:
        return None
    latest = records[0]
    values = latest.get("values") or []
    try:
        value = float(values[0])
    except (TypeError, ValueError, IndexError):
        return None

    change: str | None = None
    change_num: float | None = None
    direction = "unknown"
    if len(records) > 1:
        previous_values = records[1].get("values") or []
        try:
            previous = float(previous_values[0])
        except (TypeError, ValueError, IndexError):
            previous = None
        if previous:
            change_num = (value - previous) / previous * 100
            change = f"{change_num:+.2f}%"
            if change_num > 0:
                direction = "up"
            elif change_num < 0:
                direction = "down"
            else:
                direction = "flat"

    return Fact(
        id="rate:usdcny_fix",
        group="rate",
        label="美元兑人民币中间价",
        value=f"{value:.4f}",
        value_num=value,
        change=change,
        change_num=change_num,
        change_unit="pct" if change else None,
        direction=direction,
        status="ok",
        source_name="中国外汇交易中心",
        source_url="https://www.chinamoney.com.cn/chinese/bkccpr/",
        as_of=str(latest.get("date") or ""),
    )


async def fetch_usdcny_fix(client: httpx.AsyncClient) -> Fact | None:
    """Fetch the latest USD/CNY central parity fixing."""
    today = datetime.now(SHANGHAI).date()
    params = {
        "startDate": (today - timedelta(days=10)).isoformat(),
        "endDate": today.isoformat(),
        "currency": "USD/CNY",
        "pageNum": 1,
        "pageSize": 10,
    }
    try:
        response = await client.get(
            CHINAMONEY_URL,
            params=params,
            headers=CHINAMONEY_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - optional data, hide on failure
        return None
    try:
        return parse_usdcny_fix(response.json())
    except ValueError:
        return None
