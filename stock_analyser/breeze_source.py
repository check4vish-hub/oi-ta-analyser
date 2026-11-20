"""
ICICI Direct Breeze API as a data source for intraday futures price + OI history.

Why: NSE's website only shows the latest OI, but Breeze returns past candles with
open interest, so the tracker can show the whole day (and several past days)
instantly instead of recording it reading by reading.

Setup (one time):  python breeze_setup.py   (or double-click "Setup Breeze.bat")
  - stores your API key and secret in data/breeze_config.json on YOUR computer only
    (data/ is git-ignored, so it is never uploaded to GitHub)
Every day: Breeze needs a new session token. The tracker opens the ICICI login page
and asks you to paste the token (or the whole address bar URL) once per day.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import webbrowser
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

from .intraday import IST, MARKET_CLOSE, MARKET_OPEN, analyse_intervals

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFIG = DATA_DIR / "breeze_config.json"
LOGIN_URL = "https://api.icicidirect.com/apiuser/login?api_key="

# Breeze uses ICICI's own codes. Indices are not in the equity name list, so map them.
INDEX_CODES = {"NIFTY": "NIFTY", "BANKNIFTY": "CNXBAN", "FINNIFTY": "NIFFIN"}


class BreezeError(RuntimeError):
    pass


# =========================================================================== config & session
def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict, path: Path | None = None) -> None:
    path = path or CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def is_configured(path: Path | None = None) -> bool:
    c = load_config(path)
    return bool(c.get("api_key") and c.get("api_secret"))


def login_url(api_key: str) -> str:
    return LOGIN_URL + urllib.parse.quote_plus(api_key)


def parse_session_token(text: str) -> str | None:
    """Accept the bare token or the whole redirected URL containing apisession=..."""
    text = text.strip()
    m = re.search(r"apisession=([A-Za-z0-9]+)", text)
    if m:
        return m.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9]{4,}", text) else None


def today_ist() -> date:
    return datetime.now(IST).date()


def connect(interactive: bool = True, path: Path | None = None, client_factory=None):
    """Return a logged-in BreezeConnect client, asking for today's session token if needed."""
    cfg = load_config(path)
    if not (cfg.get("api_key") and cfg.get("api_secret")):
        raise BreezeError("Breeze is not set up yet - run  python breeze_setup.py  first.")
    if client_factory is None:
        from breeze_connect import BreezeConnect  # downloads ICICI's security master on import
        client_factory = BreezeConnect
    token = cfg.get("session_token") if cfg.get("session_date") == today_ist().isoformat() else None
    for attempt in range(3):
        if not token:
            if not interactive:
                raise BreezeError("Breeze session expired - start the tracker to log in again.")
            url = login_url(cfg["api_key"])
            print("\nBreeze needs today's login. Opening the ICICI login page in your browser…")
            print(f"  {url}")
            print("After logging in, the browser goes to an address containing  apisession=XXXXXXX")
            print("(the page itself may show an error - that's fine). Copy that address or just the number.")
            webbrowser.open(url)
            token = parse_session_token(input("Paste the address or session token here: "))
            if not token:
                print("That didn't look like a session token - try again.")
                continue
        try:
            client = client_factory(api_key=cfg["api_key"])
            client.generate_session(api_secret=cfg["api_secret"], session_token=token)
            cfg["session_token"], cfg["session_date"] = token, today_ist().isoformat()
            save_config(cfg, path)
            return client
        except Exception as e:
            print(f"Breeze login failed: {str(e)[:150]}")
            token = None
    raise BreezeError("Could not log in to Breeze.")


