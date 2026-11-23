import json
from datetime import date, datetime, timedelta

import numpy as np
import pytest

import tracker
from stock_analyser import breeze_source as bz
from stock_analyser import intraday as it


def last_tuesday(y, m):
    return bz._last_weekday(y, m, 1)


class FakeBreeze:
    """Mimics BreezeConnect: real expiries are the last Tuesday of each month."""
    calls = 0

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.session = None

    def generate_session(self, api_secret, session_token):
        if session_token != "GOOD123":
            raise RuntimeError("Invalid session")
        self.session = session_token

    def get_names(self, exchange_code, stock_code):
        return {"isec_stock_code": {"COFORGE": "COFTEC", "TCS": "TCS"}.get(stock_code, stock_code[:6])}

    def get_historical_data_v2(self, interval, from_date, to_date, stock_code, exchange_code,
                               product_type, expiry_date, right, strike_price):
        FakeBreeze.calls += 1
        assert exchange_code == "NFO" and product_type == "futures" and right == "others"
        exp = date.fromisoformat(expiry_date[:10])
        if exp != last_tuesday(exp.year, exp.month):
            return {"Status": 500, "Success": None, "Error": "No Data Found"}
        # like the real Breeze API: the 'Z' timestamps are read as India wall-clock time
        start = datetime.strptime(from_date[:19], "%Y-%m-%dT%H:%M:%S")
        end = datetime.strptime(to_date[:19], "%Y-%m-%dT%H:%M:%S")
        out, d = [], start.date()
        base = 2000.0 + exp.month * 15

        def px(t):      # deterministic in time, so every request sees the same prices
            k = (t - datetime(2026, 1, 1)).total_seconds() / 300
            return base * (1 + 0.02 * np.sin(k / 40)), (40_000 + exp.month * 1_000) * (1 + 0.15 * np.sin(k / 90) + 0.0005 * (k % 75))
        while d <= min(end.date(), exp):
            if d.weekday() < 5:
                if interval == "1day":
                    price, oi = px(datetime.combine(d, datetime.min.time()).replace(hour=15))
                    out.append({"datetime": f"{d} 00:00:00", "close": price, "open_interest": oi, "volume": 1})
                else:
                    t = datetime.combine(d, datetime.min.time()).replace(hour=9, minute=15)
                    while (t.hour < 15 or (t.hour == 15 and t.minute < 30)) and t <= end:
                        price, oi = px(t)
                        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": price, "high": price,
                                    "low": price, "close": round(price, 2), "open_interest": round(oi),
                                    "volume": 1000, "stock_code": stock_code, "exchange_code": "NFO",
                                    "product_type": "Futures", "expiry_date": expiry_date})
                        t += timedelta(minutes=5)
            d += timedelta(days=1)
        return {"Status": 200, "Success": out, "Error": None}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "breeze.json"
    path.write_text(json.dumps({"api_key": "KEY+1=", "api_secret": "SECRET"}))
    monkeypatch.setattr(bz, "CONFIG", path)
    return path


def test_parse_session_token():
    assert bz.parse_session_token("http://127.0.0.1/?apisession=4823991") == "4823991"
    assert bz.parse_session_token("  4823991 ") == "4823991"
    assert bz.parse_session_token("not a token!") is None


def test_login_url_encodes_special_characters():
    assert bz.login_url("ab+c=d") == "https://api.icicidirect.com/apiuser/login?api_key=ab%2Bc%3Dd"


def test_expiry_candidates_start_with_last_tuesday():
    c = bz.expiry_candidates(2026, 11)
    assert c[0] == date(2026, 11, 24) and date(2026, 11, 26) in c   # Tue first, Thu as fallback


