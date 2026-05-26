#!/usr/bin/env python3
"""
Watchlist Advisor — top 3 picks, portfolio management, position tracking.
Scoring → picks → size positions → check stops → output report.
"""

import json
import os
import sys
import time
import csv
import subprocess
from datetime import date, datetime

DATA_DIR = "/home/mrotatori/.hermes/watchlist"
WATCHLIST_FILE = f"{DATA_DIR}/watchlist.json"
PORTFOLIO_FILE = f"{DATA_DIR}/portfolio.json"
HISTORY_FILE = f"{DATA_DIR}/trade_history.csv"
SCORER_SCRIPT = f"{DATA_DIR}/scorer.py"

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


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_watchlist():
    return load_json(WATCHLIST_FILE)


def load_portfolio():
    return load_json(PORTFOLIO_FILE)


def save_portfolio(portfolio):
    save_json(PORTFOLIO_FILE, portfolio)


def run_scorer():
    """Run scorer.py and return parsed results."""
    result = subprocess.run(
        [sys.executable, SCORER_SCRIPT],
        capture_output=True, text=True, timeout=120
    )
    # The scorer prints status lines then JSON array to stdout
    # Find the JSON array in stdout
    stdout = result.stdout.strip()
    # Try to find the JSON array — it starts with [ and ends with ]
    start = stdout.find("[")
    end = stdout.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(stdout[start:end+1])
        except json.JSONDecodeError:
            pass
    print(f"  ⚠️ Scorer output: {stdout[:300]}", file=sys.stderr)
    return []


def check_stops(portfolio):
    """Check if any open position hit stop-loss. Close them if so."""
    settings = portfolio["settings"]
    stop_pct = settings["stop_loss_pct"]
    cash = portfolio["portfolio"]["cash"]
    closed = []

    remaining = []
    for pos in portfolio["portfolio"]["open_positions"]:
        if pos.get("current_price"):
            pnl_pct = (pos["current_price"] / pos["entry_price"]) - 1
            if pnl_pct <= stop_pct:
                # Close at loss
                proceeds = pos["shares"] * pos["current_price"]
                pnl = proceeds - pos["cost"]
                cash += proceeds
                pos["exit_price"] = pos["current_price"]
                pos["exit_date"] = datetime.now().isoformat()
                pos["exit_reason"] = "stop_loss"
                pos["pnl"] = round(pnl, 2)
                pos["pnl_pct"] = round(pnl_pct * 100, 2)
                portfolio["portfolio"]["closed_positions"].append(pos)
                closed.append(pos)
                # Log to CSV
                log_trade(pos)
            else:
                remaining.append(pos)
        else:
            remaining.append(pos)

    portfolio["portfolio"]["cash"] = round(cash, 2)
    portfolio["portfolio"]["open_positions"] = remaining
    return closed


def update_positions(portfolio, scores):
    """Update current prices of open positions from scores."""
    price_map = {s["symbol"]: s.get("price", 0) for s in scores if "price" in s}
    total_position_value = 0

    for pos in portfolio["portfolio"]["open_positions"]:
        symbol = pos["symbol"]
        if symbol in price_map:
            pos["current_price"] = price_map[symbol]
            pos["pnl"] = round((price_map[symbol] - pos["entry_price"]) * pos["shares"], 2)
            pos["pnl_pct"] = round((price_map[symbol] / pos["entry_price"] - 1) * 100, 2)
            total_position_value += pos["shares"] * price_map[symbol]

    cash = portfolio["portfolio"]["cash"]
    total = cash + total_position_value
    portfolio["portfolio"]["total_value"] = round(total, 2)
    portfolio["portfolio"]["total_pnl"] = round(total - portfolio["portfolio"]["starting_balance"], 2)
    portfolio["portfolio"]["total_pnl_pct"] = round(
        (total / portfolio["portfolio"]["starting_balance"] - 1) * 100, 2
    )


def pick_positions(portfolio, scores):
    """
    Pick up to 3 new positions from scores.
    Skip if already in a position for that ticker.
    Respect max_per_position and min_cash_reserve.
    """
    settings = portfolio["settings"]
    cash = portfolio["portfolio"]["cash"]
    total_value = portfolio["portfolio"]["total_value"]
    max_per = settings["max_per_position_pct"]
    min_reserve = settings["min_cash_reserve_pct"]
    max_pos = settings["max_positions"]

    already_open = {p["symbol"] for p in portfolio["portfolio"]["open_positions"]}
    current_count = len(already_open)
    slots_left = max_pos - current_count

    picks = []
    for s in scores:
        if slots_left <= 0:
            break
        if s["symbol"] in already_open:
            continue
        if s.get("composite", 0) < 30:  # minimum score threshold
            continue
        if "error" in s or not s.get("price"):
            continue

        price = s["price"]
        # Position size: up to max_per% of total, in whole shares
        max_invest = total_value * max_per
        shares = int(max_invest / price)
        if shares <= 0:
            continue

        cost = shares * price
        # Check cash reserve
        remaining_cash = cash - cost
        if remaining_cash < total_value * min_reserve:
            # Reduce shares to maintain reserve
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
            "entry_date": datetime.now().isoformat(),
            "exit_price": None,
            "exit_date": None,
            "exit_reason": None,
        }
        picks.append(pos)

    return picks


