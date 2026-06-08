#!/usr/bin/env python3
"""
Discovery Scanner — auto-find tickers from Yahoo Finance screens.
Sources: day_gainers, day_losers, S&P 500 universe.
Uses yfinance for all data (structured, reliable, no HTML scraping).
Scores each candidate, applies filters, adds to watchlist or queues for approval.

Reads/writes Neon Postgres via stock_discovery.db.
Config stored in portfolio_settings.discovery_config JSONB.
"""

import json
import sys
import os
import time
from datetime import datetime

import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_discovery import db
from stock_discovery.scorer import fetch_chart, score_ticker


# ─── Yahoo Finance Data via yfinance ─────────────────────────

def fetch_yahoo_screen(screen_type, count=50):
    """
    Fetch tickers from Yahoo Finance screens using yfinance.
    screen_type: 'day_gainers', 'day_losers', 'growth_technology_stocks', etc.
    Returns list of {symbol, name, price, volume, market_cap, avg_volume, sector, source}
    """
    try:
        result = yf.screen(screen_type, count=count)
        quotes = result.get("quotes", [])
        tickers = []
        for q in quotes:
            sym = q.get("symbol", "")
            if not sym or sym.startswith("^"):
                continue
            tickers.append({
                "symbol": sym,
                "name": q.get("shortName", q.get("longName", sym)),
                "price": q.get("regularMarketPrice", 0) or 0,
                "volume": q.get("regularMarketVolume", 0) or 0,
                "market_cap": q.get("marketCap", 0) or 0,
                "avg_volume": q.get("averageDailyVolume3Month", 0) or 0,
                "change_pct": q.get("regularMarketChangePercent", 0) or 0,
                "source": screen_type,
            })
        return tickers
    except Exception as e:
        print(f"  ⚠️  Error fetching {screen_type}: {e}", file=sys.stderr)
        return []


def fetch_sp500_tickers():
    """
    Fetch S&P 500 constituent tickers from Wikipedia.
    Bootstrapping source — refreshed quarterly is fine.
    """
    try:
        table = yf.utils.get_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        df = table[0]  # First table is the constituents
        tickers = []
        for _, row in df.iterrows():
            sym = str(row.get("Symbol", "")).strip()
            if sym:
                tickers.append({
                    "symbol": sym,
                    "name": str(row.get("Security", sym)),
                    "price": 0,
                    "volume": 0,
                    "market_cap": 0,
                    "avg_volume": 0,
                    "change_pct": 0,
                    "source": "sp500_universe",
                })
        return tickers
    except Exception as e:
        print(f"  ⚠️  Error fetching S&P 500: {e}", file=sys.stderr)
        return []


def fetch_quotes_batch(symbols):
    """
    Fetch quote data for multiple symbols using yfinance batch download.
    Returns dict of symbol -> {price, volume, avg_volume, market_cap, name, sector, float, short_pct}
    """
    if not symbols:
        return {}

    results = {}
    # yfinance.Tickers fetches all at once
    try:
        tickers_obj = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            sym_upper = sym.upper()
            try:
                t = tickers_obj.tickers.get(sym) or tickers_obj.tickers.get(sym_upper)
                if not t:
                    continue
                info = t.info
                results[sym_upper] = {
                    "price": info.get("regularMarketPrice", 0) or 0,
                    "volume": info.get("regularMarketVolume", 0) or 0,
                    "avg_volume": info.get("averageDailyVolume3Month", 0) or 0,
                    "market_cap": info.get("marketCap", 0) or 0,
                    "avg_volume_10d": info.get("averageDailyVolume10Day", 0) or 0,
                    "change_pct": info.get("regularMarketChangePercent", 0) or 0,
                    "name": info.get("shortName", sym_upper),
                    "sector": info.get("sector", ""),
                    "float_shares": info.get("floatShares", 0) or 0,
                    "short_pct": info.get("shortPercentOfFloat", 0) or 0,
                    "earnings_date": info.get("earningsTimestamp", 0) or 0,
                }
            except Exception as e:
                print(f"  ⚠️  Error fetching {sym}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️  Error in batch quote fetch: {e}", file=sys.stderr)

    return results


# ─── Quick Reject (cheap pre-score filters) ─────────────────

