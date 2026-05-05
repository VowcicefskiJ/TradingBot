# Bitcoin Trading Signal Bot

A Python bot that fetches Bitcoin price data from Robinhood and generates **buy/sell signals** using technical analysis indicators.

> **Disclaimer**: This bot provides signals for educational/informational purposes only. It does NOT execute trades automatically. Use at your own risk. Past performance does not guarantee future results.

## Indicators Used

| Indicator | Weight | Buy Signal | Sell Signal |
|-----------|--------|------------|-------------|
| **RSI (14)** | ±30 pts | RSI < 30 (oversold) | RSI > 70 (overbought) |
| **MACD** | ±25 pts | Bullish crossover | Bearish crossover |
| **Bollinger Bands** | ±25 pts | Price at lower band | Price at upper band |
| **EMA 9/21** | ±20 pts | Bullish crossover | Bearish crossover |

Combined score from -100 to +100 maps to: **STRONG BUY / BUY / HOLD / SELL / STRONG SELL**

## Fast-Mover Filter (no slow-tape day trades)

Day traders need movement. Before any BUY/SELL is emitted, the bot computes
a **velocity score (0–100)** from four short-term volatility/momentum
measures and requires it to clear a threshold. If the tape is flat, the
signal is downgraded to **HOLD** with a `SLOW TAPE` reason — so you don't
waste a day-trade entry on chop.

| Component | What it measures | Default threshold |
|-----------|------------------|-------------------|
| **ATR %** (14) | Realized volatility as % of price | ≥ 0.50% |
| **ROC (6 bars)** | Short-term momentum % | \|ROC\| ≥ 0.80% |
| **Volume surge** | Current vol / 20-bar avg vol | ≥ 1.20× |
| **Range expansion** | Bar range / 20-bar avg range | bigger = faster |
| **Velocity composite** | All four blended (0–100) | ≥ 55 |

Tune the gate via env vars (in `.env`):

```
FAST_MOVER_ATR_PCT=0.5
FAST_MOVER_ROC_PCT=0.8
FAST_MOVER_VOL_SURGE=1.2
FAST_MOVER_VELOCITY_MIN=55
```

Lower values = more signals (including slower tape). Higher = stricter,
fewer but more decisive moves.

## Setup

1. **Clone and install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure credentials:**
   ```bash
   cp .env.example .env
   # Edit .env with your Robinhood username and password
   ```

3. **If you use 2FA**, add your TOTP secret to `.env` (also install `pyotp`):
   ```bash
   pip install pyotp
   ```

## Usage

```bash
# Run once — get a single buy/sell signal
python bot.py

# Run continuously (checks every 15 minutes by default)
python bot.py --loop

# Custom interval (e.g., every 5 minutes)
python bot.py --loop --interval 5

# Backtest on the past year of daily data
python bot.py --backtest
```

## Run from your desktop (no CMD typing)

Two options on Windows:

### Option A — `.bat` launcher (zero build, easiest)
1. Right-click **`run_bot.bat`** → *Send to* → *Desktop (create shortcut)*.
2. Double-click the desktop shortcut any time you want a signal.

`run_bot_loop.bat` is the same but runs the bot continuously.

### Option B — Build a real `.exe`
1. Double-click **`build_exe.bat`** once. It installs PyInstaller and
   produces **`dist\TradingBot.exe`**.
2. Move (or shortcut) `dist\TradingBot.exe` to your desktop.
3. Double-click it any time. No Python install needed on subsequent runs
   for the user — just the `.env` file alongside the exe (or you'll be
   prompted for credentials).

> The `.bat` files automatically use a local `venv\` if present, otherwise
> they fall back to the system `python` on `PATH`.

## Example Output

```
  Time:       2026-02-06 14:30:00
  BTC Price:  $69,969.81
  Signal:     🟢 BUY
  Score:      +35.0 / 100

  Indicators:
    RSI (14):        32.4
    MACD:            BULLISH CROSS
    Bollinger Bands: LOWER HALF
    EMA 9/21:        BEARISH

  Reasoning:
    - RSI oversold at 32.4 (+8)
    - MACD bullish crossover (+25)
    - Price below BB midline (+5)
    - EMA 9 below EMA 21 (-8)
```

## Project Structure

```
├── bot.py            # Main entry point
├── data_fetcher.py   # Robinhood data retrieval
├── signals.py        # Technical analysis & fast-mover gate
├── run_bot.bat       # Double-click launcher (one-shot signal)
├── run_bot_loop.bat  # Double-click launcher (continuous mode)
├── build_exe.bat     # Build standalone TradingBot.exe via PyInstaller
├── requirements.txt  # Python dependencies
├── .env.example      # Credential template
└── .gitignore
```
