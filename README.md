# OI + Technical Analyser for NSE stocks

A Python tool that analyses any **NSE F&O stock or index** (RELIANCE, TCS, NIFTY, BANKNIFTY…) from two angles and combines them into one verdict:

| Open Interest (options & futures) | Technical analysis (daily chart) |
|---|---|
| Put-Call Ratio on total OI and on today's change in OI | Price vs 200-DMA, 50/200 Golden/Death cross |
| Max Pain strike | EMA 9/21 momentum |
| OI support (largest put OI) and resistance (largest call OI) | Supertrend (10,3) |
| Fresh put/call writing today | RSI (14), MACD (12,26,9), ADX (14) |
| ATM straddle → market-implied move by expiry | Bollinger %B, volume vs 20-day average |
| Futures build-up: long build-up / short build-up / short covering / long unwinding | 52-week range position, swing support/resistance, floor pivots, ATR |

Each check is scored **+1 bullish / 0 neutral / −1 bearish**. The tool averages them into a Technical score and an OI score (−100 to +100), weights them 60/40 into an **Overall** score, and flags whether the two agree.

Output: a summary in the terminal, an **HTML report** (dark theme, candlestick + indicator charts, OI-by-strike chart), and a **JSON** file. Both files go in `reports/`.

---

## 1. Install (one time)

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>. On Windows, tick **"Add Python to PATH"** during setup.
2. Open a terminal (Windows: *Command Prompt*) inside this folder and run:

```bash
pip install -r requirements.txt
```

## 2. Run it

```bash
python analyse.py --demo                 # try it first: synthetic data, no internet needed
python analyse.py RELIANCE               # live prices + live NSE option chain (nearest expiry)
python analyse.py NIFTY --expiry 25-Nov-2026
python analyse.py INFY --no-oi           # technicals only
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

## 3. Reading the output

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
- **Expected move** (ATM straddle ÷ spot) is how far the options market thinks price can travel by expiry.

## 4. Project layout

```
analyse.py                    command-line entry point
stock_analyser/
  nse_client.py               fetches option chain + futures quotes from nseindia.com
  open_interest.py            parsing (JSON/CSV) + PCR, max pain, OI walls, build-up
  technicals.py               indicators in plain pandas (no TA-Lib needed) + signals
  signals.py                  scoring and verdict
  report.py                   terminal + HTML report
  demo_data.py                synthetic data for --demo and tests
tests/                        pytest suite (run: python -m pytest)
```

## 5. Put it on GitHub

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
