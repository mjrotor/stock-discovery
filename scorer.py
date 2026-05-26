#!/usr/bin/env python3
"""
Stock Scoring Engine — composite score 0-100 for each ticker.
Factors: Momentum, Volume, Trend, Volatility, Options Interest.
Data source: Yahoo Finance chart API (free, no key needed).
"""

import json
import sys
import time
import math
from datetime import date, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

DATA_DIR = "/home/mrotatori/.hermes/watchlist"
WATCHLIST_FILE = f"{DATA_DIR}/watchlist.json"

# Scoring weights (must sum to 1.0)
WEIGHTS = {
    "momentum": 0.25,
    "volume": 0.20,
    "trend": 0.25,
    "volatility": 0.15,
    "options": 0.15,
}

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def load_watchlist():
    with open(WATCHLIST_FILE) as f:
        return json.load(f)


def fetch_chart(symbol, range_days="3mo", interval="1d"):
    """Fetch OHLCV data from Yahoo Finance."""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_days}&interval={interval}"
    req = Request(url, headers=YAHOO_HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        result = data["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        volumes = result["indicators"]["quote"][0]["volume"]
        opens = result["indicators"]["quote"][0]["open"]
        highs = result["indicators"]["quote"][0]["high"]
        lows = result["indicators"]["quote"][0]["low"]
        return {
            "symbol": symbol,
            "name": meta.get("shortName", symbol),
            "price": meta.get("regularMarketPrice", 0),
            "prev_close": meta.get("chartPreviousClose", meta.get("previousClose", 0)),
            "timestamps": timestamps,
            "closes": [c for c in closes if c is not None],
            "volumes": [v for v in volumes if v is not None],
            "opens": [o for o in opens if o is not None],
            "highs": [h for h in highs if h is not None],
            "lows": [l for l in lows if l is not None],
        }
    except Exception as e:
        print(f"  ⚠️  Error fetching {symbol}: {e}", file=sys.stderr)
        return None


def sma(values, period):
    """Simple moving average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def score_momentum(closes, price, prev_close):
    """
    Rate of change + price vs SMA.
    High momentum = strong recent gains but not overextended.
    Score 0-10.
    """
    if len(closes) < 10:
        return 5.0

    # 5-day rate of change
    roc5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    # 10-day rate of change
    roc10 = (closes[-1] / closes[-11] - 1) * 100 if len(closes) >= 11 else 0

    # Price vs 10-SMA
    avg10 = sma(closes, 10)
    vs_sma = (closes[-1] / avg10 - 1) * 100 if avg10 else 0

    # Composite: positive ROC and above SMA = good momentum
    raw = (roc5 * 0.4 + roc10 * 0.3 + vs_sma * 0.3)

    # Map to 0-10: sweet spot is +2% to +8% over 5 days
    if raw > 15:
        score = max(2.0, 10 - (raw - 8) * 0.5)  # overextended = lower
    elif raw > 0:
        score = 5.0 + raw * 0.6
    elif raw > -5:
        score = 3.0 + (raw + 5) * 0.4
    else:
        score = max(0, 3.0 + (raw + 5) * 0.3)

    return min(10.0, max(0.0, score))


def score_volume(volumes):
    """
    Relative volume vs 20-day average.
    Higher relative volume = more interest = higher score.
    Score 0-10.
    """
    if len(volumes) < 21:
        return 5.0

    today_vol = volumes[-1]
    avg_vol = sum(volumes[-21:-1]) / 20  # exclude today
    if avg_vol == 0:
        return 5.0

    rel_vol = today_vol / avg_vol
    # 1.0 = average = 5 score
    # 2.0+ = high interest = 8-10
    # <0.5 = low interest = 1-3
    if rel_vol >= 2.0:
        score = min(10.0, 8.0 + (rel_vol - 2.0))
    elif rel_vol >= 1.0:
        score = 5.0 + (rel_vol - 1.0) * 6.0
    else:
        score = max(1.0, rel_vol * 5.0)

    return min(10.0, max(0.0, score))


def score_trend(closes):
    """
    Higher highs/lows pattern + directional bias.
    Count up-moves vs down-moves over last 10 days.
    Score 0-10.
    """
    if len(closes) < 11:
        return 5.0

    recent = closes[-11:]
    ups = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
    downs = 10 - ups

    # 7+ up days out of 10 = strong uptrend
    if ups >= 8:
        score = 9.0 + (ups - 8) * 0.5
    elif ups >= 6:
        score = 7.0 + (ups - 6) * 1.0
    elif ups >= 4:
        score = 4.0 + (ups - 4) * 1.5
    elif ups >= 2:
        score = 2.0 + (ups - 2) * 1.0
    else:
        score = ups * 1.0

    # Bonus: making higher highs
    highs_5 = max(closes[-5:]) if len(closes) >= 5 else closes[-1]
    highs_10 = max(closes[-10:]) if len(closes) >= 10 else closes[-1]
    if highs_5 >= highs_10:
        score = min(10.0, score + 0.5)

    return min(10.0, max(0.0, score))


def score_volatility(highs, lows, closes):
    """
    ATR-based volatility scoring. Sweet spot: moderate volatility.
    Too low = no movement. Too high = risky.
    Score 0-10.
    """
    if len(highs) < 15 or len(lows) < 15 or len(closes) < 15:
        return 5.0

    # Calculate 14-period ATR as % of price
    trs = []
    for i in range(-14, 0):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)

    atr = sum(trs) / len(trs)
    atr_pct = (atr / closes[-1]) * 100 if closes[-1] else 0

    # Sweet spot: 1.5% - 3.5% daily ATR
    if 1.5 <= atr_pct <= 3.5:
        score = 7.0 + (3.5 - abs(atr_pct - 2.5)) * 1.2
    elif atr_pct < 1.5:
        score = 3.0 + atr_pct * 2.0  # too low
    elif atr_pct <= 6.0:
        score = 7.0 - (atr_pct - 3.5) * 1.5  # elevated
    else:
        score = max(1.0, 6.0 - (atr_pct - 6.0))  # crazy vol

    return min(10.0, max(0.0, score))


def score_options(price, prev_close, volumes):
    """
    Proxy for options interest using volume spike + price movement.
    Uses more granular thresholds to avoid ceiling effect.
    Score 0-10.
    """
    if len(volumes) < 6 or not prev_close:
        return 5.0

    day_change_pct = abs(price / prev_close - 1) * 100
    avg_vol = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    vol_spike = volumes[-1] / avg_vol if avg_vol else 1.0

    # More graduated scoring
    # Typical: 0-2% change, 0.8-1.2x vol = neutral (4-6)
    # Notable: 2-5% change, 1.5-2x vol = interesting (6-8)
    # Extreme: 5%+ change, 2.5x+ vol = unusual activity (8-10)
    vol_score = min(10, vol_spike * 4.5)  # 1x vol = 4.5, 2x = 9
    move_score = min(10, day_change_pct * 2.5)  # 1% = 2.5, 4% = 10

    # Weight: volume matters more than single-day move
    raw = vol_score * 0.6 + move_score * 0.4

    # Avoid ceiling: only top-tier gets 9-10
    if vol_spike >= 2.5 and day_change_pct >= 4.0:
        raw = min(10.0, 8.0 + (vol_spike - 2.5) + (day_change_pct - 4.0) * 0.3)

    return min(10.0, max(1.0, raw))


def score_ticker(chart):
    """Score a single ticker. Returns dict with scores and composite."""
    closes = chart["closes"]
    volumes = chart["volumes"]
    highs = chart["highs"]
    lows = chart["lows"]
    price = chart["price"]
    prev_close = chart["prev_close"]

    if not closes or not price:
        return {"composite": 0, "factors": {}, "price": 0, "error": "no data"}

    factors = {
        "momentum": score_momentum(closes, price, prev_close),
        "volume": score_volume(volumes),
        "trend": score_trend(closes),
        "volatility": score_volatility(highs, lows, closes),
        "options": score_options(price, prev_close, volumes),
    }

    composite = sum(factors[k] * WEIGHTS[k] for k in factors) * 10

    return {
        "composite": round(composite, 1),
        "factors": {k: round(v, 1) for k, v in factors.items()},
        "price": price,
        "name": chart["name"],
    }


def run_scoring():
    """Score all tickers in watchlist."""
    watchlist = load_watchlist()
    tickers = [t["symbol"] for t in watchlist["tickers"]]
    results = []

    print(f"Scoring {len(tickers)} tickers...")
    for i, symbol in enumerate(tickers):
        if i > 0:
            time.sleep(1.5)  # rate limit avoidance
        chart = fetch_chart(symbol)
        if chart:
            scored = score_ticker(chart)
            scored["symbol"] = symbol
            results.append(scored)
            print(f"  {symbol}: {scored['composite']}/100")
        else:
            results.append({"symbol": symbol, "composite": 0, "error": "fetch failed"})

    # Sort by composite score descending
    results.sort(key=lambda x: x.get("composite", 0), reverse=True)
    return results


if __name__ == "__main__":
    results = run_scoring()
    print(json.dumps(results, indent=2))
