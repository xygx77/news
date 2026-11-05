from src.daily.market.eastmoney import parse_clist, parse_ulist
from src.daily.market.rates import parse_fred_csv


def test_parse_ulist_maps_fields():
    payload = {
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f14": "上证指数",
                    "f2": 3388.81,
                    "f3": 1.23,
                    "f6": 512345678900,
                },
            ]
        }
    }
    facts = parse_ulist(payload, group="ashare", as_of="2026-11-04 收盘")
    assert facts[0].value == "3388.81"
    assert facts[0].change == "+1.23%"
    assert facts[0].change_num == 1.23
    assert facts[0].change_unit == "pct"
    assert facts[0].direction == "up"
    assert facts[0].status == "ok"
    assert facts[0].label == "上证指数"


def test_parse_ulist_uses_configured_label():
    payload = {
        "data": {
            "diff": [
                {"f12": "CL00Y", "f14": "WTI原油", "f2": 78.40, "f3": 1.15}
            ]
        }
    }
    facts = parse_ulist(
        payload,
        group="commodity",
        as_of="抓取时点",
        labels={"CL00Y": "WTI 原油"},
    )
    assert facts[0].label == "WTI 原油"


def test_parse_ulist_marks_missing_value():
    payload = {
        "data": {"diff": [{"f12": "000001", "f14": "上证指数", "f2": "-", "f3": None}]}
    }
    fact = parse_ulist(payload, group="ashare", as_of="x")[0]
    assert fact.value == "暂缺"
    assert fact.status == "missing"
    assert fact.value_num is None


def test_parse_fred_csv_uses_last_two_observations():
    text = "observation_date,DGS10\n2026-11-03,4.91\n2026-11-04,4.95\n2026-11-05,.\n"
    fact = parse_fred_csv(text, label="美国 10 年期国债", series_id="DGS10")
    assert fact.value == "4.95%"
    assert fact.change == "+4BP"
    assert fact.change_num == 4.0
    assert fact.change_unit == "bp"


def test_parse_clist_formats_sector_rows():
    payload = {"data": {"diff": [{"f12": "BK001", "f14": "半导体", "f3": 3.21}]}}
    facts = parse_clist(payload, as_of="2026-11-04 收盘")
    assert facts[0].group == "sector"
    assert facts[0].change == "+3.21%"
