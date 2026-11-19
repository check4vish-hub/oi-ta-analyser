"""
Watchlist dashboard: one interactive HTML page that ranks every scanned stock and
lets you filter for setups (bullish & aligned, long build-up, cheap options...).

No server needed - the page is a single file with the data embedded.
"""
from __future__ import annotations

import html
import json
import math
from datetime import datetime

from .report import DISCLAIMER


def _clean(v):
    """JSON-safe numbers (NaN -> None)."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _by_name(signals):
    return {s["name"]: s for s in signals}


def summarise(r: dict, report_link: str | None = None) -> dict:
    """Condense one run_analysis() result into a dashboard row."""
    df, combo, oi, fut = r["df"], r["combo"], r["oi"], r["futures"]
    x, prev = df.iloc[-1], df.iloc[-2]
    oi_sigs = oi.signals if oi else []
    allsig = _by_name(r["tech"] + r["cash"] + r["vol"] + oi_sigs)
    rs = next((s for s in r["cash"] if s["name"] == "RS vs Nifty 50"), None)
    get = lambda name: allsig.get(name)  # noqa: E731

    tags = [combo["verdict"][0]]
    if combo["aligned"]:
        tags.append("Aligned")
    if fut:
        tags.append(fut["buildup"])
    mw = get("MWPL / F&O ban")
    if mw:
        if "BAN" in str(mw["value"]):
            tags.append("In F&O ban")
        elif (mw.get("metric") or 0) >= 80:
            tags.append("Near ban")
    st, ih = get("Straddle vs normal move"), get("Stock IV vs realised vol")
    ratio = (st or {}).get("metric") or (ih or {}).get("metric")
    if ratio:
        cheap_cut, rich_cut = (0.8, 1.25) if st else (0.85, 1.3)
        if ratio <= cheap_cut:
            tags.append("Options cheap")
        elif ratio >= rich_cut:
            tags.append("Options expensive")
    dl = get("Delivery %")
    if dl and dl["score"] == 1:
        tags.append("Delivery buying")
    elif dl and dl["score"] == -1:
        tags.append("Delivery selling")
    if rs:
        tags.append({1: "Outperformer", -1: "Underperformer"}.get(rs["score"], "")) if rs["score"] else None
    if "just flipped" in (get("Supertrend (10,3)") or {}).get("note", ""):
        tags.append("Supertrend flip")
    note50 = (get("50-DMA vs 200-DMA") or {}).get("note", "")
    if "Golden" in note50:
        tags.append("Golden cross")
    elif "Death" in note50:
        tags.append("Death cross")
    if x["RSI"] >= 70:
        tags.append("RSI overbought")
    elif x["RSI"] <= 30:
        tags.append("RSI oversold")
    if oi and oi.resistances and abs(oi.resistances[0][0] - oi.spot) / oi.spot <= 0.01:
        tags.append("At call wall")
    if oi and oi.supports and abs(oi.spot - oi.supports[0][0]) / oi.spot <= 0.01:
        tags.append("At put wall")
    tags = [t for t in tags if t]

    groups = [{"title": "Technical", "signals": r["tech"]},
              {"title": "Cash-market confirmation", "signals": r["cash"]},
              {"title": "Open interest & futures", "signals": oi_sigs},
              {"title": "Volatility & risk", "signals": r["vol"]}]
    lv = r["levels"]
    levels = {
        "Swing support": ", ".join(f"{v:g}" for v in lv["swing_support"]) or "-",
        "Swing resistance": ", ".join(f"{v:g}" for v in lv["swing_resistance"]) or "-",
        "Pivots": "  ".join(f"{k} {v:.2f}" for k, v in lv["pivots"].items()),
    }
    if oi:
        levels["Put-OI support"] = ", ".join(f"{s:g}" for s, _ in oi.supports) or "-"
        levels["Call-OI resistance"] = ", ".join(f"{s:g}" for s, _ in oi.resistances) or "-"
        if oi.expected_move_pct and oi.straddle:
            levels["Expected range"] = (f"{oi.spot - oi.straddle:.0f} – {oi.spot + oi.straddle:.0f} "
                                        f"(±{oi.expected_move_pct:.1f}% by {oi.expiry})")
    return {k: _clean(v) for k, v in {
        "symbol": r["symbol"], "date": str(df.index[-1].date()),
        "close": round(float(x["Close"]), 2), "chg": round(float((x["Close"] / prev["Close"] - 1) * 100), 2),
        "score": combo["overall"], "verdict": combo["verdict"][0], "colour": combo["verdict"][1],
        "tech": combo["technical"], "oi_score": combo["oi"], "aligned": combo["aligned"],
        "rsi": round(float(x["RSI"]), 1),
        "trend": None if x["SMA200"] != x["SMA200"] else ("Up" if x["Close"] > x["SMA200"] else "Down"),
        "supertrend": "Buy" if x["ST_dir"] > 0 else "Sell",
        "buildup": fut["buildup"] if fut else None,
        "pcr": oi.pcr_oi if oi else None, "max_pain": oi.max_pain if oi else None,
        "exp_move": oi.expected_move_pct if oi else None, "atm_iv": oi.atm_iv if oi else None,
        "expiry": oi.expiry if oi else None, "oi_time": oi.timestamp if oi else None,
        "delivery": dl.get("metric") if dl else None,
        "rs20": rs.get("metric") if rs else None,
        "mwpl": mw.get("metric") if mw else None,
        "vol_ratio": ratio,
        "tags": tags, "groups": groups, "levels": levels,
        "skipped": r["skipped"], "report": report_link,
    }.items()}


def build_html(rows: list[dict], failed: list[tuple[str, str]], market: dict, title: str) -> str:
    rows = sorted(rows, key=lambda r: r["score"], reverse=True)
    data = json.dumps({"rows": rows, "failed": failed, "market": market},
                      default=lambda o: None).replace("</", "<\\/")
    return (_TEMPLATE
            .replace("__TITLE__", html.escape(title))
            .replace("__GENERATED__", datetime.now().strftime("%d %b %Y, %H:%M"))
            .replace("__DISCLAIMER__", html.escape(DISCLAIMER))
            .replace("__DATA__", data))


_TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0b1120;--card:#111827;--line:#1e293b;--mut:#94a3b8;--txt:#e2e8f0;
--g:#22c55e;--a:#f59e0b;--r:#ef4444;--b:#38bdf8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,Segoe UI,Arial}
.wrap{max-width:1400px;margin:auto;padding:22px}
h1{margin:0;font-size:24px}.sub{color:var(--mut);margin-top:4px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:18px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .l{color:var(--mut);font-size:12px}.kpi .v{font-size:22px;font-weight:700;margin-top:2px}.kpi .s{color:var(--mut);font-size:12px}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 14px}
input,select{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}
input{min-width:200px}
.chip{border:1px solid var(--line);background:var(--card);color:var(--txt);border-radius:999px;padding:5px 12px;cursor:pointer;font-size:13px;user-select:none}
.chip.on{background:#1d4ed8;border-color:#3b82f6}.chip .n{color:var(--mut);margin-left:4px}
.chip.on .n{color:#dbeafe}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{color:var(--mut);font-weight:500;font-size:12px;cursor:pointer;position:sticky;top:0;background:#0f172a}
th:hover{color:var(--txt)}tr.row{cursor:pointer}tr.row:hover td{background:#0f1a2e}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;color:#0b1120}
.bg-green{background:var(--g)}.bg-amber{background:var(--a)}.bg-red{background:var(--r)}.bg-blue{background:var(--b)}
.up{color:var(--g)}.dn{color:var(--r)}.mut{color:var(--mut)}
.bar{position:relative;display:inline-block;vertical-align:middle;margin-right:6px;width:120px;height:8px;background:#1e293b;border-radius:4px}
.bar i{position:absolute;top:0;height:8px;border-radius:4px}.bar b{position:absolute;left:50%;top:-3px;width:1px;height:14px;background:#475569}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:6px;background:#1e293b;color:#cbd5e1;margin:1px 2px}
.detail td{background:#0d1526;white-space:normal;padding:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
.gcard{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.gcard h4{margin:0 0 8px;font-size:13px;color:var(--mut);font-weight:600}
.sig{display:grid;grid-template-columns:76px 1fr;gap:6px;padding:5px 0;border-top:1px solid #172033}
.sig:first-of-type{border-top:none}.sig .nm{font-weight:600}.sig .vl{color:#cbd5e1}.sig .nt{color:var(--mut);font-size:12px}
.lv{display:flex;flex-wrap:wrap;gap:8px 22px;margin:12px 0 4px}.lv div span{color:var(--mut);margin-right:6px}
a{color:var(--b)}.foot{color:var(--mut);font-size:12px;margin-top:22px;border-top:1px solid var(--line);padding-top:12px}
.fail{color:var(--a);margin-top:12px;font-size:13px}
@media(max-width:900px){.hide-sm{display:none}}
</style></head><body><div class="wrap">
<h1>__TITLE__</h1>
<div class="sub">Generated __GENERATED__ · click a row for the full breakdown · ▲ bullish ▼ bearish ● neutral ◆ context</div>
<div class="kpis" id="kpis"></div>
<div class="controls">
  <input id="q" placeholder="Search symbol…">
  <select id="sort">
    <option value="score:-1">Overall score: high → low</option>
    <option value="score:1">Overall score: low → high</option>
    <option value="tech:-1">Technical score</option>
    <option value="oi_score:-1">OI score</option>
    <option value="chg:-1">Day change %</option>
    <option value="rsi:-1">RSI</option>
    <option value="delivery:-1">Delivery %</option>
    <option value="rs20:-1">Relative strength (20d)</option>
    <option value="mwpl:-1">MWPL used %</option>
    <option value="vol_ratio:1">Options cheapest first</option>
    <option value="exp_move:-1">Expected move %</option>
  </select>
</div>
<div class="controls" id="chips"></div>
<table><thead><tr>
<th data-k="symbol">Stock</th><th data-k="close">Price</th><th data-k="score">Verdict</th><th data-k="score">Score</th>
<th data-k="tech" class="hide-sm">Tech</th><th data-k="oi_score" class="hide-sm">OI</th>
<th data-k="buildup">Futures</th><th data-k="pcr" class="hide-sm">PCR</th><th data-k="rsi" class="hide-sm">RSI</th>
<th data-k="trend" class="hide-sm">Trend · ST</th><th data-k="delivery" class="hide-sm">Deliv %</th>
<th data-k="rs20" class="hide-sm">RS 20d</th><th data-k="exp_move" class="hide-sm">Exp. move</th><th>Signals</th>
</tr></thead><tbody id="tb"></tbody></table>
<div id="fail" class="fail"></div>
<div class="foot">Scores run from −100 (all bearish) to +100 (all bullish): Bullish ≥ 50 · Mildly bullish ≥ 20 · Neutral · Mildly bearish ≤ −20 · Bearish ≤ −50. “Aligned” means technicals and OI agree. Volatility &amp; risk items are context and are not scored.<br>__DISCLAIMER__</div>
</div>
<script>
const D = __DATA__;
const rows = D.rows, M = D.market || {};
const CHIPS = ["Bullish","Mildly bullish","Neutral","Mildly bearish","Bearish","Aligned","Long build-up","Short build-up",
  "Short covering","Long unwinding","Options cheap","Options expensive","Delivery buying","Delivery selling",
  "Outperformer","Underperformer","Supertrend flip","Golden cross","Death cross","RSI overbought","RSI oversold",
  "At call wall","At put wall","Near ban","In F&O ban"];
let active = new Set(), sortKey = "score", sortDir = -1, open = new Set();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const num = (v, d=1, suf="") => v === null || v === undefined ? '<span class="mut">–</span>' : (+v).toFixed(d) + suf;
const sc = v => v === null || v === undefined ? '<span class="mut">–</span>' :
  `<span class="${v>0?'up':v<0?'dn':''}">${v>0?'+':''}${v}</span>`;
const badge = (txt, col) => `<span class="badge bg-${col}">${esc(txt)}</span>`;
const sigBadge = s => s===1?badge("Bullish","green"):s===-1?badge("Bearish","red"):s===0?badge("Neutral","amber"):badge("Context","blue");
function bar(v){const w=Math.abs(v)/2, c=v>=20?'var(--g)':v<=-20?'var(--r)':'var(--a)';
  return `<div class="bar"><b></b><i style="background:${c};width:${w}px;${v>=0?'left:60px':'left:'+(60-w)+'px'}"></i></div>`}
function kpis(){
  const n = rows.length, c = t => rows.filter(r => r.tags.includes(t)).length;
  const bull = rows.filter(r=>r.score>=20).length, bear = rows.filter(r=>r.score<=-20).length;
  let h = `<div class="kpi"><div class="l">Scanned</div><div class="v">${n}</div><div class="s">${D.failed.length} failed</div></div>
  <div class="kpi"><div class="l">Bullish (≥ +20)</div><div class="v up">${bull}</div><div class="s">${c("Aligned")} with technicals + OI aligned</div></div>
  <div class="kpi"><div class="l">Bearish (≤ −20)</div><div class="v dn">${bear}</div><div class="s">${n-bull-bear} neutral</div></div>
  <div class="kpi"><div class="l">Long / Short build-up</div><div class="v"><span class="up">${c("Long build-up")}</span> / <span class="dn">${c("Short build-up")}</span></div><div class="s">futures, today</div></div>`;
  if (M.nifty) h += `<div class="kpi"><div class="l">Nifty 50</div><div class="v">${M.nifty.last.toLocaleString('en-IN')}</div>
     <div class="s"><span class="${M.nifty.chg>=0?'up':'dn'}">${M.nifty.chg>=0?'+':''}${M.nifty.chg}%</span> · 5d ${M.nifty.chg5>=0?'+':''}${M.nifty.chg5}% · ${M.nifty.trend}</div></div>`;
  if (M.vix) h += `<div class="kpi"><div class="l">India VIX</div><div class="v">${M.vix.value}</div><div class="s">${esc(M.vix.note)}</div></div>`;
  if (M.ban !== undefined) h += `<div class="kpi"><div class="l">F&amp;O ban today</div><div class="v" style="font-size:15px">${M.ban.length? esc(M.ban.join(", ")) : "None"}</div></div>`;
  document.getElementById("kpis").innerHTML = h;
}
function chips(){
  const el = document.getElementById("chips");
  el.innerHTML = CHIPS.map(t => { const n = rows.filter(r => r.tags.includes(t)).length;
    return n ? `<span class="chip ${active.has(t)?'on':''}" data-t="${t}">${t}<span class="n">${n}</span></span>` : ""; }).join("")
    + (active.size ? `<span class="chip" data-t="__clear">✕ Clear filters</span>` : "");
  el.querySelectorAll(".chip").forEach(c => c.onclick = () => {
    const t = c.dataset.t; if (t === "__clear") active.clear(); else active.has(t) ? active.delete(t) : active.add(t); render(); });
}
function detail(r){
  const groups = r.groups.filter(g => g.signals.length).map(g => `<div class="gcard"><h4>${esc(g.title)}</h4>` +
    g.signals.map(s => `<div class="sig"><div>${sigBadge(s.score)}</div><div><span class="nm">${esc(s.name)}</span>
      · <span class="vl">${esc(s.value)}</span><div class="nt">${esc(s.note)}</div></div></div>`).join("") + `</div>`).join("");
  const lv = Object.entries(r.levels).map(([k,v]) => `<div><span>${esc(k)}</span>${esc(v)}</div>`).join("");
  const meta = [r.oi_time ? `OI data as of ${esc(r.oi_time)}` : "", `Price data ${esc(r.date)}`,
                r.report ? `<a href="${encodeURI(r.report)}" target="_blank">Open full chart report →</a>` : ""].filter(Boolean).join(" · ");
  const sk = r.skipped && r.skipped.length ? `<div class="mut" style="margin-top:8px;font-size:12px">Skipped: ${esc(r.skipped.join("; "))}</div>` : "";
  return `<tr class="detail"><td colspan="14"><div class="lv">${lv}</div><div class="grid">${groups}</div>
    <div style="margin-top:10px">${meta}</div>${sk}</td></tr>`;
}
function render(){
  chips();
  const q = document.getElementById("q").value.trim().toUpperCase();
  let list = rows.filter(r => (!q || r.symbol.includes(q)) && [...active].every(t => r.tags.includes(t)));
  list.sort((a,b) => { const x=a[sortKey], y=b[sortKey];
    if (x===null||x===undefined) return 1; if (y===null||y===undefined) return -1;
    return (x>y?1:x<y?-1:0) * sortDir; });
  document.getElementById("tb").innerHTML = list.map(r => {
    const tags = r.tags.filter(t => t !== r.verdict);
    const fut = r.buildup ? `<span class="${/Long build|Short cov/.test(r.buildup)?'up':/Short build|Long unw/.test(r.buildup)?'dn':''}">${esc(r.buildup)}</span>` : '<span class="mut">–</span>';
    const row = `<tr class="row" data-s="${esc(r.symbol)}">
      <td><b>${esc(r.symbol)}</b>${r.aligned?' <span title="Technicals and OI agree">✔</span>':''}</td>
      <td>${r.close.toLocaleString('en-IN')} <span class="${r.chg>=0?'up':'dn'}">${r.chg>=0?'+':''}${r.chg}%</span></td>
      <td>${badge(r.verdict, r.colour)}</td><td>${bar(r.score)} <span class="mut">${r.score>0?'+':''}${r.score}</span></td>
      <td class="hide-sm">${sc(r.tech)}</td><td class="hide-sm">${sc(r.oi_score)}</td><td>${fut}</td>
      <td class="hide-sm">${num(r.pcr,2)}</td><td class="hide-sm">${num(r.rsi,0)}</td>
      <td class="hide-sm">${r.trend?`<span class="${r.trend==='Up'?'up':'dn'}">${r.trend}</span>`:'–'} · <span class="${r.supertrend==='Buy'?'up':'dn'}">${r.supertrend}</span></td>
      <td class="hide-sm">${num(r.delivery,0,'%')}</td><td class="hide-sm">${r.rs20===null?'<span class="mut">–</span>':`<span class="${r.rs20>=0?'up':'dn'}">${r.rs20>0?'+':''}${r.rs20.toFixed(1)}%</span>`}</td>
      <td class="hide-sm">${r.exp_move===null?'<span class="mut">–</span>':'±'+r.exp_move.toFixed(1)+'%'}</td>
      <td style="white-space:normal">${tags.slice(0,4).map(t=>`<span class="tag">${esc(t)}</span>`).join("")}${tags.length>4?`<span class="tag">+${tags.length-4}</span>`:''}</td></tr>`;
    return row + (open.has(r.symbol) ? detail(r) : "");
  }).join("") || `<tr><td colspan="14" class="mut">No stocks match these filters.</td></tr>`;
  document.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => {
    const s = tr.dataset.s; open.has(s) ? open.delete(s) : open.add(s); render(); });
  document.getElementById("fail").innerHTML = D.failed.length ?
    "Could not analyse: " + D.failed.map(([s,e]) => `<b>${esc(s)}</b> (${esc(e)})`).join(" · ") : "";
}
document.getElementById("q").oninput = render;
document.getElementById("sort").onchange = e => { [sortKey, sortDir] = e.target.value.split(":"); sortDir = +sortDir; render(); };
document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {
  const k = th.dataset.k; if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = k === "symbol" ? 1 : -1; } render(); });
kpis(); render();
</script></body></html>"""
