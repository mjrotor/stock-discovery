# Watchlist Advisory — UI Dashboard Plan

## Overview

A multi-page web dashboard for the $1,500 paper trading watchlist system.
Built as a single-page app with tab-based navigation. Reads data from the
watchlist files (`portfolio.json`, `trade_history.csv`, `watchlist.json`)
via a lightweight Python backend.

**Stack:** Python (Flask) backend + vanilla HTML/CSS/JS frontend + Chart.js
**Port:** 5150 (localhost, or reverse-proxied through nginx)
**Data source:** Reads directly from `~/.hermes/watchlist/` files — no database needed.

---

## Page 1: Dashboard (Home)

**Route:** `/`

The main overview. First thing you see when you open the app.

### Top Bar
- **Total Portfolio Value** — large number, green/red based on P&L
- **Total P&L** — `$+127.50 (+8.5%)` format, color-coded
- **Cash Available** — `$361.97`
- **Day's P&L** — change since last close
- **Last Updated** — timestamp of last cron run

### Positions Table
All open positions, one row per position:

| Ticker | Shares | Entry | Current | P&L $ | P&L % | Score | Stop $ | Action |
|--------|--------|-------|---------|--------|-------|-------|--------|--------|
| AAPL | 1 | $308.82 | $315.40 | +$6.58 | +2.1% | 73 | $277.94 | [Close] |
| PLTR | 3 | $136.88 | $131.20 | -$17.04 | -4.2% | 55 | $123.19 | [Close] |
| MSFT | 1 | $418.57 | $422.10 | +$3.53 | +0.8% | 54 | $376.71 | [Close] |

- Green/red row highlighting based on P&L
- **Close** button sells the position (writes to portfolio.json, logs to CSV)
- Click a ticker → go to Page 3 (Ticker Detail)

### Top Scores Panel (right sidebar)
Top 5 scored tickers not already in a position:

| Ticker | Score | M | V | T | Vol | Opt |
|--------|-------|---|---|---|-----|-----|
| AAPL | 73 | 7 | 4 | 8 | 10 | 7 |
| QQQ | 64 | 6 | 4 | 7 | 10 | 6 |
| AMD | 64 | 9 | 4 | 6 | 5 | 6 |

- Click → go to Ticker Detail page
- Lock icon if already in portfolio

### Mini Charts
- **Portfolio Value Over Time** — line chart, last 30 days
- **Daily P&L** — bar chart, last 14 days

---

## Page 2: Trades (Trade History)

**Route:** `/trades`

### Filters
- Date range picker (default: last 30 days)
- Ticker dropdown (filter by symbol)
- Action: All / Buy / Sell / Stop-Loss

### Trades Table

| Date | Action | Ticker | Shares | Price | Cost | P&L $ | P&L % | Reason |
|------|--------|--------|--------|-------|------|--------|-------|--------|
| 05/26 09:30 | BUY | AAPL | 1 | $308.82 | $308.82 | — | — | entry |
| 05/26 09:30 | BUY | PLTR | 3 | $136.88 | $410.64 | — | — | entry |
| 05/23 15:30 | SELL | TSLA | 2 | $445.00 | $890.00 | +$34.00 | +4.0% | manual |
| 05/22 12:00 | STOP | NVDA | 1 | $198.50 | $215.00 | -$16.50 | -7.7% | stop_loss |

- Green rows for buys, red for sells/stops
- Sortable columns (click header)
- Pagination (25 per page)

### Trade Stats Panel
- **Total Trades:** 47
- **Win Rate:** 58% (27/47)
- **Avg Win:** +$18.30
- **Avg Loss:** -$12.10
- **Profit Factor:** 1.51
- **Best Trade:** +$67.50 (NVDA)
- **Worst Trade:** -$28.40 (COIN)
- **Avg Hold Time:** 2.3 days

### Charts
- **Cumulative P&L** — line chart over time
- **Win/Loss Distribution** — histogram of trade P&L %
- **Monthly Performance** — bar chart, P&L per month

---

## Page 3: Ticker Detail

**Route:** `/ticker/<symbol>`

Deep dive into a single ticker.

### Header
- **Symbol + Name** — e.g. "AAPL — Apple Inc."
- **Current Price** — large, with daily change
- **Composite Score** — big number 0-100, color gradient (red → yellow → green)
- **Position Status** — "Not in portfolio" or "Position: 1 share @ $308.82"

