"""Console summary and a self-contained dark-theme HTML report with charts."""
from __future__ import annotations

import html
from datetime import datetime

import pandas as pd

from .open_interest import OIResult, fmt_oi

DISCLAIMER = ("Educational, AI-assisted research output generated from public market data. "
              "Not investment advice and not a recommendation to buy or sell. "
              "Derivatives carry high risk; do your own research.")

ICON = {1: "▲", 0: "●", -1: "▼", None: "◆"}
COLOUR = {"green": "#22c55e", "amber": "#f59e0b", "red": "#ef4444", "blue": "#38bdf8"}


def _join(vals):
    return ", ".join(f"{v:g}" for v in vals) or "-"


def _fmt_levels(levels):
    return ", ".join(f"{s:g} ({fmt_oi(v)})" for s, v in levels) or "-"


# --------------------------------------------------------------------------- console
def _lines(signals):
    return [f"  {ICON[s['score']]} {s['name']:<26} {str(s['value']):<40} {s['note']}" for s in signals]


def console_report(symbol: str, df: pd.DataFrame, tech: list[dict], oi: OIResult | None,
                   combo: dict, levels: dict, cash: list | None = None, vol: list | None = None,
                   skipped: list | None = None) -> str:
    x = df.iloc[-1]
    lines = [
        "=" * 72,
        f" {symbol}  |  Close {x['Close']:.2f}  |  {df.index[-1].date()}",
        "=" * 72,
        f" OVERALL: {combo['verdict'][0].upper()}  (score {combo['overall']:+d} / 100)",
        f"   Technical {combo['technical']:+d}" +
        (f"   |   Open Interest {combo['oi']:+d}" if combo["oi"] is not None else "   |   OI: not run"),
    ]
    if combo["aligned"] is not None:
        lines.append("   Technicals and OI " + ("AGREE" if combo["aligned"] else "do NOT clearly agree"))
    lines += ["   (▲ bullish  ▼ bearish  ● neutral  ◆ context - not scored)", "", " TECHNICAL SIGNALS"]
    lines += _lines(tech)
    lines += ["", f" Price levels: support {_join(levels['swing_support'])}  |  resistance {_join(levels['swing_resistance'])}",
              "   Pivots: " + "  ".join(f"{k} {v:.2f}" for k, v in levels["pivots"].items()),
              f"   ATR(14) {x['ATR']:.2f}  ->  1-ATR stop distance {x['ATR'] / x['Close'] * 100:.1f}%"]
    if cash:
        lines += ["", " CASH-MARKET CONFIRMATION (counted in technical score)"] + _lines(cash)
    if oi:
        asof = f", data as of {oi.timestamp}" if oi.timestamp else ""
        lines += ["", f" OPEN INTEREST & FUTURES  (expiry {oi.expiry}, spot {oi.spot:.2f}{asof}; OI in contracts)"]
        lines += _lines(oi.signals)
        lines += [f"   Put-OI supports   : {_fmt_levels(oi.supports)}",
                  f"   Call-OI resistance: {_fmt_levels(oi.resistances)}",
                  f"   Fresh put writing : {_fmt_levels(oi.fresh_put_writing)}",
                  f"   Fresh call writing: {_fmt_levels(oi.fresh_call_writing)}"]
        if oi.expected_move_pct:
            lines.append(f"   ATM {oi.atm_strike:g} straddle {oi.straddle:.2f} -> market expects "
                         f"±{oi.expected_move_pct:.1f}% by expiry (ATM IV {oi.atm_iv})")
    if vol:
        lines += ["", " VOLATILITY & RISK"] + _lines(vol)
    if skipped:
        lines += ["", " Skipped (data source unavailable): " + "; ".join(skipped)]
    lines += ["", " " + DISCLAIMER, "=" * 72]
    return "\n".join(lines)