# =========================================================================== codes & expiries
def isec_code(client, symbol: str, path: Path | None = None) -> str:
    """NSE symbol -> ICICI stock code (cached in the config file)."""
    symbol = symbol.upper()
    cfg = load_config(path)
    cache = cfg.setdefault("codes", {})
    if symbol in cache:
        return cache[symbol]
    if symbol in INDEX_CODES:
        code = INDEX_CODES[symbol]
    else:
        r = client.get_names(exchange_code="NSE", stock_code=symbol) or {}
        code = r.get("isec_stock_code") if isinstance(r, dict) else None
        if not code:
            raise BreezeError(f"Breeze doesn't recognise {symbol} - check the NSE symbol.")
    cache[symbol] = code
    save_config(cfg, path)
    return code


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _expiry_iso(d: date) -> str:
    return f"{d.isoformat()}T07:00:00.000Z"


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def expiry_candidates(year: int, month: int) -> list[date]:
    """Monthly futures expire on the last Tuesday (NSE since Sep 2025; Thursday before),
    moved earlier when that day is a holiday."""
    out = []
    for wd in (1, 3):  # Tuesday, Thursday
        base = _last_weekday(year, month, wd)
        for back in range(0, 4):
            d = base - timedelta(days=back)
            if d.weekday() < 5 and d not in out:
                out.append(d)
    return out


def _call(client, **kw) -> list[dict]:
    r = client.get_historical_data_v2(exchange_code="NFO", product_type="futures", right="others",
                                      strike_price="0", **kw)
    if not isinstance(r, dict):
        raise BreezeError("unexpected Breeze response")
    if r.get("Status") not in (200, "200") or r.get("Success") is None:
        err = r.get("Error") or "no data"
        return [] if "no data" in str(err).lower() else _raise(err)
    return r["Success"] or []


def _raise(err):
    raise BreezeError(f"Breeze: {str(err)[:150]}")


def find_expiry(client, code: str, year: int, month: int, path: Path | None = None) -> date | None:
    """Find the real monthly expiry by asking Breeze which candidate date has data (cached)."""
    key = f"{code}:{year}-{month:02d}"
    cfg = load_config(path)
    cache = cfg.setdefault("expiries", {})
    if key in cache:
        return date.fromisoformat(cache[key]) if cache[key] else None
    today = today_ist()
    found = None
    for d in expiry_candidates(year, month):
        end = min(d, today)
        start = end - timedelta(days=12)
        try:
            rows = _call(client, interval="1day", from_date=_iso(datetime.combine(start, dtime(0), IST)),
                         to_date=_iso(datetime.combine(end, dtime(23, 59), IST)), stock_code=code,
                         expiry_date=_expiry_iso(d))
        except BreezeError:
            rows = []
        if rows:
            found = d
            break
    cache[key] = found.isoformat() if found else None
    save_config(cfg, path)
    return found


def contracts_for(client, code: str, start: date, end: date, path: Path | None = None) -> list[date]:
    """Expiries of every monthly contract needed to cover [start, end] + the next month."""
    out, y, m = [], start.year, start.month
    while True:
        e = find_expiry(client, code, y, m, path)
        if e and e >= start:
            out.append(e)
        if (y, m) > (end.year, end.month) and len([x for x in out if x > end]) >= 1:
            break
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        if (y, m) > (end.year + 1, end.month):
            break
    return sorted(set(out))


# =========================================================================== candles -> readings
def fetch_candles(client, code: str, expiry: date, start: date, end: date,
                  interval: str = "5minute") -> list[dict]:
    """5-minute futures candles for one contract, split into requests of <=1000 candles."""
    out, d = [], start
    while d <= end:
        chunk_end = min(end, d + timedelta(days=10))
        out += _call(client, interval=interval,
                     from_date=_iso(datetime.combine(d, dtime(*MARKET_OPEN), IST)),
                     to_date=_iso(datetime.combine(chunk_end, dtime(*MARKET_CLOSE), IST)),
                     stock_code=code, expiry_date=_expiry_iso(expiry))
        d = chunk_end + timedelta(days=1)
    return out


