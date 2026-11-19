#!/usr/bin/env python3
"""
Scan a watchlist and open one interactive dashboard in your browser.

  python dashboard.py                    # asks which stocks to scan (type one or many)
  python dashboard.py TCS INFY COFORGE   # scan just these, no question
  python dashboard.py --watchlist-all    # scan everything in watchlist.txt
  python dashboard.py --fast             # skip delivery %, MWPL, RS, VIX (quicker)
  python dashboard.py --demo             # synthetic data, no internet needed

Windows: you can also just double-click  "Run Dashboard.bat".
"""
from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from analyse import AnalysisError, build_parser, run_analysis, write_reports
from stock_analyser.dashboard import build_html, summarise

HERE = Path(__file__).resolve().parent
DEMO_SYMBOLS = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL", "INDIA", "JULIET"]


def parse_symbols(text: str) -> list[str]:
    """'tcs, infy  coforge' -> ['TCS', 'INFY', 'COFORGE'] (commas/spaces, any case, no duplicates)."""
    text = text.split("#", 1)[0]
    words = [w.strip().upper() for w in text.replace(",", " ").replace(";", " ").split()]
    words = [w.removesuffix(".NS") for w in words if w]
    return list(dict.fromkeys(words))


def read_watchlist(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        out += parse_symbols(line)
    return list(dict.fromkeys(out))  # de-duplicate, keep order


def market_snapshot(rows: list[dict], demo: bool, nse=None) -> dict:
    """Nifty, India VIX and the F&O ban list for the dashboard header."""
    from stock_analyser import market_context as mc
    m = {}
    try:
        if demo:
            from stock_analyser.demo_data import demo_context, demo_prices
            nifty = demo_context(demo_prices())["nifty"]
        else:
            nifty = mc.load_yahoo("^NSEI", "1y")
        last, prev, wk = float(nifty.iloc[-1]), float(nifty.iloc[-2]), float(nifty.iloc[-6])
        sma50 = float(nifty.tail(50).mean())
        m["nifty"] = {"last": round(last, 1), "chg": round((last / prev - 1) * 100, 2),
                      "chg5": round((last / wk - 1) * 100, 2),
                      "trend": "above 50-DMA" if last > sma50 else "below 50-DMA"}
    except Exception:
        pass
    for r in rows:  # VIX is the same for every stock - take it from the first row that has it
        for g in r["groups"]:
            for s in g["signals"]:
                if s["name"] == "India VIX":
                    m["vix"] = {"value": s["value"].split(" ")[0], "note": s["note"].split(" - ")[0]}
                    break
            if "vix" in m:
                break
        if "vix" in m:
            break
    if not demo:
        try:
            m["ban"] = mc.ContextFetcher(nse).ban_list()
        except Exception:
            pass
    else:
        m["ban"] = []
    return m


def scan(symbols: list[str], a) -> Path | None:
    """Analyse `symbols`, write the dashboard, open it. Returns the dashboard path."""
    # same options analyse.py uses, with live NSE data
    opts = build_parser().parse_args(["X"] + (["--demo"] if a.demo else []) + (["--no-context"] if a.fast else []))
    out = Path(a.out)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    detail_dir = out / f"scan_{stamp}"
    rows, failed = [], []
    t0 = time.time()
    print(f"\nScanning {len(symbols)} symbol{'s' if len(symbols) != 1 else ''}"
          f"{' (fast mode)' if a.fast else ''}: {', '.join(symbols)}\n")
    for i, sym in enumerate(symbols, 1):
        print(f"  [{i:>2}/{len(symbols)}] {sym:<12}", end="", flush=True)
        try:
            r = run_analysis(sym, opts, nse=_STATE.get("nse"), seed=(100 + i) if a.demo else None, quiet=True)
            _STATE["nse"] = r["nse"] or _STATE.get("nse")
            html_path = write_reports(r, detail_dir)
            rows.append(summarise(r, f"{detail_dir.name}/{html_path.name}"))
            row = rows[-1]
            print(f"{row['verdict']:<15} score {row['score']:+4d}"
                  + (f"   ({len(r['skipped'])} sources skipped)" if r["skipped"] else ""))
        except AnalysisError as e:
            failed.append((sym, str(e).split(". ")[0][:90]))
            print("FAILED -", failed[-1][1])
        except Exception as e:  # never let one symbol stop the scan
            failed.append((sym, f"{type(e).__name__}: {str(e)[:80]}"))
            print("FAILED -", failed[-1][1])
        if not a.demo and i < len(symbols):
            time.sleep(0.8)  # be polite to NSE - too many fast requests get blocked

    if not rows:
        print("\nNothing could be analysed - check the symbol spelling (NSE symbols, e.g. HDFCBANK, "
              "M&M) and your internet connection.", file=sys.stderr)
        return None
    market = market_snapshot(rows, a.demo, _STATE.get("nse"))
    page = build_html(rows, failed, market, "F&O Scanner – OI + Technical Dashboard")
    out.mkdir(parents=True, exist_ok=True)
    dash = out / f"dashboard_{stamp}.html"
    dash.write_text(page, encoding="utf-8")
    (out / "dashboard_latest.html").write_text(page, encoding="utf-8")
    print(f"\nDone in {time.time() - t0:.0f}s. Dashboard: {dash.resolve()}")
    if not a.no_open:
        webbrowser.open(dash.resolve().as_uri())
    return dash


_STATE: dict = {}   # re-use one NSE session across scans in the same window

PROMPT = ("\nEnter NSE symbols to scan, separated by space or comma (e.g.  TCS INFY COFORGE)\n"
          "  W = whole watchlist.txt    Enter on its own = quit\n> ")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Scan stocks into one dashboard")
    p.add_argument("symbols", nargs="*", help="Symbols to scan (if none, you are asked)")
    p.add_argument("--watchlist", default=str(HERE / "watchlist.txt"))
    p.add_argument("--watchlist-all", action="store_true", help="Scan every symbol in watchlist.txt")
    p.add_argument("--fast", action="store_true", help="Skip delivery %%, MWPL, RS and VIX")
    p.add_argument("--demo", action="store_true", help="Synthetic data, no internet needed")
    p.add_argument("--no-open", action="store_true", help="Don't open the browser at the end")
    p.add_argument("--out", default=str(HERE / "reports"))
    a = p.parse_args(argv)

    symbols = parse_symbols(" ".join(a.symbols))
    if symbols or a.watchlist_all:
        symbols = symbols or read_watchlist(Path(a.watchlist))
        return 0 if scan(symbols, a) else 1

    if not sys.stdin.isatty():  # not typed by a person (e.g. automated test run)
        return 0 if scan(DEMO_SYMBOLS if a.demo else read_watchlist(Path(a.watchlist)), a) else 1

    # interactive: keep asking until the user presses Enter on an empty line
    print("F&O Scanner - OI + Technical Dashboard" + ("  [DEMO DATA]" if a.demo else ""))
    while True:
        try:
            text = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            break
        if text.upper() in ("W", "WATCHLIST"):
            symbols = read_watchlist(Path(a.watchlist))
        else:
            symbols = parse_symbols(text)
        if symbols:
            scan(symbols, a)
    print("Bye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
