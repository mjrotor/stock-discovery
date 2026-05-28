#!/usr/bin/env python3
"""
Watchlist Advisor — top 3 picks, portfolio management, position tracking.
Reads/writes Neon Postgres via stock_discovery.db.
Scoring → picks → size positions → check stops → output report.
"""

import json
import sys
import os
import time
from datetime import date, datetime

# Add parent dir for package import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_discovery import db

# US Market Holidays (NYSE observed) 2025-2027
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


def run_scorer():
    """Run scoring engine and return results."""
    from stock_discovery.scorer import run_scoring
    return run_scoring()


def update_positions(scores):
    """Update current prices of open positions from scores."""
    open_pos = db.get_open_positions()
    price_map = {s["symbol"]: s.get("price", 0) for s in scores if "price" in s}
    total_position_value = 0

    for pos in open_pos:
        symbol = pos["symbol"]
        if symbol in price_map:
            cp = price_map[symbol]
            ep = float(pos["entry_price"])
            pnl = round((cp - ep) * pos["shares"], 2)
            pnl_pct = round((cp / ep - 1) * 100, 2) if ep else 0
            db.update_position_price(pos["id"], cp, pnl, pnl_pct)
            total_position_value += pos["shares"] * cp

    cash = float(db.get_settings().get("cash", 0))
    total = cash + total_position_value
    starting = float(db.get_settings().get("starting_balance", 1500))
    total_pnl = round(total - starting, 2)
    total_pnl_pct = round((total / starting - 1) * 100, 2) if starting else 0

    open_count = db.query("SELECT COUNT(*) as cnt FROM positions WHERE status = 'open'")[0]["cnt"]
    db.save_daily_snapshot(total, cash, total_pnl, total_pnl_pct, int(open_count))

    return total, cash, total_pnl, total_pnl_pct


def check_stops():
    """Check if any open position hit stop-loss. Close them if so."""
    settings = db.get_settings()
    stop_pct = float(settings.get("stop_loss_pct", -0.10))
    cash = float(settings.get("cash", 0))
    closed = []

    for pos in db.get_open_positions():
        if pos.get("current_price"):
            pnl_pct = (float(pos["current_price"]) / float(pos["entry_price"])) - 1
            if pnl_pct <= stop_pct:
                result = db.close_position(pos["id"], float(pos["current_price"]), "stop_loss")
                if result:
                    proceeds = pos["shares"] * float(pos["current_price"])
                    cash += proceeds
                    db.log_trade("STOP_LOSS", pos["symbol"], pos["shares"],
                                 float(pos["current_price"]), pos["cost"],
                                 pnl_pct=result["pnl_pct"], reason="stop_loss",
                                 score=pos.get("score_at_entry"), position_id=pos["id"])
                    closed.append({**pos, **result, "exit_price": pos["current_price"], "exit_reason": "stop_loss"})
                    print(f"  🔴 STOP: {pos['symbol']} @ {pos['current_price']} ({result['pnl_pct']:+.1f}%)")

    if closed:
        db.execute("UPDATE portfolio_settings SET cash = %s, updated_at = NOW() WHERE id = 1", (round(cash, 2),))

    return closed