def _parse_dt(s: str) -> datetime:
    s = str(s).replace("T", " ")[:19]
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def candles_to_days(near_by_day: dict, next_by_day: dict, minutes: int) -> dict:
    """{day: [candles of the near-month contract]} -> {day: [readings every `minutes`]}.

    Each reading = close of the last candle in the bar, OI at the end of the bar,
    cumulative volume for the day; total_oi adds the next-month contract's OI.
    Every day's first reading is measured against the previous day's close/OI.
    """
    days = sorted(near_by_day)
    out, prev = {}, None
    for day in days:
        cs = sorted(near_by_day[day], key=lambda c: c["_t"])
        nxt = {c["_t"]: (_f(c.get("open_interest")), _f(c.get("close"))) for c in next_by_day.get(day, [])}
        o = datetime.combine(day, dtime(*MARKET_OPEN), IST)
        c_end = datetime.combine(day, dtime(*MARKET_CLOSE), IST)
        buckets: dict[int, list] = {}
        for c in cs:
            k = int((c["_t"] - o).total_seconds() // (minutes * 60))
            if k >= 0:
                buckets.setdefault(k, []).append(c)
        readings, cum_vol, last_next, last_next_close = [], 0.0, None, None
        for k in sorted(buckets):
            b = buckets[k]
            last = b[-1]
            cum_vol += sum(_f(c.get("volume")) or 0 for c in b)
            if last["_t"] in nxt and nxt[last["_t"]][0] is not None:
                last_next, last_next_close = nxt[last["_t"]]
            t = min(o + timedelta(minutes=(k + 1) * minutes), c_end)
            readings.append({"time": t.strftime("%Y-%m-%d %H:%M:%S"), "price": _f(last.get("close")),
                             "oi": _f(last.get("open_interest")), "volume": cum_vol,
                             "next_oi": last_next, "next_close": last_next_close,
                             "expiry": last.get("_expiry")})
        readings = [r for r in readings if r["price"] is not None and r["oi"] is not None]
        if not readings:
            continue
        for r in readings:
            r["total_oi"] = r["oi"] + (r.get("next_oi") or 0)
        if prev is not None:
            rolled = prev.get("expiry") != readings[0].get("expiry")
            if rolled:
                # first day of a new near-month contract: yesterday's figures must come from
                # this same contract (it was next-month yesterday), not the expired one
                pc = prev.get("next_close") or readings[0]["price"]
                po = prev.get("next_oi") or readings[0]["oi"]
                pt = po
            else:
                pc, po, pt = prev["price"], prev["oi"], prev["total_oi"]
            for r in readings:
                r["prev_close"], r["prev_oi"], r["prev_total_oi"] = pc, po, pt
            out[day] = readings
        prev = readings[-1]
    return out


def history(client, symbol: str, days: int = 7, minutes: int = 15, path: Path | None = None) -> dict:
    """Last `days` trading days of `minutes` readings for the near-month future, using the
    contract that was near-month on each day. Returns {day: readings}."""
    code = isec_code(client, symbol, path)
    end = today_ist()
    start = end - timedelta(days=int(days * 1.6) + 6)          # calendar span incl. weekends + 1 baseline day
    expiries = contracts_for(client, code, start, end, path)
    if not expiries:
        raise BreezeError(f"No futures contracts found for {symbol} on Breeze.")
    near_by_day: dict = {}
    next_by_day: dict = {}
    for i, e in enumerate(expiries):
        prev_e = expiries[i - 1] if i else start - timedelta(days=1)
        lo, hi = max(start, prev_e + timedelta(days=1)), min(end, e)
        if lo > hi:
            continue
        for c in fetch_candles(client, code, e, lo, hi):
            c["_t"], c["_expiry"] = _parse_dt(c["datetime"]), e.strftime("%d-%b-%Y")
            near_by_day.setdefault(c["_t"].date(), []).append(c)
        # next-month OI is only needed near expiry (for the combined-OI rollover check)
        if i + 1 < len(expiries):
            lo2 = max(lo, e - timedelta(days=7))
            for c in fetch_candles(client, code, expiries[i + 1], lo2, hi):
                c["_t"] = _parse_dt(c["datetime"])
                next_by_day.setdefault(c["_t"].date(), []).append(c)
    by_day = candles_to_days(near_by_day, next_by_day, minutes)
    keep = sorted(by_day)[-days:]
    return {d: by_day[d] for d in keep}


def analyse_days(by_day: dict, minutes: int, unit: str = "units") -> list[dict]:
    """Run the interval analysis day by day and join into one list (with day + global index)."""
    rows = []
    for day in sorted(by_day):
        for x in analyse_intervals(by_day[day], minutes=minutes, unit=unit):
            x["day"] = day.isoformat()
            x["gi"] = len(rows)
            rows.append(x)
    return rows
