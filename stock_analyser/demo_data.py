"""Synthetic price history and option chain so the tool can be tried offline (--demo)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .open_interest import COLUMNS, OptionChain


def demo_prices(days: int = 400, start: float = 1400.0, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0006, 0.015, days)
    close = start * np.exp(np.cumsum(ret))
    open_ = np.r_[start, close[:-1]] * (1 + rng.normal(0, 0.003, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, days)))
    vol = rng.integers(3_000_000, 9_000_000, days).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": vol}, index=idx)


def demo_chain(spot: float, step: float = 20, n: int = 25, seed: int = 3) -> OptionChain:
    rng = np.random.default_rng(seed)
    atm = round(spot / step) * step
    strikes = atm + step * np.arange(-n, n + 1)
    dist = (strikes - spot) / spot
    ce_oi = (np.exp(-((dist - 0.03) / 0.04) ** 2) * 900_000 + rng.integers(5e3, 6e4, len(strikes)))
    pe_oi = (np.exp(-((dist + 0.03) / 0.04) ** 2) * 1_100_000 + rng.integers(5e3, 6e4, len(strikes)))
    iv = 22 + 40 * dist ** 2 * 10
    t = 10 / 365
    ce_ltp = np.maximum(spot - strikes, 0) + spot * iv / 100 * np.sqrt(t) * 0.4 * np.exp(-abs(dist) * 15)
    pe_ltp = np.maximum(strikes - spot, 0) + spot * iv / 100 * np.sqrt(t) * 0.4 * np.exp(-abs(dist) * 15)
    df = pd.DataFrame({
        "strike": strikes,
        "ce_oi": ce_oi.round(), "ce_chg_oi": (ce_oi * rng.normal(0.05, 0.1, len(strikes))).round(),
        "ce_vol": (ce_oi * 2).round(), "ce_iv": iv.round(1), "ce_ltp": ce_ltp.round(2), "ce_ltp_chg": 0.0,
        "pe_oi": pe_oi.round(), "pe_chg_oi": (pe_oi * rng.normal(0.08, 0.1, len(strikes))).round(),
        "pe_vol": (pe_oi * 2).round(), "pe_iv": iv.round(1), "pe_ltp": pe_ltp.round(2), "pe_ltp_chg": 0.0,
    })[COLUMNS]
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=6)).strftime("%d-%b-%Y")
    return OptionChain(symbol="DEMO", expiry=expiry, spot=float(spot), df=df)


def demo_futures(spot: float) -> dict:
    """Synthetic near/next/far futures with a normal carry premium."""
    from datetime import date, timedelta
    from .open_interest import classify_buildup

    today = date.today()
    exps = [today + timedelta(days=d) for d in (6, 34, 62)]
    prices = [spot * (1 + 0.065 * d / 365) for d in (6, 34, 62)]
    ois = [42_000, 21_000, 2_500]
    curve = [{"expiry": e.strftime("%d-%b-%Y"), "price": round(p, 2), "oi": o}
             for e, p, o in zip(exps, prices, ois)]
    return {"expiry": curve[0]["expiry"], "last_price": curve[0]["price"], "price_change": 4.2,
            "price_change_pct": 0.44, "oi": ois[0], "oi_change": 1_800, "oi_change_pct": 4.5,
            "buildup": classify_buildup(4.2, 1_800), "curve": curve}


def demo_context(prices: pd.DataFrame, seed: int = 11) -> dict:
    """Synthetic Nifty, sector index, India VIX, delivery and MWPL data."""
    rng = np.random.default_rng(seed)
    idx = pd.to_datetime(prices.index).normalize()
    n = len(idx)
    nifty = pd.Series(24000 * np.exp(np.cumsum(rng.normal(0.0004, 0.009, n))), idx)
    sector = pd.Series(38000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n))), idx)
    vix = pd.Series(np.clip(14 + np.cumsum(rng.normal(0, 0.4, n)), 9, 30), idx)
    tail = prices.tail(11)
    delivery = [{"date": d.date().isoformat(), "close": float(c), "prev_close": float(p),
                 "deliv_pct": float(dp), "volume": float(v)}
                for d, c, p, dp, v in zip(tail.index, tail.Close, tail.Close.shift().bfill(),
                                          rng.uniform(35, 55, len(tail)), tail.Volume)]
    return {"nifty": nifty, "sector_series": sector, "sector": "DEMO-SECTOR", "vix": vix,
            "delivery": delivery, "mwpl": {"mwpl": 1e8, "oi": 4.6e7, "used_pct": 46.0,
                                           "basis": "Future-equivalent OI"}}
