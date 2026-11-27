"""
Intraday futures price + OI tracker.

NSE only shows the *latest* futures price and OI, so this module records a reading
for each stock every few minutes, then classifies every interval between two
readings:

    price up   + OI up   -> Long build-up    (new buyers)
    price down + OI up   -> Short build-up   (new sellers)
    price up   + OI down -> Short covering   (sellers exiting)
    price down + OI down -> Long unwinding   (buyers exiting)

Each interval also gets a strength (vs the day's typical OI change), volume, the
day-so-far picture and streaks, written out as a plain-English interpretation.
In expiry week the classification uses OI of all futures months combined, so
rollover from near to next month is not mistaken for unwinding/covering.
"""
from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .open_interest import fmt_oi, parse_futures

IST = timezone(timedelta(hours=5, minutes=30))     # India has no daylight saving
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)
EXPIRY_WEEK_DAYS = 5

FIELDS = ["time", "price", "prev_close", "oi", "prev_oi", "total_oi", "volume", "spot", "expiry"]

COLOURS = {"Long build-up": "#22c55e", "Short build-up": "#ef4444", "Short covering": "#86efac",
           "Long unwinding": "#f59e0b", "No clear signal": "#64748b"}

MEANING = {
    "Long build-up": "Fresh long positions were added while price rose: new buyers are opening "
                     "positions, a sign of conviction behind the move.",
    "Short build-up": "Fresh short positions were added while price fell: new sellers are opening "
                      "positions, a sign of conviction behind the fall.",
    "Short covering": "Price rose while OI fell: earlier sellers are buying back to exit. The rise "
                      "comes from shorts leaving rather than new buyers, so it often fades once "
                      "covering ends.",
    "Long unwinding": "Price fell while OI fell: earlier buyers are selling to exit. The fall comes "
                      "from longs leaving rather than new sellers, so it often slows once "
                      "unwinding ends.",
    "No clear signal": "Price and/or OI barely changed in this interval, so there is no meaningful "
                       "build-up to read.",
}


def now_ist() -> datetime:
    return datetime.now(IST)


def market_times(day: datetime):
    o = day.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    c = day.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return o, c


def is_market_open(t: datetime | None = None) -> bool:
    t = t or now_ist()
    o, c = market_times(t)
    return t.weekday() < 5 and o <= t <= c


def next_slot(t: datetime, minutes: int) -> datetime:
    """Next clock-aligned reading time (09:15, 09:30, ... for 15-min) after t."""
    o, c = market_times(t)
    if t < o:
        return o
    k = int((t - o).total_seconds() // (minutes * 60)) + 1
    nxt = o + timedelta(minutes=k * minutes)
    return min(nxt, c) if t < c else nxt


# =========================================================================== recording
def classify(dp: float, doi: float) -> str:
    if dp > 0 and doi > 0:
        return "Long build-up"
    if dp < 0 and doi > 0:
        return "Short build-up"
    if dp > 0 and doi < 0:
        return "Short covering"
    if dp < 0 and doi < 0:
        return "Long unwinding"
    return "No clear signal"


def reading_from_futures(fut: dict, t: datetime | None = None) -> dict:
    """Turn parse_futures() output into one stored reading."""
    t = t or now_ist()
    total = sum(c["oi"] for c in fut.get("curve", [])) or fut["oi"]
    return {
        "time": t.strftime("%Y-%m-%d %H:%M:%S"),
        "price": round(fut["last_price"], 2),
        "prev_close": round(fut["last_price"] - fut["price_change"], 2),
        "oi": fut["oi"],
        "prev_oi": fut["oi"] - fut["oi_change"],
        "total_oi": total,
        "volume": fut.get("volume"),
        "spot": fut.get("spot"),
        "expiry": fut["expiry"],
    }


def fetch_reading(nse, symbol: str) -> dict:
    fut = parse_futures(nse.get_futures_raw(symbol))
    if not fut:
        raise ValueError(f"no futures data for {symbol}")
    return reading_from_futures(fut)


def data_file(base: Path, symbol: str, day: datetime) -> Path:
    return base / day.strftime("%Y-%m-%d") / f"{symbol.upper()}.csv"


def append_reading(path: Path, r: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: r.get(k) for k in FIELDS})