def quick_reject(candidate):
    """
    Fast negative screen before expensive scoring.
    Returns (rejected: bool, reason: str)
    """
    price = candidate.get("price", 0) or 0
    if price <= 0:
        return True, "no price data"
    if price < 1.0:
        return True, f"price ${price:.2f} < $1.00 (penny stock)"

    avg_vol = candidate.get("avg_volume", 0) or candidate.get("volume", 0) or 0
    if avg_vol < 50000:
        return True, f"avg_vol {avg_vol:,} < 50,000"

    mcap = candidate.get("market_cap", 0) or 0
    if mcap > 0 and mcap < 100_000_000:
        return True, f"mcap ${mcap:,.0f} < $100M"

    # High short interest — likely a short squeeze candidate, too risky
    short_pct = candidate.get("short_pct", 0) or 0
    if short_pct > 0.20:
        return True, f"short interest {short_pct:.0%} > 20%"

    # Low float — easily manipulated
    float_shares = candidate.get("float_shares", 0) or 0
    if 0 < float_shares < 10_000_000:
        return True, f"low float {float_shares:,.0f} shares"

    return False, ""


# ─── Discovery Engine ───────────────────────────────────────

def get_discovery_config():
    """Get discovery config from portfolio_settings.discovery_config JSONB."""
    return db.get_discovery_config()


def apply_filters(candidate, config):
    """
    Apply discovery filters to a scored candidate.
    Returns (passed: bool, reason: str)
    """
    filters = config.get("filters", {})
    min_price = filters.get("min_price", 5.0)
    max_price = filters.get("max_price", 200.0)
    min_volume = filters.get("min_avg_volume", 200000)
    min_mcap = filters.get("min_market_cap", 500_000_000)
    score_threshold = config.get("score_threshold", 45)
    min_factor = config.get("min_factor_score", 7)

    price = candidate.get("price", 0) or 0
    if price < min_price:
        return False, f"price ${price:.2f} < min ${min_price}"
    if price > max_price:
        return False, f"price ${price:.2f} > max ${max_price}"

    avg_vol = candidate.get("avg_volume", 0) or candidate.get("volume", 0) or 0
    if avg_vol < min_volume:
        return False, f"avg_vol {avg_vol:,} < min {min_volume:,}"

    mcap = candidate.get("market_cap", 0) or 0
    if mcap < min_mcap:
        return False, f"mcap ${mcap:,.0f} < min ${min_mcap:,.0f}"

    composite = candidate.get("composite", 0) or 0
    if composite < score_threshold:
        return False, f"score {composite:.1f} < threshold {score_threshold}"

    # Check no factor is below minimum (except options which may be 0)
    factors = candidate.get("factors", {})
    for factor_name, factor_val in factors.items():
        if factor_name == "options":
            continue
        if (factor_val or 0) < min_factor:
            return False, f"factor {factor_name}={factor_val:.1f} < min {min_factor}"

    return True, "passed"


