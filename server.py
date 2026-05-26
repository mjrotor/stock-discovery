#!/usr/bin/env python3
"""
Stock Discovery — Watchlist Advisory Dashboard
Flask backend serving the UI and API endpoints.
"""

import json
import csv
import os
import sys
import subprocess
from datetime import datetime, date
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

DATA_DIR = os.path.expanduser("~/.hermes/watchlist")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.csv")
SCORER_SCRIPT = os.path.join(DATA_DIR, "scorer.py")


# ─── Helpers ────────────────────────────────────────────────

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_csv(path):
    rows = []
    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def save_csv(path, rows, fieldnames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_scorer():
    """Run scorer.py and return parsed results."""
    result = subprocess.run(
        [sys.executable, SCORER_SCRIPT],
        capture_output=True, text=True, timeout=120
    )
    stdout = result.stdout.strip()
    start = stdout.find("[")
    end = stdout.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(stdout[start:end + 1])
        except json.JSONDecodeError:
            pass
    return []


# ─── Pages ──────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/trades")
def trades():
    return render_template("trades.html")


@app.route("/ticker/<symbol>")
def ticker_detail(symbol):
    return render_template("ticker.html", symbol=symbol)


@app.route("/watchlist")
def watchlist_mgmt():
    return render_template("watchlist.html")


@app.route("/analytics")
def analytics():
    return render_template("analytics.html")


@app.route("/settings")
def settings():
    return render_template("settings.html")


# ─── API: Portfolio ─────────────────────────────────────────

@app.route("/api/portfolio")
def api_portfolio():
    portfolio = load_json(PORTFOLIO_FILE)
    return jsonify(portfolio)


@app.route("/api/scores")
def api_scores():
    scores = run_scorer()
    return jsonify(scores)


@app.route("/api/trades")
def api_trades():
    trades = load_csv(HISTORY_FILE)
    # Optional filters
    symbol = request.args.get("symbol")
    action = request.args.get("action")
    if symbol:
        trades = [t for t in trades if t.get("Symbol", "").upper() == symbol.upper()]
    if action:
        trades = [t for t in trades if t.get("Action", "").upper() == action.upper()]
    # Sort by date descending
    trades.sort(key=lambda x: x.get("Date", ""), reverse=True)
    return jsonify(trades)


@app.route("/api/ticker/<symbol>")
def api_ticker(symbol):
    """Fetch chart data for a ticker from Yahoo Finance."""
    import urllib.request
    from urllib.error import URLError

    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=3mo&interval=1d"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]

        # Build OHLCV arrays
        ohlcv = []
        for i, ts in enumerate(timestamps):
            o = quote["open"][i] if quote["open"][i] is not None else None
            h = quote["high"][i] if quote["high"][i] is not None else None
            l = quote["low"][i] if quote["low"][i] is not None else None
            c = quote["close"][i] if quote["close"][i] is not None else None
            v = quote["volume"][i] if quote["volume"][i] is not None else 0
            if all(x is not None for x in [o, h, l, c]):
                ohlcv.append({
                    "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                    "open": round(o, 2) if o else 0,
                    "high": round(h, 2) if h else 0,
                    "low": round(l, 2) if l else 0,
                    "close": round(c, 2) if c else 0,
                    "volume": v or 0,
                })

        return jsonify({
            "symbol": symbol,
            "name": meta.get("shortName", symbol),
            "price": meta.get("regularMarketPrice", 0),
            "prev_close": meta.get("chartPreviousClose", meta.get("previousClose", 0)),
            "ohlcv": ohlcv,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics")
def api_analytics():
    """Compute performance analytics from trade history and portfolio."""
    portfolio = load_json(PORTFOLIO_FILE)
    trades = load_csv(HISTORY_FILE)
    p = portfolio.get("portfolio", {})

    # Basic metrics
    total_value = p.get("total_value", 0)
    starting = p.get("starting_balance", 0)
    total_pnl = p.get("total_pnl", 0)
    total_pnl_pct = p.get("total_pnl_pct", 0)

    # Trade stats
    closed = [t for t in trades if t.get("Action", "").upper() in ("SELL", "STOP_LOSS", "CLOSE")]
    wins = [t for t in closed if float(t.get("PnL%", 0) or 0) > 0]
    losses = [t for t in closed if float(t.get("PnL%", 0) or 0) <= 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0

    avg_win = round(sum(float(t.get("PnL%", 0) or 0) for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(float(t.get("PnL%", 0) or 0) for t in losses) / len(losses), 2) if losses else 0

    # P&L by ticker
    ticker_pnl = {}
    for t in closed:
        sym = t.get("Symbol", "?")
        pnl = float(t.get("PnL%", 0) or 0)
        ticker_pnl[sym] = round(ticker_pnl.get(sym, 0) + pnl, 2)

    # Cumulative P&L over time (from closed trades)
    cumulative = []
    running = 0
    for t in sorted(closed, key=lambda x: x.get("Date", "")):
        running += float(t.get("PnL%", 0) or 0)
        cumulative.append({"date": t.get("Date", ""), "pnl": round(running, 2)})

    return jsonify({
        "total_value": total_value,
        "starting_balance": starting,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "total_trades": len(closed),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "open_positions": len(p.get("open_positions", [])),
        "ticker_pnl": ticker_pnl,
        "cumulative_pnl": cumulative,
    })


# ─── API: Actions ───────────────────────────────────────────

@app.route("/api/buy", methods=["POST"])
def api_buy():
    data = request.json
    symbol = data.get("symbol", "").upper()
    shares = int(data.get("shares", 0))
    price = float(data.get("price", 0))

    if not symbol or shares <= 0 or price <= 0:
        return jsonify({"error": "Invalid parameters"}), 400

    portfolio = load_json(PORTFOLIO_FILE)
    cost = shares * price
    cash = portfolio["portfolio"]["cash"]

    if cost > cash:
        return jsonify({"error": "Insufficient cash"}), 400

    pos = {
        "id": f"{symbol}-{datetime.now().strftime('%Y%m%d%H%M')}",
        "symbol": symbol,
        "name": data.get("name", symbol),
        "entry_price": price,
        "current_price": price,
        "shares": shares,
        "cost": round(cost, 2),
        "pnl": 0.0,
        "pnl_pct": 0.0,
        "score_at_entry": data.get("score", 0),
        "entry_date": datetime.now().isoformat(),
        "exit_price": None,
        "exit_date": None,
        "exit_reason": None,
    }

    portfolio["portfolio"]["cash"] -= cost
    portfolio["portfolio"]["open_positions"].append(pos)
    portfolio["last_updated"] = datetime.now().isoformat()
    save_json(PORTFOLIO_FILE, portfolio)

    # Log to CSV
    trades = load_csv(HISTORY_FILE)
    trades.append({
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Action": "BUY",
        "Symbol": symbol,
        "Shares": shares,
        "Price": price,
        "Cost": round(cost, 2),
        "PnL%": "",
        "Reason": "manual",
        "Score": data.get("score", ""),
    })
    save_csv(HISTORY_FILE, trades, ["Date", "Action", "Symbol", "Shares", "Price", "Cost", "PnL%", "Reason", "Score"])

    return jsonify({"ok": True, "position": pos})


@app.route("/api/close", methods=["POST"])
def api_close():
    data = request.json
    pos_id = data.get("position_id", "")
    price = float(data.get("price", 0))

    portfolio = load_json(PORTFOLIO_FILE)
    positions = portfolio["portfolio"]["open_positions"]

    for i, pos in enumerate(positions):
        if pos["id"] == pos_id:
            proceeds = pos["shares"] * price
            pnl = proceeds - pos["cost"]
            pnl_pct = (price / pos["entry_price"] - 1) * 100

            pos["exit_price"] = price
            pos["exit_date"] = datetime.now().isoformat()
            pos["exit_reason"] = "manual"
            pos["pnl"] = round(pnl, 2)
            pos["pnl_pct"] = round(pnl_pct, 2)

            portfolio["portfolio"]["cash"] += proceeds
            portfolio["portfolio"]["closed_positions"].append(pos)
            positions.pop(i)
            portfolio["last_updated"] = datetime.now().isoformat()
            save_json(PORTFOLIO_FILE, portfolio)

            # Log to CSV
            trades = load_csv(HISTORY_FILE)
            trades.append({
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "Action": "SELL",
                "Symbol": pos["symbol"],
                "Shares": pos["shares"],
                "Price": price,
                "Cost": pos["cost"],
                "PnL%": round(pnl_pct, 2),
                "Reason": "manual",
                "Score": pos.get("score_at_entry", ""),
            })
            save_csv(HISTORY_FILE, trades, ["Date", "Action", "Symbol", "Shares", "Price", "Cost", "PnL%", "Reason", "Score"])

            return jsonify({"ok": True, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2)})

    return jsonify({"error": "Position not found"}), 404


# ─── API: Watchlist CRUD ────────────────────────────────────

@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    wl = load_json(WATCHLIST_FILE)
    return jsonify(wl)


@app.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    data = request.json
    symbol = data.get("symbol", "").upper()
    name = data.get("name", symbol)
    notes = data.get("notes", "")

    if not symbol:
        return jsonify({"error": "Symbol required"}), 400

    wl = load_json(WATCHLIST_FILE)
    # Check if already exists
    if any(t["symbol"] == symbol for t in wl.get("tickers", [])):
        return jsonify({"error": "Already in watchlist"}), 400

    wl.setdefault("tickers", []).append({
        "symbol": symbol,
        "name": name,
        "type": "stock",
        "notes": notes,
    })
    save_json(WATCHLIST_FILE, wl)
    return jsonify({"ok": True})


@app.route("/api/watchlist/remove", methods=["POST"])
def api_watchlist_remove():
    data = request.json
    symbol = data.get("symbol", "").upper()

    wl = load_json(WATCHLIST_FILE)
    wl["tickers"] = [t for t in wl.get("tickers", []) if t["symbol"] != symbol]
    save_json(WATCHLIST_FILE, wl)
    return jsonify({"ok": True})


@app.route("/api/settings/update", methods=["POST"])
def api_settings_update():
    data = request.json
    wl = load_json(WATCHLIST_FILE)

    settings_map = {
        "starting_balance": "starting_balance",
        "max_positions": "max_positions",
        "max_per_position_pct": "max_per_position_pct",
        "min_cash_reserve_pct": "min_cash_reserve_pct",
        "stop_loss_pct": "stop_loss_pct",
        "paper_trading": "paper_trading",
    }

    for key, setting_key in settings_map.items():
        if key in data:
            wl["settings"][setting_key] = data[key]

    save_json(WATCHLIST_FILE, wl)

    # Also update portfolio settings
    portfolio = load_json(PORTFOLIO_FILE)
    for key, setting_key in settings_map.items():
        if key in data:
            portfolio["settings"][setting_key] = data[key]
    save_json(PORTFOLIO_FILE, portfolio)

    return jsonify({"ok": True})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset portfolio to starting balance."""
    wl = load_json(WATCHLIST_FILE)
    starting = wl.get("settings", {}).get("starting_balance", 1500)

    portfolio = {
        "settings": wl.get("settings", {}),
        "portfolio": {
            "cash": starting,
            "starting_balance": starting,
            "total_value": starting,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "open_positions": [],
            "closed_positions": [],
            "trade_history": [],
        },
        "last_updated": datetime.now().isoformat(),
    }
    save_json(PORTFOLIO_FILE, portfolio)

    # Clear trade history
    save_csv(HISTORY_FILE, [], ["Date", "Action", "Symbol", "Shares", "Price", "Cost", "PnL%", "Reason", "Score"])

    return jsonify({"ok": True})


@app.route("/api/rescore", methods=["POST"])
def api_rescore():
    scores = run_scorer()
    return jsonify({"ok": True, "scores": scores})


# ─── Run ────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5150))
    app.run(host="0.0.0.0", port=port, debug=False)
