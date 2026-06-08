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
    "score_threshold": 45,
    "min_factor_score": 7,
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

-- 2. Track discovery origin on watchlist
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMPTZ;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS discovered_by VARCHAR(50) DEFAULT 'manual';
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ;

-- 3. Discovery candidates (pending approval queue)
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(20) NOT NULL,
    name        VARCHAR(200),
    composite   DECIMAL(6,1),
    factors     JSONB,
    price       DECIMAL(12,4),
    source      VARCHAR(50),
    status      VARCHAR(20) DEFAULT 'pending',
    run_id      INT,
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

COMMIT;
