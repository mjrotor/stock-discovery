"""
Stock Discovery — Neon Postgres database layer.
All functions use psycopg2 with autocommit and RealDictCursor for dict-like rows.
"""

import os
import psycopg2
import psycopg2.extras
from datetime import datetime

# Lazily initialized connection
_conn = None


def _get_conn():
    """Get or create a Neon Postgres connection."""
    global _conn
    if _conn is None or _conn.closed:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL environment variable is required")
        _conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        _conn.autocommit = True
    return _conn


def query(sql, params=()):
    """Execute a SELECT and return list of dicts."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql, params=()):
    """Execute a SELECT and return first row or None."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql, params=()):
    """Execute an INSERT/UPDATE/DELETE."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)


def execute_returning(sql, params=()):
    """Execute INSERT ... RETURNING * and return the row."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


# ─── Watchlist ──────────────────────────────────────────────

def get_watchlist():
    """Return {settings, tickers} from database."""
    row = query_one("SELECT * FROM portfolio_settings WHERE id = 1")
    if row is None:
        execute("INSERT INTO portfolio_settings (id) VALUES (1)")
        row = query_one("SELECT * FROM portfolio_settings WHERE id = 1")
    settings = dict(row)

    tickers = query("SELECT * FROM watchlist WHERE active = TRUE ORDER BY symbol")
    return {"settings": settings, "tickers": [dict(t) for t in tickers]}


def add_ticker(symbol, name=None, notes="", ticker_type="stock"):
    """Add a ticker to the watchlist."""
    execute(
        "INSERT INTO watchlist (symbol, name, notes, type) VALUES (%s, %s, %s, %s) ON CONFLICT (symbol) DO UPDATE SET active = TRUE, name = EXCLUDED.name, notes = EXCLUDED.notes",
        (symbol.upper(), name or symbol.upper(), notes, ticker_type),
    )


def remove_ticker(symbol):
    """Deactivate a ticker (soft delete)."""
    execute("UPDATE watchlist SET active = FALSE WHERE symbol = %s", (symbol.upper(),))


def get_active_tickers():
    """Return list of active ticker dicts."""
    rows = query("SELECT * FROM watchlist WHERE active = TRUE ORDER BY symbol")
    return [dict(r) for r in rows]


# ─── Portfolio Settings ─────────────────────────────────────

def get_settings():
    """Return settings dict."""
    row = query_one("SELECT * FROM portfolio_settings WHERE id = 1")
    return dict(row) if row else {}


def update_settings(data):
    """Update settings from a dict. Only updates provided keys."""
    allowed = {
        "starting_balance": "starting_balance",
        "cash": "cash",
        "max_positions": "max_positions",
        "max_per_position_pct": "max_per_position_pct",
        "min_cash_reserve_pct": "min_cash_reserve_pct",
        "stop_loss_pct": "stop_loss_pct",
        "paper_trading": "paper_trading",
    }
    sets = []
    params = []
    for key, col in allowed.items():
        if key in data:
            sets.append(f"{col} = %s")
            params.append(data[key])
    if not sets:
        return
    sets.append("updated_at = NOW()")
    sql = f"UPDATE portfolio_settings SET {', '.join(sets)} WHERE id = 1"
    execute(sql, tuple(params))


# ─── Positions ──────────────────────────────────────────────

def get_open_positions():
    """Return list of open positions, newest first."""
    rows = query("SELECT * FROM positions WHERE status = 'open' ORDER BY entry_date DESC")
    return [dict(r) for r in rows]


def get_all_positions():
    """Return all positions (open + closed)."""
    rows = query("SELECT * FROM positions ORDER BY entry_date DESC")
    return [dict(r) for r in rows]


def get_position(pos_id):
    """Return single position by id."""
    row = query_one("SELECT * FROM positions WHERE id = %s", (pos_id,))
    return dict(row) if row else None


def insert_position(pos):
    """Insert a new open position."""
    execute(
        """INSERT INTO positions (id, symbol, name, entry_price, current_price, shares, cost, pnl, pnl_pct, score_at_entry, status, entry_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', NOW())""",
        (pos["id"], pos["symbol"], pos["name"], pos["entry_price"], pos["current_price"],
         pos["shares"], pos["cost"], pos.get("pnl", 0), pos.get("pnl_pct", 0), pos.get("score_at_entry")),
    )


