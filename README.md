# OI + Technical Analyser for NSE stocks

A Python tool that analyses any **NSE F&O stock or index** (RELIANCE, TCS, NIFTY, BANKNIFTY…) from four angles and combines them into one verdict.

| Group | Checks | Data source |
|---|---|---|
| **Technical** (scored) | Price vs 200-DMA, 50/200 golden/death cross, EMA 9/21, Supertrend (10,3), RSI, MACD, ADX, Bollinger %B, volume vs 20-day average, 52-week position, swing support/resistance, pivots, ATR | Yahoo Finance daily prices |
| **Cash-market confirmation** (scored with technicals) | Delivery % vs its 10-day average, relative strength vs Nifty 50 and vs the stock's sector index (20 and 60 days, plus how it behaved on the index's down days) | NSE full bhavcopy, Yahoo Finance |
| **Open interest & futures** (scored) | PCR, change in OI today, max pain, put/call OI walls, fresh writing, futures build-up (long/short build-up, short covering, long unwinding), futures basis vs fair carry, rollover % and roll cost in expiry week | NSE option chain + derivatives quote |
| **Volatility & risk** (context, not scored) | India VIX level/trend/percentile, ATM IV vs 20-day historical volatility, IV percentile (from your own daily log), ATM straddle vs normal movement, MWPL usage and F&O ban status | Yahoo Finance, NSE archives |

Each scored check is **+1 bullish / 0 neutral / −1 bearish**. Technical + cash-market checks average into a Technical score, OI + futures checks into an OI score (both −100 to +100), weighted 60/40 into an **Overall** score. Volatility & risk items are shown as **context** (◆): they tell you *how* the stock may move and whether options are cheap or expensive, not *which way*.

If any data source is unavailable the tool keeps going and lists what it skipped at the bottom.

Output: a summary in the terminal, an **HTML report** (dark theme, candlestick + indicator charts, OI-by-strike chart), and a **JSON** file in `reports/`. Downloaded NSE files are cached in `data/cache/`, and your daily ATM IV log is kept in `data/iv_history.csv`.

---

## 1. Install (one time)

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>. On Windows, tick **"Add Python to PATH"** during setup.
2. Open a terminal (Windows: *Command Prompt*) inside this folder and run:

```bash
pip install -r requirements.txt
```

## 2. The dashboard (easiest way)

**Windows: double-click `Run Dashboard.bat`.** It scans every symbol in `watchlist.txt` and opens a dashboard in your browser:

- top tiles: how many stocks are bullish/bearish, long vs short build-up, Nifty 50, India VIX, today's F&O ban list
- a ranked table (best score first) with verdict badges, score bars, futures build-up, PCR, RSI, trend, delivery %, relative strength and expected move
- **filter chips** to spot setups, for example *Aligned* + *Long build-up*, or *Options cheap*
- click any row to expand the full breakdown, with a link to that stock's chart report

Edit `watchlist.txt` (Notepad is fine) to choose your stocks. Each symbol takes roughly 10–20 seconds, so 25 symbols take about 5–8 minutes. Other ways to run it:

```bash
python dashboard.py                    # same as the .bat file
python dashboard.py TCS INFY COFORGE   # just these symbols
python dashboard.py --fast             # skip delivery %, MWPL, RS and VIX (about twice as fast)
python dashboard.py --demo             # try it with made-up data
```

Each scan is saved in `reports/` as `dashboard_<date>_<time>.html`, plus `dashboard_latest.html`.

## 3. Single-stock analysis

```bash
python analyse.py --demo                 # try it first: synthetic data, no internet needed
python analyse.py RELIANCE               # live prices + live NSE option chain (nearest expiry)
python analyse.py NIFTY --expiry 25-Nov-2026
python analyse.py INFY --no-oi           # technicals only
python analyse.py SBIN --no-context      # faster: skip delivery %, MWPL, RS and VIX
python analyse.py COFORGE --sector-index ^CNXIT   # set the sector index yourself
```

