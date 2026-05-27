"""
Database migration: JSON files → Neon Postgres.

Usage:
    Set DATABASE_URL env var, then run:
    python3 -m stock_discovery.migrate

Migrates:
    ~/.hermes/watchlist/watchlist.json → watchlist + portfolio_settings tables
    ~/.hermes/watchlist/portfolio.json → positions table
    ~/.hermes/watchlist/trade_history.csv → trade_log table
"""

import json
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_discovery import db

DATA_DIR = os.path.expanduser("~/.hermes/watchlist")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.csv")


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_csv(path):
    rows = []
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def migrate():
    print("=== Stock Discovery: JSON → Neon Migration ===\n")

    # 1. Portfolio settings
    wl_data = load_json(WATCHLIST_FILE)
    settings = wl_data.get("settings", {})
    if settings:
        print("Migrating settings...")
        db.update_settings({
            "starting_balance": settings.get("starting_balance", 1500),
            "max_positions": settings.get("max_positions", 3),
            "max_per_position_pct": settings.get("max_per_position_pct", 0.30),
            "min_cash_reserve_pct": settings.get("min_cash_reserve_pct", 0.20),
            "stop_loss_pct": settings.get("stop_loss_pct", -0.10),
            "paper_trading": settings.get("paper_trading", True),
        })
        # Set cash from portfolio if available
    pf_data = load_json(PORTFOLIO_FILE)
    pf = pf_data.get("portfolio", {})
    if pf.get("cash"):
        db.update_settings({"cash": pf["cash"]})
        print("  ✓ Settings migrated")
    else:
        print("  No settings found, using defaults")

    # 2. Watchlist tickers
    tickers = wl_data.get("tickers", [])
    print(f"\nMigrating {len(tickers)} tickers...")
    for t in tickers:
        db.add_ticker(
            symbol=t["symbol"],
            name=t.get("name", t["symbol"]),
            notes=t.get("notes", ""),
            ticker_type=t.get("type", "stock"),
        )
    print(f"  ✓ {len(tickers)} tickers migrated")

    # 3. Positions
    positions = pf.get("open_positions", []) + pf.get("closed_positions", [])
    print(f"\nMigrating {len(positions)} positions...")
    for pos in positions:
        db.execute(
            """INSERT INTO positions (id, symbol, name, entry_price, current_price, exit_price, shares, cost, pnl, pnl_pct, score_at_entry, status, entry_date, exit_date, exit_reason)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (
                pos["id"], pos["symbol"], pos.get("name", pos["symbol"]),
                pos["entry_price"], pos.get("current_price"), pos.get("exit_price"),
                pos["shares"], pos["cost"], pos.get("pnl", 0), pos.get("pnl_pct", 0),
                pos.get("score_at_entry"),
                "closed" if pos.get("exit_price") else "open",
                pos.get("entry_date"), pos.get("exit_date"), pos.get("exit_reason"),
            ),
        )
    print(f"  ✓ {len(positions)} positions migrated")

    # 4. Trade history
    trades = load_csv(HISTORY_FILE)
    print(f"\nMigrating {len(trades)} trade log entries...")
    for t in trades:
        db.execute(
            """INSERT INTO trade_log (trade_date, action, symbol, shares, price, cost, pnl_pct, reason, score, position_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                t.get("Date", ""), t.get("Action", ""), t.get("Symbol", ""),
                int(t.get("Shares", 0) or 0),
                float(t.get("Price", 0) or 0),
                float(t.get("Cost", 0) or 0),
                float(t["PnL%"]) if t.get("PnL%") else None,
                t.get("Reason", "manual"),
                float(t["Score"]) if t.get("Score") else None,
                None,
            ),
        )
    print(f"  ✓ {len(trades)} trades migrated")

    # 5. Verify
    wl = db.get_watchlist()
    pf_summary = db.get_portfolio_summary()
    print(f"\n=== Migration Complete ===")
    print(f"  Watchlist: {len(wl['tickers'])} tickers")
    print(f"  Open positions: {len(pf_summary['portfolio']['open_positions'])}")
    print(f"  Closed positions: {len(pf_summary['portfolio']['closed_positions'])}")
    print(f"  Cash: ${pf_summary['portfolio']['cash']:,.2f}")
    print(f"  Total value: ${pf_summary['portfolio']['total_value']:,.2f}")


if __name__ == "__main__":
    migrate()
