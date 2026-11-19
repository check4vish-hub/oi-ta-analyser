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
    return OptionChain(symbol="DEMO", expiry="DEMO-EXPIRY", spot=float(spot), df=df)
