"""
Open Interest (OI) analysis for NSE options and futures.

What it calculates
------------------
* Put-Call Ratio (PCR) on total OI and on today's change in OI
* Max Pain strike (where option buyers collectively lose the most)
* OI-based support (heaviest put writing) and resistance (heaviest call writing)
* Fresh OI additions today (where writers are adding positions right now)
* ATM straddle price and IV -> market-implied move until expiry
* Futures build-up: Long build-up / Short build-up / Short covering / Long unwinding
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

COLUMNS = [
    "strike",
    "ce_oi", "ce_chg_oi", "ce_vol", "ce_iv", "ce_ltp", "ce_ltp_chg",
    "pe_oi", "pe_chg_oi", "pe_vol", "pe_iv", "pe_ltp", "pe_ltp_chg",
]


@dataclass
class OptionChain:
    symbol: str
    expiry: str
    spot: float
    df: pd.DataFrame          # one row per strike, columns = COLUMNS
    timestamp: str = ""


@dataclass
class OIResult:
    symbol: str
    expiry: str
    spot: float
    pcr_oi: float
    pcr_chg_oi: float | None
    max_pain: float
    atm_strike: float
    atm_iv: float | None
    straddle: float | None
    expected_move_pct: float | None
    supports: list[tuple[float, float]]        # (strike, put OI)
    resistances: list[tuple[float, float]]     # (strike, call OI)
    fresh_put_writing: list[tuple[float, float]]
    fresh_call_writing: list[tuple[float, float]]
    signals: list[dict] = field(default_factory=list)
    futures: dict | None = None


# --------------------------------------------------------------------------- parsing
def _num(x) -> float:
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x) if not (isinstance(x, float) and np.isnan(x)) else 0.0
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "--"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_nse_json(data: dict, symbol: str = "", expiry: str | None = None) -> OptionChain:
    """Parse NSE option-chain JSON (v3 or legacy) into an OptionChain."""
    records = data.get("records", data)
    rows = records.get("data") or data.get("filtered", {}).get("data") or []
    expiry = expiry or data.get("_requested_expiry")

    # legacy endpoints return all expiries mixed together -> keep one
    key = next((k for k in ("expiryDate", "expiryDates") if rows and k in rows[0]), None)
    if key:
        available = sorted({r[key] for r in rows if r.get(key)},
                           key=lambda d: datetime.strptime(d, "%d-%b-%Y"))
        if available:
            expiry = expiry if expiry in available else available[0]
            rows = [r for r in rows if r.get(key) == expiry]

    out = []
    spot = _num(records.get("underlyingValue"))
    for r in rows:
        ce, pe = r.get("CE") or {}, r.get("PE") or {}
        if not spot:
            spot = _num(ce.get("underlyingValue") or pe.get("underlyingValue"))
        out.append({
            "strike": _num(r.get("strikePrice")),
            "ce_oi": _num(ce.get("openInterest")),
            "ce_chg_oi": _num(ce.get("changeinOpenInterest")),
            "ce_vol": _num(ce.get("totalTradedVolume")),
            "ce_iv": _num(ce.get("impliedVolatility")),
            "ce_ltp": _num(ce.get("lastPrice")),
            "ce_ltp_chg": _num(ce.get("change")),
            "pe_oi": _num(pe.get("openInterest")),
            "pe_chg_oi": _num(pe.get("changeinOpenInterest")),
            "pe_vol": _num(pe.get("totalTradedVolume")),
            "pe_iv": _num(pe.get("impliedVolatility")),
            "pe_ltp": _num(pe.get("lastPrice")),
            "pe_ltp_chg": _num(pe.get("change")),
        })
    if not out:
        raise ValueError("Option chain JSON had no strike rows - NSE may have blocked the request.")
    df = pd.DataFrame(out, columns=COLUMNS).groupby("strike", as_index=False).sum()
    return OptionChain(symbol=symbol.upper(), expiry=expiry or "", spot=spot,
                       df=df.sort_values("strike").reset_index(drop=True),
                       timestamp=records.get("timestamp", ""))


def parse_nse_csv(path_or_text: str, symbol: str = "", spot: float | None = None,
                  expiry: str = "") -> OptionChain:
    """Parse the CSV you get from the 'Download (.csv)' link on nseindia.com/option-chain.

    Layout (21 columns): CALLS side (OI, CHNG IN OI, VOLUME, IV, LTP, CHNG, BID QTY,
    BID, ASK, ASK QTY) | STRIKE | PUTS side mirrored (BID QTY, BID, ASK, ASK QTY,
    CHNG, LTP, IV, VOLUME, CHNG IN OI, OI). A leading blank column is tolerated.
    """
    if "\n" in path_or_text:
        text = path_or_text
    else:
        with open(path_or_text, encoding="utf-8-sig") as f:
            text = f.read()
    rows = list(csv.reader(io.StringIO(text)))

    # find the header row containing STRIKE
    hdr_idx = next(i for i, r in enumerate(rows) if any(c.strip().upper() == "STRIKE" for c in r))
    hdr = [c.strip().upper() for c in rows[hdr_idx]]
    k = hdr.index("STRIKE")
    out = []
    for r in rows[hdr_idx + 1:]:
        if len(r) <= k + 10 or not str(r[k]).strip():
            continue
        g = lambda i: _num(r[i]) if 0 <= i < len(r) else 0.0  # noqa: E731
        strike = g(k)
        if strike <= 0:
            continue
        out.append({
            "strike": strike,
            "ce_oi": g(k - 10), "ce_chg_oi": g(k - 9), "ce_vol": g(k - 8),
            "ce_iv": g(k - 7), "ce_ltp": g(k - 6), "ce_ltp_chg": g(k - 5),
            "pe_ltp_chg": g(k + 5), "pe_ltp": g(k + 6), "pe_iv": g(k + 7),
            "pe_vol": g(k + 8), "pe_chg_oi": g(k + 9), "pe_oi": g(k + 10),
        })
    df = pd.DataFrame(out, columns=COLUMNS).sort_values("strike").reset_index(drop=True)
    if spot is None:
        # estimate spot from put-call parity near the money: strike where CE LTP ~ PE LTP
        priced = df[(df.ce_ltp > 0) & (df.pe_ltp > 0)]
        if priced.empty:
            raise ValueError("Pass --spot: could not infer the underlying price from the CSV.")
        i = (priced.ce_ltp - priced.pe_ltp).abs().idxmin()
        spot = float(df.loc[i, "strike"] + df.loc[i, "ce_ltp"] - df.loc[i, "pe_ltp"])
    return OptionChain(symbol=symbol.upper(), expiry=expiry, spot=float(spot), df=df)


def parse_futures(data: dict) -> dict | None:
    """Pick the near-month stock/index future from NSE quote-derivative JSON."""
    futs = []
    for s in data.get("stocks", []):
        md = s.get("metadata", {})
        if "Futures" not in md.get("instrumentType", ""):
            continue
        ti = (s.get("marketDeptOrderBook") or {}).get("tradeInfo", {})
        try:
            exp = datetime.strptime(md.get("expiryDate", ""), "%d-%b-%Y")
        except ValueError:
            continue
        futs.append({
            "expiry": md.get("expiryDate"),
            "_exp": exp,
            "last_price": _num(md.get("lastPrice")),
            "price_change": _num(md.get("change")),
            "price_change_pct": _num(md.get("pChange")),
            "oi": _num(ti.get("openInterest")),
            "oi_change": _num(ti.get("changeinOpenInterest")),
            "oi_change_pct": _num(ti.get("pchangeinOpenInterest")),
        })
    if not futs:
        return None
    near = min(futs, key=lambda f: f["_exp"])
    near.pop("_exp")
    near["buildup"] = classify_buildup(near["price_change"], near["oi_change"])
    return near


# --------------------------------------------------------------------------- analytics
def classify_buildup(price_change: float, oi_change: float) -> str:
    """Classic price-vs-OI interpretation."""
    if price_change > 0 and oi_change > 0:
        return "Long build-up"
    if price_change < 0 and oi_change > 0:
        return "Short build-up"
    if price_change > 0 and oi_change < 0:
        return "Short covering"
    if price_change < 0 and oi_change < 0:
        return "Long unwinding"
    return "No clear build-up"


def max_pain(df: pd.DataFrame) -> float:
    """Strike at which total intrinsic value paid to option holders is minimum."""
    strikes = df["strike"].to_numpy()
    ce, pe = df["ce_oi"].to_numpy(), df["pe_oi"].to_numpy()
    # pain[i] = total payout if expiry settles at strikes[i]
    settle = strikes[:, None]
    payout = (np.maximum(settle - strikes, 0) * ce).sum(axis=1) + \
             (np.maximum(strikes - settle, 0) * pe).sum(axis=1)
    return float(strikes[int(np.argmin(payout))])


def _top(df: pd.DataFrame, col: str, n: int = 3) -> list[tuple[float, float]]:
    t = df[df[col] > 0].nlargest(n, col)
    return [(float(s), float(v)) for s, v in zip(t["strike"], t[col])]


def analyse_option_chain(chain: OptionChain, futures: dict | None = None,
                         strikes_each_side: int = 15) -> OIResult:
    df_all = chain.df
    spot = chain.spot
    atm_idx = (df_all["strike"] - spot).abs().idxmin()
    atm = float(df_all.loc[atm_idx, "strike"])

    # focus on strikes near the money - far OTM strikes add noise
    lo, hi = max(0, atm_idx - strikes_each_side), atm_idx + strikes_each_side + 1
    df = df_all.iloc[lo:hi]

    ce_tot, pe_tot = df_all["ce_oi"].sum(), df_all["pe_oi"].sum()
    pcr_oi = round(pe_tot / ce_tot, 2) if ce_tot else float("nan")
    ce_chg, pe_chg = df_all["ce_chg_oi"].sum(), df_all["pe_chg_oi"].sum()
    pcr_chg = round(pe_chg / ce_chg, 2) if ce_chg > 0 and pe_chg >= 0 else None

    row = df_all.loc[atm_idx]
    ivs = [v for v in (row.ce_iv, row.pe_iv) if v > 0]
    atm_iv = round(float(np.mean(ivs)), 1) if ivs else None
    straddle = float(row.ce_ltp + row.pe_ltp) if row.ce_ltp > 0 and row.pe_ltp > 0 else None
    exp_move = round(straddle / spot * 100, 2) if straddle and spot else None

    supports = _top(df[df.strike <= spot * 1.01], "pe_oi")
    resistances = _top(df[df.strike >= spot * 0.99], "ce_oi")

    res = OIResult(
        symbol=chain.symbol, expiry=chain.expiry, spot=spot,
        pcr_oi=pcr_oi, pcr_chg_oi=pcr_chg, max_pain=max_pain(df_all),
        atm_strike=atm, atm_iv=atm_iv, straddle=straddle, expected_move_pct=exp_move,
        supports=supports, resistances=resistances,
        fresh_put_writing=_top(df, "pe_chg_oi"),
        fresh_call_writing=_top(df, "ce_chg_oi"),
        futures=futures,
    )
    res.signals = oi_signals(res)
    return res


def oi_signals(r: OIResult) -> list[dict]:
    """Turn OI numbers into scored signals: +1 bullish, -1 bearish, 0 neutral."""
    s = []

    # PCR
    if r.pcr_oi != r.pcr_oi:  # NaN
        pass
    elif r.pcr_oi >= 1.7:
        s.append(_sig("PCR (OI)", r.pcr_oi, 0, "Extremely high - heavy put writing, but crowded; "
                      "contrarian risk of a pullback"))
    elif r.pcr_oi >= 1.2:
        s.append(_sig("PCR (OI)", r.pcr_oi, 1, "Put writers dominant - supportive"))
    elif r.pcr_oi <= 0.5:
        s.append(_sig("PCR (OI)", r.pcr_oi, 0, "Extremely low - call writers dominant, "
                      "but oversold; contrarian bounce risk"))
    elif r.pcr_oi <= 0.7:
        s.append(_sig("PCR (OI)", r.pcr_oi, -1, "Call writers dominant - capping upside"))
    else:
        s.append(_sig("PCR (OI)", r.pcr_oi, 0, "Balanced"))

    # Today's change in OI
    if r.pcr_chg_oi is not None:
        sc = 1 if r.pcr_chg_oi > 1.2 else -1 if r.pcr_chg_oi < 0.8 else 0
        s.append(_sig("PCR (change in OI today)", r.pcr_chg_oi, sc,
                      {1: "More puts written today - bulls adding",
                       -1: "More calls written today - bears adding",
                       0: "Fresh writing balanced"}[sc]))

    # Max pain vs spot
    gap = (r.max_pain - r.spot) / r.spot * 100
    sc = 1 if gap > 1.5 else -1 if gap < -1.5 else 0
    s.append(_sig("Max pain", f"{r.max_pain:g} ({gap:+.1f}% vs spot)", sc,
                  "Spot tends to drift toward max pain near expiry (weak pull mid-series)"))

    # Distance to OI walls
    if r.supports and r.resistances:
        sup, resi = r.supports[0][0], r.resistances[0][0]
        room_up = (resi - r.spot) / r.spot * 100
        room_dn = (r.spot - sup) / r.spot * 100
        sc = 1 if room_up > 2 * max(room_dn, 0.1) else -1 if room_dn > 2 * max(room_up, 0.1) else 0
        s.append(_sig("OI range", f"Support {sup:g} / Resistance {resi:g}", sc,
                      f"{room_dn:.1f}% to put wall, {room_up:.1f}% to call wall"))

    # Futures build-up
    if r.futures:
        b = r.futures["buildup"]
        sc = {"Long build-up": 1, "Short covering": 1,
              "Short build-up": -1, "Long unwinding": -1}.get(b, 0)
        s.append(_sig("Futures build-up", b, sc,
                      f"Price {r.futures['price_change_pct']:+.2f}%, "
                      f"OI {r.futures['oi_change_pct']:+.2f}%"))
    return s


def _sig(name, value, score, note):
    return {"group": "Open Interest", "name": name, "value": value, "score": score, "note": note}
