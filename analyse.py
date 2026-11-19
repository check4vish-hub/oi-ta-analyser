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

import pandas as pd

from stock_analyser import open_interest as oi_mod
from stock_analyser import technicals as ta
from stock_analyser.report import console_report, html_report
from stock_analyser.signals import combine


def build_parser() -> argparse.ArgumentParser:
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
    p.add_argument("--no-context", action="store_true",
                   help="Skip extra data (delivery %%, MWPL, relative strength, VIX) for a faster run")
    p.add_argument("--sector-index", help="Yahoo code of the sector index, e.g. ^CNXIT, ^NSEBANK")
    p.add_argument("--out", default="reports", help="Folder for the HTML/JSON report")
    return p


class AnalysisError(RuntimeError):
    pass


def run_analysis(symbol: str, a, nse=None, seed: int | None = None, quiet: bool = False) -> dict:
    """Analyse one symbol. Returns a dict with everything the reports need.

    `nse` lets a scanner re-use one NSE session for many symbols; `seed` varies demo data.
    """
    symbol = symbol.upper()
    log = (lambda *x, **k: None) if quiet else (lambda *x, **k: print(*x, file=sys.stderr, **k))

    # ---------------- prices + technicals
    if a.demo:
        from stock_analyser.demo_data import demo_prices
        prices = demo_prices(seed=seed if seed is not None else 7)
    elif a.price_csv:
        prices = ta.load_prices_csv(a.price_csv)
    else:
        try:
            prices = ta.load_prices(symbol, a.period)
        except Exception as e:
            raise AnalysisError(f"Could not download prices for {symbol}: {str(e).splitlines()[0][:200]}. "
                                "Check the symbol and your internet, or pass a CSV with --price-csv.")
    if len(prices) < 60:
        raise AnalysisError(f"{symbol}: need at least 60 daily bars for the indicators.")
    df = ta.add_indicators(prices)
    tech = ta.technical_signals(df)
    sup, res = ta.swing_levels(df)
    levels = {"swing_support": sup, "swing_resistance": res, "pivots": ta.pivots(df)}

    # ---------------- open interest
    oi_result, chain, futures = None, None, None
    live_nse = not (a.demo or a.oi_csv or a.oi_json)
    if not a.no_oi:
        try:
            if a.demo:
                from stock_analyser.demo_data import demo_chain, demo_futures
                chain = demo_chain(float(df["Close"].iloc[-1]), seed=(seed or 3) + 1)
                chain.symbol = symbol
                futures = demo_futures(chain.spot, seed=seed)
            elif a.oi_csv:
                chain = oi_mod.parse_nse_csv(a.oi_csv, symbol, a.spot, a.expiry or "")
            elif a.oi_json:
                chain = oi_mod.parse_nse_json(json.loads(Path(a.oi_json).read_text()), symbol, a.expiry)
            else:
                if nse is None:
                    from stock_analyser.nse_client import NSEClient
                    nse = NSEClient()
                chain = oi_mod.parse_nse_json(nse.get_option_chain_raw(symbol, a.expiry), symbol, a.expiry)
            if live_nse or a.oi_csv or a.oi_json:
                try:
                    if nse is None:
                        from stock_analyser.nse_client import NSEClient
                        nse = NSEClient()
                    futures = oi_mod.parse_futures(nse.get_futures_raw(symbol))
                except Exception as e:  # futures are a bonus; don't fail the run
                    log(f"({symbol}: futures data skipped: {_short(e)})")
            oi_result = oi_mod.analyse_option_chain(chain, futures)
        except Exception as e:
            log(f"\n[!] {symbol}: open interest step failed: {_short(e)}\n"
                "    NSE often blocks scripts. Download the CSV from "
                "https://www.nseindia.com/option-chain and re-run with --oi-csv <file>.\n")

    # ---------------- positioning / cash market / volatility context
    cash, vol, skipped = [], [], []
    if not a.no_context:
        cash, vol, extra_oi, skipped = collect_context(symbol, df, oi_result, futures, nse, a, seed)
        if oi_result is not None:
            oi_result.signals.extend(extra_oi)
    if oi_result is None and not a.no_oi:
        skipped.insert(0, "Open interest: option chain unavailable")

    combo = combine(tech + cash, oi_result.signals if oi_result else [])
    return {"symbol": symbol, "df": df, "tech": tech, "cash": cash, "vol": vol, "oi": oi_result,
            "chain": chain, "futures": futures, "combo": combo, "levels": levels,
            "skipped": skipped, "nse": nse}


def write_reports(r: dict, out: Path) -> Path:
    """Write the detailed HTML + JSON report for one analysis result; returns the HTML path."""
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    sym, df, oi_result, combo = r["symbol"], r["df"], r["oi"], r["combo"]
    html_path = out / f"{sym}_{stamp}.html"
    html_path.write_text(html_report(sym, df, r["tech"], oi_result, r["chain"].df if r["chain"] else None,
                                     combo, r["levels"], r["cash"], r["vol"], r["skipped"]),
                         encoding="utf-8")
    summary = {"symbol": sym, "date": str(df.index[-1].date()), "close": float(df["Close"].iloc[-1]),
               "scores": {k: v for k, v in combo.items() if k != "verdict"},
               "verdict": combo["verdict"][0], "technical_signals": r["tech"],
               "cash_market_signals": r["cash"],
               "oi_signals": oi_result.signals if oi_result else [], "volatility_signals": r["vol"],
               "oi_timestamp": oi_result.timestamp if oi_result else None,
               "skipped": r["skipped"], "levels": r["levels"]}
    (out / f"{sym}_{stamp}.json").write_text(json.dumps(summary, indent=2, default=str))
    return html_path


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    symbol = "DEMO" if a.demo else a.symbol.upper()
    try:
        r = run_analysis(symbol, a)
    except AnalysisError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 1
    print(console_report(symbol, r["df"], r["tech"], r["oi"], r["combo"], r["levels"],
                         r["cash"], r["vol"], r["skipped"]))
    html_path = write_reports(r, Path(a.out))
    print(f"\nReport saved: {html_path.resolve()}")
    return 0


