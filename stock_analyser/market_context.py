"""
Extra market data that confirms or questions the main signals.

1. Derivatives positioning : futures basis (premium/discount), rollover %, roll cost,
                             MWPL usage and F&O ban status
2. Cash-market confirmation: delivery %, relative strength vs Nifty and sector
3. Volatility              : India VIX, stock IV vs its own historical volatility,
                             IV percentile (from a local log), straddle vs normal move

Every fetch is optional: if a source fails the tool keeps going and lists what it
skipped. The calculation functions are pure (no internet) so they are unit-tested.
"""
from __future__ import annotations

import csv
import io
import math
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ARCHIVES = "https://nsearchives.nseindia.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FAIR_CARRY_PCT = 6.5          # approx. annual cost of carry (risk-free rate) for futures

# Yahoo Finance sector -> NSE sector index (approximate; override with --sector-index)
SECTOR_INDEX = {
    "Technology": ("^CNXIT", "Nifty IT"),
    "Healthcare": ("^CNXPHARMA", "Nifty Pharma"),
    "Consumer Defensive": ("^CNXFMCG", "Nifty FMCG"),
    "Energy": ("^CNXENERGY", "Nifty Energy"),
    "Utilities": ("^CNXENERGY", "Nifty Energy"),
    "Real Estate": ("^CNXREALTY", "Nifty Realty"),
    "Industrials": ("^CNXINFRA", "Nifty Infra"),
    "Financial Services": ("NIFTY_FIN_SERVICE.NS", "Nifty Financial Services"),
}
INDUSTRY_INDEX = [  # checked first: keyword in Yahoo industry name
    ("Bank", ("^NSEBANK", "Nifty Bank")),
    ("Auto", ("^CNXAUTO", "Nifty Auto")),
    ("Steel", ("^CNXMETAL", "Nifty Metal")),
    ("Aluminum", ("^CNXMETAL", "Nifty Metal")),
    ("Copper", ("^CNXMETAL", "Nifty Metal")),
    ("Metal", ("^CNXMETAL", "Nifty Metal")),
    ("Mining", ("^CNXMETAL", "Nifty Metal")),
]


def _sig(group, name, value, score, note):
    """score: +1 bullish, -1 bearish, 0 neutral, None = context only (not scored)."""
    return {"group": group, "name": name, "value": value, "score": score, "note": note}


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return float("nan")


def days_to_expiry(expiry: str, today: date | None = None) -> int | None:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(expiry.strip(), fmt).date()
            return max((d - (today or date.today())).days, 0)
        except (ValueError, AttributeError):
            pass
    return None


# =========================================================================== fetching
class ContextFetcher:
    """Downloads NSE archive files (re-using the NSE session cookies) with a local cache."""

    def __init__(self, nse_client=None, cache_dir: Path = DATA_DIR / "cache"):
        if nse_client is None:
            from .nse_client import NSEClient
            nse_client = NSEClient()
        self.nse = nse_client
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)

    def _get(self, url: str) -> bytes:
        self.nse._warm_up()
        r = self.nse.session.get(url, timeout=self.nse.timeout,
                                 headers={"Referer": "https://www.nseindia.com/"})
        if r.status_code != 200 or not r.content:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        return r.content

    def _cached(self, name: str, url: str, keep: bool = True) -> bytes:
        path = self.cache / name
        if keep and path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
        data = self._get(url)
        if keep:
            path.write_bytes(data)
        return data

    @staticmethod
    def _recent_days(n: int, start: date | None = None):
        d = start or date.today()
        out = []
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d)
            d -= timedelta(days=1)
        return out

    def ban_list(self) -> list[str]:
        txt = self._get(f"{ARCHIVES}/content/fo/fo_secban.csv").decode("utf-8", "ignore")
        return parse_secban(txt)

    def mwpl(self, symbol: str) -> dict | None:
        """Latest market-wide OI vs MWPL for a stock (tries the last few trading days)."""
        for d in self._recent_days(6):
            tag = d.strftime("%d%m%Y")
            try:
                raw = self._cached(f"combineoi_{tag}.zip",
                                   f"{ARCHIVES}/archives/nsccl/mwpl/combineoi_{tag}.zip")
            except Exception:
                continue
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                txt = z.read(name).decode("utf-8", "ignore")
            row = parse_combineoi(txt, symbol)
            if row:
                row["date"] = d.isoformat()
                return row
        return None

    def delivery_history(self, symbol: str, days: int = 11) -> list[dict]:
        """Daily delivery % from NSE's full bhavcopy for the last ~`days` sessions."""
        rows = []
        for d in self._recent_days(days + 6):          # extra days to skip holidays
            tag = d.strftime("%d%m%Y")
            try:
                raw = self._cached(f"bhav_{tag}.csv",
                                   f"{ARCHIVES}/products/content/sec_bhavdata_full_{tag}.csv",
                                   keep=d < date.today())   # today's file may not be final
            except Exception:
                continue
            r = parse_bhav_delivery(raw.decode("utf-8", "ignore"), symbol)
            if r:
                rows.append(r)
            if len(rows) >= days:
                break
        return sorted(rows, key=lambda r: r["date"])