def run_discovery():
    """
    Main discovery pipeline.
    1. Fetch candidates from enabled sources
    2. Deduplicate + filter out existing/removed/pending
    3. Batch fetch quote data (price, volume, mcap, sector, float, short interest)
    4. Quick reject (cheap filters before scoring)
    5. Score remaining candidates
    6. Apply full filters
    7. Auto-add or queue for approval
    8. Log run in discovery_runs
    """
    config = get_discovery_config()
    if not config.get("enabled", False):
        return {"ok": False, "error": "Discovery disabled in config"}

    sources = config.get("sources", {})
    max_per_run = config.get("max_per_run", 5)
    max_watchlist = config.get("max_watchlist_size", 50)
    auto_add = config.get("auto_add", True)
    skip_removed_days = config.get("skip_removed_days", 14)

    enabled_sources = [k for k, v in sources.items() if v]
    print(f"🔍 Discovery run started — sources: {enabled_sources}")

    # ── Step 1: Collect candidates from sources ──
    raw_candidates = []

    # Map config source keys to yfinance screen names
    screen_map = {
        "yahoo_gainers": ("day_gainers", 50),
        "yahoo_losers": ("day_losers", 50),
        "yahoo_most_active": ("most_active", 50),  # May not work, but try
    }

    for source_key, enabled in sources.items():
        if not enabled:
            continue
        if source_key in screen_map:
            screen_name, count = screen_map[source_key]
            print(f"  Fetching {screen_name}...")
            # Use yfinance screen
            tickers = fetch_yahoo_screen(screen_name, count)
            raw_candidates.extend(tickers)
            print(f"    → {len(tickers)} tickers")
            time.sleep(1)
        elif source_key == "sp500_universe":
            print(f"  Fetching S&P 500 universe...")
            tickers = fetch_sp500_tickers()
            raw_candidates.extend(tickers)
            print(f"    → {len(tickers)} tickers")
            time.sleep(1)

    if not raw_candidates:
        return {"ok": True, "candidates": 0, "added": 0, "message": "No candidates from sources"}

    # ── Step 2: Deduplicate (prefer gainers > losers > most_active > sp500) ──
    priority = {"day_gainers": 1, "day_losers": 2, "most_active": 3, "sp500_universe": 4}
    seen = {}
    for c in raw_candidates:
        sym = c["symbol"].upper()
        src_priority = priority.get(c.get("source", ""), 99)
        if sym not in seen or src_priority < seen[sym]["_priority"]:
            c["_priority"] = src_priority
            seen[sym] = c
    unique = [c for c in seen.values()]
    print(f"  Unique candidates: {len(unique)}")

    # ── Step 3: Filter out existing + recently removed + pending ──
    existing = db.get_active_tickers()
    existing_syms = {t["symbol"] for t in existing}

    recently_removed = db.query(
        "SELECT symbol FROM watchlist WHERE active = FALSE AND removed_at > NOW() - INTERVAL '%s days'",
        (skip_removed_days,)
    )
    removed_syms = {r["symbol"] for r in recently_removed}

    current_pending = db.query(
        "SELECT symbol FROM discovery_candidates WHERE status = 'pending'"
    )
    pending_syms = {c["symbol"] for c in current_pending}

    to_process = []
    skipped = 0
    for c in unique:
        sym = c["symbol"].upper()
        if sym in existing_syms:
            skipped += 1
            continue
        if sym in removed_syms:
            skipped += 1
            continue
        if sym in pending_syms:
            skipped += 1
            continue
        to_process.append(c)

    print(f"  Skipped (existing/removed/pending): {skipped}")
    print(f"  To process: {len(to_process)}")

    # ── Step 4: Batch fetch quote data ──
    symbols_to_fetch = [c["symbol"] for c in to_process]
    all_quotes = fetch_quotes_batch(symbols_to_fetch)

    # Enrich candidates with quote data
    for c in to_process:
        sym = c["symbol"].upper()
        if sym in all_quotes:
            q = all_quotes[sym]
            c["price"] = q.get("price", c.get("price", 0))
            c["volume"] = q.get("volume", c.get("volume", 0))
            c["avg_volume"] = q.get("avg_volume", c.get("avg_volume", 0))
            c["market_cap"] = q.get("market_cap", c.get("market_cap", 0))
            c["change_pct"] = q.get("change_pct", c.get("change_pct", 0))
            c["float_shares"] = q.get("float_shares", 0)
            c["short_pct"] = q.get("short_pct", 0)
            c["sector"] = q.get("sector", "")
            if q.get("name"):
                c["name"] = q["name"]

    # ── Step 5: Quick reject (cheap filters before scoring) ──
    to_score = []
    quick_rejected = 0
    for c in to_process:
        rejected, reason = quick_reject(c)
        if rejected:
            c["reject_reason"] = reason
            quick_rejected += 1
            continue
        to_score.append(c)

    print(f"  Quick rejected: {quick_rejected}")
    print(f"  To score: {len(to_score)}")

    # ── Step 6: Score each candidate (expensive — OHLCV fetch + 5-factor) ──
    scored = []
    for i, c in enumerate(to_score):
        sym = c["symbol"].upper()
        if i > 0 and i % 10 == 0:
            time.sleep(1.5)  # Rate limit

        chart = fetch_chart(sym)
        if not chart:
            continue

        result = score_ticker(chart)
        if result.get("composite", 0) == 0:
            continue

        c["composite"] = result["composite"]
        c["factors"] = result.get("factors", {})
        c["price"] = result.get("price", c.get("price", 0))
        scored.append(c)

    scored.sort(key=lambda x: x.get("composite", 0), reverse=True)
    print(f"  Scored: {len(scored)}")

    # ── Step 7: Apply full filters ──
    passed = []
    rejected = []
    for c in scored:
        ok, reason = apply_filters(c, config)
        if ok:
            passed.append(c)
        else:
            c["reject_reason"] = reason
            rejected.append(c)

    print(f"  Passed filters: {len(passed)}, Rejected: {len(rejected)}")

    # ── Step 8: Create run record ──
    run_id = db.execute_returning(
        """INSERT INTO discovery_runs (candidates, rejected, source, config_snapshot)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (len(scored), len(rejected) + quick_rejected, "auto", json.dumps(config))
    )["id"]

    # ── Step 9: Auto-add or queue ──
    added = 0
    queued = 0

    current_watchlist_count = len(existing)
    room_left = max_watchlist - current_watchlist_count

    for c in passed[:max_per_run]:
        sym = c["symbol"].upper()
        if auto_add and room_left > 0:
            db.add_ticker(
                sym,
                name=c.get("name", sym),
                notes=f"Auto-discovered: {c.get('source', 'unknown')}"
            )
            db.execute(
                "UPDATE watchlist SET discovered_at = NOW(), discovered_by = %s WHERE symbol = %s",
                (f"discovery:{c.get('source', 'auto')}", sym)
            )
            added += 1
            room_left -= 1
        else:
            db.execute(
                """INSERT INTO discovery_candidates (symbol, name, composite, factors, price, source, status, run_id)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                   ON CONFLICT (symbol, run_id) DO NOTHING""",
                (sym, c.get("name", sym), c["composite"],
                 json.dumps(c.get("factors", {})), c.get("price", 0),
                 c.get("source", "auto"), run_id)
            )
            queued += 1

    # Save rejected candidates (for transparency/analytics)
    rejected_all = rejected[:20]
    for c in rejected_all:
        db.execute(
            """INSERT INTO discovery_candidates (symbol, name, composite, factors, price, source, status, run_id)
               VALUES (%s, %s, %s, %s, %s, %s, 'rejected', %s)
               ON CONFLICT (symbol, run_id) DO NOTHING""",
            (c["symbol"].upper(), c.get("name", c["symbol"]), c.get("composite", 0),
             json.dumps(c.get("factors", {})), c.get("price", 0),
             c.get("source", "auto"), run_id)
        )

    # Update run record
    db.execute(
        "UPDATE discovery_runs SET added = %s, pending = %s WHERE id = %s",
        (added, queued, run_id)
    )

    result = {
        "ok": True,
        "run_id": run_id,
        "candidates": len(scored) + quick_rejected,
        "quick_rejected": quick_rejected,
        "scored": len(scored),
        "passed": len(passed),
        "rejected": len(rejected),
        "added": added,
        "queued": queued,
        "top_passed": [
            {"symbol": c["symbol"], "composite": c["composite"], "price": c.get("price", 0)}
            for c in passed[:5]
        ],
    }

    print(f"  ✅ Run complete: {added} added, {queued} queued, {len(rejected)} rejected, {quick_rejected} quick-rejected")
    return result


def approve_candidate(candidate_id):
    """Approve a pending candidate → add to watchlist."""
    row = db.query_one(
        "SELECT * FROM discovery_candidates WHERE id = %s AND status = 'pending'",
        (candidate_id,)
    )
    if not row:
        return {"error": "Candidate not found or not pending"}

    db.add_ticker(row["symbol"], row.get("name", row["symbol"]),
                  notes=f"Discovered: {row.get('source', 'auto')}")
    db.execute(
        "UPDATE watchlist SET discovered_at = NOW(), discovered_by = %s WHERE symbol = %s",
        (f"discovery:{row.get('source', 'auto')}", row["symbol"])
    )
    db.execute(
        "UPDATE discovery_candidates SET status = 'approved', resolved_at = NOW() WHERE id = %s",
        (candidate_id,)
    )
    return {"ok": True, "symbol": row["symbol"]}


def reject_candidate(candidate_id):
    """Reject a pending candidate."""
    db.execute(
        "UPDATE discovery_candidates SET status = 'rejected', resolved_at = NOW() WHERE id = %s",
        (candidate_id,)
    )
    return {"ok": True}


if __name__ == "__main__":
    result = run_discovery()
    print(json.dumps(result, indent=2, default=str))
