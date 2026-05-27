#!/usr/bin/env python3
"""
Stock Discovery — Watchlist Advisory Dashboard
Flask backend serving the UI and API endpoints.
Data layer: Neon Postgres via stock_discovery.db
"""

import json
import sys
import os
from datetime import date, datetime
from functools import wraps
from flask import Flask, render_template, jsonify, request

# Add parent dir to path so we can import stock_discovery
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_discovery import db

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"),
    static_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"),
)


# ─── Market Holidays (NYSE) ─────────────────────────────────

HOLIDAYS = {
    "2025-01-01","2025-01-20","2025-02-17","2025-04-18","2025-05-26",
    "2025-06-19","2025-07-04","2025-09-01","2025-11-27","2025-12-25",
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03","2026-05-25",
    "2026-06-19","2026-07-03","2026-09-07","2026-11-26","2026-12-25",
    "2027-01-01","2027-01-18","2027-02-15","2027-03-26","2027-05-31",
    "2027-06-18","2027-07-05","2027-09-06","2027-11-25","2027-12-24",
}


def is_market_closed():
    today = date.today()
    if today.weekday() >= 5:
        return True, "weekend"
    if today.isoformat() in HOLIDAYS:
        return True, "holiday"
    return False, None


# ─── Auth Helpers ────────────────────────────────────────────

def require_api_key(f):
    """Decorator: require ADVISORY_API_KEY in Authorization header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = os.environ.get("ADVISORY_API_KEY", "")
        if not key:
            return jsonify({"error": "Server misconfigured: no ADVISORY_API_KEY"}), 500
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != key:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def require_deposit_password(f):
    """Decorator: require DEPOSIT_PASSWORD in request body (for browser form)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        pwd = os.environ.get("DEPOSIT_PASSWORD", "")
        if not pwd:
            return jsonify({"error": "Server misconfigured: no DEPOSIT_PASSWORD"}), 500
        # Accept password in JSON body or form data
        data = request.json if request.is_json else request.form
        submitted = data.get("password", "")
        if submitted != pwd:
            return jsonify({"error": "Invalid password"}), 401
        return f(*args, **kwargs)
    return decorated


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


@app.route("/deposit")
def deposit_page():
    return render_template("deposit.html")


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


# ─── API: Advisory Engine (Option D) ────────────────────────