def execute_picks(portfolio, picks):
    """Add new picks to portfolio."""
    for pos in picks:
        portfolio["portfolio"]["cash"] -= pos["cost"]
        portfolio["portfolio"]["open_positions"].append(pos)
        log_trade(pos, entry=True)
        print(f"  ✅ BUY {pos['shares']}x {pos['symbol']} @ ${pos['entry_price']:.2f} (score: {pos['score_at_entry']})")


def log_trade(pos, entry=False):
    """Append trade to CSV history."""
    file_exists = os.path.isfile(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "Date", "Action", "Symbol", "Shares", "Price",
                "Cost", "PnL%", "Reason", "Score"
            ])
        action = "BUY" if entry else pos.get("exit_reason", "sell").upper()
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            action,
            pos["symbol"],
            pos["shares"],
            pos["entry_price"] if entry else pos.get("exit_price", ""),
            pos["cost"],
            pos.get("pnl_pct", ""),
            pos.get("exit_reason", "entry"),
            pos.get("score_at_entry", ""),
        ])


def format_report(scores, picks, closed_stops, portfolio):
    """Format Telegram-ready report."""
    p = portfolio["portfolio"]
    settings = portfolio["settings"]
    now = datetime.now().strftime("%m/%d/%Y %H:%M")
    mode = "📝 PAPER" if settings["paper_trading"] else "💰 LIVE"

    lines = [
        f"📊 **Watchlist Report** — {now} {mode}",
        f"",
        f"**Portfolio**  `${p['total_value']:,.2f}`  ({p['total_pnl']:+,.2f} / {p['total_pnl_pct']:+.1f}%)",
        f"Cash: `${p['cash']:,.2f}`  |  Positions: {len(p['open_positions'])}/{settings['max_positions']}",
    ]

    # Open positions
    if p["open_positions"]:
        lines.append(f"\n**Open Positions:**")
        for pos in p["open_positions"]:
            emoji = "🟢" if pos["pnl_pct"] >= 0 else "🔴"
            sl_price = pos["entry_price"] * (1 + settings["stop_loss_pct"])
            lines.append(
                f"  {emoji} {pos['symbol']} {pos['shares']}x  "
                f"${pos['entry_price']:.2f} → ${pos['current_price']:.2f}  "
                f"({pos['pnl_pct']:+.1f}%)  SL: ${sl_price:.2f}"
            )

    # Stop-loss triggers
    if closed_stops:
        lines.append(f"\n**⚠️ Stopped Out:**")
        for pos in closed_stops:
            lines.append(
                f"  🔴 {pos['symbol']} {pos['shares']}x  "
                f"${pos['entry_price']:.2f} → ${pos.get('exit_price', 0):.2f}  "
                f"({pos.get('pnl_pct', 0):+.1f}%)"
            )

    # New picks
    if picks:
        lines.append(f"\n**🆕 New Picks:**")
        for pos in picks:
            lines.append(
                f"  ✅ {pos['symbol']} {pos['shares']}x @ ${pos['entry_price']:.2f}  "
                f"(score: {pos['score_at_entry']})"
            )

    # Top scored (top 5)
    top5 = [s for s in scores if s.get("composite", 0) > 0][:5]
    if top5:
        lines.append(f"\n**Top Scored:**")
        for s in top5:
            factors = s.get("factors", {})
            factor_str = f"M{factors.get('momentum',0):.0f}|V{factors.get('volume',0):.0f}|T{factors.get('trend',0):.0f}"
            already = " 🔒" if s["symbol"] in {p2["symbol"] for p2 in p["open_positions"]} else ""
            lines.append(f"  {s['symbol']:<6} {s['composite']:.0f}/100  [{factor_str}]{already}")

    lines.append(f"\n_Stoploss: {settings['stop_loss_pct']*100:.0f}%  |  Max/pos: {settings['max_per_position_pct']*100:.0f}%_")

    return "\n".join(lines)


def main():
    closed_reason = is_market_closed()
    if closed_reason[0]:
        print(f"MARKETS_CLOSED:{closed_reason[1]}")
        return

    print("Running advisor...")
    portfolio = load_portfolio()
    portfolio["last_updated"] = datetime.now().isoformat()

    # Step 1: Score all tickers
    print("\n[1/4] Scoring tickers...")
    scores = run_scorer()
    if not scores:
        print("ERROR:No scores returned")
        return

    # Step 2: Update existing positions with live prices
    print("\n[2/4] Updating positions...")
    update_positions(portfolio, scores)

    # Step 3: Check stop-losses
    print("\n[3/4] Checking stops...")
    closed_stops = check_stops(portfolio)
    if closed_stops:
        for cs in closed_stops:
            print(f"  🔴 STOP: {cs['symbol']} @ {cs.get('exit_price', 0):.2f} ({cs.get('pnl_pct', 0):+.1f}%)")

    # Step 4: Pick new positions
    print("\n[4/4] Picking new positions...")
    picks = pick_positions(portfolio, scores)
    if picks:
        execute_picks(portfolio, picks)
    else:
        print("  No new picks this run.")

    # Save portfolio
    save_portfolio(portfolio)

    # Generate report
    report = format_report(scores, picks, closed_stops, portfolio)
    print(f"\nREPORT_START\n{report}\nREPORT_END")


if __name__ == "__main__":
    main()
