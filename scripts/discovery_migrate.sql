-- Discovery Module - DB Migration
-- Run: psql $DATABASE_URL -f scripts/discovery_migrate.sql

BEGIN;

-- 1. Add discovery_config JSONB to portfolio_settings
ALTER TABLE portfolio_settings
  ADD COLUMN IF NOT EXISTS discovery_config JSONB DEFAULT '{
    "enabled": false,
    "schedule": "0 8 * * 1-5",
    "max_per_run": 5,
    "max_watchlist_size": 50,
    "score_threshold": 50,
    "min_factor_score": 5,
    "max_per_sector": 3,
    "sp500_sample_size": 75,
    "max_to_score": 30,
    "sources": {
        "yahoo_most_active": true,
        "yahoo_gainers": true,
        "yahoo_losers": false,
        "sp500_universe": true,
        "russell2000_universe": false
    },
    "filters": {
        "min_price": 5.0,
        "max_price": 200.0,
        "min_avg_volume": 200000,
        "min_market_cap": 500000000
    },
    "auto_add": true,
    "require_approval": false,
    "skip_removed_days": 14
}'::jsonb;

-- 2. Track discovery origin + sector on watchlist
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMPTZ;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS discovered_by VARCHAR(50) DEFAULT 'manual';
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS sector VARCHAR(100) DEFAULT '';

-- 3. Discovery candidates (pending approval queue)
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(20) NOT NULL,
    name        VARCHAR(200),
    composite   DECIMAL(6,1),
    factors     JSONB,
    price       DECIMAL(12,4),
    source      VARCHAR(50),
    sector      VARCHAR(100) DEFAULT '',
    status      VARCHAR(20) DEFAULT 'pending',
    run_id      INT,
    reject_reason TEXT DEFAULT '',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE(symbol, run_id)
);

CREATE INDEX IF NOT EXISTS idx_disc_candidates_status ON discovery_candidates(status);
CREATE INDEX IF NOT EXISTS idx_disc_candidates_run ON discovery_candidates(run_id);

-- 4. Discovery run history
CREATE TABLE IF NOT EXISTS discovery_runs (
    id              SERIAL PRIMARY KEY,
    run_date        TIMESTAMPTZ DEFAULT NOW(),
    candidates      INT DEFAULT 0,
    added           INT DEFAULT 0,
    rejected        INT DEFAULT 0,
    pending         INT DEFAULT 0,
    source          VARCHAR(50),
    details         JSONB,
    config_snapshot JSONB
);

-- 5. Static universes (cached index constituents)
CREATE TABLE IF NOT EXISTS static_universes (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL UNIQUE,
    description     TEXT,
    tickers         JSONB NOT NULL DEFAULT '[]'::jsonb,
    ticker_count    INT DEFAULT 0,
    last_refreshed  TIMESTAMPTZ,
    refresh_interval_days INT DEFAULT 90,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Seed S&P 500 universe placeholder (populated on first discovery run)
INSERT INTO static_universes (name, description, refresh_interval_days)
VALUES ('sp500', 'S&P 500 Index Constituents', 90)
ON CONFLICT (name) DO NOTHING;

INSERT INTO static_universes (name, description, refresh_interval_days)
VALUES ('nasdaq100', 'Nasdaq-100 Index Constituents', 90)
ON CONFLICT (name) DO NOTHING;

COMMIT;
