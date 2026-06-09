#!/usr/bin/env python3
"""
Test run the discovery scanner — no DB required.
Runs: Yahoo screeners → batch quotes → quick reject → score → filter → print results.
"""
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yfinance as yf
from stock_discovery.scorer import fetch_chart, score_ticker

# ── Config (same as discovery_config defaults) ──
CONFIG = {
    "enabled": True,
    "sources": {
        "yahoo_gainers": True,
        "yahoo_most_active": True,
        "yahoo_losers": False,
        "sp500_universe": True,
    },
    "filters": {
        "min_price": 5.0,
        "max_price": 200.0,
        "min_avg_volume": 200000,
        "min_market_cap": 500_000_000,
    },
    "score_threshold": 50,
    "min_factor_score": 5,
    "max_per_run": 5,
}

def fetch_yahoo_screen(screen_type, count=50):
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
        print(f"  ⚠️  Error fetching {screen_type}: {e}")
        return []

def fetch_sp500_wikipedia():
    """Fetch S&P 500 from Wikipedia using stdlib HTML parser."""
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
                        "price": 0, "volume": 0, "market_cap": 0,
                        "avg_volume": 0, "change_pct": 0,
                        "source": "sp500_universe",
                    })
        return tickers
    except Exception as e:
        print(f"  ⚠️  Error fetching S&P 500: {e}")
        return []

def fetch_quotes_batch(symbols):
    results = {}
    if not symbols:
        return results
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
                    "change_pct": info.get("regularMarketChangePercent", 0) or 0,
                    "name": info.get("shortName", sym_upper),
                    "sector": info.get("sector", ""),
                    "float_shares": info.get("floatShares", 0) or 0,
                    "short_pct": info.get("shortPercentOfFloat", 0) or 0,
                }
            except Exception as e:
                print(f"  ⚠️  Error fetching {sym}: {e}")
    except Exception as e:
        print(f"  ⚠️  Error in batch quote fetch: {e}")
    return results

def quick_reject(c):
    price = c.get("price", 0) or 0
    if price <= 0:
        return True, "no price data"
    if price < 1.0:
        return True, f"price ${price:.2f} < $1.00"
    avg_vol = c.get("avg_volume", 0) or c.get("volume", 0) or 0
    if avg_vol < 50000:
        return True, f"avg_vol {avg_vol:,} < 50k"
    mcap = c.get("market_cap", 0) or 0
    if mcap > 0 and mcap < 100_000_000:
        return True, f"mcap ${mcap:,.0f} < $100M"
    short_pct = c.get("short_pct", 0) or 0
    if short_pct > 0.20:
        return True, f"short {short_pct:.0%} > 20%"
    float_shares = c.get("float_shares", 0) or 0
    if 0 < float_shares < 10_000_000:
        return True, f"low float {float_shares:,}"
    return False, ""

def apply_filters(c, config):
    f = config["filters"]
    price = c.get("price", 0) or 0
    if price < f["min_price"]:
        return False, f"price ${price:.2f} < min ${f['min_price']}"
    if price > f["max_price"]:
        return False, f"price ${price:.2f} > max ${f['max_price']}"
    avg_vol = c.get("avg_volume", 0) or c.get("volume", 0) or 0
    if avg_vol < f["min_avg_volume"]:
        return False, f"avg_vol {avg_vol:,} < min {f['min_avg_volume']:,}"
    mcap = c.get("market_cap", 0) or 0
    if mcap < f["min_market_cap"]:
        return False, f"mcap ${mcap:,.0f} < min ${f['min_market_cap']:,.0f}"
    composite = c.get("composite", 0) or 0
    if composite < config["score_threshold"]:
        return False, f"score {composite:.1f} < threshold {config['score_threshold']}"
    factors = c.get("factors", {})
    for fname, fval in factors.items():
        if fname == "options":
            continue
        if (fval or 0) < config["min_factor_score"]:
            return False, f"factor {fname}={fval:.1f} < min {config['min_factor_score']}"
    return True, "passed"

# ── RUN ──
print("=" * 60)
print("🔍 DISCOVERY SCANNER — TEST RUN (no DB writes)")
print("=" * 60)

# Step 1: Collect from sources
raw = []
sources_config = CONFIG["sources"]

if sources_config.get("yahoo_gainers"):
    print("\n📊 Fetching Yahoo day_gainers...")
    tickers = fetch_yahoo_screen("day_gainers", 50)
    raw.extend(tickers)
    print(f"  → {len(tickers)} tickers")
    time.sleep(0.5)

# Try screen name for "most active"
if sources_config.get("yahoo_most_active"):
    print(f"\n📊 Fetching Yahoo most_actives...")
    tickers = fetch_yahoo_screen("most_actives", 50)
    existing_syms = {c["symbol"] for c in raw}
    new_tickers = [t for t in tickers if t["symbol"] not in existing_syms]
    raw.extend(new_tickers)
    print(f"  → {len(new_tickers)} new tickers (from {len(tickers)} total)")

