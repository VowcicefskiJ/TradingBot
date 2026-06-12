# Penny-Stock Day-Trading Signal Bot

A Python bot that scans US stocks for day-trading opportunities and flags
**BUY / HOLD / SELL** signals using technical analysis. It does **NOT place
trades** — every decision is yours to execute manually.

> **Disclaimer**: Signals are for educational / informational purposes only.
> Penny stocks are highly volatile. Past performance does not guarantee future
> results. Use at your own risk.

## What each run shows you

**1. Buy candidates (up to 5)**
The top US stocks priced $0.50–$2.00, up ≥3% today with ≥1M shares of volume,
ranked by a technical score.

**2. Top pick (1 actionable suggestion)**
The single best candidate from that list, with:
- Share count for your position size ($150 default)
- Sell target (+5% take-profit)
- Stop loss (-3%)
- Force-sell time (15:55 ET)
- Confidence tier (HIGH / MEDIUM / LOW)

The bot skips the pick and says **"sit out"** when nothing is clean
(overbought, already up >15%, RSI too hot, below VWAP, etc.). Every run
recalculates — the same ticker can repeat across runs.

**3. Your watchlist (SELL / HOLD)**
For each ticker in `watchlist.txt`, prints SELL or HOLD with reasoning.
If you provide your entry price, the bot also tracks live P&L and triggers
SELL when you hit your target or stop. In the last 5 min of the trading day
everything flips to SELL automatically.

## Indicators

| Indicator | Weight | Bullish | Bearish |
|-----------|--------|---------|---------|
| **RSI (14)**        | ±25 | RSI < 30 | RSI > 70 |
| **MACD**            | ±20 | Bullish cross | Bearish cross |
| **Bollinger Bands** | ±20 | At lower band | At upper band |
| **EMA 9/21**        | ±15 | Bullish cross | Bearish cross |
| **VWAP**            | ±10 | Price above VWAP | Price below VWAP |
| **Volume surge**    | ±10 | 2x+ avg volume | 0.5x or less |

Score is clamped to -100..+100 and mapped to STRONG BUY / BUY / HOLD / SELL /
STRONG SELL.

## Setup

```bash
pip install -r requirements.txt
```

No API keys, no logins — `yfinance` pulls public market data directly.

## Quick start on Windows (no command line)

Three double-clickable launchers are included:

- **`run_bot.bat`** — standard scan, loops every 5 min
- **`run_bot_fast.bat`** — fast-movers mode (see below)
- **`run_bot_once.bat`** — one snapshot, then exit

Just double-click. The console window stays open until you press a key.

## Usage (command line)

```bash
python bot.py                   # One scan, then exit
python bot.py --loop            # Loop every 5 min (default)
python bot.py --loop -i 10      # Loop every 10 min
python bot.py --fast            # FAST MOVERS mode (see below)
python bot.py --size 200        # $200 position size (default $150)
python bot.py --target 7        # +7% take-profit (default 5)
python bot.py --stop 4          # -4% stop-loss (default 3)
python bot.py --scan-only       # Only buy candidates + top pick
python bot.py --watch-only      # Only watchlist.txt
```

On Windows, use `py bot.py` instead of `python bot.py` if `python` isn't on
your PATH.

## Fast-movers mode (`--fast`)

For when you want to catch stocks that are accelerating *right now*. Adds a
new "Momentum" indicator (30-min rate-of-change) and re-ranks candidates so
FAST UP movers float to the top.

Differences vs standard mode:

| Gate | Standard | Fast |
|---|---|---|
| Min score for pick | +25 | +20 |
| Max % up today | 15% | 30% (less chase-averse) |
| Max RSI | 65 | 75 |
| Requires recent UP momentum? | No | Yes (FAST UP or BUILDING) |

Higher reward potential, higher risk. Use when the standard mode keeps
saying "sit out" but you can see stocks ripping.

## Watchlist format

Open `watchlist.txt` and put one ticker per line:

```
SNDL 1.50        # ticker + entry price: enables P&L-based sells
ACRV 1.95
BLNK             # ticker only: technicals-based sells only
```

Lines starting with `#` or blank lines are ignored.

## Market hours

The screener only returns fresh data during US market hours (9:30 AM – 4:00 PM
ET, weekdays). Outside that window you'll mostly get stale or empty results.

## Project structure

```
bot.py             # Entry point: scan + top pick + watchlist
data_fetcher.py    # yfinance screener + intraday bar fetch
signals.py         # Technical analysis + scoring
watchlist.txt      # Tickers you currently hold
requirements.txt   # Python dependencies
```
