# Stock Discovery Dashboard — Architecture & Plan

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Static HTML/CSS/JS (Vercel CDN) |
| **Backend API** | Flask (Python) — Vercel serverless function via WSGI wrapper |
| **Database** | Neon Postgres (serverless) |
| **Scoring** | Python — Yahoo Finance API (`urllib`, no key needed) |
| **Cron** | Hermes cron jobs — run `advisor.py` + `scorer.py` directly against Neon |
| **Charts** | Chart.js (CDN) |
| **Deploy** | Vercel — auto-deploy on push to `main` |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Vercel                                             │
│  ┌──────────────┐  ┌────────────────────────────┐  │
│  │ Static Files │  │ Serverless Flask API        │  │
│  │ (templates/) │  │ /api/portfolio, /api/buy... │  │
│  │ (static/)    │  │ Uses vercel-python runtime   │  │
│  └──────┬───────┘  └──────────┬─────────────────┘  │
│         │ fetch()             │ SQL                 │
│         └───────── ───────────┘                    │
└────────────────────────┬────────────────────────────┘
                         │ psycopg2
                         │ DATABASE_URL env var
                ┌────────▼────────┐
                │   Neon Postgres  │
                │  (serverless)    │
                │                  │
                │  Tables:         │
                │  • watchlist     │
                │  • positions     │
                │  • trade_log     │
                │  • scores        │
                │  • daily_snapshots│
                └──────────────────┘
                         ▲
                         │ SQL
         ┌───────────────┼───────────────┐
         │               │               │
    ┌────┴────┐   ┌──────┴──────┐  ┌─────┴─────┐
    │ advisor │   │   scorer    │  │  manual   │
    │ (cron)  │   │  (cron)     │  │  (user    │
    │         │   │             │  │  actions) │
    └─────────┘   └─────────────┘  └───────────┘

    Cron (Hermes)          Cron (Hermes)        Dashboard UI
    9:30 AM weekdays       9:30 AM weekdays     Anytime
    12:00 PM weekdays      12:00 PM weekdays
    3:30 PM weekdays       3:30 PM weekdays
