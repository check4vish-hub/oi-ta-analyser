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

**Windows: double-click `Run Dashboard.bat`.** A window asks which stocks to scan:

```
Enter NSE symbols to scan, separated by space or comma (e.g.  TCS INFY COFORGE)
  W = whole watchlist.txt    Enter on its own = quit
> coforge, hdfcbank infy
```

Type one or several NSE symbols and press Enter. It scans only those (roughly 10–20 seconds each) and opens the dashboard in your browser. Then it asks again, so you can check more stocks without restarting. Press Enter on an empty line to close.

The dashboard shows:
- top tiles: how many stocks are bullish/bearish, long vs short build-up, Nifty 50, India VIX, today's F&O ban list
- a ranked table (best score first) with verdict badges, score bars, futures build-up, PCR, RSI, trend, delivery %, relative strength and expected move
- **filter chips** to spot setups, for example *Aligned* + *Long build-up*, or *Options cheap*
- click any row to expand the full breakdown, with a link to that stock's chart report

Other ways to run it:

```bash
python dashboard.py                    # asks for symbols (same as the .bat file)
python dashboard.py TCS INFY COFORGE   # scan these straight away
python dashboard.py --watchlist-all    # scan everything in watchlist.txt
python dashboard.py --fast             # skip delivery %, MWPL, RS and VIX (about twice as fast)
python dashboard.py --demo             # try it with made-up data
```

`watchlist.txt` holds a saved list you can scan by typing `W`. Edit it in Notepad. Each scan is saved in `reports/` as `dashboard_<date>_<time>.html`, plus `dashboard_latest.html`.

## 3. Intraday futures OI tracker (every 15 minutes)

**Windows: double-click `Run Tracker.bat`**, then type the stocks to track, for example `COFORGE TCS NIFTY`.

### With ICICI Direct Breeze (recommended)

If you have Breeze API access, the tracker pulls the history straight from ICICI. **Today's full chart appears instantly**, even if you start late or after the close, **plus the last 7 trading days** (`--days 3` for fewer). While the market is open it refreshes every 15 minutes.

One-time setup:
1. Log in at <https://api.icicidirect.com/apiuser/home> with your ICICI Direct ID and register an app. For the redirect URL you can enter `http://127.0.0.1`. Note the **API Key** and **Secret Key**.
2. Double-click **`Setup Breeze.bat`**. It installs the Breeze library, asks for the key and secret, logs you in and tests with NIFTY futures.

Your key and secret are stored only on your computer, in `data/breeze_config.json`. The `data/` folder is never uploaded to GitHub. Don't share the secret with anyone.

**Every day:** Breeze needs a new login. The first time you start the tracker each day, it opens the ICICI login page. After you log in, the browser goes to an address containing `apisession=1234567` (the page itself may show an error, which is fine). Paste that address, or just the number, into the black window. It's then remembered until midnight.

How the Breeze data is used:
- 5-minute futures candles are combined into 15-minute bars: closing price, OI at the end of the bar, and volume
- each day's first bar is compared with the previous day's close
- each day uses the contract that was near-month *on that day*. On the day after expiry the comparison uses the new contract's own previous close, so the switch doesn't show as a fake jump.
- in expiry week, OI of the next month is added in, so rollover isn't mistaken for unwinding
- Breeze reports OI and volume in **units (shares)**, not contracts

### Without Breeze

Every 15 minutes during market hours it records each stock's near-month futures price and open interest from NSE.

### The chart

A live chart opens in your browser (the page refreshes every minute):

- the futures price line, with each point coloured by what happened in that interval: **long build-up**, **short build-up**, **short covering**, **long unwinding** or **no clear signal**. Bigger dots mean stronger moves.
- OI-change bars underneath, in the same colours
- **click any point or bar** for the interpretation of that interval: price and OI change, volume, what it means, how strong it is compared with the day's typical change, streaks (for example "4 long build-up intervals in a row") and the day so far
- in expiry week it classifies on OI of all futures months combined, so traders rolling positions to next month isn't mistaken for unwinding
- with Breeze: **day buttons** above the chart (each coloured by that day's overall build-up) to view one day or all days together

While it runs, type in the black window:

| Type | Does |
|---|---|
| `stop` | stops tracking (the final chart stays saved) |
| `add INFY SBIN` | starts tracking more stocks |
| `remove TCS` | stops tracking one stock |
| `list` | shows what is being tracked |

It stops by itself at market close (3:30 PM IST = 1:00 PM Qatar time). Other options: `python tracker.py --interval 5` (every 5 minutes) and `python tracker.py --demo COFORGE` (a simulated day, to see how it looks).

**Without Breeze:** NSE only shows the *latest* futures price and OI, so the tracker can only chart from the moment you start it, and your PC must stay on and awake while it runs. Readings are saved in `data/intraday/<date>/`. If you close and restart the tracker on the same day, it continues from them.

## 4. Single-stock analysis

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

## 5. Reading the output

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

## 6. Project layout

```
Run Dashboard.bat             double-click to scan the watchlist (Windows)
dashboard.py                  watchlist scanner -> interactive dashboard
watchlist.txt                 your symbols
Run Tracker.bat               double-click to start the intraday OI tracker (Windows)
Setup Breeze.bat              one-time ICICI Breeze setup (Windows)
breeze_setup.py               saves your Breeze key locally and tests the login
tracker.py                    intraday futures price + OI recorder with live chart
analyse.py                    single-stock analysis
stock_analyser/
  nse_client.py               fetches option chain + futures quotes from nseindia.com
  open_interest.py            parsing (JSON/CSV) + PCR, max pain, OI walls, build-up
  market_context.py           delivery %, MWPL/ban, RS, basis, rollover, VIX, IV vs HV
  technicals.py               indicators in plain pandas (no TA-Lib needed) + signals
  signals.py                  scoring and verdict
  report.py                   terminal + HTML report
  dashboard.py                dashboard rows, tags/filters and page template
  intraday.py                 interval build-up classification, interpretation, live chart page
  breeze_source.py            ICICI Breeze login, contract codes/expiries, candles -> 15-min bars
  demo_data.py                synthetic data for --demo and tests
tests/                        pytest suite (run: python -m pytest)
```

## 7. Put it on GitHub

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