def test_connect_asks_for_token_and_caches_it(cfg, monkeypatch):
    answers = iter(["garbage!", "http://127.0.0.1/?apisession=GOOD123"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    monkeypatch.setattr(bz.webbrowser, "open", lambda *_: None)
    c = bz.connect(path=cfg, client_factory=FakeBreeze)
    assert c.session == "GOOD123"
    saved = json.loads(cfg.read_text())
    assert saved["session_token"] == "GOOD123" and saved["session_date"] == bz.today_ist().isoformat()
    # second time today: no prompt needed
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("should not ask again"))
    assert bz.connect(path=cfg, client_factory=FakeBreeze).session == "GOOD123"


def test_not_configured_raises(tmp_path):
    with pytest.raises(bz.BreezeError):
        bz.connect(path=tmp_path / "none.json", client_factory=FakeBreeze)


def _client(cfg):
    c = FakeBreeze("KEY")
    c.generate_session("SECRET", "GOOD123")
    return c


def test_history_builds_15min_bars_for_n_days(cfg, monkeypatch):
    monkeypatch.setattr(bz, "today_ist", lambda: date(2026, 11, 20))
    by_day = bz.history(_client(cfg), "COFORGE", days=5, minutes=15, path=cfg)
    assert len(by_day) == 5 and max(by_day) == date(2026, 11, 20)
    day = by_day[date(2026, 11, 20)]
    assert len(day) == 25 and day[0]["time"].endswith("09:30:00") and day[-1]["time"].endswith("15:30:00")
    assert all("prev_close" in r and "prev_oi" in r for r in day)
    assert day[1]["volume"] > day[0]["volume"]                      # cumulative within the day
    # first reading of a day compares with the previous day's last bar
    prev = by_day[date(2026, 11, 19)][-1]
    assert day[0]["prev_close"] == prev["price"] and day[0]["prev_oi"] == prev["oi"]
    # expiry week (Nov expiry 24 Nov): total OI includes next month
    assert day[0]["total_oi"] > day[0]["oi"]
    # codes and expiries are cached
    saved = json.loads(cfg.read_text())
    assert saved["codes"]["COFORGE"] == "COFTEC" and saved["expiries"]["COFTEC:2026-11"] == "2026-11-24"


def test_history_switches_contract_after_expiry(cfg, monkeypatch):
    monkeypatch.setattr(bz, "today_ist", lambda: date(2026, 11, 27))     # after 24 Nov expiry
    by_day = bz.history(_client(cfg), "TCS", days=5, minutes=15, path=cfg)
    days = sorted(by_day)
    assert by_day[days[-1]][0]["expiry"] == "29-Dec-2026"            # Dec is near-month now
    assert by_day[date(2026, 11, 24)][0]["expiry"] == "24-Nov-2026"
    roll = by_day[date(2026, 11, 25)][0]
    nov_last = by_day[date(2026, 11, 24)][-1]
    # roll day compares with the Dec contract's own previous close/OI, not the expired Nov one
    assert roll["prev_close"] != nov_last["price"] and roll["prev_oi"] != nov_last["oi"]
    dec_prev_close, _ = None, None
    c = _client(cfg)
    dec = c.get_historical_data_v2("5minute", "2026-11-24T09:15:00.000Z", "2026-11-24T15:30:00.000Z", "TCS", "NFO",
                                   "futures", "2026-12-29T07:00:00.000Z", "others", "0")["Success"]
    assert roll["prev_close"] == dec[-1]["close"] and roll["prev_oi"] == dec[-1]["open_interest"]


def test_analyse_days_and_page(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(bz, "today_ist", lambda: date(2026, 11, 20))
    client = _client(cfg)
    page, msgs = tracker.breeze_refresh(client, ["COFORGE", "TCS"], 3, 15, page=tmp_path / "p.html")
    html = page.read_text()
    assert "ICICI Breeze" in html and '"symbol": "COFORGE"' in html and "All 3 days" not in html  # chips built in JS
    data = json.loads(html.split("const D = ")[1].split(", C = D.colours;")[0].replace("<\\/", "</"))
    st = data["stocks"][0]
    assert len(st["daily"]) == 3 and len(st["rows"]) == 75
    assert [r["gi"] for r in st["rows"]] == list(range(75))
    assert "units" in st["rows"][5]["text"]
    assert all("FAILED" not in m for m in msgs)


def test_breeze_refresh_reports_bad_symbol(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(bz, "today_ist", lambda: date(2026, 11, 20))
    client = _client(cfg)
    client.get_names = lambda exchange_code, stock_code: {}
    _, msgs = tracker.breeze_refresh(client, ["NOPE"], 3, 15, page=tmp_path / "p.html")
    assert "FAILED" in msgs[0] and "doesn't recognise" in msgs[0]


def test_closing_1530_candle_merges_into_last_bar():
    from datetime import date as d_
    day = d_(2026, 11, 20)
    mk = lambda hh, mm, px, oi: {"_t": datetime(2026, 11, 20, hh, mm, tzinfo=it.IST), "close": px,  # noqa: E731
                                 "open_interest": oi, "volume": 10, "_expiry": "29-Dec-2026"}
    prev = {d_(2026, 11, 19): [mk(15, 25, 100, 1000)]}
    for c in prev[d_(2026, 11, 19)]:
        c["_t"] = c["_t"].replace(day=19)
    near = {**prev, day: [mk(15, 20, 101, 1010), mk(15, 25, 102, 1020), mk(15, 30, 103, 1030)]}
    out = bz.candles_to_days(near, {}, 15)
    bars = out[day]
    assert len(bars) == 1 and bars[0]["time"].endswith("15:30:00") and bars[0]["price"] == 103


def test_next_month_oi_uses_latest_earlier_candle_and_carries_over():
    from datetime import date as d_
    T = lambda d, hh, mm: datetime(2026, 11, d, hh, mm, tzinfo=it.IST)  # noqa: E731
    near = {d_(2026, 11, 19): [{"_t": T(19, 15, 25), "close": 100, "open_interest": 1000, "volume": 1,
                                "_expiry": "23-Nov-2026"}],
            d_(2026, 11, 20): [{"_t": T(20, 9, 25), "close": 101, "open_interest": 900, "volume": 1,
                                "_expiry": "23-Nov-2026"}]}
    # Dec traded at 15:10 on the 19th but not at 15:25, and not before 9:25 on the 20th
    nxt = {d_(2026, 11, 19): [{"_t": T(19, 15, 10), "close": 102, "open_interest": 500}],
           d_(2026, 11, 20): [{"_t": T(20, 9, 40), "close": 103, "open_interest": 650}]}
    out = bz.candles_to_days(near, nxt, 15)
    r = out[d_(2026, 11, 20)][0]
    assert r["prev_total_oi"] == 1500      # 1000 near + 500 Dec (from 15:10, carried to 15:25)
    assert r["total_oi"] == 1400           # 900 near + 500 Dec carried overnight (no Dec trade yet)


def test_every_day_runs_to_1530_including_request_end_days(cfg, monkeypatch):
    """Regression: converting to UTC made Breeze stop each request at 10:00 AM IST."""
    monkeypatch.setattr(bz, "today_ist", lambda: date(2026, 11, 20))
    by_day = bz.history(_client(cfg), "COFORGE", days=7, minutes=15, path=cfg)
    for d, bars in by_day.items():
        assert bars[-1]["time"].endswith("15:30:00"), f"{d} stops at {bars[-1]['time']}"
        assert len(bars) == 25
    assert bz._iso(datetime(2026, 11, 20, 15, 30, tzinfo=it.IST)) == "2026-11-20T15:30:00.000Z"
