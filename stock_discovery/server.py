#!/usr/bin/env python3
"""
Stock Discovery — Watchlist Advisory Dashboard
Flask backend serving the UI and API endpoints.
Data layer: Neon Postgres via stock_discovery.db
"""

import json
import sys
import os
from datetime import datetime
from flask import Flask, render_template, jsonify, request

# Add parent dir to path so we can import stock_discovery
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_discovery import db

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
    static_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"),
)


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
    try:
        portfolio = db.get_portfolio_summary()
        return jsonify(portfolio)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scores")
def api_scores():
    try:
        scores = db.get_latest_scores()
        return jsonify(scores)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
def api_trades():
    try:
        symbol = request.args.get("symbol")
        action = request.args.get("action")
        trades = db.get_trades(symbol=symbol, action=action)
        return jsonify(trades)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]

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
    try:
        data = db.get_analytics()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── API: Actions ───────────────────────────────────────────

@app.route("/api/buy", methods=["POST"])
def api_buy():
    try:
        data = request.json
        symbol = data.get("symbol", "").upper()
        shares = int(data.get("shares", 0))
        price = float(data.get("price", 0))
        thesis = data.get("thesis", {})

        if not symbol or shares <= 0 or price <= 0:
            return jsonify({"error": "Invalid parameters"}), 400

        settings = db.get_settings()
        cash = float(settings.get("cash", 0))
        cost = shares * price

        if cost > cash:
            return jsonify({"error": "Insufficient cash"}), 400

        pos_id = f"{symbol}-{datetime.now().strftime('%Y%m%d%H%M')}"
        pos = {
            "id": pos_id,
            "symbol": symbol,
            "name": data.get("name", symbol),
            "entry_price": price,
            "current_price": price,
            "shares": shares,
            "cost": round(cost, 2),
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "score_at_entry": data.get("score", 0),
            "thesis": thesis,
        }

        db.insert_position(pos)
        db.execute("UPDATE portfolio_settings SET cash = cash - %s, updated_at = NOW() WHERE id = 1", (cost,))
        trade_reason = thesis.get("why", "manual") if thesis else "manual"
        db.log_trade("BUY", symbol, shares, price, round(cost, 2),
                     reason=trade_reason, score=data.get("score"),
                     position_id=pos_id)
        # Store thesis in trade_log as JSON (column may not exist yet)
        if thesis:
            try:
                db.execute("UPDATE trade_log SET thesis = %s WHERE id = (SELECT id FROM trade_log WHERE position_id = %s ORDER BY trade_date DESC LIMIT 1)",
                           (json.dumps(thesis), pos_id))
            except Exception:
                pass  # thesis column may not exist yet

        return jsonify({"ok": True, "position": pos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/close", methods=["POST"])
def api_close():
    try:
        data = request.json
        pos_id = data.get("position_id", "")
        price = float(data.get("price", 0))

        pos = db.get_position(pos_id)
        if not pos:
            return jsonify({"error": "Position not found"}), 404

        result = db.close_position(pos_id, price, "manual")
        if not result:
            return jsonify({"error": "Close failed"}), 500

        proceeds = pos["shares"] * price
        db.execute("UPDATE portfolio_settings SET cash = cash + %s, updated_at = NOW() WHERE id = 1", (proceeds,))
        db.log_trade("SELL", pos["symbol"], pos["shares"], price, pos["cost"],
                     pnl_pct=result["pnl_pct"], reason="manual", score=pos.get("score_at_entry"), position_id=pos_id)

        return jsonify({"ok": True, "pnl": result["pnl"], "pnl_pct": result["pnl_pct"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── API: Watchlist CRUD ────────────────────────────────────

@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    try:
        wl = db.get_watchlist()
        return jsonify(wl)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    try:
        data = request.json
        symbol = data.get("symbol", "").upper()
        name = data.get("name", symbol)
        notes = data.get("notes", "")

        if not symbol:
            return jsonify({"error": "Symbol required"}), 400

        # Check if already active
        existing = db.get_watchlist()
        if any(t["symbol"] == symbol for t in existing.get("tickers", [])):
            return jsonify({"error": "Already in watchlist"}), 400

        db.add_ticker(symbol, name, notes)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/remove", methods=["POST"])
def api_watchlist_remove():
    try:
        data = request.json
        symbol = data.get("symbol", "").upper()
        db.remove_ticker(symbol)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings/update", methods=["POST"])
def api_settings_update():
    try:
        data = request.json
        db.update_settings(data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def api_reset():
    try:
        db.reset_portfolio()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rescore", methods=["POST"])
def api_rescore():
    try:
        from stock_discovery.scorer import run_scoring
        scores = run_scoring()
        if scores:
            db.save_scores(scores)
        return jsonify({"ok": True, "scores": scores, "count": len(scores)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Run ────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5150))
    app.run(host="0.0.0.0", port=port, debug=False)
