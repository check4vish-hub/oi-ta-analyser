"""
Technical indicators written in plain pandas (no TA-Lib install needed).

Input: DataFrame indexed by date with columns Open, High, Low, Close, Volume.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- data
def load_prices(symbol: str, period: str = "2y") -> pd.DataFrame:
    """Daily OHLCV from Yahoo Finance. NSE stocks get the '.NS' suffix automatically."""
    import yfinance as yf

    index_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
                 "MIDCPNIFTY": "NIFTY_MID_SELECT.NS"}
    s = symbol.upper()
    ticker = index_map.get(s, s if "." in s or s.startswith("^") else f"{s}.NS")
    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if df.empty:
        raise ValueError(f"No price data for {ticker}. Check the NSE symbol.")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def load_prices_csv(path: str) -> pd.DataFrame:
    """CSV with Date, Open, High, Low, Close, Volume columns (any capitalisation)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().title() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True)
    return df.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]]


# --------------------------------------------------------------------------- indicators
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(),
                    (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14):
    up, dn = df["High"].diff(), -df["Low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    plus_di = 100 * pd.Series(plus_dm, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), plus_di, minus_di


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0):
    """Returns (supertrend line, direction) where direction is +1 up-trend / -1 down-trend."""
    hl2 = (df["High"] + df["Low"]) / 2
    a = atr(df, n)
    upper, lower = (hl2 + mult * a).to_numpy(), (hl2 - mult * a).to_numpy()
    close = df["Close"].to_numpy()
    fu, fl = upper.copy(), lower.copy()
    direction = np.ones(len(df))
    st = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        if np.isnan(a.iloc[i]):
            continue
        if np.isnan(fu[i - 1]):  # first bar after the ATR warm-up
            direction[i] = 1 if close[i] >= hl2.iloc[i] else -1
            st[i] = fl[i] if direction[i] == 1 else fu[i]
            continue
        fu[i] = upper[i] if (upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if close[i] > fu[i - 1]:
            direction[i] = 1
        elif close[i] < fl[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        st[i] = fl[i] if direction[i] == 1 else fu[i]
    return pd.Series(st, df.index), pd.Series(direction, df.index)


def pivots(df: pd.DataFrame) -> dict:
    """Classic floor pivots from the previous completed session."""
    h, l, c = df["High"].iloc[-1], df["Low"].iloc[-1], df["Close"].iloc[-1]
    p = (h + l + c) / 3
    return {"R2": p + (h - l), "R1": 2 * p - l, "P": p, "S1": 2 * p - h, "S2": p - (h - l)}


def swing_levels(df: pd.DataFrame, lookback: int = 120, window: int = 5, n: int = 3):
    """Recent swing highs above price (resistance) and swing lows below price (support)."""
    d = df.tail(lookback)
    hi, lo, last = d["High"], d["Low"], d["Close"].iloc[-1]
    is_hi = hi == hi.rolling(2 * window + 1, center=True).max()
    is_lo = lo == lo.rolling(2 * window + 1, center=True).min()
    def merge(levels, gap=0.01):
        out = []
        for v in levels:  # levels are ordered nearest-to-price first
            if all(abs(v - u) / u > gap for u in out):
                out.append(v)
        return out[:n]

    res = merge(sorted(round(v, 2) for v in hi[is_hi] if v > last))
    sup = merge(sorted((round(v, 2) for v in lo[is_lo] if v < last), reverse=True))
    return sup, res


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["Close"]
    for n in (20, 50, 200):
        df[f"SMA{n}"] = c.rolling(n).mean()
    for n in (9, 21):
        df[f"EMA{n}"] = c.ewm(span=n, adjust=False).mean()
    df["RSI"] = rsi(c)
    df["MACD"], df["MACD_signal"], df["MACD_hist"] = macd(c)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["BB_upper"], df["BB_mid"], df["BB_lower"] = mid + 2 * sd, mid, mid - 2 * sd
    df["ATR"] = atr(df)
    df["ADX"], df["+DI"], df["-DI"] = adx(df)
    df["Supertrend"], df["ST_dir"] = supertrend(df)
    df["Vol_avg20"] = df["Volume"].rolling(20).mean()
    return df


# --------------------------------------------------------------------------- signals
def _sig(name, value, score, note):
    return {"group": "Technical", "name": name, "value": value, "score": score, "note": note}


def technical_signals(df: pd.DataFrame) -> list[dict]:
    """Score the latest bar. Needs ~200 bars for the 200-DMA check."""
    x, prev = df.iloc[-1], df.iloc[-2]
    c = x["Close"]
    s = []

    # Trend: price vs moving averages
    if not np.isnan(x["SMA200"]):
        sc = 1 if c > x["SMA200"] else -1
        s.append(_sig("Price vs 200-DMA", f"{c:.2f} vs {x['SMA200']:.2f}", sc,
                      "Long-term uptrend" if sc > 0 else "Long-term downtrend"))
    if not np.isnan(x["SMA50"]) and not np.isnan(x["SMA200"]):
        sc = 1 if x["SMA50"] > x["SMA200"] else -1
        cross = ""
        p50, p200 = df["SMA50"].iloc[-6], df["SMA200"].iloc[-6]
        if (p50 <= p200) and sc > 0:
            cross = " - fresh Golden Cross"
        elif (p50 >= p200) and sc < 0:
            cross = " - fresh Death Cross"
        s.append(_sig("50-DMA vs 200-DMA", "50 above 200" if sc > 0 else "50 below 200", sc,
                      ("Bullish alignment" if sc > 0 else "Bearish alignment") + cross))
    sc = 1 if x["EMA9"] > x["EMA21"] else -1
    s.append(_sig("EMA 9 / 21", f"{x['EMA9']:.2f} / {x['EMA21']:.2f}", sc,
                  "Short-term momentum up" if sc > 0 else "Short-term momentum down"))

    # Supertrend
    sc = int(x["ST_dir"])
    flip = " (just flipped)" if x["ST_dir"] != prev["ST_dir"] else ""
    s.append(_sig("Supertrend (10,3)", f"{x['Supertrend']:.2f}", sc,
                  ("Buy mode" if sc > 0 else "Sell mode") + flip))

    # RSI
    r = x["RSI"]
    if r >= 70:
        sc, note = 0, "Overbought - strong but stretched"
    elif r >= 55:
        sc, note = 1, "Bullish momentum zone"
    elif r <= 30:
        sc, note = 0, "Oversold - weak but stretched"
    elif r <= 45:
        sc, note = -1, "Bearish momentum zone"
    else:
        sc, note = 0, "Neutral"
    s.append(_sig("RSI (14)", round(r, 1), sc, note))

    # MACD
    sc = 1 if x["MACD"] > x["MACD_signal"] else -1
    cross = " - fresh crossover" if np.sign(x["MACD_hist"]) != np.sign(prev["MACD_hist"]) else ""
    s.append(_sig("MACD (12,26,9)", f"{x['MACD']:.2f} vs signal {x['MACD_signal']:.2f}", sc,
                  ("Above signal" if sc > 0 else "Below signal") + cross))

    # ADX trend strength (direction from DI)
    if not np.isnan(x["ADX"]):
        if x["ADX"] >= 25:
            sc = 1 if x["+DI"] > x["-DI"] else -1
            note = f"Strong trend ({'+DI' if sc > 0 else '-DI'} leading)"
        else:
            sc, note = 0, "Weak / sideways trend"
        s.append(_sig("ADX (14)", round(x["ADX"], 1), sc, note))

    # Bollinger position
    bw = x["BB_upper"] - x["BB_lower"]
    if bw > 0:
        pos = (c - x["BB_lower"]) / bw
        note = ("At/above upper band - breakout or stretched" if pos > 1 else
                "At/below lower band - breakdown or stretched" if pos < 0 else
                "Upper half of the band" if pos >= 0.6 else
                "Lower half of the band" if pos <= 0.4 else "Middle of the band")
        sc = 1 if 0.6 <= pos <= 1 else -1 if 0 <= pos <= 0.4 else 0
        s.append(_sig("Bollinger %B", round(pos, 2), sc, note))

    # Volume confirmation
    if x["Vol_avg20"] > 0:
        ratio = x["Volume"] / x["Vol_avg20"]
        day_up = c >= prev["Close"]
        sc = (1 if day_up else -1) if ratio >= 1.5 else 0
        s.append(_sig("Volume vs 20-day avg", f"{ratio:.1f}x", sc,
                      ("High-volume up day" if day_up else "High-volume down day")
                      if ratio >= 1.5 else "Normal participation"))

    # 52-week position
    yr = df.tail(252)
    hi52, lo52 = yr["High"].max(), yr["Low"].min()
    pos52 = (c - lo52) / (hi52 - lo52) if hi52 > lo52 else 0.5
    sc = 1 if pos52 >= 0.8 else -1 if pos52 <= 0.2 else 0
    s.append(_sig("52-week range", f"{lo52:.2f} - {hi52:.2f}", sc,
                  f"Trading at {pos52 * 100:.0f}% of the 52-week range"))
    return s
