import time
from datetime import datetime, timedelta

import pytest

import tracker
from stock_analyser import intraday as it


def _r(t, price, oi, total=None, prev_close=100.0, prev_oi=1000.0, vol=None, exp="30-Dec-2026"):
    return {"time": f"2026-11-20 {t}:00", "price": price, "prev_close": prev_close, "oi": oi,
            "prev_oi": prev_oi, "total_oi": total if total is not None else oi * 1.1,
            "volume": vol, "spot": price, "expiry": exp}


@pytest.mark.parametrize("dp,doi,label", [(1, 1, "Long build-up"), (-1, 1, "Short build-up"),
                                          (1, -1, "Short covering"), (-1, -1, "Long unwinding"),
                                          (0, 5, "No clear signal")])
def test_classify(dp, doi, label):
    assert it.classify(dp, doi) == label


def test_intervals_gap_strength_and_streak():
    rd = [_r("09:15", 101, 1030, vol=500), _r("09:30", 101.5, 1040, vol=900), _r("09:45", 102, 1050, vol=1500),
          _r("10:00", 102.6, 1062, vol=2000), _r("10:15", 102.1, 1100, vol=2600)]
    rows = it.analyse_intervals(rd)
    assert rows[0]["gap"] and rows[0]["label"] == "Long build-up" and rows[0]["doi"] == 30
    assert [x["label"] for x in rows[1:4]] == ["Long build-up"] * 3
    assert rows[3]["streak"] == 4 and "in a row" in rows[3]["text"]
    assert rows[4]["label"] == "Short build-up" and rows[4]["strength"] == "strong"   # 38 vs typical ~10
    assert rows[2]["dvol"] == 600
    assert "Day so far" in rows[4]["text"]


def test_tiny_changes_are_no_signal():
    rows = it.analyse_intervals([_r("09:15", 100.5, 1010), _r("09:30", 100.501, 1010.1)])
    assert rows[1]["label"] == "No clear signal"


def test_expiry_week_uses_combined_oi():
    # near-month OI falls (rollover) but all-months OI rises -> not long unwinding
    rd = [_r("09:15", 100.2, 1000, total=3000, exp="24-Nov-2026"),
          _r("09:30", 99.8, 950, total=3060, exp="24-Nov-2026")]
    rows = it.analyse_intervals(rd)
    assert rows[1]["expiry_week"] and rows[1]["label"] == "Short build-up"
    assert "all futures months combined" in rows[1]["text"]


def test_next_slot_alignment():
    t = datetime(2026, 11, 20, 10, 7, tzinfo=it.IST)
    assert it.next_slot(t, 15).strftime("%H:%M") == "10:15"
    assert it.next_slot(t, 5).strftime("%H:%M") == "10:10"
    assert it.next_slot(datetime(2026, 11, 20, 8, 0, tzinfo=it.IST), 15).strftime("%H:%M") == "09:15"
    assert it.next_slot(datetime(2026, 11, 20, 15, 20, tzinfo=it.IST), 15).strftime("%H:%M") == "15:30"


def test_reading_from_futures_and_storage(tmp_path):
    fut = {"expiry": "25-Nov-2026", "last_price": 2050.0, "price_change": -25.0, "oi": 10000,
           "oi_change": 2000, "volume": 5000, "spot": 2047.0,
           "curve": [{"oi": 10000}, {"oi": 3000}]}
    r = it.reading_from_futures(fut, datetime(2026, 11, 20, 10, 0, tzinfo=it.IST))
    assert r["prev_close"] == 2075.0 and r["prev_oi"] == 8000 and r["total_oi"] == 13000
    f = it.data_file(tmp_path, "abc", datetime(2026, 11, 20, tzinfo=it.IST))
    it.append_reading(f, r)
    it.append_reading(f, r)
    back = it.load_readings(f)
    assert len(back) == 2 and back[0]["price"] == 2050.0 and back[0]["volume"] == 5000.0


def test_page_has_click_handler_and_data(tmp_path):
    p = tracker.run_demo(["AAA", "BBB"], 15, tmp_path / "d", tmp_path / "t.html", open_browser=False)
    html = p.read_text()
    assert "plotly_click" in html and '"symbol": "AAA"' in html and '"symbol": "BBB"' in html


class FakeNSE:
    def __init__(self):
        self.n = 0

    def get_futures_raw(self, symbol):
        self.n += 1
        return {"data": [{"instrumentType": "FUTSTK", "expiryDate": "30-Dec-2026", "lastPrice": 100 + self.n,
                          "change": self.n, "openInterest": 1000 + 10 * self.n,
                          "changeinOpenInterest": 10 * self.n, "totalTradedVolume": 100 * self.n,
                          "underlyingValue": 99 + self.n}]}


def test_live_loop_records_then_stops(tmp_path, monkeypatch):
    fixed = datetime(2026, 11, 20, 11, 0, tzinfo=it.IST)       # a Friday, market open
    monkeypatch.setattr(it, "now_ist", lambda: fixed)
    monkeypatch.setattr(tracker, "DATA", tmp_path / "data")
    monkeypatch.setattr(tracker, "PAGE", tmp_path / "live.html")
    import stock_analyser.nse_client as nc
    monkeypatch.setattr(nc, "NSEClient", FakeNSE)
    real_sleep = time.sleep
    monkeypatch.setattr("builtins.input", lambda *_: (real_sleep(1.5), "stop")[1])
    monkeypatch.setattr(tracker.time, "sleep", lambda s: real_sleep(min(s, 0.3)))
    assert tracker.main(["XYZ", "--no-open"]) == 0
    rd = it.load_readings(it.data_file(tmp_path / "data", "XYZ", fixed))
    assert len(rd) >= 1 and rd[0]["price"] == 101.0
    assert "Not live" in (tmp_path / "live.html").read_text()


def test_parse_symbols():
    assert tracker.parse_symbols("coforge, tcs  TCS nifty.ns") == ["COFORGE", "TCS", "NIFTY"]