@app.route("/api/advisory/run", methods=["POST"])
@require_api_key
def api_advisory_run():
    """
    Run the full advisory engine against Neon DB.
    Requires ADVISORY_API_KEY in Authorization header.
    Returns: { report, picks, closed_stops, portfolio }
    """
    try:
        closed, reason = is_market_closed()
        if closed:
            return jsonify({"ok": True, "market_closed": True, "reason": reason, "report": f"Markets closed ({reason})"})

        from stock_discovery.scorer import run_scoring

        # Step 1: Score all tickers
        scores = run_scoring()
        if not scores:
            return jsonify({"ok": False, "error": "No scores returned"}), 500
        db.save_scores(scores)

        # Step 2: Update open positions with live prices
        price_map = {s["symbol"]: s.get("price", 0) for s in scores if s.get("price")}
        open_positions = db.get_open_positions()
        total_position_value = 0
        for pos in open_positions:
            sym = pos["symbol"]
            if sym in price_map:
                new_price = price_map[sym]
                pnl = round((new_price - float(pos["entry_price"])) * pos["shares"], 2)
                pnl_pct = round((new_price / float(pos["entry_price"]) - 1) * 100, 2)
                db.update_position_price(pos["id"], new_price, pnl, pnl_pct)
            cp = float(pos["current_price"]) if pos.get("current_price") else float(pos["entry_price"])
            total_position_value += cp * pos["shares"]

        # Reload positions after price updates
        open_positions = db.get_open_positions()

        # Step 3: Check stop-losses
        settings_row = db.get_settings()
        stop_pct = float(settings_row.get("stop_loss_pct", -0.10))
        cash = float(settings_row.get("cash", 0))
        total_value = cash + total_position_value
        closed_stops = []

        remaining = []
        for pos in open_positions:
            cp = float(pos["current_price"]) if pos.get("current_price") else float(pos["entry_price"])
            pnl_pct_val = (cp / float(pos["entry_price"])) - 1
            if pnl_pct_val <= stop_pct:
                # Close position
                result = db.close_position(pos["id"], cp, "stop_loss")
                if result:
                    proceeds = pos["shares"] * cp
                    cash = round(cash + proceeds, 2)
                    db.log_trade("SELL", pos["symbol"], pos["shares"], cp, float(pos["cost"]),
                                 pnl_pct=result["pnl_pct"], reason="stop_loss",
                                 score=pos.get("score_at_entry"), position_id=pos["id"])
                    closed_stops.append({
                        "symbol": pos["symbol"],
                        "shares": pos["shares"],
                        "entry_price": float(pos["entry_price"]),
                        "exit_price": cp,
                        "pnl_pct": result["pnl_pct"],
                    })
            else:
                remaining.append(pos)

        # Step 4: Pick new positions
        max_positions = int(settings_row.get("max_positions", 3))
        max_per = float(settings_row.get("max_per_position_pct", 0.30))
        min_reserve = float(settings_row.get("min_cash_reserve_pct", 0.20))
        starting_balance = float(settings_row.get("starting_balance", 1500))
        paper_trading = settings_row.get("paper_trading", True)

        total_value = cash + sum(
            (float(p.get("current_price", p["entry_price"])) if p.get("current_price") else float(p["entry_price"])) * p["shares"]
            for p in remaining
        )

        already_open = {p["symbol"] for p in remaining}
        current_count = len(already_open)
        slots_left = max_positions - current_count
        picks = []

        for s in scores:
            if slots_left <= 0:
                break
            if s["symbol"] in already_open:
                continue
            if s.get("composite", 0) < 30:
                continue
            if "error" in s or not s.get("price"):
                continue

            price = float(s["price"])
            max_invest = total_value * max_per
            shares = int(max_invest / price)
            if shares <= 0:
                continue

            cost = shares * price
            remaining_cash = cash - cost
            if remaining_cash < total_value * min_reserve:
                affordable = int((cash - total_value * min_reserve) / price)
                if affordable <= 0:
                    continue
                shares = affordable
                cost = shares * price

            if cost > cash or shares <= 0:
                continue

            cash = round(cash - cost, 2)
            slots_left -= 1

            pos_id = f"{s['symbol']}-{datetime.now().strftime('%Y%m%d%H%M')}"
            pos = {
                "id": pos_id,
                "symbol": s["symbol"],
                "name": s.get("name", s["symbol"]),
                "entry_price": price,
                "current_price": price,
                "shares": shares,
                "cost": round(cost, 2),
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "score_at_entry": s["composite"],
            }
            db.insert_position(pos)
            db.log_trade("BUY", s["symbol"], shares, price, round(cost, 2),
                         reason="advisory", score=s["composite"], position_id=pos_id)
            picks.append(pos)

        # Save cash update
        db.execute("UPDATE portfolio_settings SET cash = %s, updated_at = NOW() WHERE id = 1", (cash,))

        # Save daily snapshot
        total_position_value_final = sum(
            (float(p.get("current_price", p["entry_price"])) if p.get("current_price") else float(p["entry_price"])) * p["shares"]
            for p in remaining
        ) + sum(
            float(p.get("current_price", p["entry_price"])) * p["shares"]
            for p in picks
        )
        total_value_final = cash + total_position_value_final
        total_pnl = total_value_final - starting_balance
        total_pnl_pct = (total_value_final / starting_balance - 1) * 100 if starting_balance else 0
        all_open = remaining + picks
        db.save_daily_snapshot(total_value_final, cash, total_pnl, total_pnl_pct, len(all_open))

        # Format report
        report = _format_advisory_report(scores, picks, closed_stops, all_open, settings_row, total_value_final, starting_balance, total_pnl, total_pnl_pct)

        return jsonify({
            "ok": True,
            "report": report,
            "picks": picks,
            "closed_stops": closed_stops,
            "portfolio": {
                "cash": round(cash, 2),
                "total_value": round(total_value_final, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 2),
                "open_count": len(all_open),
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _format_advisory_report(scores, picks, closed_stops, open_positions, settings, total_value, starting_balance, total_pnl, total_pnl_pct):
    """Format Telegram-ready advisory report."""
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    mode = "📝 PAPER" if settings.get("paper_trading", True) else "💰 LIVE"
    stop_pct = float(settings.get("stop_loss_pct", -0.10))
    max_per = float(settings.get("max_per_position_pct", 0.30))
    max_pos = int(settings.get("max_positions", 3))

    lines = [
        f"📊 **Watchlist Report** — {now} {mode}",
        f"",
        f"**Portfolio**  `${total_value:,.2f}`  ({total_pnl:+,.2f} / {total_pnl_pct:+.1f}%)",
        f"Cash: `${float(settings.get('cash', 0)):,.2f}`  |  Positions: {len(open_positions)}/{max_pos}",
    ]

    if open_positions:
        lines.append(f"\n**Open Positions:**")
        for pos in open_positions:
            pnl_pct_val = float(pos.get("pnl_pct", 0))
            emoji = "🟢" if pnl_pct_val >= 0 else "🔴"
            entry = float(pos.get("entry_price", 0))
            cur = float(pos.get("current_price", pos.get("entry_price", 0)))
            sl_price = entry * (1 + stop_pct)
            lines.append(
                f"  {emoji} {pos['symbol']} {pos['shares']}x  "
                f"${entry:.2f} → ${cur:.2f}  "
                f"({pnl_pct_val:+.1f}%)  SL: ${sl_price:.2f}"
            )

    if closed_stops:
        lines.append(f"\n**⚠️ Stopped Out:**")
        for cs in closed_stops:
            lines.append(
                f"  🔴 {cs['symbol']} {cs['shares']}x  "
                f"${cs.get('entry_price', 0):.2f} → ${cs.get('exit_price', 0):.2f}  "
                f"({cs.get('pnl_pct', 0):+.1f}%)"
            )

    if picks:
        lines.append(f"\n**🆕 New Picks:**")
        for pos in picks:
            lines.append(
                f"  ✅ {pos['symbol']} {pos['shares']}x @ ${pos['entry_price']:.2f}  "
                f"(score: {pos['score_at_entry']})"
            )

    top5 = [s for s in scores if s.get("composite", 0) > 0][:5]
    if top5:
        lines.append(f"\n**Top Scored:**")
        open_syms = {p["symbol"] for p in open_positions}
        for s in top5:
            factors = s.get("factors", {})
            factor_str = f"M{factors.get('momentum',0):.0f}|V{factors.get('volume',0):.0f}|T{factors.get('trend',0):.0f}"
            already = " 🔒" if s["symbol"] in open_syms else ""
            lines.append(f"  {s['symbol']:<6} {s['composite']:.0f}/100  [{factor_str}]{already}")

    lines.append(f"\n_Stoploss: {stop_pct*100:.0f}%  |  Max/pos: {max_per*100:.0f}%_")

    return "\n".join(lines)


# ─── API: Deposit ───────────────────────────────────────────

@app.route("/api/deposit", methods=["POST"])
@require_deposit_password
def api_deposit():
    """
    Deposit funds into the account.
    Requires DEPOSIT_PASSWORD in request body.
    Body: { password, amount, note? }
    """
    try:
        data = request.json if request.is_json else request.form
        amount = float(data.get("amount", 0))
        note = data.get("note", "")

        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400
        if amount > 1_000_000:
            return jsonify({"error": "Amount exceeds maximum ($1M)"}), 400

        # Update cash and starting_balance in Neon
        db.execute(
            "UPDATE portfolio_settings SET cash = cash + %s, starting_balance = starting_balance + %s, updated_at = NOW() WHERE id = 1",
            (amount, amount)
        )

        # Log deposit in trade_log
        db.log_trade("DEPOSIT", "CASH", 0, 0, 0, reason=note or "deposit", pnl_pct=None, score=None)

        # Return updated summary
        summary = db.get_portfolio_summary()
        return jsonify({
            "ok": True,
            "deposited": amount,
            "portfolio": summary["portfolio"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Run ────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5150))
    app.run(host="0.0.0.0", port=port, debug=False)