def _short(e) -> str:
    """Turn long network errors into a short reason for the 'Skipped' line."""
    t = str(e)
    if any(k in t for k in ("Max retries", "CONNECT", "ConnectionError", "timed out", "Failed to perform")):
        return "could not connect"
    if "HTTP 404" in t or "404" in t[:40]:
        return "not published yet / moved (404)"
    return t.splitlines()[0][:80] if t else type(e).__name__


def collect_context(symbol, df, oi_result, futures, nse, a, seed=None):
    """Gather extra data. Each piece is optional - failures are listed, never fatal."""
    from stock_analyser import market_context as mc

    cash, vol, extra_oi, skipped = [], [], [], []
    is_index = symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
    dte = mc.days_to_expiry(oi_result.expiry) if oi_result and oi_result.expiry else None
    if dte is None and futures:
        dte = mc.days_to_expiry(futures["expiry"])
    hv20 = mc.hist_vol(df["Close"])

    def attempt(label, fn):
        try:
            sig = fn()
            if sig is None:
                skipped.append(f"{label}: no data")
            return sig
        except Exception as e:
            skipped.append(f"{label}: {_short(e)}")
            return None

    if a.demo:
        from stock_analyser.demo_data import demo_context
        ctx = demo_context(df, seed=(seed or 11) + 4)
        nifty, vix, deliv, mwpl_row, banned = ctx["nifty"], ctx["vix"], ctx["delivery"], ctx["mwpl"], False
        sector = (ctx["sector"], "Demo sector")
    else:
        nifty = vix = deliv = mwpl_row = sector = None
        banned = False

    # ---- 1. derivatives positioning
    if futures and oi_result:
        s = attempt("Futures basis", lambda: mc.basis_signal(oi_result.spot, futures["last_price"], dte))
        if s: extra_oi.append(s)
        s = attempt("Rollover", lambda: mc.rollover_signal(futures.get("curve", []), dte))
        if s: extra_oi.append(s)
    elif oi_result:
        skipped.append("Futures basis / rollover: futures data unavailable")

    if not is_index:
        if not a.demo:
            fetcher = None
            try:
                fetcher = mc.ContextFetcher(nse)
            except Exception as e:
                skipped.append(f"NSE archives: {_short(e)}")
            if fetcher:
                try:
                    banned = symbol in fetcher.ban_list()
                except Exception as e:
                    skipped.append(f"F&O ban list: {_short(e)}")
                try:
                    mwpl_row = fetcher.mwpl(symbol)
                except Exception as e:
                    skipped.append(f"MWPL: {_short(e)}")
                try:
                    deliv = fetcher.delivery_history(symbol)
                except Exception as e:
                    skipped.append(f"Delivery %: {_short(e)}")
        s = mc.mwpl_signal(mwpl_row, banned)
        if s: vol.append(s)
        else: skipped.append("MWPL / ban: no data")

        # ---- 2. cash market
        s = attempt("Delivery %", lambda: mc.delivery_signal(deliv or []))
        if s: cash.append(s)

    close = df["Close"].copy()
    close.index = pd.to_datetime(close.index).normalize()
    if not is_index:
        if nifty is None:
            nifty = attempt("Nifty 50 prices", lambda: mc.load_yahoo("^NSEI", "1y"))
        if nifty is not None:
            s = attempt("RS vs Nifty", lambda: mc.rs_signal(close, nifty, "Nifty 50"))
            if s: cash.append(s)
    if not a.demo:
        if a.sector_index:
            sector = (a.sector_index, a.sector_index)
        elif not is_index:
            sector = mc.detect_sector_index(symbol)
    if sector:
        sec_series = ctx["sector_series"] if a.demo else attempt(
            f"{sector[1]} prices", lambda: mc.load_yahoo(sector[0], "1y"))
        if sec_series is not None:
            s = attempt("RS vs sector", lambda: mc.rs_signal(close, sec_series, sector[1]))
            if s: cash.append(s)
    elif not is_index:
        skipped.append("RS vs sector: sector not detected (use --sector-index ^CNXIT etc.)")

    # ---- 3. volatility
    if vix is None:
        vix = attempt("India VIX", lambda: mc.load_yahoo("^INDIAVIX", "1y"))
    if vix is not None:
        s = attempt("India VIX", lambda: mc.vix_signal(vix))
        if s: vol.append(s)
    if oi_result:
        s = mc.iv_hv_signal(oi_result.atm_iv, hv20)
        if s: vol.append(s)
        s = mc.straddle_signal(oi_result.expected_move_pct, hv20, dte)
        if s: vol.append(s)
        if not a.demo:
            hist = attempt("IV log", lambda: mc.log_iv(symbol, oi_result.atm_iv))
            if hist is not None:
                s = mc.iv_percentile_signal(hist, symbol, oi_result.atm_iv)
                if s: vol.append(s)
    return cash, vol, extra_oi, skipped


if __name__ == "__main__":
    sys.exit(main())
