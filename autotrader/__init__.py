"""
Autonomous stock trader.

Scans for stocks that are up a high percentage today, confirms momentum on
intraday bars, buys a fixed dollar amount, and exits automatically with a
take-profit / stop-loss bracket order held at the broker.

Runs against an Alpaca PAPER account by default (fake money).
"""