def update_position_price(pos_id, current_price, pnl, pnl_pct):
    """Update current price and P&L of an open position."""
    execute(
        "UPDATE positions SET current_price = %s, pnl = %s, pnl_pct = %s, updated_at = NOW() WHERE id = %s AND status = 'open'",
        (current_price, pnl, pnl_pct, pos_id),
    )


def close_position(pos_id, exit_price, exit_reason="manual"):
    """Close a position: set status=closed, compute final P&L."""
    pos = get_position(pos_id)
    if not pos:
        return None
    pnl = round((exit_price - float(pos["entry_price"])) * pos["shares"], 2)
    pnl_pct = round((exit_price / float(pos["entry_price"]) - 1) * 100, 2)
    execute(
        """UPDATE positions
           SET status = 'closed', exit_price = %s, exit_date = NOW(),
               exit_reason = %s, pnl = %s, pnl_pct = %s, current_price = %s, updated_at = NOW()
           WHERE id = %s""",
        (exit_price, exit_reason, pnl, pnl_pct, exit_price, pos_id),
    )
    return {"pnl": pnl, "pnl_pct": pnl_pct, "symbol": pos["symbol"], "shares": pos["shares"]}


# ─── Trade Log ──────────────────────────────────────────────

def log_trade(action, symbol, shares, price, cost, pnl_pct=None, reason="manual", score=None, position_id=None):
    """Append a trade log entry."""
    execute_returning(
        """INSERT INTO trade_log (action, symbol, shares, price, cost, pnl_pct, reason, score, position_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (action, symbol, shares, price, cost, pnl_pct, reason, score, position_id),
    )


def get_trades(symbol=None, action=None, limit=500):
    """Get trade log, optionally filtered. Returns rows with dashboard-friendly keys."""
    sql = "SELECT * FROM trade_log WHERE 1=1"
    params = []
    if symbol:
        sql += " AND symbol = %s"
        params.append(symbol.upper())
    if action:
        sql += " AND action = %s"
        params.append(action.upper())
    sql += " ORDER BY trade_date DESC LIMIT %s"
    params.append(limit)
    rows = query(sql, tuple(params))
    result = []
    for r in rows:
        d = dict(r)
        d["Date"] = str(d.pop("trade_date"))
        d["Action"] = d.pop("action")
        d["Symbol"] = d.pop("symbol")
        d["Shares"] = d.pop("shares")
        d["Price"] = float(d["price"]) if d.get("price") is not None else None
        d["Cost"] = float(d["cost"]) if d.get("cost") is not None else None
        d["PnL%"] = float(d["pnl_pct"]) if d.get("pnl_pct") is not None else None
        d["Reason"] = d.pop("reason")
        d["Score"] = float(d["score"]) if d.get("score") is not None else None
        d["position_id"] = d.pop("position_id")
        result.append(d)
    return result


# ─── Portfolio Summary ──────────────────────────────────────

def get_portfolio_summary():
    """Return full portfolio dict for dashboard."""
    settings = get_settings()
    open_pos = get_open_positions()

    total_position_value = sum(
        (float(p["current_price"]) if p.get("current_price") else float(p["entry_price"])) * p["shares"]
        for p in open_pos
    )
    cash = float(settings.get("cash", 0))
    total_value = cash + total_position_value
    starting = float(settings.get("starting_balance", 1500))
    total_pnl = total_value - starting
    total_pnl_pct = (total_value / starting - 1) * 100 if starting else 0

    closed = query("SELECT * FROM positions WHERE status = 'closed' ORDER BY exit_date DESC")

    for pos in open_pos:
        ep = float(pos["entry_price"])
        cp = float(pos["current_price"]) if pos.get("current_price") else ep
        pos["pnl"] = round((cp - ep) * pos["shares"], 2)
        pos["pnl_pct"] = round((cp / ep - 1) * 100, 2) if ep else 0

    updated_at = settings.get("updated_at")
    if updated_at is not None and hasattr(updated_at, "isoformat"):
        updated_at = updated_at.isoformat()

    return {
        "settings": settings,
        "portfolio": {
            "cash": round(cash, 2),
            "starting_balance": starting,
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "open_positions": open_pos,
            "closed_positions": [dict(p) for p in closed],
        },
        "last_updated": updated_at or "",
    }


# ─── Scores ─────────────────────────────────────────────────

def save_scores(scores_list):
    """Bulk insert latest scores."""
    conn = _get_conn()
    with conn.cursor() as cur:
        for s in scores_list:
            cur.execute(
                """INSERT INTO scores (symbol, composite, momentum, volume, trend, volatility, price, scored_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
                (s["symbol"], s["composite"],
                 s.get("factors", {}).get("momentum"),
                 s.get("factors", {}).get("volume"),
                 s.get("factors", {}).get("trend"),
                 s.get("factors", {}).get("volatility"),
                 s.get("price")),
            )


def get_latest_scores():
    """Return the most recent score for each ticker."""
    rows = query("""
        SELECT DISTINCT ON (symbol) *
        FROM scores
        ORDER BY symbol, scored_at DESC
    """)
    result = []
    for r in rows:
        d = dict(r)
        d["composite"] = float(d["composite"])
        d["factors"] = {
            "momentum": float(d["momentum"]) if d.get("momentum") is not None else 0,
            "volume": float(d["volume"]) if d.get("volume") is not None else 0,
            "trend": float(d["trend"]) if d.get("trend") is not None else 0,
            "volatility": float(d["volatility"]) if d.get("volatility") is not None else 0,
        }
        d["price"] = float(d["price"]) if d.get("price") is not None else 0
        result.append(d)
    result.sort(key=lambda x: x["composite"], reverse=True)
    return result


# ─── Daily Snapshots ────────────────────────────────────────

def save_daily_snapshot(total_value, cash, total_pnl, total_pnl_pct, open_count):
    """Upsert today's snapshot."""
    execute(
        """INSERT INTO daily_snapshots (snapshot_date, total_value, cash, total_pnl, total_pnl_pct, open_positions)
           VALUES (CURRENT_DATE, %s, %s, %s, %s, %s)
           ON CONFLICT (snapshot_date) DO UPDATE SET
               total_value = EXCLUDED.total_value, cash = EXCLUDED.cash,
               total_pnl = EXCLUDED.total_pnl, total_pnl_pct = EXCLUDED.total_pnl_pct,
               open_positions = EXCLUDED.open_positions, created_at = NOW()""",
        (total_value, cash, total_pnl, total_pnl_pct, open_count),
    )


def get_snapshots(days=30):
    """Return daily snapshots for equity curve."""
    rows = query("SELECT * FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT %s", (days,))
    return [dict(r) for r in reversed(rows)]


# ─── Analytics ──────────────────────────────────────────────

def get_analytics():
    """Compute performance metrics from trade_log and positions."""
    closed_trades = get_trades(action="SELL") + get_trades(action="STOP_LOSS")
    # Deduplicate by position_id
    seen = set()
    unique_closed = []
    for t in closed_trades:
        key = t.get("position_id") or t.get("Date") + t.get("Symbol")
        if key not in seen:
            seen.add(key)
            unique_closed.append(t)

    wins = [t for t in unique_closed if (t.get("PnL%") or 0) > 0]
    losses = [t for t in unique_closed if (t.get("PnL%") or 0) <= 0]
    win_rate = round(len(wins) / len(unique_closed) * 100, 1) if unique_closed else 0

    avg_win = round(sum(t.get("PnL%", 0) for t in wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(t.get("PnL%", 0) for t in losses) / len(losses), 2) if losses else 0

    ticker_pnl = {}
    for t in unique_closed:
        sym = t.get("Symbol", "?")
        pnl = t.get("PnL%", 0) or 0
        ticker_pnl[sym] = round(ticker_pnl.get(sym, 0) + pnl, 2)

    cumulative = []
    running = 0
    for t in sorted(unique_closed, key=lambda x: x.get("Date", "")):
        running += t.get("PnL%", 0) or 0
        cumulative.append({"date": t.get("Date", ""), "pnl": round(running, 2)})

    summary = get_portfolio_summary()
    p = summary["portfolio"]

    return {
        "total_value": p["total_value"],
        "starting_balance": p["starting_balance"],
        "total_pnl": p["total_pnl"],
        "total_pnl_pct": p["total_pnl_pct"],
        "total_trades": len(unique_closed),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "open_positions": len(p["open_positions"]),
        "ticker_pnl": ticker_pnl,
        "cumulative_pnl": cumulative,
    }


# ─── Reset ──────────────────────────────────────────────────

def reset_portfolio():
    """Clear all positions and trades, reset to starting balance."""
    starting = float(get_settings().get("starting_balance", 1500))
    execute("DELETE FROM positions")
    execute("DELETE FROM trade_log")
    execute("DELETE FROM scores")
    execute("DELETE FROM daily_snapshots")
    execute("UPDATE portfolio_settings SET cash = %s, updated_at = NOW() WHERE id = 1", (starting,))
