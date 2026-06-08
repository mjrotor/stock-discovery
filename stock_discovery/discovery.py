#!/usr/bin/env python3
"""
Discovery Scanner — auto-find tickers from Yahoo Finance screens.
Sources: most_active, gainers, losers, S&P 500 universe.
Scores each candidate, applies filters, adds to watchlist or queues for approval.

Reads/writes Neon Postgres via stock_discovery.db.
Config stored in portfolio_settings.discovery_config JSONB.
"""

import json
import sys
import os
import time
import math
from datetime import date, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_discovery import db
from stock_discovery.scorer import fetch_chart, score_ticker, WEIGHTS

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; AppleWebKit/537.36",
    "Accept": "application/json",
}

# ─── Yahoo Screen Scrapers ──────────────────────────────────

def fetch_yahoo_screen(screen_type, count=50):
    """
    Fetch tickers from Yahoo Finance screens.
    screen_type: 'most_active', 'gainers', 'losers', 'trending'
    Returns list of {symbol, name, price, volume, market_cap}
    """
    url = f"https://finance.yahoo.com/screener/predefined/{screen_type}?count={count}"
    req = Request(url, headers={
        **YAHOO_HEADERS,
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # Extract JSON data from Yahoo's embedded payload
        # Yahoo embeds quote data in window.YAHOO = ... or similar
        # Fallback: extract from FinStreamer data
        return _parse_yahoo_screen_html(html, screen_type)
    except Exception as e:
        print(f"  ⚠️  Error fetching {screen_type}: {e}", file=sys.stderr)
        return []


def _parse_yahoo_screen_html(html, screen_type):
    """Parse Yahoo Finance screener HTML to extract ticker data."""
    import re
    tickers = []

    # Yahoo embeds data in <script> tags as JSON
    # Look for the FinStreamer quotes data
    patterns = [
        r'"quotes":\s*(\[.*?\])\s*[,}]',
        r'"results":\s*(\{.*?"quotes":\s*(\[.*?\]))',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, html, re.DOTALL)
        for match in matches:
            try:
                if isinstance(match, tuple):
                    match = match[-1]
                quotes = json.loads(match)
                if isinstance(quotes, list):
                    for q in quotes:
                        sym = q.get("symbol", "")
                        if sym and not sym.startswith("^"):
                            tickers.append({
                                "symbol": sym,
                                "name": q.get("shortName", q.get("longName", sym)),
                                "price": q.get("regularMarketPrice", 0),
                                "volume": q.get("regularMarketVolume", 0),
                                "market_cap": q.get("marketCap", 0),
                                "avg_volume": q.get("averageDailyVolume3Month", 0),
                                "change_pct": q.get("regularMarketChangePercent", 0),
                                "source": screen_type,
                            })
                    if tickers:
                        return tickers
            except (json.JSONDecodeError, KeyError):
                continue

    # Fallback: regex extract symbol/name pairs from table rows
    if not tickers:
        row_pattern = r'data-symbol="([^"]+)"[^>]*>.*?<td[^>]*>(.*?)</td>'
        for match in re.finditer(row_pattern, html, re.DOTALL):
            sym = match.group(1)
            name = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            if sym and not sym.startswith("^"):
                tickers.append({
                    "symbol": sym,
                    "name": name or sym,
                    "price": 0,
                    "volume": 0,
                    "market_cap": 0,
                    "avg_volume": 0,
                    "change_pct": 0,
                    "source": screen_type,
                })

    return tickers


def fetch_sp500_tickers():
    """Fetch S&P 500 constituent tickers from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = Request(url, headers=YAHOO_HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        import re
        tickers = []
        # Extract symbols from the first column of the wikitable
        for match in re.finditer(r'<td>([A-Z]{1,5})</td>', html):
            sym = match.group(1)
            if sym:
                tickers.append({
                    "symbol": sym,
                    "name": sym,
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


def fetch_yahoo_quotes_batch(symbols):
    """
    Fetch quote data for multiple symbols in one call.
    Returns dict of symbol -> quote data.
    """
    if not symbols:
        return {}
    # Yahoo Finance quote API (batch)
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(symbols)}"
    req = Request(url, headers=YAHOO_HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        results = {}
        for q in data.get("quoteResponse", {}).get("result", []):
            sym = q.get("symbol", "")
            if sym:
                results[sym] = {
                    "price": q.get("regularMarketPrice", 0),
                    "volume": q.get("regularMarketVolume", 0),
                    "avg_volume": q.get("averageDailyVolume3Month", 0),
                    "market_cap": q.get("marketCap", 0),
                    "change_pct": q.get("regularMarketChangePercent", 0),
                    "name": q.get("shortName", sym),
                }
        return results
    except Exception as e:
        print(f"  ⚠️  Error fetching batch quotes: {e}", file=sys.stderr)
        return {}


# ─── Discovery Engine ───────────────────────────────────────

def get_discovery_config():
    """Get discovery config from portfolio_settings.discovery_config JSONB."""
    settings = db.get_settings()
    config = settings.get("discovery_config", {})
    if isinstance(config, str):
        config = json.loads(config)
    return config


def apply_filters(candidate, config):
    """
    Apply discovery filters to a candidate.
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

    # Check no factor is below minimum (unless it's options which may be 0)
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
    2. Deduplicate
    3. Score each candidate
    4. Apply filters
    5. Auto-add or queue for approval
    6. Log run in discovery_runs

    Returns dict with run results.
    """
    config = get_discovery_config()
    if not config.get("enabled", False):
        return {"ok": False, "error": "Discovery disabled in config"}

    sources = config.get("sources", {})
    max_per_run = config.get("max_per_run", 5)
    max_watchlist = config.get("max_watchlist_size", 50)
    auto_add = config.get("auto_add", True)
    skip_removed_days = config.get("skip_removed_days", 14)

    print(f"🔍 Discovery run started — sources: {[k for k,v in sources.items() if v]}")

    # ── Step 1: Collect candidates from sources ──
    raw_candidates = []
    source_map = {
        "yahoo_most_active": ("most_active", 50),
        "yahoo_gainers": ("gainers", 50),
        "yahoo_losers": ("losers", 50),
    }

    for source_key, enabled in sources.items():
        if not enabled:
            continue
        if source_key in source_map:
            screen_type, count = source_map[source_key]
            print(f"  Fetching {screen_type}...")
            tickers = fetch_yahoo_screen(screen_type, count)
            raw_candidates.extend(tickers)
            print(f"    → {len(tickers)} tickers")
            time.sleep(1.5)
        elif source_key == "sp500_universe":
            print(f"  Fetching S&P 500 universe...")
            tickers = fetch_sp500_tickers()
            raw_candidates.extend(tickers)
            print(f"    → {len(tickers)} tickers")
            time.sleep(1.5)

    if not raw_candidates:
        return {"ok": True, "candidates": 0, "added": 0, "message": "No candidates from sources"}

    # ── Step 2: Deduplicate ──
    seen = set()
    unique = []
    for c in raw_candidates:
        sym = c["symbol"].upper()
        if sym not in seen:
            seen.add(sym)
            unique.append(c)
    print(f"  Unique candidates: {len(unique)}")

    # ── Step 3: Filter out existing + recently removed ──
    existing = db.get_active_tickers()
    existing_syms = {t["symbol"] for t in existing}

    # Check recently removed
    recently_removed = db.query(
        "SELECT symbol FROM watchlist WHERE active = FALSE AND removed_at > NOW() - INTERVAL '%s days'",
        (skip_removed_days,)
    )
    removed_syms = {r["symbol"] for r in recently_removed}

    # Check current candidates
    current_candidates = db.query(
        "SELECT symbol FROM discovery_candidates WHERE status = 'pending'"
    )
    pending_syms = {c["symbol"] for c in current_candidates}

    to_score = []
    skipped = 0
    for c in unique:
        sym = c["symbol"].upper()
        if sym in existing_syms or sym in removed_syms or sym in pending_syms:
            skipped += 1
            continue
        to_score.append(c)

    print(f"  Skipped (existing/removed/pending): {skipped}")
    print(f"  To score: {len(to_score)}")

    # ── Step 4: Batch fetch quote data for price/volume/mcap ──
    batch_size = 100
    all_quotes = {}
    for i in range(0, len(to_score), batch_size):
        batch = [c["symbol"] for c in to_score[i:i+batch_size]]
        quotes = fetch_yahoo_quotes_batch(batch)
        all_quotes.update(quotes)
        if i + batch_size < len(to_score):
            time.sleep(1)

    # Enrich candidates with quote data
    for c in to_score:
        sym = c["symbol"].upper()
        if sym in all_quotes:
            q = all_quotes[sym]
            c["price"] = q.get("price", c.get("price", 0))
            c["volume"] = q.get("volume", c.get("volume", 0))
            c["avg_volume"] = q.get("avg_volume", c.get("avg_volume", 0))
            c["market_cap"] = q.get("market_cap", c.get("market_cap", 0))
            c["change_pct"] = q.get("change_pct", c.get("change_pct", 0))
            if q.get("name"):
                c["name"] = q["name"]

    # ── Step 5: Score each candidate ──
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

    # ── Step 6: Apply filters ──
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

    # ── Step 7: Create run record ──
    run_id = db.execute_returning(
        """INSERT INTO discovery_runs (candidates, rejected, source, config_snapshot)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (len(scored), len(rejected), "auto", json.dumps(config))
    )["id"]

    # ── Step 8: Auto-add or queue ──
    added = 0
    queued = 0

    # Check watchlist size limit
    current_watchlist_count = len(existing)
    room_left = max_watchlist - current_watchlist_count

    for c in passed[:max_per_run]:
        sym = c["symbol"].upper()
        if auto_add and room_left > 0:
            # Auto-add to watchlist
            db.add_ticker(
                sym,
                name=c.get("name", sym),
                notes=f"Auto-discovered: {c.get('source', 'unknown')}"
            )
            # Mark discovered_by
            db.execute(
                "UPDATE watchlist SET discovered_at = NOW(), discovered_by = %s WHERE symbol = %s",
                (f"discovery:{c.get('source', 'auto')}", sym)
            )
            added += 1
            room_left -= 1
        else:
            # Queue as candidate
            db.execute(
                """INSERT INTO discovery_candidates (symbol, name, composite, factors, price, source, status, run_id)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                   ON CONFLICT (symbol, run_id) DO NOTHING""",
                (sym, c.get("name", sym), c["composite"],
                 json.dumps(c.get("factors", {})), c.get("price", 0),
                 c.get("source", "auto"), run_id)
            )
            queued += 1

    # Save rejected candidates too (for analytics)
    for c in rejected[:20]:  # Cap at 20 rejected
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
        "candidates": len(scored),
        "passed": len(passed),
        "rejected": len(rejected),
        "added": added,
        "queued": queued,
        "top_passed": [
            {"symbol": c["symbol"], "composite": c["composite"], "price": c.get("price", 0)}
            for c in passed[:5]
        ],
    }

    print(f"  ✅ Run complete: {added} added, {queued} queued, {len(rejected)} rejected")
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
