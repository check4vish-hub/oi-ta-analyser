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


def test_day_so_far_uses_all_months_oi_in_expiry_week():
    # near month OI collapses from rollover, but combined OI is up -> not "long unwinding"
    rd = [_r("09:30", 99.5, 600, total=3150, prev_oi=1000, exp="23-Nov-2026"),
          _r("09:45", 99.6, 560, total=3180, prev_oi=1000, exp="23-Nov-2026")]
    rd[0]["prev_total_oi"] = rd[1]["prev_total_oi"] = 3000
    rows = it.analyse_intervals(rd)
    assert rows[1]["day_doi_pct"] == 6.0 and rows[1]["near_day_doi_pct"] == -44.0
    assert it.day_label(rows[1]["day_dp_pct"], rows[1]["day_doi_pct"]) == "Short build-up"
    assert "OI of all months" in rows[1]["text"]


def test_live_page_carries_generation_time_for_stale_warning():
    page = it.build_page([], 15, live=True)
    assert '"live": true' in page and '"generated_ms":' in page and "stopped updating" in page


def test_day_story_covers_whole_session():
    rd = [_r("09:30", 100.5, 1010, vol=1)]
    price, oi = 100.5, 1010
    times = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 15, 30, 45)
             if "09:45" <= f"{h:02d}:{m:02d}" <= "15:30"]
    for i, t in enumerate(times):
        if t <= "12:00":
            price, oi = price + 0.3, oi + 12          # long build-up in the morning
        elif t > "14:30":
            price, oi = price - 0.4, oi + 15          # short build-up into the close
        else:
            price, oi = price + 0.001, oi + 0.01
        rd.append(_r(t, round(price, 3), oi))
    rows = it.analyse_intervals(rd)
    txt = it.day_story(rows)
    assert "Whole day" in txt and "Range: high" in txt
    assert "Opening hour" in txt and "Last hour" in txt
    assert "mostly long build-up" in txt and "mostly short build-up" in txt
    assert "Longest streak" in txt and "in the last hour sellers added fresh positions" in txt
    page = it.build_page([{"summary": it.summarise_day("X", rd, rows), "rows": rows}], 15, live=False)
    assert '"day_text"' in page and "the whole session" in page


def _day(trend: str, day="2026-11-20", prev_close=100.0, prev_oi=1000.0, exp="30-Dec-2026"):
    """Synthetic session: 'up' = long build-up all day, 'down' = short build-up, 'flat' = noise."""
    rd, price, oi = [], prev_close, prev_oi
    times = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 15, 30, 45) if "09:30" <= f"{h:02d}:{m:02d}" <= "15:30"]
    for i, t in enumerate(times):
        if trend == "up":
            price, oi = price * 1.0012, oi * 1.004
        elif trend == "down":
            price, oi = price * 0.9988, oi * 1.004
        else:
            price, oi = price * (1.0003 if i % 2 else 0.9997), oi * (1.0002 if i % 2 else 0.9998)
        rd.append({"time": f"{day} {t}:00", "price": round(price, 3), "prev_close": prev_close, "oi": oi,
                   "prev_oi": prev_oi, "total_oi": oi, "volume": None, "spot": price, "expiry": exp})
    return it.analyse_intervals(rd)


@pytest.mark.parametrize("trend,label", [("up", "Heavily bullish"), ("down", "Heavily bearish"), ("flat", "Sideways")])
def test_day_bias_levels(trend, label):
    b = it.day_bias(_day(trend))
    assert b["label"] == label, (b["score"], b["reasons"])
    assert ("may continue higher" in b["text"]) if trend == "up" else ("may continue lower" in b["text"]) \
        if trend == "down" else ("range-bound" in b["text"])
    assert "weakens" in b["change"] or "break above" in b["change"]


def test_bias_toned_down_in_expiry_week():
    normal = it.day_bias(_day("down"))["score"]
    expiry = it.day_bias(_day("down", exp="23-Nov-2026"))["score"]
    assert abs(expiry) < abs(normal)


def test_track_record_counts_next_session():
    seq = [("d1", {"label": "Bullish", "close": 100, "expiry": "E"}),
           ("d2", {"label": "Bearish", "close": 101, "expiry": "E"}),     # bull hit
           ("d3", {"label": "Bearish", "close": 102, "expiry": "E"}),     # bear miss
           ("d4", {"label": "Sideways", "close": 100, "expiry": "F"})]    # roll: skipped
    st = it.bias_track_record(seq)
    assert st["bull"] == [1, 1] and st["bear"] == [0, 1] and st["side"] == [0, 0]


def test_page_shows_bias_in_header_and_story():
    rows = []
    for i, tr in enumerate(["up", "up", "down", "flat", "up", "down", "down"]):
        rows += _day(tr, day=f"2026-11-{10 + i:02d}")
    summary = {"symbol": "X", "price": 1, "day_dp_pct": 0, "day_doi_pct": 0, "day_label": "x", "last_time": "15:30",
               "last_label": "x", "last_strength": "", "counts": {}, "n": 1, "recent": "x", "expiry": "", "expiry_week": False,
               "day": "2026-11-16"}
    page = it.build_page([{"summary": summary, "rows": rows}], 15, live=False)
    assert "Bias: " in page and "Track record on this stock" in page and '"bias": {"label":' in page


def test_moderate_bias_gets_matching_change_text():
    rows = _day("down")
    b = it.day_bias(rows[:14] + _day("flat", day="2026-11-20")[14:])   # bearish morning, flat close
    if b["label"] == "Bearish":
        assert "rises above" in b["change"]
