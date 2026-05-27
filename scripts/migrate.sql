-- Stock Discovery Dashboard — Neon Postgres Schema
-- Run once: psql $DATABASE_URL -f scripts/migrate.sql

BEGIN;

-- 1. Watchlist tickers + settings
CREATE TABLE IF NOT EXISTS watchlist (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(20) NOT NULL UNIQUE,
    name        VARCHAR(200),
    type        VARCHAR(20) DEFAULT 'stock',
    notes       TEXT DEFAULT '',
    active      BOOLEAN DEFAULT TRUE,
    added_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watchlist_active ON watchlist(active);

-- 2. Portfolio settings (single row, id=1)
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

-- Seed default settings row
INSERT INTO portfolio_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- 3. Positions (open + closed)
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
    status          VARCHAR(10) DEFAULT 'open',
    entry_date      TIMESTAMPTZ DEFAULT NOW(),
    exit_date       TIMESTAMPTZ,
    exit_reason     VARCHAR(20),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);

-- 4. Trade log (immutable append)
CREATE TABLE IF NOT EXISTS trade_log (
    id          SERIAL PRIMARY KEY,
    trade_date  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action      VARCHAR(20) NOT NULL,
    symbol      VARCHAR(20) NOT NULL,
    shares      INT NOT NULL,
    price       DECIMAL(12,4) NOT NULL,
    cost        DECIMAL(12,2) NOT NULL,
    pnl_pct     DECIMAL(8,4),
    reason      VARCHAR(20) DEFAULT 'manual',
    score       DECIMAL(6,1),
    position_id VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_trade_log_symbol ON trade_log(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_log_date ON trade_log(trade_date DESC);

-- 5. Scores (latest per ticker + history)
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

CREATE INDEX IF NOT EXISTS idx_scores_at ON scores(scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_scores_symbol ON scores(symbol);

-- 6. Daily snapshots (for equity curve)
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

COMMIT;
