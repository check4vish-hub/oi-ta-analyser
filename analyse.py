#!/usr/bin/env python3
"""
Open Interest + Technical Analysis of an NSE stock or index.

Examples
--------
  python analyse.py RELIANCE                      # live price + live NSE option chain
  python analyse.py NIFTY --expiry 25-Nov-2026     # pick an expiry
  python analyse.py TCS --oi-csv option-chain-ED-TCS-25-Nov-2026.csv   # NSE CSV download
  python analyse.py INFY --no-oi                   # technicals only
  python analyse.py --demo                         # offline demo with synthetic data
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from stock_analyser import open_interest as oi_mod
from stock_analyser import technicals as ta
from stock_analyser.report import console_report, html_report
from stock_analyser.signals import combine


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="OI + technical analysis for NSE symbols")
    p.add_argument("symbol", nargs="?", default="DEMO", help="NSE symbol, e.g. RELIANCE, NIFTY")
    p.add_argument("--expiry", help="Option expiry as DD-Mon-YYYY (default: nearest)")
    p.add_argument("--oi-csv", help="Option chain CSV downloaded from nseindia.com/option-chain")
    p.add_argument("--oi-json", help="Saved NSE option-chain JSON response")
    p.add_argument("--spot", type=float, help="Underlying price (only needed with --oi-csv if it can't be inferred)")
    p.add_argument("--price-csv", help="Daily OHLCV CSV instead of Yahoo Finance")
    p.add_argument("--period", default="2y", help="Price history length for Yahoo (default 2y)")
    p.add_argument("--no-oi", action="store_true", help="Skip open interest analysis")
    p.add_argument("--demo", action="store_true", help="Run on synthetic data (no internet needed)")
    p.add_argument("--out", default="reports", help="Folder for the HTML/JSON report")
    a = p.parse_args(argv)

    symbol = a.symbol.upper()
    if a.demo:
        symbol = "DEMO"

    # ---------------- prices + technicals
    if a.demo:
        from stock_analyser.demo_data import demo_prices
        prices = demo_prices()
    elif a.price_csv:
        prices = ta.load_prices_csv(a.price_csv)
    else:
        try:
            prices = ta.load_prices(symbol, a.period)
        except Exception as e:
            print(f"[!] Could not download prices for {symbol}: {str(e).splitlines()[0][:200]}\n"
                  "    Check the symbol and your internet, or pass a CSV with --price-csv.",
                  file=sys.stderr)
            return 1
    if len(prices) < 60:
        print("Need at least 60 daily bars for the indicators.", file=sys.stderr)
        return 1
    df = ta.add_indicators(prices)
    tech = ta.technical_signals(df)
    sup, res = ta.swing_levels(df)
    levels = {"swing_support": sup, "swing_resistance": res, "pivots": ta.pivots(df)}

    # ---------------- open interest
    oi_result, chain = None, None
    if not a.no_oi:
        futures = None
        try:
            if a.demo:
                from stock_analyser.demo_data import demo_chain
                chain = demo_chain(float(df["Close"].iloc[-1]))
            elif a.oi_csv:
                chain = oi_mod.parse_nse_csv(a.oi_csv, symbol, a.spot, a.expiry or "")
            elif a.oi_json:
                chain = oi_mod.parse_nse_json(json.loads(Path(a.oi_json).read_text()), symbol, a.expiry)
            else:
                from stock_analyser.nse_client import NSEClient
                nse = NSEClient()
                chain = oi_mod.parse_nse_json(nse.get_option_chain_raw(symbol, a.expiry), symbol, a.expiry)
                try:
                    futures = oi_mod.parse_futures(nse.get_futures_raw(symbol))
                except Exception as e:  # futures are a bonus; don't fail the run
                    print(f"(futures OI skipped: {e})", file=sys.stderr)
            oi_result = oi_mod.analyse_option_chain(chain, futures)
        except Exception as e:
            print(f"\n[!] Open interest step failed: {e}\n"
                  "    NSE often blocks scripts. Download the CSV from "
                  "https://www.nseindia.com/option-chain and re-run with --oi-csv <file>.\n",
                  file=sys.stderr)

    combo = combine(tech, oi_result.signals if oi_result else [])

    # ---------------- output
    print(console_report(symbol, df, tech, oi_result, combo, levels))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    html_path = out / f"{symbol}_{stamp}.html"
    html_path.write_text(html_report(symbol, df, tech, oi_result, chain.df if chain else None,
                                     combo, levels), encoding="utf-8")
    summary = {"symbol": symbol, "date": str(df.index[-1].date()), "close": float(df["Close"].iloc[-1]),
               "scores": {k: v for k, v in combo.items() if k != "verdict"},
               "verdict": combo["verdict"][0], "technical_signals": tech,
               "oi_signals": oi_result.signals if oi_result else [], "levels": levels}
    (out / f"{symbol}_{stamp}.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nReport saved: {html_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