def load_yahoo(ticker: str, period: str = "1y") -> pd.Series:
    import yfinance as yf
    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if df.empty:
        raise ValueError(f"no data for {ticker}")
    s = df["Close"]
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s


def detect_sector_index(symbol: str) -> tuple[str, str] | None:
    import yfinance as yf
    try:
        info = yf.Ticker(f"{symbol.upper()}.NS").info or {}
    except Exception:
        return None
    industry, sector = info.get("industry", "") or "", info.get("sector", "") or ""
    for kw, idx in INDUSTRY_INDEX:
        if kw.lower() in industry.lower():
            return idx
    return SECTOR_INDEX.get(sector)


# =========================================================================== parsing
def parse_secban(text: str) -> list[str]:
    """fo_secban.csv: a title line then rows like '1,SYMBOL'."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].strip().isdigit():
            out.append(row[1].strip().upper())
    return out


def _norm(h: str) -> str:
    return "".join(ch for ch in h.lower() if ch.isalnum())


def parse_combineoi(text: str, symbol: str) -> dict | None:
    """combineoi_DDMMYYYY.csv -> MWPL utilisation for one symbol.

    Since 2025 NSE checks MWPL against Future-Equivalent OI when that column exists.
    """
    rdr = csv.reader(io.StringIO(text))
    header = [_norm(h) for h in next(rdr)]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if h == n or (len(n) > 4 and n in h):
                    return i
        return None

    i_sym, i_mwpl = col("nsesymbol", "symbol"), col("mwpl")
    i_futeq = col("futeqoi", "futureequivalent")
    i_oi = col("openinterest")
    if i_sym is None or i_mwpl is None:
        return None
    for r in rdr:
        if len(r) > i_sym and r[i_sym].strip().upper() == symbol.upper():
            mwpl = _num(r[i_mwpl])
            oi = _num(r[i_futeq]) if i_futeq is not None else float("nan")
            basis = "Future-equivalent OI"
            if not oi == oi:  # NaN
                oi, basis = _num(r[i_oi]) if i_oi is not None else float("nan"), "Open interest"
            if mwpl > 0 and oi == oi:
                return {"mwpl": mwpl, "oi": oi, "used_pct": round(oi / mwpl * 100, 1), "basis": basis}
    return None


def parse_bhav_delivery(text: str, symbol: str) -> dict | None:
    """sec_bhavdata_full_DDMMYYYY.csv -> close and delivery % for one EQ symbol."""
    rdr = csv.reader(io.StringIO(text))
    header = [h.strip().upper() for h in next(rdr)]
    ix = {h: i for i, h in enumerate(header)}
    need = ("SYMBOL", "SERIES", "DATE1", "CLOSE_PRICE", "PREV_CLOSE", "DELIV_PER")
    if not all(k in ix for k in need):
        return None
    for r in rdr:
        if len(r) < len(header):
            continue
        if r[ix["SYMBOL"]].strip().upper() == symbol.upper() and r[ix["SERIES"]].strip() == "EQ":
            dp = _num(r[ix["DELIV_PER"]])
            try:
                d = datetime.strptime(r[ix["DATE1"]].strip(), "%d-%b-%Y").date().isoformat()
            except ValueError:
                d = r[ix["DATE1"]].strip()
            return {"date": d, "close": _num(r[ix["CLOSE_PRICE"]]),
                    "prev_close": _num(r[ix["PREV_CLOSE"]]),
                    "deliv_pct": dp if dp == dp else None,
                    "volume": _num(r[ix["TTL_TRD_QNTY"]]) if "TTL_TRD_QNTY" in ix else None}
    return None


# =========================================================================== 1. positioning
def basis_signal(spot: float, fut_price: float, dte: int | None,
                 fair_pct: float = FAIR_CARRY_PCT) -> dict | None:
    if not spot or not fut_price:
        return None
    prem = (fut_price - spot) / spot * 100
    days = max(dte or 0, 1)
    ann = prem * 365 / days
    value = f"{fut_price - spot:+.2f} ({prem:+.2f}%, {ann:+.1f}% p.a.)"
    if prem < -0.05:
        return _sig("Open Interest", "Futures basis", value, -1,
                    "Future at a discount to spot - heavy shorts / bearish carry")
    if days >= 5 and ann > fair_pct + 4:
        return _sig("Open Interest", "Futures basis", value, 1,
                    f"Premium well above fair carry (~{fair_pct:g}% p.a.) - longs paying up")
    if days >= 5 and ann < fair_pct - 4:
        return _sig("Open Interest", "Futures basis", value, 0,
                    f"Premium below fair carry (~{fair_pct:g}% p.a.) - weak long appetite")
    return _sig("Open Interest", "Futures basis", value, 0,
                "Normal cost-of-carry premium" if days >= 5 else
                "Close to expiry - premium naturally converges to zero")


def rollover_signal(curve: list[dict], dte: int | None,
                    fair_pct: float = FAIR_CARRY_PCT) -> dict | None:
    """Rollover % = OI in next + far month / total futures OI."""
    if not curve or len(curve) < 2:
        return None
    total = sum(c["oi"] for c in curve)
    if total <= 0:
        return None
    roll = (total - curve[0]["oi"]) / total * 100
    near, nxt = curve[0]["price"], curve[1]["price"]
    roll_cost = (nxt - near) / near * 100 if near else 0.0
    value = f"{roll:.0f}% rolled, roll cost {roll_cost:+.2f}%"
    if dte is None or dte > 6:
        return _sig("Open Interest", "Rollover", value, None,
                    "Mid-series - rollover only becomes meaningful in the last week before expiry")
    fair_month = fair_pct / 12
    if roll >= 50 and roll_cost > fair_month * 1.3:
        return _sig("Open Interest", "Rollover", value, 1,
                    "Positions rolled at a rich premium - longs carrying into next month")
    if roll >= 50 and roll_cost < 0:
        return _sig("Open Interest", "Rollover", value, -1,
                    "Positions rolled at a discount - shorts carrying into next month")
    return _sig("Open Interest", "Rollover", value, 0,
                "Rollover under way without a clear long/short bias")


def mwpl_signal(row: dict | None, in_ban: bool) -> dict | None:
    if row is None and not in_ban:
        return None
    pct = row["used_pct"] if row else None
    value = ("IN F&O BAN" if in_ban else "") + (f" {pct:.0f}% of MWPL used" if pct is not None else "")
    if in_ban:
        note = "No fresh F&O positions allowed - only reductions; forced unwinding makes moves sharp"
    elif pct >= 80:
        note = "Near the 95% ban trigger - crowded; expect volatile, unwinding-driven moves"
    elif pct >= 60:
        note = "Elevated positioning - watch for a run toward the ban limit"
    else:
        note = "Comfortable - plenty of room before the ban limit"
    return _sig("Volatility & risk", "MWPL / F&O ban", value.strip(), None, note)


# =========================================================================== 2. cash market
def delivery_signal(rows: list[dict]) -> dict | None:
    rows = [r for r in rows if r.get("deliv_pct") is not None]
    if len(rows) < 2:
        return None
    last = rows[-1]
    avg = float(np.mean([r["deliv_pct"] for r in rows[:-1]]))
    chg = (last["close"] - last["prev_close"]) / last["prev_close"] * 100 if last["prev_close"] else 0
    high = last["deliv_pct"] >= max(avg * 1.2, avg + 5)
    low = last["deliv_pct"] <= avg * 0.8
    value = f"{last['deliv_pct']:.1f}% (avg {avg:.1f}%) on {last['date']}"
    if high and chg > 0:
        sc, note = 1, f"Price {chg:+.2f}% with above-normal delivery - genuine buying"
    elif high and chg < 0:
        sc, note = -1, f"Price {chg:+.2f}% with above-normal delivery - genuine selling"
    elif low:
        sc, note = 0, f"Price {chg:+.2f}% on low delivery - mostly intraday / speculative"
    else:
        sc, note = 0, f"Price {chg:+.2f}% on normal delivery"
    return _sig("Cash market", "Delivery %", value, sc, note)


def rs_signal(stock: pd.Series, bench: pd.Series, bench_name: str) -> dict | None:
    s = pd.concat([stock.rename("s"), bench.rename("b")], axis=1, join="inner").dropna()
    if len(s) < 61:
        return None
    r20s, r20b = s.s.iloc[-1] / s.s.iloc[-21] - 1, s.b.iloc[-1] / s.b.iloc[-21] - 1
    r60s, r60b = s.s.iloc[-1] / s.s.iloc[-61] - 1, s.b.iloc[-1] / s.b.iloc[-61] - 1
    d20, d60 = (r20s - r20b) * 100, (r60s - r60b) * 100
    # behaviour on down days of the benchmark (accumulation tell-tale)
    rets = s.pct_change().tail(20)
    down = rets[rets.b < 0]
    held = f", rose on {int((down.s > 0).sum())}/{len(down)} of its down days" if len(down) else ""
    value = f"20d {d20:+.1f}% / 60d {d60:+.1f}%"
    if d20 > 0 and d60 > 0:
        sc, note = 1, f"Outperforming {bench_name}{held}"
    elif d20 < 0 and d60 < 0:
        sc, note = -1, f"Underperforming {bench_name}{held}"
    else:
        sc, note = 0, f"Mixed vs {bench_name} (trend of leadership changing){held}"
    return _sig("Cash market", f"RS vs {bench_name}", value, sc, note)


# =========================================================================== 3. volatility
def hist_vol(close: pd.Series, n: int = 20) -> float:
    r = np.log(close / close.shift()).dropna().tail(n)
    return float(r.std() * math.sqrt(252) * 100) if len(r) >= 5 else float("nan")


def vix_signal(vix: pd.Series) -> dict | None:
    vix = vix.dropna()
    if len(vix) < 30:
        return None
    last, wk = float(vix.iloc[-1]), float(vix.iloc[-6]) if len(vix) > 6 else float(vix.iloc[0])
    pct = float((vix.tail(252) < last).mean() * 100)
    chg = (last / wk - 1) * 100
    value = f"{last:.2f} ({chg:+.1f}% in 5d, {pct:.0f}th pct of 1y)"
    if chg > 10 or pct > 80:
        note = "Fear rising / high - bigger swings likely; favours option buyers, risky for sellers"
    elif chg < -10 or pct < 20:
        note = "Calm / falling - smaller ranges likely; favours option sellers"
    else:
        note = "Normal volatility regime"
    return _sig("Volatility & risk", "India VIX", value, None, note)


def iv_hv_signal(atm_iv: float | None, hv20: float) -> dict | None:
    if not atm_iv or not hv20 == hv20 or hv20 <= 0:
        return None
    ratio = atm_iv / hv20
    value = f"IV {atm_iv:.1f}% vs 20d HV {hv20:.1f}% (x{ratio:.2f})"
    if ratio >= 1.3:
        note = "Options priced for more movement than recently seen - expensive (event priced in?)"
    elif ratio <= 0.85:
        note = "Options priced below recent movement - cheap"
    else:
        note = "IV in line with recent movement"
    return _sig("Volatility & risk", "Stock IV vs realised vol", value, None, note)


def straddle_signal(expected_move_pct: float | None, hv20: float, dte: int | None) -> dict | None:
    if not expected_move_pct or not hv20 == hv20 or dte is None:
        return None
    normal = hv20 * math.sqrt(max(dte, 1) / 365)
    ratio = expected_move_pct / normal if normal else float("nan")
    value = f"±{expected_move_pct:.1f}% priced vs ±{normal:.1f}% normal ({dte}d)"
    if ratio >= 1.25:
        note = "Straddle rich vs normal movement - historically favours premium sellers / spreads"
    elif ratio <= 0.8:
        note = "Straddle cheap vs normal movement - historically favours option buyers"
    else:
        note = "Straddle fairly priced vs normal movement"
    return _sig("Volatility & risk", "Straddle vs normal move", value, None, note)


def log_iv(symbol: str, atm_iv: float | None, path: Path = DATA_DIR / "iv_history.csv") -> pd.DataFrame:
    """Append today's ATM IV (one row per symbol per day) and return the full log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "symbol", "atm_iv"])
    if atm_iv:
        today = date.today().isoformat()
        df = df[~((df.date == today) & (df.symbol == symbol))]
        df = pd.concat([df, pd.DataFrame([{"date": today, "symbol": symbol, "atm_iv": atm_iv}])])
        df.sort_values(["symbol", "date"]).to_csv(path, index=False)
    return df


def iv_percentile_signal(history: pd.DataFrame, symbol: str, atm_iv: float | None,
                         min_obs: int = 20) -> dict | None:
    if not atm_iv:
        return None
    h = history[history.symbol == symbol]["atm_iv"].astype(float)
    if len(h) < min_obs:
        return _sig("Volatility & risk", "IV percentile", f"building history ({len(h)}/{min_obs} days)",
                    None, "Run the tool daily on this stock; percentile appears after ~20 sessions")
    pct = float((h < atm_iv).mean() * 100)
    note = ("High vs its own history - options expensive" if pct >= 80 else
            "Low vs its own history - options cheap" if pct <= 20 else "Mid-range vs its own history")
    return _sig("Volatility & risk", "IV percentile", f"{pct:.0f}th ({len(h)} days logged)", None, note)