if sources_config.get("sp500_universe"):
    print("\n📊 Fetching S&P 500 from Wikipedia...")
    tickers = fetch_sp500_wikipedia()
    # Sample if too many (scoring 500+ is slow)
    sample_size = CONFIG.get("sp500_sample_size", 75)
    if len(tickers) > sample_size:
        import random
        random.seed()
        tickers = random.sample(tickers, sample_size)
        print(f"  → Sampled {sample_size} from {len(tickers)} S&P 500 members")
    existing_syms = {c["symbol"] for c in raw}
    new_tickers = [t for t in tickers if t["symbol"] not in existing_syms]
    raw.extend(new_tickers)
    print(f"  → {len(new_tickers)} new tickers after dedup")
    time.sleep(0.5)

print(f"\n📦 Total raw candidates: {len(raw)}")

# Step 2: Deduplicate
priority = {"day_gainers": 1, "day_losers": 2, "day_most_actives": 3, "most_active": 3, "sp500_universe": 4}
seen = {}
for c in raw:
    sym = c["symbol"].upper()
    src_p = priority.get(c.get("source", ""), 99)
    if sym not in seen or src_p < seen[sym]["_priority"]:
        c["_priority"] = src_p
        seen[sym] = c
unique = list(seen.values())
print(f"  After dedup: {len(unique)}")

# Step 3: Batch fetch quotes
print("\n📡 Fetching batch quotes...")
symbols = [c["symbol"] for c in unique]
quotes = fetch_quotes_batch(symbols)
print(f"  Got quotes for {len(quotes)}/{len(symbols)} symbols")

# Enrich
for c in unique:
    sym = c["symbol"].upper()
    if sym in quotes:
        q = quotes[sym]
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

# Step 4: Quick reject
to_score = []
quick_rejected_count = 0
for c in unique:
    rejected, reason = quick_reject(c)
    if rejected:
        c["reject_reason"] = reason
        quick_rejected_count += 1
        continue
    to_score.append(c)
print(f"  Quick rejected: {quick_rejected_count}")
print(f"  To score: {len(to_score)}")

# Step 5: Score
print(f"\n🎯 Scoring {len(to_score)} candidates (this takes a moment)...")
scored = []
for i, c in enumerate(to_score):
    sym = c["symbol"].upper()
    if i > 0 and i % 10 == 0:
        time.sleep(1.5)
        print(f"  ...{i}/{len(to_score)} done")
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
print(f"  Successfully scored: {len(scored)}")

# Step 6: Apply filters
passed = []
rejected = []
for c in scored:
    ok, reason = apply_filters(c, CONFIG)
    if ok:
        passed.append(c)
    else:
        c["reject_reason"] = reason
        rejected.append(c)

# ── RESULTS ──
print("\n" + "=" * 60)
print("📋 RESULTS")
print("=" * 60)
print(f"\n{'PASS' if passed else 'NO PASSES'} — {len(passed)} candidates passed filters (score ≥ {CONFIG['score_threshold']})")

if passed:
    print(f"\n{'Symbol':<10} {'Name':<30} {'Price':>8} {'Score':>6} {'M':>5} {'V':>5} {'T':>5} {'Vol':>5} {'Opt':>5} {'Source'}")
    print("-" * 100)
    for c in passed[:10]:
        f = c.get("factors", {})
        print(f"{c['symbol']:<10} {c.get('name','')[:28]:<30} ${c.get('price',0):>7.2f} {c['composite']:>6.1f} "
              f"{f.get('momentum',0):>5.1f} {f.get('volume',0):>5.1f} {f.get('trend',0):>5.1f} "
              f"{f.get('volatility',0):>5.1f} {f.get('options',0):>5.1f} {c.get('source','')}")

print(f"\n❌ Rejected after scoring: {len(rejected)}")
if rejected:
    print(f"\n{'Symbol':<10} {'Name':<30} {'Price':>8} {'Score':>6} {'Reason'}")
    print("-" * 80)
    for c in rejected[:10]:
        print(f"{c['symbol']:<10} {c.get('name','')[:28]:<30} ${c.get('price',0):>7.2f} {c.get('composite',0):>6.1f} {c.get('reject_reason','')}")

# Top scored even if they didn't pass filters
print(f"\n🏆 Top 20 by score (all scored):")
print(f"{'Rank':<6} {'Symbol':<10} {'Name':<30} {'Price':>8} {'Score':>6} {'Status'}")
print("-" * 80)
for i, c in enumerate(scored[:20]):
    ok, reason = apply_filters(c, CONFIG)
    status = "✅ PASS" if ok else f"❌ {c.get('reject_reason', reason)}"
    print(f"{i+1:<6} {c['symbol']:<10} {c.get('name','')[:28]:<30} ${c.get('price',0):>7.2f} {c['composite']:>6.1f} {status}")

print(f"\n📊 Summary:")
print(f"  Raw candidates: {len(raw)}")
print(f"  Unique: {len(unique)}")
print(f"  Quick rejected: {quick_rejected_count}")
print(f"  Scored: {len(scored)}")
print(f"  Passed filters: {len(passed)}")
print(f"  Rejected: {len(rejected)}")
