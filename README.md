# Stock Discovery — Watchlist Advisory Dashboard

Paper trading watchlist with scoring engine, portfolio management, and web dashboard.
Deployed on **Vercel** with **Neon Postgres** backend.

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | Static HTML/CSS/JS (Vercel CDN) |
| Backend | Flask → Vercel serverless function (`@vercel/python`) |
| Database | Neon Postgres (serverless) |
| Scoring | Python — Yahoo Finance API (no key needed) |
| Cron | Hermes cron jobs → `advisor.py` + `scorer.py` → Neon |

## Quick Start (Local Dev)

```bash
cd stock-discovery
pip3 install -r requirements.txt

# Set your Neon connection string
export DATABASE_URL="postgresql://user:pass@host.neon.tech/dbname?sslmode=require"

# Run migration (one-time, from existing JSON files)
python3 -m stock_discovery.migrate

# Start Flask dev server
python3 -m stock_discovery.server
# Open http://localhost:5150
```

## Vercel Deployment

1. Push to GitHub (`main` branch)
2. Import repo at [vercel.com](https://vercel.com)
3. Set environment variable: `DATABASE_URL` = your Neon connection string
4. Vercel auto-detects `vercel.json` config → deploys

### Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Neon Postgres connection string |

## Database Schema

See `scripts/migrate.sql` for full schema. Tables:

- **watchlist** — Ticker list
- **portfolio_settings** — Account config (single row)
- **positions** — Open + closed positions
- **trade_log** — Immutable trade history
- **scores** — Scoring history per ticker
- **daily_snapshots** — Equity curve data

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/portfolio` | Portfolio summary |
| GET | `/api/scores` | Latest scores |
| GET | `/api/trades` | Trade history |
| GET | `/api/ticker/<symbol>` | Chart data + position |
| GET | `/api/analytics` | Performance metrics |
| GET | `/api/watchlist` | Watchlist + settings |
| POST | `/api/buy` | Open position |
| POST | `/api/close` | Close position |
| POST | `/api/watchlist/add` | Add ticker |
| POST | `/api/watchlist/remove` | Remove ticker |
| POST | `/api/settings/update` | Update settings |
| POST | `/api/reset` | Reset portfolio |
| POST | `/api/rescore` | Trigger scoring |

## Scoring Engine

Composite score 0-100 from 5 weighted factors:
- **Momentum (25%)** — Rate of change, price vs SMA
- **Trend (25%)** — Higher highs/lows, directional bias
- **Volume (20%)** — Relative volume vs 20-day average
- **Volatility (15%)** — ATR-based, favors moderate volatility
- **Options proxy (15%)** — Volume spike × price movement

## Trading Rules

- Starting balance: **$1,500** (fake money)
- Max **3 positions** at once
- Max **30%** per position ($450)
- Min **20%** cash reserve ($300)
- Stop-loss: **10%** per position
- Paper trading: **ON** (toggle in Settings)

## Cron Jobs (Hermes)

The advisor runs 3×/day on weekdays:
- 9:30 AM — Pre-market scores & picks
- 12:00 PM — Midday check, stop-loss scan
- 3:30 PM — End-of-day, final positions

## File Structure

```
stock-discovery/
├── api/
│   └── index.py            # Vercel serverless entry point
├── stock_discovery/        # Python package
│   ├── __init__.py
│   ├── server.py           # Flask app + all routes
│   ├── db.py               # Neon Postgres CRUD layer
│   ├── scorer.py           # Scoring engine
│   ├── advisor.py          # Portfolio advisor (cron)
│   └── migrate.py          # JSON → Neon migration
├── templates/              # Jinja2 HTML templates
├── static/                 # CSS, JS
├── scripts/
│   └── migrate.sql          # Database schema
├── vercel.json             # Vercel deployment config
├── requirements.txt
└── README.md
```