```

---

## Database Schema (Neon Postgres)

### watchlist — Ticker list + settings

```sql
CREATE TABLE IF NOT EXISTS watchlist (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(20) NOT NULL UNIQUE,
    name        VARCHAR(200),
    type        VARCHAR(20) DEFAULT 'stock',
    notes       TEXT DEFAULT '',
    active      BOOLEAN DEFAULT TRUE,
    added_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_watchlist_active ON watchlist(active);
```

### portfolio_settings — Account config (single row)

```sql
CREATE TABLE IF NOT EXISTS portfolio_settings (
    id                      INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    starting_balance        DECIMAL(12,2) NOT NULL DEFAULT 1500.00,
    cash                    DECIMAL(12,2) NOT NULL DEFAULT 1500.00,
    max_positions           INT NOT NULL DEFAULT 3,
    max_per_position_pct    DECIMAL(5,4) NOT NULL DEFAULT 0.30,
    min_cash_reserve_pct    DECIMAL(5,4) NOT NULL DEFAULT 0.20,
    stop_loss_pct           DECIMAL(5,4) NOT NULL DEFAULT -0.10,
    paper_trading           BOOLEAN NOT NULL DEFAULT TRUE,
    score_weights           JSONB DEFAULT '{"momentum":0.25,"trend":0.25,"volume":0.20,"volatility":0.15,"options":0.15}'::jsonb,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);
```

### positions — Open + closed positions

```sql
CREATE TABLE IF NOT EXISTS positions (
    id              VARCHAR(50) PRIMARY KEY,
    symbol          VARCHAR(20) NOT NULL,
    name            VARCHAR(200),
    entry_price     DECIMAL(12,4) NOT NULL,
    current_price   DECIMAL(12,4),
    exit_price      DECIMAL(12,4),
    shares          INT NOT NULL,
    cost            DECIMAL(12,2) NOT NULL,
    pnl             DECIMAL(12,2) DEFAULT 0,
    pnl_pct         DECIMAL(8,4) DEFAULT 0,
    score_at_entry  DECIMAL(6,1),
    status          VARCHAR(10) DEFAULT 'open',  -- open | closed
    entry_date      TIMESTAMPTZ DEFAULT NOW(),
    exit_date       TIMESTAMPTZ,
    exit_reason     VARCHAR(20),  -- manual | stop_loss | close
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_symbol ON positions(symbol);
```

### trade_log — Every buy/sell (immutable append)

```sql
CREATE TABLE IF NOT EXISTS trade_log (
    id          SERIAL PRIMARY KEY,
    trade_date  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action      VARCHAR(20) NOT NULL,  -- BUY | SELL | STOP_LOSS
    symbol      VARCHAR(20) NOT NULL,
    shares      INT NOT NULL,
    price       DECIMAL(12,4) NOT NULL,
    cost        DECIMAL(12,2) NOT NULL,
    pnl_pct     DECIMAL(8,4),
    reason      VARCHAR(20) DEFAULT 'manual',
    score       DECIMAL(6,1),
    position_id VARCHAR(50)
);

CREATE INDEX idx_trade_log_symbol ON trade_log(symbol);
CREATE INDEX idx_trade_log_date ON trade_log(trade_date DESC);

CREATE TABLE IF NOT EXISTS scores (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(20) NOT NULL,
    composite   DECIMAL(6,1) NOT NULL,
    momentum    DECIMAL(4,1),
    volume      DECIMAL(4,1),
    trend       DECIMAL(4,1),
    volatility  DECIMAL(4,1),
    options     DECIMAL(4,1),
    price       DECIMAL(12,4),
    scored_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_scores_at ON scores(scored_at DESC);
CREATE INDEX idx_scores_symbol ON scores(symbol);
```

### daily_snapshots — For equity curve chart

```sql
CREATE TABLE IF NOT EXISTS daily_snapshots (
    id              SERIAL PRIMARY KEY,
    snapshot_date   DATE NOT NULL UNIQUE,
    total_value     DECIMAL(12,2) NOT NULL,
    cash            DECIMAL(12,2) NOT NULL,
    total_pnl       DECIMAL(12,2) NOT NULL,
    total_pnl_pct   DECIMAL(8,4) NOT NULL,
    open_positions  INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## API Endpoints

All endpoints are served by the Flask Vercel serverless function.

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/portfolio` | Portfolio summary (settings + cash + positions) |
| GET | `/api/scores` | Latest scores for all active tickers |
| GET | `/api/trades` | Trade log (filter: `?symbol=`, `?action=`) |
| GET | `/api/ticker/<symbol>` | Chart data (Yahoo Finance) + current position |
| GET | `/api/analytics` | Performance metrics (win rate, equity curve, etc.) |
| GET | `/api/watchlist` | Active watchlist tickers + settings |
| POST | `/api/buy` | Open position `{symbol, shares, price, score, name}` |
| POST | `/api/close` | Close position `{position_id, price}` |
| POST | `/api/watchlist/add` | Add ticker `{symbol, name, notes}` |
| POST | `/api/watchlist/remove` | Remove ticker `{symbol}` |
| POST | `/api/settings/update` | Update settings object |
| POST | `/api/reset` | Reset portfolio (clear positions, reset cash) |
| POST | `/api/rescore` | Trigger scoring run |

---

## Vercel Deployment Configuration

### `vercel.json`

```json
{
  "version": 2,
  "builds": [
    {
      "src": "api/index.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    { "src": "/api/(.*)", "dest": "api/index.py" },
    { "src": "/static/(.*)", "dest": "static/$1" },
    { "src": "/(.*)", "dest": "api/index.py" }
  ]
}
```

### Entry point: `api/index.py`

A WSGI wrapper that exposes the Flask app as a Vercel serverless function.

### Environment Variables (Vercel dashboard)

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `postgresql://neondb_owner:***@ep-xxx.neon.tech/neondb?sslmode=require` |

---

## Data Migration (JSON → Neon)

One-time migration to move existing `watchlist.json`, `portfolio.json`, and `trade_history.csv`
into Neon tables:

1. `watchlist.json` → `watchlist` tickers + `portfolio_settings` single row
2. `portfolio.json` → `positions` (open + closed) + update `portfolio_settings.cash`
3. `trade_history.csv` → `trade_log`
4. Seed `daily_snapshots` from existing equity data

After migration, the dashboard uses only Neon. JSON/CSV files become read-only backups.

---

## Page Structure (unchanged from Phase 1)

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Portfolio value, positions, top scores, mini charts |
| Trades | `/trades` | Trade history with filters, stats, cumulative P&L chart |
| Ticker Detail | `/ticker/<symbol>` | Candlestick chart, SMAs, stats, buy/close actions |
| Watchlist | `/watchlist` | Add/remove tickers, trading settings, scoring weights |
| Analytics | `/analytics` | Equity curve, P&L by ticker, performance scatter |
| Settings | `/settings` | Account config, data management, cron display |

All pages: static HTML served by Flask, JS fetches `/api/*` endpoints.

---

## File Structure

```
stock-discovery/
├── api/
│   └── index.py            # Vercel entry point (Flask WSGI wrapper)
├── stock_discovery/        # Python package
│   ├── __init__.py
│   ├── server.py           # Flask app factory + all route handlers
│   ├── db.py               # Neon Postgres connection + CRUD layer
│   ├── scorer.py           # Scoring engine (reads watchlist from Neon)
│   ├── advisor.py          # Portfolio advisor (reads/writes Neon)
│   └── migrate.py          # JSON → Neon migration script
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── trades.html
│   ├── ticker.html
│   ├── watchlist.html
│   ├── analytics.html
│   └── settings.html
├── static/
│   ├── style.css
│   └── app.js
├── scripts/
│   └── migrate.sql          # Database schema
├── vercel.json
├── requirements.txt          # flask, psycopg2-binary
├── README.md
└── DASHBOARD_PLAN.md
```

---

## Implementation Order

1. **Database** — Create Neon tables, write `db.py` connection layer
2. **Migration** — Write and run `migrate.py` (JSON → Neon)
3. **Backend** — Refactor `server.py` to use `db.py` instead of file I/O
4. **Scorer/Advisor** — Refactor to use Neon
5. **Vercel** — Create `api/index.py`, `vercel.json`
6. **Test** — Verify dashboard works against Neon data
7. **Deploy** → Push to GitHub → Vercel auto-deploy
