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
    Uses HTML parser (no external deps beyond stdlib).
    """
    try:
        from urllib.request import urlopen, Request
        from html.parser import HTMLParser

        class WikiTableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_table = False
                self.in_row = False
                self.in_cell = False
                self.current_cell = ''
                self.rows = []
                self.current_row = []
                self.header_found = False

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == 'table' and 'wikitable' in attrs_dict.get('class', ''):
                    self.in_table = True
                elif self.in_table and tag == 'tr':
                    self.in_row = True
                    self.current_row = []
                elif self.in_row and tag in ('td', 'th'):
                    self.in_cell = True
                    self.current_cell = ''

            def handle_endtag(self, tag):
                if tag == 'table':
                    self.in_table = False
                elif self.in_table and tag == 'tr':
                    self.in_row = False
                    if self.current_row:
                        if not self.header_found:
                            self.header_found = True
                        else:
                            self.rows.append(self.current_row)
                elif self.in_row and tag in ('td', 'th'):
                    self.in_cell = False
                    self.current_row.append(self.current_cell.strip())

            def handle_data(self, data):
                if self.in_cell:
                    self.current_cell += data

        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8')

        p = WikiTableParser()
        p.feed(html)

        tickers = []
        for r in p.rows:
            if len(r) >= 2:
                sym = r[0].strip()
                name = r[1].strip()
                if sym and len(sym) <= 5 and sym.isalpha():
                    tickers.append({
                        "symbol": sym,
                        "name": name,
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
        "yahoo_most_active": ("most_actives", 50),
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
            # Try cached universe first
            cached = db.get_static_universe("sp500")
            if cached:
                tickers = []
                for sym in cached:
                    tickers.append({
                        "symbol": sym,
                        "name": sym,
                        "price": 0, "volume": 0, "market_cap": 0,
                        "avg_volume": 0, "change_pct": 0,
                        "source": "sp500_universe",
                    })
                print(f"    → {len(tickers)} tickers (cached)")
            else:
                # Fallback: fetch from Wikipedia
                tickers = fetch_sp500_tickers()
                # Sample if too many (scoring 500+ is slow)
                sample_size = config.get("sp500_sample_size", 75)
                if len(tickers) > sample_size:
                    import random
                    random.seed()  # Non-deterministic
                    tickers = random.sample(tickers, sample_size)
                    print(f"    → {len(tickers)} tickers (sampled from 500)")
                else:
                    print(f"    → {len(tickers)} tickers (fresh)")
                # Cache for next time
                if tickers:
                    db.save_static_universe("sp500", [t["symbol"] for t in tickers], "S&P 500 Index Constituents")
            raw_candidates.extend(tickers)
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

    # ── Step 9: Auto-add or queue (with sector diversification) ──
    added = 0
    queued = 0
    sector_queued = 0

    current_watchlist_count = len(existing)
    room_left = max_watchlist - current_watchlist_count

    # Count current sector exposure from watchlist
    sector_counts = db.get_sector_exposure()

    max_per_sector = config.get("max_per_sector", 3)

    for c in passed[:max_per_run]:
        sym = c["symbol"].upper()
        sec = c.get("sector", "") or ""

        # Sector diversification check
        if sec and sector_counts.get(sec, 0) >= max_per_sector:
            # Queue for approval instead of auto-adding
            db.execute(
                """INSERT INTO discovery_candidates (symbol, name, composite, factors, price, source, sector, status, run_id, reject_reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                   ON CONFLICT (symbol, run_id) DO NOTHING""",
                (sym, c.get("name", sym), c["composite"],
                 json.dumps(c.get("factors", {})), c.get("price", 0),
                 c.get("source", "auto"), sec, run_id,
                 f"sector cap: {sec} already has {sector_counts[sec]} tickers")
            )
            queued += 1
            sector_queued += 1
            continue

        if auto_add and room_left > 0:
            db.add_ticker(
                sym,
                name=c.get("name", sym),
                notes=f"Auto-discovered: {c.get('source', 'unknown')}"
            )
            db.execute(
                """UPDATE watchlist SET discovered_at = NOW(), discovered_by = %s, sector = %s WHERE symbol = %s""",
                (f"discovery:{c.get('source', 'auto')}", sec, sym)
            )
            added += 1
            room_left -= 1
            # Update sector count tracking
            if sec:
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
        else:
            db.execute(
                """INSERT INTO discovery_candidates (symbol, name, composite, factors, price, source, sector, status, run_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                   ON CONFLICT (symbol, run_id) DO NOTHING""",
                (sym, c.get("name", sym), c["composite"],
                 json.dumps(c.get("factors", {})), c.get("price", 0),
                 c.get("source", "auto"), sec, run_id)
            )
            queued += 1

    # Save rejected candidates (for transparency/analytics)
    rejected_all = rejected[:20]
    for c in rejected_all:
        db.execute(
            """INSERT INTO discovery_candidates (symbol, name, composite, factors, price, source, sector, status, run_id, reject_reason)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'rejected', %s, %s)
               ON CONFLICT (symbol, run_id) DO NOTHING""",
            (c["symbol"].upper(), c.get("name", c["symbol"]), c.get("composite", 0),
             json.dumps(c.get("factors", {})), c.get("price", 0),
             c.get("source", "auto"), c.get("sector", ""), run_id,
             c.get("reject_reason", ""))
        )

    # Also save quick-rejected candidates
    for c in to_process:
        if c.get("reject_reason") and c not in scored:
            db.execute(
                """INSERT INTO discovery_candidates (symbol, name, composite, factors, price, source, sector, status, run_id, reject_reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'rejected', %s, %s)
                   ON CONFLICT (symbol, run_id) DO NOTHING""",
                (c["symbol"].upper(), c.get("name", c["symbol"]), c.get("composite", 0),
                 json.dumps(c.get("factors", {})), c.get("price", 0),
                 c.get("source", "auto"), c.get("sector", ""), run_id,
                 c.get("reject_reason", ""))
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
        "sector_queued": sector_queued,
        "top_passed": [
            {"symbol": c["symbol"], "composite": c["composite"], "price": c.get("price", 0), "sector": c.get("sector", "")}
            for c in passed[:5]
        ],
    }

    print(f"  ✅ Run complete: {added} added, {queued} queued ({sector_queued} sector-capped), {len(rejected)} rejected, {quick_rejected} quick-rejected")
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
