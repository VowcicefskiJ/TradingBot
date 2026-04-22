# Penny-Stock Day-Trading Signal Bot

A Python bot that scans US stocks for day-trading opportunities and flags
**BUY / HOLD / SELL** signals using technical analysis. It does **NOT place
trades** — every decision is yours to execute manually.

> **Disclaimer**: Signals are for educational / informational purposes only.
> Penny stocks are highly volatile. Past performance does not guarantee future
> results. Use at your own risk.

## What it flags

**1. Buy candidates (the screener)**
Scans the market each cycle for US stocks that are:
- Priced between **$0.50 and $2.00**
- Up **at least 3% today**
- Trading at least **1,000,000 shares** today

Then it scores each match on technical indicators and prints the top BUY-grade
names.

**2. Sell / Hold on your own positions**
Reads tickers from `watchlist.txt` (one per line — add whatever you bought)
and flags **SELL** when the technicals turn bearish, **HOLD** otherwise.
In the final 5 minutes of the trading day (15:55–16:00 ET) it force-flags
everything on the watchlist as SELL — day-trading rule, no overnight holds.

## Indicators used

| Indicator | Weight | Bullish | Bearish |
|-----------|--------|---------|---------|
| **RSI (14)**        | +/- 25 | RSI < 30 | RSI > 70 |
| **MACD**            | +/- 20 | Bullish crossover | Bearish crossover |
| **Bollinger Bands** | +/- 20 | Price at lower band | Price at upper band |
| **EMA 9/21**        | +/- 15 | Bullish crossover | Bearish crossover |
| **VWAP**            | +/- 10 | Price above VWAP | Price below VWAP |
| **Volume surge**    | +/- 10 | 2x+ avg volume | 0.5x or less |

Total score is clamped to -100 .. +100 and mapped to:
STRONG BUY / BUY / HOLD / SELL / STRONG SELL.

## Setup

```bash
pip install -r requirements.txt
```

No API keys, no logins — `yfinance` pulls public market data directly.

## Usage

```bash
python bot.py                  # One scan, then exit
python bot.py --loop           # Loop, scanning every 5 min (default)
python bot.py --loop -i 10     # Loop, 10-min interval
python bot.py --scan-only      # Just show buy candidates
python bot.py --watch-only     # Just check watchlist.txt
```

## Editing your watchlist

Open `watchlist.txt` and put one ticker per line:

```
SNDL
NAKD
BBIG
```

Lines starting with `#` or blank lines are ignored.

## Project structure

```
bot.py             # Entry point: scan + watchlist flow
data_fetcher.py    # yfinance screener + intraday bar fetch
signals.py         # Technical analysis + scoring
watchlist.txt      # Tickers you currently hold
requirements.txt   # Python dependencies
```