def load_readings(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            r = dict(row)
            for k in ("price", "prev_close", "oi", "prev_oi", "total_oi", "volume", "spot"):
                try:
                    r[k] = float(r[k]) if r.get(k) not in (None, "", "None") else None
                except ValueError:
                    r[k] = None
            out.append(r)
    return out


# =========================================================================== analysis
def _dte(expiry: str, t: datetime) -> int | None:
    try:
        return (datetime.strptime(expiry, "%d-%b-%Y").date() - t.date()).days
    except (TypeError, ValueError):
        return None


def analyse_intervals(readings: list[dict], minutes: int = 15, unit: str = "contracts") -> list[dict]:
    """One entry per reading: the change since the previous reading (the first one is
    measured from the previous day's close), with classification and interpretation."""
    if not readings:
        return []
    t_first = datetime.strptime(readings[0]["time"], "%Y-%m-%d %H:%M:%S")
    dte = _dte(readings[0].get("expiry"), t_first)
    expiry_week = dte is not None and 0 <= dte <= EXPIRY_WEEK_DAYS
    oi_key = "total_oi" if expiry_week else "oi"

    # baseline for the first interval = previous close / previous day's OI
    first = readings[0]
    prev_total = first.get("prev_total_oi")
    if prev_total is None and first.get("total_oi"):
        prev_total = first["total_oi"] - (first["oi"] - first["prev_oi"])
    base = {"time": None, "price": first["prev_close"], "oi": first["prev_oi"],
            "total_oi": prev_total, "volume": 0.0}

    rows = []
    for i, r in enumerate(readings):
        p = readings[i - 1] if i else base
        dp = r["price"] - p["price"]
        doi = (r[oi_key] or 0) - (p[oi_key] or 0)
        dp_pct = dp / p["price"] * 100 if p["price"] else 0.0
        doi_pct = doi / p[oi_key] * 100 if p[oi_key] else 0.0
        dvol = (r["volume"] - p["volume"]) if r.get("volume") is not None and p.get("volume") is not None else None
        near_doi = r["oi"] - p["oi"]
        label = classify(dp, doi)
        if abs(doi_pct) < 0.05 or abs(dp_pct) < 0.02:
            label = "No clear signal"
        rows.append({"i": i, "time": r["time"][11:16], "start": (p["time"] or "")[11:16] or "prev close",
                     "price": r["price"], "dp": round(dp, 2), "dp_pct": round(dp_pct, 2),
                     "oi": r[oi_key], "doi": doi, "doi_pct": round(doi_pct, 2), "near_doi": near_doi,
                     "dvol": dvol, "label": label, "gap": i == 0,
                     "day_dp_pct": round((r["price"] / first["prev_close"] - 1) * 100, 2) if first["prev_close"] else 0,
                     "day_doi_pct": (round((r[oi_key] / base[oi_key] - 1) * 100, 2) if base[oi_key] else 0),
                     "near_day_doi_pct": round((r["oi"] / first["prev_oi"] - 1) * 100, 2) if first["prev_oi"] else 0,
                     "expiry_week": expiry_week, "dte": dte, "unit": unit, "mins": minutes,
                     "day": r["time"][:10]})

    # strength vs the day's typical intraday OI change (first 'gap' interval excluded)
    intraday = [abs(x["doi"]) for x in rows[1:] if x["doi"]]
    typical = sorted(intraday)[len(intraday) // 2] if len(intraday) >= 3 else None
    streak, last = 0, None
    for x in rows:
        if x["gap"]:
            x["strength"] = "strong" if abs(x["doi_pct"]) >= 3 else "moderate" if abs(x["doi_pct"]) >= 1 else "weak"
            x["rel"] = None
        else:
            rel = abs(x["doi"]) / typical if typical else None
            x["rel"] = round(rel, 1) if rel is not None else None
            if rel is not None:
                x["strength"] = "strong" if rel >= 2 else "moderate" if rel >= 0.8 else "weak"
            else:
                x["strength"] = "strong" if abs(x["doi_pct"]) >= 1 else "moderate" if abs(x["doi_pct"]) >= 0.3 else "weak"
        if x["label"] != "No clear signal" and x["label"] == last:
            streak += 1
        else:
            streak = 1 if x["label"] != "No clear signal" else 0
        last = x["label"]
        x["streak"] = streak
        x["text"] = interpret(x)
    return rows


def day_label(dp_pct: float, doi_pct: float) -> str:
    if abs(doi_pct) < 0.2 or abs(dp_pct) < 0.05:
        return "No clear signal"
    return classify(dp_pct, doi_pct)


def interpret(x: dict) -> str:
    """Plain-English explanation shown when a point is clicked."""
    span = "Since the previous close" if x["gap"] else f"{x['start']}–{x['time']} IST"
    lines = []
    head = x["label"] if x["label"] != "No clear signal" else "No clear signal"
    if x["label"] != "No clear signal":
        head += f" ({x['strength']})"
    lines.append(f"<b>{span}: {html.escape(head)}</b>")
    unit = x.get("unit", "contracts")
    vol = f", volume {fmt_oi(x['dvol'])} {unit}" if x.get("dvol") not in (None, 0) else ""
    lines.append(f"Price {x['dp']:+.2f} ({x['dp_pct']:+.2f}%), OI {fmt_oi(x['doi'], signed=True)} {unit} "
                 f"({x['doi_pct']:+.2f}%){vol}.")
    lines.append(MEANING[x["label"]])
    if not x["gap"] and x["label"] != "No clear signal":
        if x["rel"] is not None:
            lines.append(f"This OI change is {x['rel']}× the day's typical {x.get('mins', 15)}-minute change"
                         + (" – a significant move." if x["strength"] == "strong" else
                            " – treat it as noise." if x["strength"] == "weak" else "."))
        elif x["strength"] == "weak":
            lines.append("The OI change is small, so treat this as noise rather than a signal.")
    if x["gap"]:
        lines.append("This first point covers the gap from yesterday's close to the first reading "
                     "(overnight positioning and the opening trades).")
    if x["streak"] >= 3:
        lines.append(f"{x['streak']} {x['label'].lower()} intervals in a row – a persistent trend in positioning.")
    dl = day_label(x["day_dp_pct"], x["day_doi_pct"])
    oi_name = "OI of all months" if x["expiry_week"] else "near-month OI"
    lines.append(f"Day so far: price {x['day_dp_pct']:+.2f}%, {oi_name} {x['day_doi_pct']:+.2f}% "
                 f"vs yesterday → {dl}.")
    if x["expiry_week"]:
        lines.append(f"Expiry in {x['dte']} day(s): classified on OI of all futures months combined, "
                     f"because traders are rolling positions to next month (near-month OI alone changed "
                     f"{fmt_oi(x['near_doi'], signed=True)} in this interval and "
                     f"{x['near_day_doi_pct']:+.2f}% on the day – mostly rollover, not real exits).")
    return "<br>".join(lines)


def summarise_day(symbol: str, readings: list[dict], rows: list[dict]) -> dict:
    last = rows[-1]
    counts = {}
    for x in rows[1:]:
        counts[x["label"]] = counts.get(x["label"], 0) + 1
    recent = [x["label"] for x in rows[-4:] if x["label"] != "No clear signal"]
    return {"symbol": symbol, "price": last["price"], "day_dp_pct": last["day_dp_pct"],
            "day_doi_pct": last["day_doi_pct"], "day_label": day_label(last["day_dp_pct"], last["day_doi_pct"]),
            "last_time": readings[-1]["time"][11:16], "last_label": last["label"],
            "day": readings[-1]["time"][:10], "near_day_doi_pct": last.get("near_day_doi_pct"),
            "last_strength": last.get("strength"), "counts": counts, "n": len(readings),
            "recent": max(set(recent), key=recent.count) if recent else "No clear signal",
            "expiry": readings[-1].get("expiry"), "expiry_week": last["expiry_week"]}


# =========================================================================== page
PHASES = [("Opening hour", "09:15", "10:15"), ("Late morning", "10:15", "12:00"),
          ("Midday", "12:00", "13:30"), ("Afternoon", "13:30", "14:30"), ("Last hour", "14:30", "15:30")]

TAKEAWAY = {
    "Long build-up": "buyers added fresh positions",
    "Short build-up": "sellers added fresh positions",
    "Short covering": "shorts were exiting (a weaker kind of rise)",
    "Long unwinding": "longs were exiting (a weaker kind of fall)",
}


def _dominant(rows: list[dict]) -> str:
    """Build-up type with the largest total OI change among the given intervals."""
    w: dict = {}
    for x in rows:
        if x["label"] != "No clear signal":
            w[x["label"]] = w.get(x["label"], 0) + abs(x["doi"])
    return max(w, key=w.get) if w else "No clear signal"


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b else 0.0


LABEL_SCORE = {"Long build-up": 2, "Short covering": 1, "Long unwinding": -1, "Short build-up": -2}
BIAS_LEVELS = [(4.5, "Heavily bullish", "#16a34a"), (1.5, "Bullish", "#22c55e"),
               (-1.5, "Sideways", "#94a3b8"), (-4.5, "Bearish", "#ef4444")]
BIAS_TEXT = {
    "Heavily bullish": "Strong fresh buying through the day and into the close – the stock may continue "
                       "higher next session.",
    "Bullish": "Positioning leans towards buyers – the stock may drift higher, but conviction is moderate.",
    "Sideways": "Buyers and sellers are roughly balanced – the stock may stay range-bound between "
                "{lo} and {hi}.",
    "Bearish": "Positioning leans towards sellers – the stock may drift lower, but conviction is moderate.",
    "Heavily bearish": "Strong fresh selling through the day and into the close – the stock may continue "
                       "lower next session.",
}


def day_bias(rows: list[dict], prev_labels: list[str] | None = None) -> dict:
    """Score a session's positioning from -9 (heavily bearish) to +9 (heavily bullish).

    Parts: whole-day build-up (sized by how big the price/OI moves were), the last hour,
    the last 4 intervals, where price closed in the day's range, and the previous 2 days.
    """
    if not rows:
        return {"label": "Sideways", "score": 0.0, "colour": "#94a3b8", "reasons": [], "text": ""}
    last = rows[-1]
    prices = [x["price"] for x in rows]
    hi, lo = max(prices), min(prices)
    reasons, score = [], 0.0

    dl = day_label(last["day_dp_pct"], last["day_doi_pct"])
    if dl in LABEL_SCORE:
        big = abs(last["day_doi_pct"]) >= 3 and abs(last["day_dp_pct"]) >= 1
        small = abs(last["day_doi_pct"]) < 1 and abs(last["day_dp_pct"]) < 0.5
        mag = 1.5 if big else 0.6 if small else 1.0
        score += LABEL_SCORE[dl] * mag
        reasons.append(f"whole day was {dl.lower()} (price {last['day_dp_pct']:+.2f}%, "
                       f"OI {last['day_doi_pct']:+.2f}%)")

    close_seg = [x for x in rows if not x.get("gap") and x["time"] > "14:30"]
    cd = _dominant(close_seg)
    if cd in LABEL_SCORE:
        strong = any(x.get("strength") == "strong" and x["label"] == cd for x in close_seg)
        score += LABEL_SCORE[cd] * (1.3 if strong else 1.0)
        reasons.append(f"last hour was mostly {cd.lower()}" + (" with strong bars" if strong else ""))

    recent = [x["label"] for x in rows if not x.get("gap")][-4:]
    rs = sum(LABEL_SCORE.get(l, 0) for l in recent) / 2 * 0.5
    if abs(rs) >= 0.5:
        score += rs
        side = "buyers" if rs > 0 else "sellers"
        reasons.append(f"the last {len(recent)} intervals favoured {side}")

    if hi > lo:
        pos = (last["price"] - lo) / (hi - lo)
        if pos >= 0.75:
            score += 0.7
            reasons.append("price closed near the day's high")
        elif pos <= 0.25:
            score -= 0.7
            reasons.append("price closed near the day's low")

    prev = [l for l in (prev_labels or [])[-2:] if l in LABEL_SCORE]
    ps = sum(LABEL_SCORE[l] for l in prev) * 0.25
    if abs(ps) >= 0.5:
        score += ps
        reasons.append("the previous days leaned the same way" if (ps > 0) == (score > 0)
                       else "the previous days leaned the other way")

    if last.get("expiry_week"):
        score *= 0.75
        reasons.append("expiry week (rollover) – signal toned down")

    label, colour = "Heavily bearish", "#b91c1c"
    for cut, name, col in BIAS_LEVELS:
        if score >= cut:
            label, colour = name, col
            break

    # what would change the view: last hour's extremes, or the day's range when sideways
    lh = [x["price"] for x in close_seg] or prices
    if label.lower().endswith("bullish"):
        change = f"This view weakens if price falls below {min(lh):,.2f} with short build-up."
    elif label.lower().endswith("bearish"):
        change = f"This view weakens if price rises above {max(lh):,.2f} with long build-up."
    else:
        change = f"A break above {hi:,.2f} or below {lo:,.2f} with a build-up would set the direction."
    text = BIAS_TEXT[label].format(hi=f"{hi:,.2f}", lo=f"{lo:,.2f}")
    return {"label": label, "score": round(score, 1), "colour": colour, "reasons": reasons,
            "text": text, "change": change, "close": last["price"], "expiry": last.get("expiry")}


def bias_track_record(day_items: list[tuple[str, dict]]) -> dict:
    """How often the next session's close moved the way each day's bias pointed.
    day_items: [(day, bias), ...] in date order. Pairs across a contract roll are skipped."""
    stats = {"bull": [0, 0], "bear": [0, 0], "side": [0, 0]}
    for (d1, b1), (d2, b2) in zip(day_items, day_items[1:]):
        if b1.get("expiry") != b2.get("expiry") or not b1.get("close"):
            continue
        move = (b2["close"] / b1["close"] - 1) * 100
        if b1["label"].lower().endswith("bullish"):
            stats["bull"][1] += 1
            stats["bull"][0] += move > 0
        elif b1["label"].lower().endswith("bearish"):
            stats["bear"][1] += 1
            stats["bear"][0] += move < 0
        else:
            stats["side"][1] += 1
            stats["side"][0] += abs(move) < 0.75
    return stats


def track_record_text(stats: dict, n_days: int) -> str:
    parts = []
    for key, what in (("bull", "a bullish bias, the next session closed higher"),
                      ("bear", "a bearish bias, the next session closed lower"),
                      ("side", "a sideways bias, the next session moved less than 0.75%")):
        hit, n = stats[key]
        if n >= 5:
            parts.append(f"after {what} {hit} of {n} times ({hit / n * 100:.0f}%)")
        elif n:
            parts.append(f"after {what} {hit} of {n} times (too few cases to judge)")
    if not parts:
        return ""
    return (f"<b>Track record on this stock</b> (last {n_days} days loaded): " + "; ".join(parts)
            + ". Load more days for a more reliable figure.")


def day_story(rows: list[dict], bias: dict | None = None, record: str = "") -> str:
    """Plain-English summary of one whole session, built from its interval rows."""
    if not rows:
        return ""
    unit = rows[0].get("unit", "contracts")
    first, last = rows[0], rows[-1]
    prev_close = first["price"] - first["dp"]
    prices = [x["price"] for x in rows]
    hi, lo = max(prices), min(prices)
    t_hi = rows[prices.index(hi)]["time"]
    t_lo = rows[prices.index(lo)]["time"]
    dl = day_label(last["day_dp_pct"], last["day_doi_pct"])
    oi_name = "OI of all months" if last.get("expiry_week") else "OI"
    out = [f"<b>Whole day: {html.escape(dl)}</b> – price {last['day_dp_pct']:+.2f}% "
           f"({prev_close:,.2f} → {last['price']:,.2f}), {oi_name} {last['day_doi_pct']:+.2f}% vs the previous day."]
    out.append(f"Range: high {hi:,.2f} at {t_hi}, low {lo:,.2f} at {t_lo} "
               f"({_pct(hi, lo):.2f}% from low to high).")
    if dl != "No clear signal":
        out.append(MEANING[dl])

    # opening gap
    if first.get("gap"):
        g = first["label"]
        out.append(f"<b>Open:</b> {first['dp_pct']:+.2f}% from the previous close by {first['time']}, "
                   f"OI {first['doi_pct']:+.2f}%"
                   + (f" – {g.lower()} overnight / at the open." if g != "No clear signal" else
                      " – no clear positioning change overnight."))

    # phases of the day
    lines = []
    for name, a, b in PHASES:
        seg = [x for x in rows if not x.get("gap") and a < x["time"] <= b]
        if not seg:
            continue
        start_price = seg[0]["price"] - seg[0]["dp"]
        dp = _pct(seg[-1]["price"], start_price)
        doi = sum(x["doi"] for x in seg)
        base_oi = seg[0]["oi"] - seg[0]["doi"]
        doi_pct = doi / base_oi * 100 if base_oi else 0.0
        dom = _dominant(seg)
        if abs(doi_pct) < 0.1 and abs(dp) < 0.1:
            what = "quiet – little change"
        elif dom == "No clear signal":
            what = "no clear build-up"
        else:
            what = f"mostly {dom.lower()}"
        lines.append(f"{name} ({a}–{b}): price {dp:+.2f}%, OI {fmt_oi(doi, signed=True)} {unit} "
                     f"({doi_pct:+.2f}%) – {what}.")
    if lines:
        out.append("<b>How the day unfolded:</b><br>• " + "<br>• ".join(lines))

    # strongest moves and longest streak
    strong = sorted([x for x in rows if not x.get("gap") and x["label"] != "No clear signal"
                     and x.get("strength") == "strong"], key=lambda x: -abs(x["doi"]))[:3]
    if strong:
        out.append("<b>Strongest moves:</b> " + "; ".join(
            f"{x['start']}–{x['time']} {x['label'].lower()} (price {x['dp_pct']:+.2f}%, "
            f"OI {fmt_oi(x['doi'], signed=True)})" for x in sorted(strong, key=lambda x: x["time"])) + ".")
    best = max(rows, key=lambda x: x.get("streak", 0))
    if best.get("streak", 0) >= 3:
        i = rows.index(best)
        start = rows[i - best["streak"] + 1]
        out.append(f"<b>Longest streak:</b> {best['streak']} {best['label'].lower()} intervals in a row "
                   f"({start['start']}–{best['time']}).")

    # takeaway: whole day vs last hour
    close_seg = [x for x in rows if not x.get("gap") and x["time"] > "14:30"]
    close_dom = _dominant(close_seg)
    if dl == "No clear signal" and close_dom == "No clear signal":
        take = "No side showed real conviction today – a range day for positioning."
    elif close_dom == "No clear signal" or close_dom == dl:
        take = (f"Through the day {TAKEAWAY.get(dl, 'positioning was mixed')}"
                + (", and the close confirmed it." if close_dom == dl else ", with a quiet close."))
    else:
        take = (f"Over the whole day {TAKEAWAY.get(dl, 'positioning was mixed')}, but in the last hour "
                f"{TAKEAWAY[close_dom]} – watch whether that continues next session.")
    if last.get("expiry_week"):
        take += " (Expiry week: rollover makes OI noisier than usual.)"
    out.append(f"<b>Takeaway:</b> {take}")
    if bias:
        out.append(f'<div class="bias" style="border-color:{bias["colour"]}">'
                   f'<span class="badge" style="background:{bias["colour"]}">Bias: {bias["label"]}</span> '
                   f'{html.escape(bias["text"])}<br>'
                   + (f'<span class="mut">Why: {html.escape("; ".join(bias["reasons"]))}.</span><br>' if bias["reasons"] else "")
                   + f'{html.escape(bias["change"])}'
                   + (f"<br>{record}" if record else "")
                   + '<br><span class="mut">This reads positioning data – it is not a forecast or advice. '
                     'OI shows who is positioned, not who will be right; always use a stop-loss.</span></div>')
    return "<br>".join(out)


def daily_strip(rows: list[dict]) -> list[dict]:
    """One entry per day: the day's overall build-up (last reading vs previous close)."""
    out = {}
    for x in rows:
        out[x.get("day")] = x
    return [{"day": d, "label": day_label(x["day_dp_pct"], x["day_doi_pct"]),
             "dp": x["day_dp_pct"], "doi": x["day_doi_pct"]} for d, x in out.items() if d]


def build_page(stocks: list[dict], interval: int, live: bool, refresh_s: int = 60,
               source: str = "NSE (recorded by this tracker)", unit: str = "contracts") -> str:
    """stocks: [{'summary': ..., 'rows': [...]}, ...]  (rows may span several days)"""
    for st in stocks:
        st.setdefault("daily", daily_strip(st["rows"]))
        by_day: dict = {}
        for x in st["rows"]:
            by_day.setdefault(x.get("day") or "", []).append(x)
        days = sorted(d for d in by_day if d)
        biases, prev_labels = {}, []
        for d in days:
            r = by_day[d]
            biases[d] = day_bias(r, prev_labels)
            prev_labels.append(day_label(r[-1]["day_dp_pct"], r[-1]["day_doi_pct"]))
        record = track_record_text(bias_track_record([(d, biases[d]) for d in days]), len(days)) \
            if len(days) >= 6 else ""
        st["day_text"] = {d: day_story(r, biases.get(d) or day_bias(r), record) for d, r in by_day.items()}
        if days:
            b = biases[days[-1]]
            st["bias"] = {"label": b["label"], "colour": b["colour"]}
        for d in st.get("daily", []):
            if d["day"] in biases:
                d["bias"] = biases[d["day"]]["label"]
        for gi, x in enumerate(st["rows"]):
            x["gi"] = gi
            x.setdefault("day", "")
    data = json.dumps({"stocks": stocks, "colours": COLOURS, "interval": interval, "unit": unit,
                       "generated_ms": int(now_ist().timestamp() * 1000), "live": live},
                      default=lambda o: None).replace("</", "<\\/")
    status = (f"Live – updates every {interval} min, page refreshes every {refresh_s}s"
              if live else "Not live – snapshot") + f" · data: {source}"
    meta = f'<meta http-equiv="refresh" content="{refresh_s}">' if live else ""
    return (_PAGE.replace("__META__", meta).replace("__STATUS__", html.escape(status))
            .replace("__GENERATED__", now_ist().strftime("%d %b %Y %H:%M IST"))
            .replace("__UNIT__", html.escape(unit)).replace("__DATA__", data))


_PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">__META__
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Intraday futures OI tracker</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
body{margin:0;background:#0b1120;color:#e2e8f0;font:14px/1.5 system-ui,Segoe UI,Arial}
.wrap{max-width:1200px;margin:auto;padding:20px}h1{margin:0;font-size:22px}.mut{color:#94a3b8}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:12px 0;font-size:13px}
.legend span i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.nav{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 4px}.nav a{color:#e2e8f0;text-decoration:none;background:#111827;border:1px solid #1e293b;border-radius:999px;padding:4px 12px}
.card{background:#111827;border:1px solid #1e293b;border-radius:12px;padding:14px 16px;margin:18px 0}
.head{display:flex;flex-wrap:wrap;gap:10px 24px;align-items:baseline}.head h2{margin:0;font-size:20px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;color:#0b1120}
.kv b{display:block;font-size:16px}.kv{font-size:12px;color:#94a3b8}
.panel{background:#0d1526;border:1px solid #1e3a5f;border-radius:10px;padding:12px 14px;margin-top:8px;min-height:40px}
.counts span{margin-right:12px;font-size:12px}
.panel .sect{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:.04em;margin:2px 0 4px}
.panel hr{border:none;border-top:1px solid #1e3a5f;margin:12px 0}
.panel .bias{border:1px solid;border-radius:10px;padding:10px 12px;margin-top:8px;background:#0b1220}
.days{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.chip{border:1px solid #1e293b;background:#0f172a;border-radius:999px;padding:3px 10px;cursor:pointer;font-size:12px;user-select:none}
.chip.on{background:#1d4ed8;border-color:#3b82f6}.chip i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.foot{color:#94a3b8;font-size:12px;margin-top:20px;border-top:1px solid #1e293b;padding-top:10px}
</style></head><body><div class="wrap">
<h1>Intraday futures price + OI build-up</h1>
<div class="mut">__STATUS__ · last generated __GENERATED__ · click any point or bar for its interpretation</div>
<div id="stale" style="display:none;background:#7f1d1d;border:1px solid #ef4444;border-radius:10px;padding:10px 14px;margin:10px 0"></div>
<div class="legend" id="legend"></div>
<div class="mut" style="font-size:12px">Hatched first bar = change from yesterday's close to the first reading (overnight + opening).</div><div class="nav" id="nav"></div>
<div id="stocks"></div>
<div class="foot">Each point compares a reading with the previous one (the first point of each day is measured from the previous day's close).
OI in __UNIT__. Coloured squares on the day buttons show each day's overall build-up. Strength compares the OI change with the day's typical interval change. In expiry week, classification
uses all futures months combined to avoid rollover distortion. Educational analysis of public NSE data – not investment advice.</div>
</div><script>
const D = __DATA__, C = D.colours;
(function staleCheck(){
  // a 'live' page that hasn't been rewritten for a while means the tracker stopped
  const mins = (Date.now() - D.generated_ms) / 60000;
  if (D.live && mins > D.interval + 5) {
    const t = new Date(D.generated_ms);
    const el = document.getElementById("stale"); el.style.display = "block";
    el.innerHTML = `<b>This chart stopped updating ${Math.round(mins)} minutes ago</b> (last update ${t.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})} your time). ` +
      `The tracker window was probably closed, or the PC slept or lost internet. Start <b>Run Tracker</b> again – with Breeze it reloads the missing part of the day.`;
  }
})();
document.getElementById("legend").innerHTML = Object.entries(C).map(([k,v]) => `<span><i style="background:${v}"></i>${k}</span>`).join("");
document.getElementById("nav").innerHTML = D.stocks.map(s => `<a href="#" onclick="document.getElementById('c-${s.summary.symbol}').scrollIntoView();return false">${s.summary.symbol}</a>`).join("");
const badge = l => `<span class="badge" style="background:${C[l]||'#64748b'}">${l}</span>`;
const sign = v => (v>0?"+":"")+v.toFixed(2)+"%";
const root = document.getElementById("stocks");
const view = {};            // symbol -> selected day ("" = all days)
function dshort(d){ const m=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]; return d? (+d.slice(8,10))+" "+m[+d.slice(5,7)-1] : ""; }
function visible(s){
  const v = view[s.summary.symbol];
  if (v === "last5") { const keep = new Set(s.daily.slice(-5).map(d => d.day)); return s.rows.filter(r => keep.has(r.day)); }
  return s.rows.filter(r => !v || r.day === v);
}
function show(sym, gi){
  const s = D.stocks.find(x => x.summary.symbol === sym); if(!s) return; const r = s.rows[gi]; if(!r) return;
  const dayTxt = (s.day_text || {})[r.day || ""] || "";
  document.getElementById("p-"+sym).innerHTML =
    (dayTxt ? `<div class="sect">${r.day ? dshort(r.day) + " – " : ""}the whole session</div>${dayTxt}<hr>` : "") +
    `<div class="sect">Selected ${D.interval}-minute interval${r.day ? " · " + dshort(r.day) : ""}</div>${r.text}`;
  history.replaceState(null, "", "#sel=" + sym + "," + gi + "," + (view[sym]||"all"));
}
function fmtN(v){ const a=Math.abs(v); return a>=1e7?(v/1e7).toFixed(2)+"Cr":a>=1e5?(v/1e5).toFixed(2)+"L":a>=1e3?(v/1e3).toFixed(1)+"K":Math.round(v)+""; }
function oiRange(rows){
  // scale the OI panel to the intraday bars; the overnight (hatched) bar is clipped and labelled
  const intra = rows.filter(r => !r.gap).map(r => Math.abs(r.doi));
  const all = rows.map(r => Math.abs(r.doi));
  const m = Math.max(...(intra.length ? intra : all), 1);
  return [-m * 1.25, m * 1.25];
}
function draw(s){
  const m = s.summary, rows = visible(s), multi = (!view[m.symbol] || view[m.symbol] === "last5") && s.daily.length > 1;
  const x = rows.map(r => multi ? dshort(r.day)+" "+r.time : r.time), col = rows.map(r => C[r.label]);
  const k = rows.length > 200 ? 0.45 : rows.length > 80 ? 0.7 : 1;     // smaller dots when many days are shown
  const size = rows.map(r => k * (r.label === "No clear signal" ? 7 : r.strength === "strong" ? 14 : r.strength === "moderate" ? 10 : 7));
  const hov = rows.map(r => `<b>${r.price.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2})}</b>  ${dshort(r.day)} ${r.time}<br>` +
    `${r.label}${r.label!=="No clear signal"?" ("+r.strength+")":""}<br>` +
    `${r.gap?"since prev close":r.start+"→"+r.time}: price ${r.dp>=0?"+":""}${r.dp.toFixed(2)} (${r.dp_pct>=0?"+":""}${r.dp_pct}%)<br>` +
    `OI ${r.doi>=0?"+":""}${fmtN(r.doi)} (${r.doi_pct>=0?"+":""}${r.doi_pct}%)`);
  const traces = [
    {x, y: rows.map(r => r.price), type:"scatter", mode:"lines+markers", name:"Futures price", line:{color:"#cbd5e1", width:2},
     marker:{color:col, size, line:{color:"#0b1120", width:1}}, hovertext:hov, hoverinfo:"text", yaxis:"y", customdata: rows.map(r=>r.gi)},
    {x, y: rows.map(r => r.doi), type:"bar", name:"OI change", hovertext:hov, hoverinfo:"text", yaxis:"y2", customdata: rows.map(r=>r.gi),
     marker:{color:col, opacity: rows.map(r => r.gap ? 0.45 : 1), pattern:{shape: rows.map(r => r.gap ? "/" : ""), fgcolor:"#0b1120", size:6}}}
  ];
  const layout = {paper_bgcolor:"#111827", plot_bgcolor:"#111827", font:{color:"#cbd5e1"}, showlegend:false,
    margin:{l:60,r:20,t:10,b:50}, hovermode:"closest",
    xaxis:{type:"category", gridcolor:"#1e293b", title:"Time (IST)", nticks: 16},
    yaxis:{domain:[0.42,1], gridcolor:"#1e293b", title:"Price"},
    yaxis2:{domain:[0,0.34], gridcolor:"#1e293b", title:"OI change", zeroline:true, zerolinecolor:"#475569",
            range: oiRange(rows)},
    annotations: rows.map((r, i) => r.gap && rows.length <= 80 && Math.abs(r.doi) > oiRange(rows)[1] ? {x: x[i], y: Math.sign(r.doi) * oiRange(rows)[1] * 0.92,
      xref:"x", yref:"y2", text: (r.doi > 0 ? "+" : "") + fmtN(r.doi), showarrow:false, font:{size:11, color:"#e2e8f0"},
      bgcolor:"#0b1120cc"} : null).filter(Boolean),
    shapes: multi ? rows.map((r, i) => i && r.day !== rows[i-1].day ? {type:"line", xref:"x", yref:"paper", x0:x[i], x1:x[i],
      y0:0, y1:1, line:{color:"#475569", width:1, dash:"dot"}} : null).filter(Boolean) : []};
  Plotly.react("g-"+m.symbol, traces, layout, {displayModeBar:false, responsive:true});
  document.querySelectorAll(`#d-${m.symbol} .chip`).forEach(c => c.classList.toggle("on", (c.dataset.d||"") === (view[m.symbol]||"")));
}
D.stocks.forEach(s => {
  const m = s.summary;
  view[m.symbol] = s.daily.length ? s.daily[s.daily.length-1].day : "";
  const counts = Object.entries(m.counts).map(([k,v]) => `<span style="color:${C[k]}">● ${k}: ${v}</span>`).join("");
  const days = s.daily.length > 1 ? `<div class="days" id="d-${m.symbol}">` + s.daily.map(d =>
      `<span class="chip" data-d="${d.day}" title="Price ${sign(d.dp)}, OI ${sign(d.doi)}${d.bias ? " · bias: " + d.bias : ""}"><i style="background:${C[d.label]}"></i>${dshort(d.day)}</span>`).join("")
      + (s.daily.length > 7 ? `<span class="chip" data-d="last5">Last 5 days</span>` : "")
      + `<span class="chip" data-d="">All ${s.daily.length} days</span></div>` : "";
  const el = document.createElement("div"); el.className = "card"; el.id = "c-" + m.symbol;
  el.innerHTML = `<div class="head"><h2>${m.symbol}</h2>
    <div class="kv">Futures<b>${m.price.toLocaleString('en-IN')} <span style="color:${m.day_dp_pct>=0?'#22c55e':'#ef4444'}">${sign(m.day_dp_pct)}</span></b></div>
    <div class="kv">${m.expiry_week ? "OI all months vs yesterday" : "OI vs yesterday"}<b>${sign(m.day_doi_pct)}</b>${m.expiry_week ? `<span style="font-size:11px">near month ${sign(m.near_day_doi_pct)} (rollover)</span>` : ""}</div>
    <div class="kv">Day so far (${dshort(m.day)})<b>${badge(m.day_label)}</b></div>
    <div class="kv">Last ${D.interval} min (${m.last_time})<b>${badge(m.last_label)} <span class="mut" style="font-size:12px">${m.last_label!=="No clear signal"?m.last_strength:""}</span></b></div>
    <div class="kv">Recent trend<b>${badge(m.recent)}</b></div>
    ${s.bias ? `<div class="kv">Bias (${dshort(m.day)})<b><span class="badge" style="background:${s.bias.colour}">${s.bias.label}</span></b></div>` : ""}
    <div class="kv">Expiry<b>${m.expiry||"-"}${m.expiry_week?" · expiry week":""}</b></div></div>
    <div class="counts mut" style="margin-top:6px">${dshort(m.day)}: ${counts}</div>${days}
    <div class="mut" style="font-size:12px">The figures above are for ${dshort(m.day)}, the latest session. The chart shows the day selected below.</div>
    <div id="g-${m.symbol}" style="height:430px"></div>
    <div class="panel" id="p-${m.symbol}"><span class="mut">Click a point on the price line or an OI bar to see the whole day and that interval.</span></div>`;
  root.appendChild(el);
  draw(s);
  document.getElementById("g-"+m.symbol).on("plotly_click", ev => show(m.symbol, ev.points[0].customdata));
  el.querySelectorAll(".days .chip").forEach(c => c.onclick = () => {
    view[m.symbol] = c.dataset.d; draw(s); const v = visible(s); if (v.length) show(m.symbol, v[v.length-1].gi); });
});
const h = location.hash.match(/sel=([^,]+),(\d+),?([^,]*)/);
if (h) { const sym = decodeURIComponent(h[1]), st = D.stocks.find(x => x.summary.symbol === sym);
  if (st) { view[sym] = h[3] === "all" ? "" : (h[3] || view[sym]); draw(st); show(sym, +h[2]); document.getElementById("c-"+sym).scrollIntoView(); } }
else D.stocks.forEach(s => show(s.summary.symbol, s.rows.length - 1));
</script></body></html>"""
