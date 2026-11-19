import numpy as np
import pandas as pd
import pytest

from stock_analyser import open_interest as oi
from stock_analyser import technicals as ta
from stock_analyser.demo_data import demo_chain, demo_prices
from stock_analyser.signals import combine, verdict


# ---------------------------------------------------------------- open interest
def _chain(rows, spot):
    df = pd.DataFrame(rows, columns=["strike", "ce_oi", "pe_oi"])
    for c in oi.COLUMNS:
        if c not in df:
            df[c] = 0.0
    return oi.OptionChain("TEST", "X", spot, df[oi.COLUMNS])


def test_max_pain_hand_calculated():
    # strikes 90/100/110. Payout if settle at 100: calls@90 -> 10*100=1000, puts@110 -> 10*100=1000
    ch = _chain([(90, 100, 0), (100, 50, 50), (110, 0, 100)], 100)
    assert oi.max_pain(ch.df) == 100


def test_pcr_and_walls():
    ch = _chain([(90, 10, 500), (100, 100, 100), (110, 600, 20)], 100)
    r = oi.analyse_option_chain(ch)
    assert r.pcr_oi == round(620 / 710, 2)
    assert r.supports[0][0] == 90
    assert r.resistances[0][0] == 110


@pytest.mark.parametrize("p,o,label", [(1, 1, "Long build-up"), (-1, 1, "Short build-up"),
                                       (1, -1, "Short covering"), (-1, -1, "Long unwinding")])
def test_buildup(p, o, label):
    assert oi.classify_buildup(p, o) == label


def test_parse_nse_json_v3_shape():
    data = {"records": {"underlyingValue": 1500, "timestamp": "19-Nov-2026 15:30:00", "data": [
        {"strikePrice": 1480, "expiryDates": "25-Nov-2026",
         "CE": {"openInterest": 100, "changeinOpenInterest": 10, "lastPrice": 30, "impliedVolatility": 20},
         "PE": {"openInterest": 400, "changeinOpenInterest": 50, "lastPrice": 8, "impliedVolatility": 21}},
        {"strikePrice": 1500, "expiryDates": "25-Nov-2026",
         "CE": {"openInterest": 300, "lastPrice": 15}, "PE": {"openInterest": 300, "lastPrice": 14}},
        {"strikePrice": 1520, "expiryDates": "25-Nov-2026",
         "CE": {"openInterest": 700, "lastPrice": 6}, "PE": {"openInterest": 50, "lastPrice": 26}},
    ]}}
    ch = oi.parse_nse_json(data, "abc")
    assert ch.spot == 1500 and ch.symbol == "ABC" and ch.expiry == "25-Nov-2026"
    assert list(ch.df.strike) == [1480, 1500, 1520]
    r = oi.analyse_option_chain(ch)
    assert r.straddle == 29 and r.resistances[0][0] == 1520


def test_parse_legacy_json_filters_nearest_expiry():
    row = lambda k, e, n: {"strikePrice": k, "expiryDate": e,  # noqa: E731
                           "CE": {"openInterest": n}, "PE": {"openInterest": n}}
    data = {"records": {"underlyingValue": 100, "data": [
        row(100, "30-Dec-2026", 999), row(100, "25-Nov-2026", 5), row(110, "25-Nov-2026", 7)]}}
    ch = oi.parse_nse_json(data)
    assert ch.expiry == "25-Nov-2026" and ch.df.ce_oi.sum() == 12


def test_parse_nse_csv():
    hdr = ",OI,CHNG IN OI,VOLUME,IV,LTP,CHNG,BID QTY,BID,ASK,ASK QTY,STRIKE," \
          "BID QTY,BID,ASK,ASK QTY,CHNG,LTP,IV,VOLUME,CHNG IN OI,OI,"
    r1 = ',"1,000",50,10,20,12.5,1,1,1,1,1,"1,480.00",1,1,1,1,1,2.5,22,10,"30","4,000",'
    r2 = ',"3,000",80,10,20,4,1,1,1,1,1,"1,500.00",1,1,1,1,1,4,22,10,"-",500,'
    text = "CALLS,,PUTS\n" + hdr + "\n" + r1 + "\n" + r2 + "\n"
    ch = oi.parse_nse_csv(text, "x")
    assert list(ch.df.strike) == [1480, 1500]
    assert ch.df.ce_oi.tolist() == [1000, 3000] and ch.df.pe_oi.tolist() == [4000, 500]
    assert ch.df.pe_chg_oi.tolist() == [30, 0]
    assert ch.spot == 1500  # CE == PE LTP at 1500


def test_parse_futures_picks_near_month():
    mk = lambda e, chg, oich: {"metadata": {"instrumentType": "Stock Futures", "expiryDate": e,  # noqa: E731
                                            "lastPrice": 100, "change": chg, "pChange": chg},
                               "marketDeptOrderBook": {"tradeInfo": {"openInterest": 1,
                                                                     "changeinOpenInterest": oich,
                                                                     "pchangeinOpenInterest": oich}}}
    data = {"stocks": [mk("30-Dec-2026", 1, 1), mk("25-Nov-2026", -2, 5),
                       {"metadata": {"instrumentType": "Stock Options"}}]}
    f = oi.parse_futures(data)
    assert f["expiry"] == "25-Nov-2026" and f["buildup"] == "Short build-up"


# ---------------------------------------------------------------- technicals
def test_rsi_bounds_and_trend():
    up = pd.Series(np.linspace(100, 200, 100))
    assert ta.rsi(up).iloc[-1] > 95
    down = pd.Series(np.linspace(200, 100, 100))
    assert ta.rsi(down).iloc[-1] < 5


def test_supertrend_direction_follows_trend():
    idx = pd.bdate_range("2025-01-01", periods=120)
    c = np.r_[np.linspace(100, 160, 60), np.linspace(160, 90, 60)]
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, idx)
    _, d = ta.supertrend(df)
    assert d.iloc[55] == 1 and d.iloc[-1] == -1


def test_full_pipeline_on_demo():
    df = ta.add_indicators(demo_prices())
    tech = ta.technical_signals(df)
    assert len(tech) >= 9 and all(s["score"] in (-1, 0, 1) for s in tech)
    r = oi.analyse_option_chain(demo_chain(float(df.Close.iloc[-1])))
    c = combine(tech, r.signals)
    assert -100 <= c["overall"] <= 100
    assert c["verdict"][0] in {"Bullish", "Mildly bullish", "Neutral", "Mildly bearish", "Bearish"}


def test_verdict_thresholds():
    assert verdict(60)[0] == "Bullish" and verdict(0)[0] == "Neutral" and verdict(-60)[0] == "Bearish"