### Score Breakdown (Radar/Spider Chart)
5-axis radar chart showing:
- Momentum: 7.2/10
- Volume: 4.5/10
- Trend: 8.5/10
- Volatility: 10/10
- Options: 6.8/10

### Price Chart
- **Candlestick chart** — last 3 months of daily OHLC
- Overlay: 10-SMA (blue), 20-SMA (orange)
- Volume bars at bottom
- Stop-loss line (horizontal red dashed)

### Ticker Stats

| Metric | Value |
|--------|-------|
| 52-Week High | $342.50 |
| 52-Week Low | $245.10 |
| Avg Volume (20d) | 45.2M |
| Today's Volume | 52.1M |
| Relative Volume | 1.15x |
| ATR (14d) | $4.82 (1.56%) |
| 5-Day ROC | +3.2% |
| 10-Day ROC | +5.8% |

### Trade History for This Ticker
All buys/sells of this specific ticker from trade_history.csv

### Actions
- **[Buy]** — open a position (modal: enter shares, confirm)
- **[Close Position]** — if currently held
- **[Add to Watchlist]** — if not already in watchlist.json

---

## Page 4: Watchlist (Manage Tickers)

**Route:** `/watchlist`

### Current Watchlist Table

| # | Symbol | Name | Type | Score | Price | Change | Notes | Action |
|---|--------|------|------|-------|-------|--------|-------|--------|
| 1 | NVDA | NVIDIA | Stock | 53 | $215.33 | -0.3% | AI/GPU leader | [Remove] |
| 2 | AAPL | Apple | Stock | 73 | $308.82 | +1.2% | Large cap | [Remove] |
| ... | | | | | | | | |

- Sortable by score, price, change
- Drag-and-drop reordering

### Add Ticker
- **Symbol input** — typeahead/autocomplete (search Yahoo Finance)
- **Type** — Stock / Option
- **Notes** — free text
- **[Add]** button

### Settings Panel

| Setting | Current | Edit |
|---------|---------|------|
| Starting Balance | $1,500.00 | [Edit] |
| Max Positions | 3 | [Edit] |
| Max Per Position | 30% | [Edit] |
| Min Cash Reserve | 20% | [Edit] |
| Stop Loss | 10% | [Edit] |
| Paper Trading | ✅ ON | [Toggle] |

- Edit → inline edit with save/cancel
- Paper Trading toggle → big switch, red when OFF (live mode)

### Scoring Weights Panel

| Factor | Weight | Slider |
|--------|--------|--------|
| Momentum | 25% | ████████░░ |
| Trend | 25% | ████████░░ |
| Volume | 20% | ███████░░░ |
| Volatility | 15% | █████░░░░░ |
| Options | 15% | █████░░░░░ |

- Interactive sliders, must sum to 100%
- **[Re-score All]** — re-runs scoring with new weights

---

## Page 5: Performance Analytics

**Route:** `/analytics`

### Summary Cards (top row)
- **Total Return** — `+$127.50 (+8.5%)`
- **Annualized Return** — `+42.3%`
- **Sharpe Ratio** — `1.85`
- **Max Drawdown** — `-12.3%`
- **Win Rate** — `58%`
- **Profit Factor** — `1.51`

### Charts Row 1
- **Equity Curve** — portfolio value over time vs starting balance
- **Drawdown Chart** — underwater chart, peak-to-trough decline

### Charts Row 2
- **P&L by Ticker** — horizontal bar chart, which tickers made/lost money
- **P&L by Day of Week** — are Mondays better than Fridays?

### Charts Row 3
- **Score vs. Return Scatter** — did higher-scored picks perform better?
- **Position Size vs. P&L** — bubble chart

### Monthly Returns Table

| Month | Return | P&L | Trades | Win Rate |
|-------|--------|-----|--------|----------|
| May 2026 | +8.5% | +$127.50 | 12 | 67% |
| April 2026 | +3.2% | +$48.00 | 18 | 56% |
| March 2026 | -1.8% | -$27.00 | 15 | 47% |

---

## Page 6: Settings & Config

**Route:** `/settings`

### Account Settings
- Starting balance
- Paper trading toggle
- Currency display

### Trading Rules
- Max positions
- Max per position %
- Min cash reserve %
- Stop-loss %
- Score threshold for new picks (default: 30)

### Cron Schedule
Display current cron jobs with next run times:

| Job | Schedule | Next Run | Status |
|-----|----------|----------|--------|
| Pre-Market | 9:30 AM Mon-Fri | Today 9:30 AM | ✅ Active |
| Midday | 12:00 PM Mon-Fri | Today 12:00 PM | ✅ Active |
| Close | 3:30 PM Mon-Fri | Today 3:30 PM | ✅ Active |