# --------------------------------------------------------------------------- html
def _price_chart(df: pd.DataFrame, symbol: str) -> str:
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    d = df.tail(180)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.2, 0.2],
                        vertical_spacing=0.03, subplot_titles=("", "RSI (14)", "MACD"))
    fig.add_trace(go.Candlestick(x=d.index, open=d.Open, high=d.High, low=d.Low, close=d.Close,
                                 name="Price"), 1, 1)
    for col, colr in (("EMA21", "#38bdf8"), ("SMA50", "#f59e0b"), ("SMA200", "#a78bfa")):
        fig.add_trace(go.Scatter(x=d.index, y=d[col], name=col, line=dict(width=1.3, color=colr)), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d.BB_upper, name="BB upper",
                             line=dict(width=0.8, dash="dot", color="#64748b")), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d.BB_lower, name="BB lower",
                             line=dict(width=0.8, dash="dot", color="#64748b")), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d.Supertrend, name="Supertrend", mode="markers",
                             marker=dict(size=3, color=["#22c55e" if v > 0 else "#ef4444"
                                                        for v in d.ST_dir])), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d.RSI, name="RSI", line=dict(color="#38bdf8")), 2, 1)
    for lvl in (30, 70):
        fig.add_hline(y=lvl, line=dict(dash="dash", color="#475569", width=1), row=2, col=1)
    fig.add_trace(go.Bar(x=d.index, y=d.MACD_hist, name="MACD hist",
                         marker_color=["#22c55e" if v >= 0 else "#ef4444" for v in d.MACD_hist]), 3, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d.MACD, name="MACD", line=dict(color="#38bdf8", width=1)), 3, 1)
    fig.add_trace(go.Scatter(x=d.index, y=d.MACD_signal, name="Signal",
                             line=dict(color="#f59e0b", width=1)), 3, 1)
    fig.update_layout(template="plotly_dark", height=820, xaxis_rangeslider_visible=False,
                      paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                      legend=dict(orientation="h", y=1.0, yanchor="bottom"), margin=dict(l=40, r=20, t=50, b=30))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def _oi_chart(oi_df: pd.DataFrame, r: OIResult) -> str:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    atm_i = (oi_df.strike - r.spot).abs().idxmin()
    d = oi_df.iloc[max(0, atm_i - 15): atm_i + 16]
    fig = make_subplots(rows=1, cols=2, subplot_titles=(
        f"Open Interest by strike  (blue = spot {r.spot:.2f}, orange = max pain {r.max_pain:g})",
        "Change in OI today"))
    fig.add_trace(go.Bar(x=d.strike, y=d.ce_oi, name="Call OI", marker_color="#ef4444"), 1, 1)
    fig.add_trace(go.Bar(x=d.strike, y=d.pe_oi, name="Put OI", marker_color="#22c55e"), 1, 1)
    fig.add_trace(go.Bar(x=d.strike, y=d.ce_chg_oi, name="Call ΔOI", marker_color="#f87171",
                         showlegend=False), 1, 2)
    fig.add_trace(go.Bar(x=d.strike, y=d.pe_chg_oi, name="Put ΔOI", marker_color="#4ade80",
                         showlegend=False), 1, 2)
    for col in (1, 2):
        fig.add_vline(x=r.spot, line=dict(color="#38bdf8", width=2), row=1, col=col)
        fig.add_vline(x=r.max_pain, line=dict(color="#f59e0b", dash="dash"), row=1, col=col)
    fig.update_layout(template="plotly_dark", barmode="group", height=430,
                      paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                      margin=dict(l=40, r=20, t=70, b=30), legend=dict(orientation="h", y=-0.15))
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _rows(signals):
    out = []
    for s in signals:
        c = {1: COLOUR["green"], 0: COLOUR["amber"], -1: COLOUR["red"], None: COLOUR["blue"]}[s["score"]]
        lab = {1: "Bullish", 0: "Neutral", -1: "Bearish", None: "Context"}[s["score"]]
        out.append(f"<tr><td>{html.escape(s['name'])}</td><td>{html.escape(str(s['value']))}</td>"
                   f"<td><span class='badge' style='background:{c}'>{lab}</span></td>"
                   f"<td>{html.escape(s['note'])}</td></tr>")
    return "\n".join(out)


def _table(signals):
    return ("<table><tr><th>Check</th><th>Value</th><th>Read</th><th>Why</th></tr>"
            f"{_rows(signals)}</table>")


