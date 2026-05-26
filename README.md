# Stock Discovery — Watchlist Advisory Dashboard

Paper trading watchlist with scoring engine, portfolio management, and web dashboard.

## Quick Start

```bash
cd stock-discovery
./start.sh          # Starts on port 5150
# Open http://localhost:5150
```

Or manually:
```bash
pip3 install flask
python3 server.py   # Default port 5150
PORT=8080 python3 server.py  # Custom port
```

## Features

- **Dashboard** — Portfolio value, P&L, open positions, top scores, mini charts
- **Trades** — Full trade history with filters, win/loss stats, distribution charts
- **Ticker Detail** — 3-month price chart with SMAs, key stats, buy/sell actions
- **Watchlist Management** — Add/remove tickers, adjust trading settings & scoring weights
- **Analytics** — Equity curve, P&L by ticker, performance scatter
- **Settings** — Account config, trading rules, cron schedule, data management

## Data

All data lives in `~/.hermes/watchlist/`:
- `watchlist.json` — Ticker list + settings (shared with cron advisor)
- `portfolio.json` — Positions, cash, P&L (shared with cron advisor)
- `trade_history.csv` — Every buy/sell logged

The dashboard reads/writes the same files as the cron-based advisor, so
everything stays in sync.

## Scoring Engine

Composite score 0-100 from 5 weighted factors:
- **Momentum (25%)** — Rate of change, price vs SMA
- **Trend (25%)** — Higher highs/lows, directional bias
- **Volume (20%)** — Relative volume vs 20-day average
- **Volatility (15%)** — ATR-based, favors moderate volatility
- **Options proxy (15%)** — Volume spike × price movement

## Cron Jobs

The advisor runs 3×/day on weekdays via Hermes cron:
- 9:30 AM — Pre-market scores & picks
- 12:00 PM — Midday check, stop-loss scan
- 3:30 PM — End-of-day, final positions

## Trading Rules

- Starting balance: **$1,500** (fake money)
- Max **3 positions** at once
- Max **30%** per position ($450)
- Min **20%** cash reserve ($300)
- Stop-loss: **10%** per position (adjustable)
- Paper trading: **ON** (toggle in Settings)

## Screenshots

| Dashboard | Ticker Detail | Analytics |
|-----------|--------------|-----------|
| Portfolio summary, positions table, top scores, mini charts | Candlestick chart, SMAs, key stats, position info | Equity curve, P&L by ticker, scatter plot |