### Data Management
- **[Export CSV]** — download trade_history.csv
- **[Reset Portfolio]** — re-initialize to starting balance (confirm modal)
- **[Purge All Data]** — nuclear option, double confirm
- **[View Log]** — show last 100 lines of advisor output

### API / Webhook (future)
- Telegram notification toggle
- Webhook URL for external triggers

---

## Technical Architecture

### Backend: Flask (`server.py`)

```
~/.hermes/watchlist/
├── server.py              # Flask app
├── scorer.py              # (existing) scoring engine
├── advisor.py             # (existing) portfolio manager
├── watchlist.json         # (existing) ticker list + settings
├── portfolio.json         # (existing) positions + P&L
├── trade_history.csv      # (existing) trade log
├── templates/
│   ├── base.html          # layout, nav, CSS
│   ├── dashboard.html     # Page 1
│   ├── trades.html        # Page 2
│   ├── ticker.html        # Page 3
│   ├── watchlist_mgmt.html# Page 4
│   ├── analytics.html     # Page 5
│   └── settings.html      # Page 6
└── static/
    ├── style.css          # all styles
    ├── app.js             # frontend logic
    └── chartjs/           # Chart.js library
```

### API Endpoints

| Method | Route | Returns |
|--------|-------|---------|
| GET | `/api/portfolio` | portfolio.json contents |
| GET | `/api/scores` | current scores for all tickers |
| GET | `/api/trades` | trade history (filterable) |
| GET | `/api/ticker/<symbol>` | chart data + stats |
| GET | `/api/analytics` | performance metrics |
| POST | `/api/buy` | execute buy (ticker, shares) |
| POST | `/api/sell` | execute sell (position_id) |
| POST | `/api/close` | close position at market |
| POST | `/api/watchlist/add` | add ticker |
| POST | `/api/watchlist/remove` | remove ticker |
| POST | `/api/settings/update` | update settings |
| POST | `/api/rescore` | re-run scoring |
| POST | `/api/reset` | reset portfolio |

### Data Flow

```
Cron (9:30/12:00/15:30)
    → advisor.py
        → scorer.py (Yahoo Finance API)
        → updates portfolio.json
        → appends trade_history.csv
        → sends Telegram report

Dashboard (anytime)
    → reads portfolio.json
    → reads trade_history.csv
    → reads watchlist.json
    → serves via Flask
    → JS fetches /api/* endpoints
    → renders with Chart.js
```

### Real-time Updates
- Dashboard polls `/api/portfolio` every 30 seconds
- If `last_updated` timestamp changed, refresh all data
- No WebSocket needed — simple polling is fine for this use case

---

## Visual Design

### Theme
- **Dark mode** default (easy on the eyes for all-day monitoring)
- Toggle for light mode
- Color palette:
  - Background: `#0d1117` (GitHub dark)
  - Cards: `#161b22`
  - Borders: `#30363d`
  - Text: `#c9d1d9`
  - Green (profit): `#3fb950`
  - Red (loss): `#f85149`
  - Accent: `#58a6ff` (blue)
  - Warning: `#d29922` (yellow)

### Navigation
- Left sidebar with icons + labels
- Collapsible on mobile
- Active page highlighted

### Responsive
- Desktop: full sidebar + multi-column
- Tablet: collapsed sidebar, 2-col
- Mobile: hamburger menu, single column, cards instead of tables

---

## Implementation Phases

### Phase 1: Core Dashboard (this week)
- Flask server + base template
- Page 1 (Dashboard) — positions, scores, mini charts
- API endpoints: `/api/portfolio`, `/api/scores`
- Read-only, dark theme

### Phase 2: Trade History + Ticker Detail
- Page 2 (Trades) — table, filters, stats
- Page 3 (Ticker Detail) — candlestick chart, score radar
- API endpoints: `/api/trades`, `/api/ticker/<symbol>`

### Phase 3: Management
- Page 4 (Watchlist) — add/remove tickers, settings
- Buy/Sell/Close actions from dashboard
- API endpoints: buy, sell, watchlist CRUD

### Phase 4: Analytics
- Page 5 (Performance) — equity curve, drawdown, Sharpe
- Page 6 (Settings) — config, data management
- All remaining API endpoints

### Phase 5: Polish
- Mobile responsive
- Light mode toggle
- Keyboard shortcuts
- Export/print reports
- Sound alerts for stop-loss triggers
