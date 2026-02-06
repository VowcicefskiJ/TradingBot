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
├── signals.py        # Technical analysis & signal generation
├── requirements.txt  # Python dependencies
├── .env.example      # Credential template
└── .gitignore
```
