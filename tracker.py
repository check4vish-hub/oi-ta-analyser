#!/usr/bin/env python3
"""
Track futures price + OI build-up for chosen stocks every 15 minutes (or 5),
with a live chart page you can click for the interpretation of each interval.

  python tracker.py                      # asks which stocks to track
  python tracker.py COFORGE TCS          # start tracking these straight away
  python tracker.py --interval 5         # every 5 minutes instead of 15
  python tracker.py --demo               # simulated day, no internet needed
  python tracker.py --days 3             # with Breeze: show the last 3 trading days (default 7)

If ICICI Breeze is set up (python breeze_setup.py), the tracker pulls the history
straight from Breeze: today's full chart appears instantly (even if you start late
or after the market closes), plus the previous days. Without Breeze it records
from NSE while it runs.

While it runs, type in the window:
  stop            stop tracking (the final chart stays saved)
  add INFY SBIN   start tracking more stocks
  remove TCS      stop tracking a stock
  list            show what is being tracked

It also stops by itself at market close (3:30 PM IST = 1:00 PM Qatar time).
Windows: double-click "Run Tracker.bat".
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

from stock_analyser import intraday as it

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "intraday"
PAGE = HERE / "reports" / "tracker_live.html"


def parse_symbols(text: str) -> list[str]:
    words = [w.strip().upper().removesuffix(".NS") for w in text.replace(",", " ").split()]
    return list(dict.fromkeys(w for w in words if w))


def render(symbols: list[str], day: datetime, interval: int, live: bool, base: Path | None = None,
           page: Path | None = None) -> Path:
    base, page = base or DATA, page or PAGE
    stocks = []
    for s in symbols:
        rd = it.load_readings(it.data_file(base, s, day))
        if rd:
            rows = it.analyse_intervals(rd)
            stocks.append({"summary": it.summarise_day(s, rd, rows), "rows": rows})
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(it.build_page(stocks, interval, live), encoding="utf-8")
    return page


def take_readings(symbols, nse, day, base: Path | None = None) -> list[str]:
    base = base or DATA
    msgs = []
    for s in symbols:
        try:
            r = it.fetch_reading(nse, s)
            it.append_reading(it.data_file(base, s, day), r)
            rows = it.analyse_intervals(it.load_readings(it.data_file(base, s, day)))
            last = rows[-1]
            msgs.append(f"  {s:<12} {r['price']:>10,.2f}  OI {it.fmt_oi(last['doi'], signed=True):>8}"
                        f"  → {last['label']}{' (' + last['strength'] + ')' if last['label'] != 'No clear signal' else ''}")
        except Exception as e:
            t = str(e)
            if any(k in t for k in ("HTTPSConnectionPool", "Max retries", "timed out", "HTTP 4", "HTTP 5")):
                t = "could not get data from NSE (check internet; NSE may be blocking - retrying next time)"
            msgs.append(f"  {s:<12} FAILED – {t[:110]}")
        time.sleep(0.6)  # be polite to NSE
    return msgs


def command_reader(q: "queue.Queue[str]") -> None:
    """Read typed commands without blocking the timer loop."""
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            q.put("stop")
            return
        q.put(line.strip())
        if line.strip().lower() in ("stop", "quit", "exit", "q"):
            return


def breeze_refresh(client, symbols, days, minutes, page=None) -> tuple[Path, list[str]]:
    """Pull history from Breeze for every symbol and rebuild the chart page."""
    from stock_analyser import breeze_source as bz
    stocks, msgs = [], []
    for s in symbols:
        try:
            by_day = bz.history(client, s, days=days, minutes=minutes)
            if not by_day:
                msgs.append(f"  {s:<12} no data from Breeze yet")
                continue
            rows = bz.analyse_days(by_day, minutes, unit="units")
            last_day = max(by_day)
            last_rows = [x for x in rows if x["day"] == last_day.isoformat()]
            summary = it.summarise_day(s, by_day[last_day], last_rows)
            stocks.append({"summary": summary, "rows": rows})
            x = last_rows[-1]
            msgs.append(f"  {s:<12} {x['price']:>10,.2f}  last {x['start']}–{x['time']}: {x['label']}"
                        f"{' (' + x['strength'] + ')' if x['label'] != 'No clear signal' else ''}"
                        f"  · day: {summary['day_label']}")
        except Exception as e:
            msgs.append(f"  {s:<12} FAILED – {str(e)[:120]}")
    page = page or PAGE
    page.parent.mkdir(parents=True, exist_ok=True)
    live = it.is_market_open()
    page.write_text(it.build_page(stocks, minutes, live, source="ICICI Breeze",
                                  unit="units (shares) – Breeze reports OI and volume as quantity"),
                    encoding="utf-8")
    return page, msgs


def run_breeze(symbols, a) -> int:
    from stock_analyser import breeze_source as bz
    import importlib.util
    import subprocess
    if not importlib.util.find_spec("breeze_connect"):
        print("Installing the ICICI Breeze library (one time)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "breeze-connect"])
        importlib.invalidate_caches()
    try:
        client = bz.connect(interactive=True)
    except bz.BreezeError as e:
        print(f"[!] {e}")
        return 1
    print(f"\nLoading the last {a.days} trading days for {', '.join(symbols)} from ICICI Breeze…")
    page, msgs = breeze_refresh(client, symbols, a.days, a.interval)
    print("\n".join(msgs))
    if not a.no_open:
        webbrowser.open(page.resolve().as_uri())
    print(f"\nChart: {page.resolve()}")
    now = it.now_ist()
    o, c = it.market_times(now)
    if not it.is_market_open(now):
        print("\nThe market is closed right now, so this is the latest saved picture. "
              "Start the tracker during market hours for live 15-minute updates.")
        return 0

    print(f"\nLive: refreshing every {a.interval} min until market close.")
    print("Type  stop  to finish,  add SYMBOL  /  remove SYMBOL  to change the list,  list  to see it.\n")
    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=command_reader, args=(q,), daemon=True).start()
    # refresh a minute after each slot so the last 5-minute candle is complete
    next_t = it.next_slot(now, a.interval) + timedelta(minutes=1)
    print(f"  next update at {next_t:%H:%M} IST ({(next_t - timedelta(hours=2, minutes=30)):%H:%M} Qatar)")
    while True:
        changed = False
        while not q.empty():
            cmd = q.get()
            low = cmd.lower()
            if low in ("stop", "quit", "exit", "q"):
                print("\nStopped. The chart stays saved:", page.resolve())
                return 0
            if low.startswith("add "):
                new = [x for x in parse_symbols(cmd[4:]) if x not in symbols]
                symbols += new
                changed = bool(new)
                print(f"Added {', '.join(new) or 'nothing new'}. Now tracking: {', '.join(symbols)}")
            elif low.startswith("remove "):
                gone = parse_symbols(cmd[7:])
                symbols = [x for x in symbols if x not in gone]
                changed = True
                print(f"Now tracking: {', '.join(symbols) or 'nothing'}")
            elif low == "list":
                print(f"Tracking: {', '.join(symbols) or 'nothing'}")
            elif cmd:
                print("Commands: stop | add SYMBOL | remove SYMBOL | list")
        if not symbols:
            print("Nothing left to track.")
            return 0
        now = it.now_ist()
        if changed or now >= next_t:
            print(f"[{now:%H:%M} IST]")
            page, msgs = breeze_refresh(client, symbols, a.days, a.interval)
            print("\n".join(msgs))
            if now >= c:
                print("\nMarket closed (3:30 PM IST). Final chart saved:", page.resolve())
                return 0
            if not changed or now >= next_t:
                next_t = it.next_slot(now, a.interval) + timedelta(minutes=1)
            print(f"  next update at {next_t:%H:%M} IST ({(next_t - timedelta(hours=2, minutes=30)):%H:%M} Qatar)")
        time.sleep(1)


def run_demo(symbols: list[str], interval: int, base: Path, page: Path, open_browser: bool) -> Path:
    """Simulate a full trading day of readings instantly (for testing / a preview)."""
    import numpy as np
    day = it.now_ist()
    o, c = it.market_times(day)
    rng = np.random.default_rng(5)
    for s in symbols:
        f = it.data_file(base, s, day)
        if f.exists():
            f.unlink()
        price = float(rng.uniform(500, 4000))
        prev_close, oi = price, float(rng.integers(20_000, 80_000))
        prev_oi, vol = oi, 0.0
        drift = rng.normal(0, 0.0008)
        t = o
        price *= 1 + rng.normal(0, 0.006)
        oi *= 1 + rng.normal(0.01, 0.02)
        while t <= c:
            vol += float(rng.integers(500, 4000))
            exp = (day + timedelta(days=12)).strftime("%d-%b-%Y")
            it.append_reading(f, {"time": t.strftime("%Y-%m-%d %H:%M:%S"), "price": round(price, 2),
                                  "prev_close": round(prev_close, 2), "oi": round(oi), "prev_oi": round(prev_oi),
                                  "total_oi": round(oi * 1.08), "volume": vol, "spot": round(price * 0.998, 2),
                                  "expiry": exp})
            price *= 1 + drift + rng.normal(0, 0.0025)
            oi *= 1 + rng.normal(0.001, 0.006)
            t += timedelta(minutes=interval)
    p = render(symbols, day, interval, live=False, base=base, page=page)
    if open_browser:
        webbrowser.open(p.resolve().as_uri())
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Intraday futures price + OI build-up tracker")
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--interval", type=int, default=15, choices=[5, 10, 15, 30],
                    help="Minutes between readings (default 15)")
    ap.add_argument("--demo", action="store_true", help="Simulated day, no internet needed")
    ap.add_argument("--days", type=int, default=7, help="Breeze only: trading days of history to show")
    ap.add_argument("--source", choices=["auto", "breeze", "nse"], default="auto",
                    help="auto = Breeze if set up, otherwise record from NSE")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)

    symbols = parse_symbols(" ".join(a.symbols))
    if not symbols:
        print("Intraday futures OI tracker")
        symbols = parse_symbols(input("Enter NSE F&O stocks to track (e.g.  COFORGE TCS NIFTY):\n> "))
        if not symbols:
            print("No stocks entered.")
            return 1

    if a.demo:
        p = run_demo(symbols, a.interval, DATA, PAGE, not a.no_open)
        print(f"Demo chart: {p.resolve()}")
        return 0

    from stock_analyser import breeze_source as bz
    if a.source == "breeze" or (a.source == "auto" and bz.is_configured()):
        return run_breeze(symbols, a)
    if a.source == "auto":
        print("(ICICI Breeze not set up - recording from NSE instead. Run  python breeze_setup.py  "
              "to get instant full-day history.)")

    from stock_analyser.nse_client import NSEClient
    nse = NSEClient()
    day = it.now_ist()
    print(f"\nTracking {', '.join(symbols)} every {a.interval} min.")
    print("Type  stop  to finish,  add SYMBOL  /  remove SYMBOL  to change the list,  list  to see it.\n")

    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=command_reader, args=(q,), daemon=True).start()
    opened = False
    o, c = it.market_times(day)
    if day.weekday() >= 5:
        print("The market is closed today (weekend). Start the tracker on a trading day.")
        return 0
    if day > c:
        print("The market has already closed for today (3:30 PM IST). Showing today's recorded data, if any.")
        p = render(symbols, day, a.interval, live=False)
        if not a.no_open:
            webbrowser.open(p.resolve().as_uri())
        return 0
    if day < o:
        print(f"Market opens at 9:15 AM IST ({(o - day).seconds // 60} min from now). Waiting…")

    next_t = it.now_ist() if it.is_market_open() else o
    stop = False
    while not stop:
        # handle typed commands
        while not q.empty():
            cmd = q.get()
            low = cmd.lower()
            if low in ("stop", "quit", "exit", "q"):
                stop = True
            elif low.startswith("add "):
                new = [s for s in parse_symbols(cmd[4:]) if s not in symbols]
                symbols += new
                print(f"Added {', '.join(new) or 'nothing new'}. Now tracking: {', '.join(symbols)}")
                if new and it.is_market_open():
                    print("\n".join(take_readings(new, nse, day)))
                    render(symbols, day, a.interval, live=True)
            elif low.startswith("remove "):
                gone = parse_symbols(cmd[7:])
                symbols = [s for s in symbols if s not in gone]
                print(f"Now tracking: {', '.join(symbols) or 'nothing'}")
                render(symbols, day, a.interval, live=True)
            elif low == "list":
                print(f"Tracking: {', '.join(symbols) or 'nothing'}")
            elif cmd:
                print("Commands: stop | add SYMBOL | remove SYMBOL | list")
        if stop or not symbols:
            break

        now = it.now_ist()
        if now >= next_t and now.date() == day.date():
            if it.is_market_open(now) or now <= c + timedelta(minutes=2):
                print(f"[{now:%H:%M} IST]")
                print("\n".join(take_readings(symbols, nse, day)))
                p = render(symbols, day, a.interval, live=True)
                if not opened and not a.no_open:
                    webbrowser.open(p.resolve().as_uri())
                    opened = True
                    print(f"\nLive chart opened in your browser: {p.resolve()}\n")
            if now >= c:
                print("\nMarket closed (3:30 PM IST). Tracking finished for today.")
                break
            next_t = it.next_slot(now, a.interval)
            print(f"  next reading at {next_t:%H:%M} IST ({(next_t - timedelta(hours=2, minutes=30)):%H:%M} Qatar)")
        time.sleep(1)

    p = render(symbols, day, a.interval, live=False)
    print(f"\nStopped. Final chart: {p.resolve()}")
    print(f"Today's readings are saved in: {(DATA / day.strftime('%Y-%m-%d')).resolve()}")
    print("You can restart the tracker later today - it continues from these readings.")
    return 0


def run() -> int:
    """Run main() but keep the window open on errors (e.g. when tracker.py is double-clicked)."""
    import traceback
    try:
        return main()
    except KeyboardInterrupt:
        return 1
    except Exception:
        print("\n[!] Something went wrong. Please send a screenshot of this to get it fixed:\n")
        traceback.print_exc()
        input("\nPress Enter to close this window…")
        return 1


if __name__ == "__main__":
    sys.exit(run())
