# Trading Bots

This repo contains two independent tools:

1. **`autotrade.py` — Autonomous stock trader.** Finds stocks that are up a high % today, buys a set dollar amount, and sells automatically when it hits a profit target (or a stop loss). Runs on its own. Trades through **Robinhood** (real money) or [Alpaca](https://alpaca.markets) (paper/fake money by default).
2. **`bot.py` — Bitcoin signal bot.** Advisory only; never places trades.

---

## Autonomous Stock Trader

> **Read this first.** No strategy reliably makes money, and momentum trading on big daily gainers is high-risk: these stocks can reverse sharply. Run it with `--dry-run` (or on Alpaca paper trading) for weeks and check the results in `logs/trades.csv` before you put real money behind it. Only ever trade money you can afford to lose. This is not financial advice.

### How it works

Every minute while the market is open:

1. **Scan.** It pulls today's top % gainers from Alpaca and keeps stocks that are:
   - up between `MIN_GAIN_PCT` and `MAX_GAIN_PCT` (default 5–30%, which skips the most extreme spikes)
   - priced between `MIN_PRICE` and `MAX_PRICE` (default $5–$500, which skips penny stocks)
   - traded more than `MIN_VOLUME` shares today, listed on an exchange (not OTC), and tradable
2. **Confirm momentum.** It scores each stock 0–100 from its 5-minute chart:

   | Check | Points |
   |---|---|
   | Price above today's VWAP | +25 |
   | EMA 9 above EMA 21 | +20 |
   | RSI(14) between 50 and 75 | +20 |
   | Price higher than 3 bars ago | +15 |
   | Within 3% of the day's high | up to +20 |
   | RSI ≥ 80 (overextended) | **veto: never buy** |

3. **Buy.** It buys the highest-scoring stocks that reach `MIN_SCORE`, spending about `TRADE_AMOUNT` dollars each in whole shares, up to `MAX_POSITIONS` positions at once.
4. **Sell automatically** at `+TAKE_PROFIT_PCT` (profit) or `-STOP_LOSS_PCT` (stop loss):
   - **Alpaca:** each buy is a bracket order, so both exits live at the broker and fire even if the bot is off.
   - **Robinhood:** Robinhood has no bracket orders. Once the buy fills, the bot places a **stop-loss order at Robinhood** (so your downside is protected even if the bot stops) and **watches the take-profit itself**, selling at market when the price reaches it. Profits are only taken while the bot is running, and it checks once per `SCAN_INTERVAL_SECONDS`.
5. **End of day.** `FLATTEN_MINUTES_BEFORE_CLOSE` minutes before the close it sells whatever it bought that's still open, so nothing is held overnight. Set it to `0` to hold overnight.

The bot **only ever sells shares it bought itself** (tracked in `logs/state.json`). Stocks you already own in the same account are never sold, and it won't buy a stock you already hold.

### Safety rails

- **Real money needs `--live`.** Robinhood is always real money, so it refuses to place orders without `--live` (use `--dry-run` to try it out). Alpaca uses paper trading unless you set **both** `ALPACA_PAPER=false` and `--live`.
- **Daily loss limit.** Once equity is down `MAX_DAILY_LOSS_PCT` for the day, it stops opening new trades.
- **Pattern day trader rule.** Accounts under $25k get 3 day trades per 5 business days. The bot stops opening trades when that limit is reached rather than getting your account restricted.
- **One trade per stock per day.** It won't keep re-buying a stock it just exited. This survives restarts (`logs/state.json`).
- **No trading in the first 15 or last 30 minutes** of the session, when moves are most erratic.
- `--dry-run` scans and logs exactly what it *would* buy, without placing orders.

### Setup: Robinhood

1. Install the dependencies and create your settings file:
   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   ```
2. In `.env` set `BROKER=robinhood`. Either fill in `ROBINHOOD_USERNAME` / `ROBINHOOD_PASSWORD`, or leave them blank and type them in when the bot starts (the password isn't stored).
3. **Two-factor login.** The bot has to log back in about every 12 hours on its own. That only works unattended if you give it your authenticator-app secret in `ROBINHOOD_TOTP`: in the Robinhood app go to *Security → Two-Factor Authentication → Authenticator app*, and when it shows the QR code choose to see the text code. That base32 text is the secret. With SMS or app-approval 2FA instead, Robinhood will ask you to approve each login, so someone needs to be at the terminal.
4. Try it with no orders first:
   ```bash
   python autotrade.py --dry-run
   ```
5. When you're happy, let it trade (real money):
   ```bash
   python autotrade.py --live
   ```

Things to know about Robinhood:

- It connects through [`robin_stocks`](https://github.com/jmfernandes/robin_stocks), an **unofficial** library. Robinhood doesn't officially support automated trading on its stock API and can change or block it at any time, which could break the bot or flag your account.
- **There is no paper trading** on Robinhood, so start with a small `TRADE_AMOUNT`.
- Robinhood has no full-market "top gainers" screener. The bot combines Robinhood's Top Movers, S&P 500 up-movers and 100 Most Popular lists, so it sees fewer stocks than with Alpaca.
- In a **cash account**, selling and re-buying with unsettled money can cause good-faith violations. In a **margin account under $25k**, the 3-day-trades rule applies; the bot stops buying once you hit it.

### Setup: Alpaca (paper trading)

1. Create a free account at [alpaca.markets](https://alpaca.markets), switch to **Paper Trading**, and generate API keys.
2. Install the dependencies and configure:
   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   # BROKER=alpaca, and put ALPACA_API_KEY / ALPACA_API_SECRET in .env
   ```

### Usage

```bash
python autotrade.py --dry-run   # watch what it would do, no orders
python autotrade.py             # trade on its own (Alpaca paper account)
python autotrade.py --live      # trade real money (Robinhood, or live Alpaca)
python autotrade.py --once      # run a single cycle and exit
```

Leave it running (for example in `tmux`, `screen`, or as a service on an always-on machine). Outside market hours it sleeps until the next open. Logs go to `logs/autotrader.log`, and every buy and sell is recorded in `logs/trades.csv`.

```bash
python -m pytest tests          # run the test suite (uses a fake broker, no network)
```

---

## Bitcoin Trading Signal Bot

A Python bot that fetches Bitcoin price data from Robinhood and generates **buy/sell signals** using technical analysis indicators.

> **Disclaimer**: This bot provides signals for educational/informational purposes only. It does NOT execute trades automatically. Use at your own risk. Past performance does not guarantee future results.

### Indicators Used

| Indicator | Weight | Buy Signal | Sell Signal |
|-----------|--------|------------|-------------|
| **RSI (14)** | ±30 pts | RSI < 30 (oversold) | RSI > 70 (overbought) |
| **MACD** | ±25 pts | Bullish crossover | Bearish crossover |
| **Bollinger Bands** | ±25 pts | Price at lower band | Price at upper band |
| **EMA 9/21** | ±20 pts | Bullish crossover | Bearish crossover |

Combined score from -100 to +100 maps to: **STRONG BUY / BUY / HOLD / SELL / STRONG SELL**

### Setup

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

### Usage

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

### Example Output

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
├── autotrade.py          # Autonomous stock trader entry point
├── autotrader/
│   ├── config.py         # Settings from .env
│   ├── broker.py         # Alpaca API client (orders, data, screener)
│   ├── robinhood_broker.py  # Robinhood adapter (robin_stocks)
│   ├── strategy.py       # Momentum scoring, sizing, exit prices
│   └── trader.py         # Trading loop and risk checks
├── tests/                # Tests for the auto-trader (fake broker)
├── bot.py            # Bitcoin signal bot entry point
├── data_fetcher.py   # Robinhood data retrieval
├── signals.py        # Technical analysis & signal generation
├── requirements.txt  # Python dependencies
├── .env.example      # Credential template
└── .gitignore
```