def pick_positions(scores, total_value, cash):
    """Pick up to 3 new positions from scores."""
    settings = db.get_settings()
    max_per = float(settings.get("max_per_position_pct", 0.30))
    min_reserve = float(settings.get("min_cash_reserve_pct", 0.20))
    max_pos = int(settings.get("max_positions", 3))

    open_pos = db.get_open_positions()
    already_open = {p["symbol"] for p in open_pos}
    slots_left = max_pos - len(already_open)

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

        price = s["price"]
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

        cash -= cost
        slots_left -= 1

        pos = {
            "id": f"{s['symbol']}-{datetime.now().strftime('%Y%m%d%H%M')}",
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
        picks.append(pos)

    return picks


def execute_picks(picks):
    """Add new picks to portfolio in Neon. Deduct cash for each pick."""
    settings = db.get_settings()
    cash = float(settings.get("cash", 0))
    for pos in picks:
        cost = pos["cost"]
        if cost > cash:
            print(f"  ⚠️ Skip {pos['symbol']}: insufficient cash (${cost:.2f} > ${cash:.2f})")
            continue
        db.insert_position(pos)
        db.execute("UPDATE portfolio_settings SET cash = cash - %s, updated_at = NOW() WHERE id = 1", (cost,))
        cash -= cost
        db.log_trade("BUY", pos["symbol"], pos["shares"], pos["entry_price"], cost,
                     reason="advisor", score=pos.get("score_at_entry"), position_id=pos["id"])
        print(f"  ✅ BUY {pos['shares']}x {pos['symbol']} @ ${pos['entry_price']:.2f} (score: {pos['score_at_entry']})")


def format_report(scores, picks, closed_stops, total_value, cash, total_pnl, total_pnl_pct):
    """Format Telegram-ready report."""
    settings = db.get_settings()
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    mode = "📝 PAPER" if settings.get("paper_trading", True) else "💰 LIVE"

    open_pos = db.get_open_positions()

    lines = [
        f"📊 **Watchlist Report** — {now} {mode}",
        f"",
        f"**Portfolio**  `${total_value:,.2f}`  ({total_pnl:+,.2f} / {total_pnl_pct:+.1f}%)",
        f"Cash: `${cash:,.2f}`  |  Positions: {len(open_pos)}/{settings.get('max_positions', 3)}",
    ]

    if open_pos:
        lines.append(f"\n**Open Positions:**")
        for pos in open_pos:
            pnl_pct = pos.get("pnl_pct", 0) or 0
            emoji = "🟢" if pnl_pct >= 0 else "🔴"
            ep = float(pos["entry_price"])
            sl_price = ep * (1 + float(settings.get("stop_loss_pct", -0.1)))
            cp = float(pos.get("current_price") or ep)
            lines.append(
                f"  {emoji} {pos['symbol']} {pos['shares']}x  "
                f"${ep:.2f} → ${cp:.2f}  "
                f"({pnl_pct:+.1f}%)  SL: ${sl_price:.2f}"
            )

    if closed_stops:
        lines.append(f"\n**⚠️ Stopped Out:**")
        for pos in closed_stops:
            lines.append(
                f"  🔴 {pos['symbol']} {pos['shares']}x  "
                f"${float(pos['entry_price']):.2f} → ${float(pos.get('exit_price', 0)):.2f}  "
                f"({pos.get('pnl_pct', 0):+.1f}%)"
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
        open_symbols = {p["symbol"] for p in open_pos}
        for s in top5:
            factors = s.get("factors", {})
            factor_str = f"M{factors.get('momentum',0):.0f}|V{factors.get('volume',0):.0f}|T{factors.get('trend',0):.0f}"
            already = " 🔒" if s["symbol"] in open_symbols else ""
            lines.append(f"  {s['symbol']:<6} {s['composite']:.0f}/100  [{factor_str}]{already}")

    stop_pct = float(settings.get("stop_loss_pct", -0.1))
    max_per = float(settings.get("max_per_position_pct", 0.3))
    lines.append(f"\n_Stoploss: {stop_pct*100:.0f}%  |  Max/pos: {max_per*100:.0f}%_")

    return "\n".join(lines)


def main():
    closed_reason = is_market_closed()
    if closed_reason[0]:
        print(f"MARKETS_CLOSED:{closed_reason[1]}")
        return

    print("Running advisor...")

    # Step 1: Score all tickers
    print("\n[1/4] Scoring tickers...")
    scores = run_scorer()
    if not scores:
        print("ERROR:No scores returned")
        return

    # Save scores to Neon
    db.save_scores(scores)

    # Step 2: Update existing positions with live prices
    print("\n[2/4] Updating positions...")
    total_value, cash, total_pnl, total_pnl_pct = update_positions(scores)

    # Step 3: Check stop-losses
    print("\n[3/4] Checking stops...")
    closed_stops = check_stops()
    if closed_stops:
        # Re-read cash after stops
        cash = float(db.get_settings().get("cash", 0))

    # Step 4: Pick new positions
    print("\n[4/4] Picking new positions...")
    picks = pick_positions(scores, total_value, cash)
    if picks:
        execute_picks(picks)
    else:
        print("  No new picks this run.")

    # Generate report
    report = format_report(scores, picks, closed_stops, total_value, cash, total_pnl, total_pnl_pct)
    print(f"\nREPORT_START\n{report}\nREPORT_END")


if __name__ == "__main__":
    main()