Open the `.html` file that gets created in `reports/` in your browser.

### If NSE blocks the live option chain

NSE has no official free API, and its website sometimes refuses scripts. If you see `Open interest step failed`:

1. Open <https://www.nseindia.com/option-chain>, choose the stock and expiry.
2. Click **Download (.csv)**.
3. Run:

```bash
python analyse.py TCS --oi-csv option-chain-ED-TCS-25-Nov-2026.csv --expiry 25-Nov-2026
```

The spot price is worked out from the CSV automatically. Add `--spot 4125.5` if that fails.

### Using your own price data

If Yahoo Finance is down, export daily prices (from your broker, Screener.in or NSE) to a CSV with the columns `Date,Open,High,Low,Close,Volume` and run:

```bash
python analyse.py SBIN --price-csv sbin_daily.csv
```

## 4. Reading the output

| Score | Verdict |
|---|---|
| ≥ +50 | Bullish |
| +20 to +49 | Mildly bullish |
| −19 to +19 | Neutral |
| −49 to −20 | Mildly bearish |
| ≤ −50 | Bearish |

Notes on reading the OI numbers:
- **PCR** above 1.2 means put writers dominate, which usually acts as support. Above 1.7 the trade is crowded, so the tool scores it neutral rather than bullish. PCR below 0.7 is read the opposite way.
- **Max pain** pulls hardest in the last few days before expiry and only weakly mid-series.
- **OI walls** are where option writers have the most at stake. A decisive close through a wall, together with that wall's OI unwinding, often comes before a sharp move.
- **Expected move** (ATM straddle ÷ spot) is how far the options market thinks price can travel by expiry. The tool compares it with the stock's normal movement (20-day historical volatility): a rich straddle has historically favoured option sellers, a cheap one option buyers.
- **Futures basis** is compared with a fair cost of carry of about 6.5% a year. A discount to spot usually means heavy shorts.
- **Rollover** only matters in the last week before expiry. Rolling at a rich premium suggests longs are carried forward; at a discount, shorts.
- **Delivery %** above its recent average on an up day suggests genuine buying; low delivery means mostly intraday trading.
- **MWPL**: at 95% usage a stock enters the F&O ban (no new positions). Stocks near the limit often move sharply as positions are forced to unwind.
- **IV percentile** needs about 20 days of history. Run the tool on the same stock daily to build it.
- **Data timing:** NSE publishes delivery and MWPL files after market close (~6–7 PM IST), so during market hours they show the previous session. Option-chain OI refreshes every few minutes during market hours (9:15 AM – 3:30 PM IST).

## 5. Project layout

```
Run Dashboard.bat             double-click to scan the watchlist (Windows)
dashboard.py                  watchlist scanner -> interactive dashboard
watchlist.txt                 your symbols
analyse.py                    single-stock analysis
stock_analyser/
  nse_client.py               fetches option chain + futures quotes from nseindia.com
  open_interest.py            parsing (JSON/CSV) + PCR, max pain, OI walls, build-up
  market_context.py           delivery %, MWPL/ban, RS, basis, rollover, VIX, IV vs HV
  technicals.py               indicators in plain pandas (no TA-Lib needed) + signals
  signals.py                  scoring and verdict
  report.py                   terminal + HTML report
  dashboard.py                dashboard rows, tags/filters and page template
  demo_data.py                synthetic data for --demo and tests
tests/                        pytest suite (run: python -m pytest)
```

## 6. Put it on GitHub

```bash
git init            # skip if the folder already has a .git directory
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/oi-ta-analyser.git
git push -u origin main
```

Create the empty repository on github.com first, and don't add a README there. GitHub Actions then runs the tests on every push (`.github/workflows/tests.yml`).

---

**Disclaimer:** this is educational, AI-assisted research output generated from public market data. It is not investment advice or a recommendation to buy or sell any security. Futures and options carry a high risk of loss.
