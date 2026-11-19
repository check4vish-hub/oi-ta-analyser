import io
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stock_analyser import market_context as mc

BHAV = (" SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, "
        "CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        "COFORGE, EQ, 18-Nov-2026, 2000.00, 2001, 2060, 1990, 2049, 2049.00, 2030, 900000, 1, 1, 540000, 60.00\n"
        "COFORGE, BE, 18-Nov-2026, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1\n"
        "TCS, EQ, 18-Nov-2026, 4000, 1, 1, 1, 1, 4010, 1, 1, 1, 1, 1, -\n")


# ------------------------------------------------------------------ parsing
def test_parse_bhav_delivery():
    r = mc.parse_bhav_delivery(BHAV, "coforge")
    assert r == {"date": "2026-11-18", "close": 2049.0, "prev_close": 2000.0,
                 "deliv_pct": 60.0, "volume": 900000.0}
    assert mc.parse_bhav_delivery(BHAV, "TCS")["deliv_pct"] is None
    assert mc.parse_bhav_delivery(BHAV, "INFY") is None


def test_parse_secban():
    txt = "Securities in Ban For Trade Date 19-NOV-2026:\n1,RBLBANK\n2,SAIL\n"
    assert mc.parse_secban(txt) == ["RBLBANK", "SAIL"]
    assert mc.parse_secban("Securities in Ban For Trade Date 19-NOV-2026: NIL\n") == []


def test_parse_combineoi_prefers_future_equivalent_oi():
    txt = ("Date,ISIN,Scrip Name,NSE Symbol,MWPL,Open Interest,NCL FutEq OI,Limit for Next Day\n"
           "18-Nov-2026,INE591G01017,COFORGE LTD,COFORGE,10000000,9000000,4500000,No Fresh Positions\n")
    r = mc.parse_combineoi(txt, "COFORGE")
    assert r["used_pct"] == 45.0 and r["basis"] == "Future-equivalent OI"
    old = "Date,ISIN,Scrip Name,NSE Symbol,MWPL,Open Interest,Limit for Next Day\n" \
          "18-Nov-2026,X,COFORGE LTD,COFORGE,10000000,8200000,x\n"
    assert mc.parse_combineoi(old, "COFORGE")["used_pct"] == 82.0


def test_days_to_expiry():
    assert mc.days_to_expiry("25-Nov-2026", date(2026, 11, 19)) == 6
    assert mc.days_to_expiry("bad") is None


# ------------------------------------------------------------------ fetcher with fake network
class FakeNSE:
    timeout = 5

    def _warm_up(self):
        pass


def test_fetcher_mwpl_and_delivery(tmp_path, monkeypatch):
    f = mc.ContextFetcher(FakeNSE(), cache_dir=tmp_path)
    csv_txt = "Date,ISIN,Scrip Name,NSE Symbol,MWPL,Open Interest\nx,x,x,COFORGE,100,30\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("combineoi_x.csv", csv_txt)
    calls = []

    def fake_get(url):
        calls.append(url)
        if url.endswith(".zip"):
            return buf.getvalue()
        return BHAV.encode()
    monkeypatch.setattr(f, "_get", fake_get)
    assert f.mwpl("COFORGE")["used_pct"] == 30.0
    rows = f.delivery_history("COFORGE", days=3)
    assert len(rows) == 1 or all(r["deliv_pct"] == 60.0 for r in rows)
    assert any("sec_bhavdata_full_" in c for c in calls)


# ------------------------------------------------------------------ signals
def test_basis_signal():
    assert mc.basis_signal(100, 99.5, 20)["score"] == -1                 # discount
    assert mc.basis_signal(100, 101.0, 20)["score"] == 1                 # 18% p.a. premium
    assert mc.basis_signal(100, 100.36, 20)["score"] == 0                # ~6.6% p.a. normal


def test_rollover_signal():
    curve = [{"expiry": "a", "price": 100, "oi": 30}, {"expiry": "b", "price": 101, "oi": 70}]
    assert mc.rollover_signal(curve, 15)["score"] is None                 # mid-series
    assert mc.rollover_signal(curve, 3)["score"] == 1                     # rolled at rich premium
    curve[1]["price"] = 99.5
    assert mc.rollover_signal(curve, 3)["score"] == -1                    # rolled at discount


def test_delivery_signal():
    base = [{"date": f"d{i}", "close": 100, "prev_close": 100, "deliv_pct": 40} for i in range(5)]
    up = base + [{"date": "d9", "close": 103, "prev_close": 100, "deliv_pct": 62}]
    dn = base + [{"date": "d9", "close": 97, "prev_close": 100, "deliv_pct": 62}]
    low = base + [{"date": "d9", "close": 103, "prev_close": 100, "deliv_pct": 25}]
    assert mc.delivery_signal(up)["score"] == 1
    assert mc.delivery_signal(dn)["score"] == -1
    assert "speculative" in mc.delivery_signal(low)["note"]


def test_rs_signal():
    idx = pd.bdate_range("2026-01-01", periods=100)
    bench = pd.Series(np.linspace(100, 110, 100), idx)
    strong = pd.Series(np.linspace(100, 140, 100), idx)
    weak = pd.Series(np.linspace(100, 101, 100), idx)
    assert mc.rs_signal(strong, bench, "Nifty 50")["score"] == 1
    assert mc.rs_signal(weak, bench, "Nifty 50")["score"] == -1


def test_volatility_signals_are_context_only():
    idx = pd.bdate_range("2025-01-01", periods=260)
    vix = pd.Series(np.r_[np.full(250, 13.0), np.linspace(13, 19, 10)], idx)
    v = mc.vix_signal(vix)
    assert v["score"] is None and "Fear" in v["note"]
    assert "expensive" in mc.iv_hv_signal(40, 25)["note"]
    assert "cheap" in mc.iv_hv_signal(18, 25)["note"]
    assert "sellers" in mc.straddle_signal(5.0, 20, 10)["note"]
    assert mc.mwpl_signal({"used_pct": 88.0}, False)["note"].startswith("Near")
    assert "BAN" in mc.mwpl_signal(None, True)["value"]


def test_iv_log_and_percentile(tmp_path):
    p = tmp_path / "iv.csv"
    h = mc.log_iv("ABC", 30.0, p)
    h = mc.log_iv("ABC", 31.0, p)            # same day overwrites
    assert len(h[h.symbol == "ABC"]) == 1
    hist = pd.DataFrame({"date": range(30), "symbol": "ABC", "atm_iv": np.linspace(20, 40, 30)})
    assert mc.iv_percentile_signal(hist, "ABC", 39)["value"].startswith("9")
    assert "building" in mc.iv_percentile_signal(hist.head(5), "ABC", 30)["value"]


def test_combine_ignores_context_signals():
    from stock_analyser.signals import combine
    tech = [{"score": 1}, {"score": None}]
    oi = [{"score": None}]
    c = combine(tech, oi)
    assert c["technical"] == 100 and c["oi"] is None