def html_report(symbol: str, df: pd.DataFrame, tech: list[dict], oi: OIResult | None,
                oi_df: pd.DataFrame | None, combo: dict, levels: dict, cash: list | None = None,
                vol: list | None = None, skipped: list | None = None) -> str:
    x = df.iloc[-1]
    label, colr = combo["verdict"]
    oi_block = ""
    if oi is not None:
        extra = ""
        if oi.expected_move_pct:
            extra = (f"<p>ATM {oi.atm_strike:g} straddle costs <b>{oi.straddle:.2f}</b> → the options market "
                     f"is pricing roughly <b>±{oi.expected_move_pct:.1f}%</b> by expiry "
                     f"(range {oi.spot - oi.straddle:.0f} – {oi.spot + oi.straddle:.0f}). ATM IV {oi.atm_iv}.</p>")
        oi_block = f"""
        <h2>Open Interest &amp; futures <small>expiry {html.escape(oi.expiry)} · score {'—' if combo['oi'] is None else f"{combo['oi']:+d}"} · OI in contracts{(' · data as of ' + html.escape(oi.timestamp)) if oi.timestamp else ''}</small></h2>
        <table><tr><th>Check</th><th>Value</th><th>Read</th><th>Why</th></tr>{_rows(oi.signals)}</table>
        <div class="grid">
          <div class="card"><h3>Put-OI supports</h3><p>{_fmt_levels(oi.supports)}</p></div>
          <div class="card"><h3>Call-OI resistance</h3><p>{_fmt_levels(oi.resistances)}</p></div>
          <div class="card"><h3>Fresh put writing</h3><p>{_fmt_levels(oi.fresh_put_writing)}</p></div>
          <div class="card"><h3>Fresh call writing</h3><p>{_fmt_levels(oi.fresh_call_writing)}</p></div>
        </div>{extra}
        {_oi_chart(oi_df, oi)}"""
    piv = " · ".join(f"{k} {v:.2f}" for k, v in levels["pivots"].items())
    agree = ""
    if combo["aligned"] is not None:
        agree = ("Technicals and OI point the same way." if combo["aligned"]
                 else "Technicals and OI are not clearly aligned — treat the signal with more caution.")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(symbol)} – OI & Technical Analysis</title>
<style>
body{{background:#0b1120;color:#e2e8f0;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px;}}
.wrap{{max-width:1150px;margin:auto}} h1{{margin:0}} h2{{margin-top:36px;border-bottom:1px solid #1e293b;padding-bottom:6px}}
small{{color:#94a3b8;font-weight:400;font-size:14px}}
.hero{{display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin:18px 0}}
.verdict{{font-size:28px;font-weight:700;padding:10px 22px;border-radius:10px;background:{COLOUR[colr]}22;
border:1px solid {COLOUR[colr]};color:{COLOUR[colr]}}}
.kpi{{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:10px 16px}}
.kpi b{{display:block;font-size:20px}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:left;padding:8px;border-bottom:1px solid #1e293b}}
th{{color:#94a3b8;font-weight:500}} .badge{{padding:2px 10px;border-radius:999px;color:#0b1120;font-weight:600;font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:16px 0}}
.card{{background:#111827;border:1px solid #1e293b;border-radius:10px;padding:12px}} .card h3{{margin:0 0 6px;font-size:14px;color:#94a3b8}}
.disc{{margin-top:36px;color:#94a3b8;font-size:12px;border-top:1px solid #1e293b;padding-top:12px}}
</style></head><body><div class="wrap">
<h1>{html.escape(symbol)} <small>close {x['Close']:.2f} · {df.index[-1].date()} · generated {datetime.now():%d %b %Y %H:%M}</small></h1>
<div class="hero"><div class="verdict">{label}</div>
<div class="kpi">Overall<b>{combo['overall']:+d}</b></div>
<div class="kpi">Technical<b>{combo['technical']:+d}</b></div>
<div class="kpi">Open Interest<b>{'—' if combo['oi'] is None else f"{combo['oi']:+d}"}</b></div>
<div class="kpi">RSI<b>{x['RSI']:.0f}</b></div><div class="kpi">ATR %<b>{x['ATR'] / x['Close'] * 100:.1f}%</b></div></div>
<p>{agree}</p>
<h2>Technical analysis <small>score {combo['technical']:+d}</small></h2>
<table><tr><th>Check</th><th>Value</th><th>Read</th><th>Why</th></tr>{_rows(tech)}</table>
<div class="grid">
<div class="card"><h3>Swing support</h3><p>{_join(levels['swing_support'])}</p></div>
<div class="card"><h3>Swing resistance</h3><p>{_join(levels['swing_resistance'])}</p></div>
<div class="card"><h3>Floor pivots (next session)</h3><p>{piv}</p></div></div>
{_price_chart(df, symbol)}
{('<h2>Cash-market confirmation <small>counted in the technical score</small></h2>' + _table(cash)) if cash else ''}
{oi_block}
{('<h2>Volatility &amp; risk <small>context - not scored</small></h2>' + _table(vol)) if vol else ''}
{('<p class="disc">Skipped (source unavailable): ' + html.escape('; '.join(skipped)) + '</p>') if skipped else ''}
<p class="disc">{DISCLAIMER}</p></div></body></html>"""
