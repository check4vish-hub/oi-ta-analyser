import json
import re

import dashboard as dash
from analyse import build_parser, run_analysis
from stock_analyser.dashboard import build_html, summarise


def _demo(sym="ABC", seed=101):
    opts = build_parser().parse_args(["X", "--demo"])
    return run_analysis(sym, opts, seed=seed, quiet=True)


def test_summarise_row_fields():
    row = summarise(_demo(), "scan/ABC.html")
    for k in ("symbol", "score", "verdict", "tech", "oi_score", "pcr", "rsi", "delivery", "rs20",
              "mwpl", "exp_move", "tags", "groups", "levels", "report"):
        assert k in row
    assert row["verdict"] in row["tags"]
    assert len(row["groups"]) == 4 and row["groups"][0]["signals"]
    json.dumps(row)   # must be JSON-serialisable


def test_build_html_embeds_valid_json():
    rows = [summarise(_demo("AAA", 101)), summarise(_demo("BBB", 102))]
    page = build_html(rows, [("BAD", "no data")], {"vix": {"value": "14", "note": "calm"}}, "T")
    m = re.search(r"const D = (.*?);\nconst rows", page, re.S)
    data = json.loads(m.group(1).replace("<\\/", "</"))
    assert [r["symbol"] for r in data["rows"]] == sorted(["AAA", "BBB"], key=lambda s: -next(
        r["score"] for r in rows if r["symbol"] == s))
    assert data["failed"] == [["BAD", "no data"]]


def test_read_watchlist(tmp_path):
    p = tmp_path / "w.txt"
    p.write_text("# comment\nnifty, tcs\nTCS infy  # trailing\n\n")
    assert dash.read_watchlist(p) == ["NIFTY", "TCS", "INFY"]


def test_demo_scan_end_to_end(tmp_path):
    assert dash.main(["--demo", "--no-open", "--out", str(tmp_path), "AAA", "BBB", "CCC"]) == 0
    pages = list(tmp_path.glob("dashboard_2*.html"))
    assert len(pages) == 1 and (tmp_path / "dashboard_latest.html").exists()
    assert len(list(tmp_path.glob("scan_*/*.html"))) == 3
