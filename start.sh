#!/bin/bash
# Stock Discovery Dashboard — Startup Script
# Usage: ./start.sh [port]

PORT="${1:-5150}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Install dependencies if needed
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Installing Flask..."
    pip3 install flask
fi

# Ensure data files exist
DATA_DIR="$HOME/.hermes/watchlist"
mkdir -p "$DATA_DIR"

if [ ! -f "$DATA_DIR/watchlist.json" ]; then
    echo "Creating default watchlist.json..."
    cp "$SCRIPT_DIR/watchlist.json" "$DATA_DIR/watchlist.json" 2>/dev/null || echo '{"settings":{"max_positions":3,"max_per_position_pct":0.30,"min_cash_reserve_pct":0.20,"stop_loss_pct":-0.10,"starting_balance":1500,"paper_trading":true},"tickers":[]}' > "$DATA_DIR/watchlist.json"
fi

if [ ! -f "$DATA_DIR/portfolio.json" ]; then
    echo "Creating default portfolio.json..."
    cp "$SCRIPT_DIR/portfolio.json" "$DATA_DIR/portfolio.json" 2>/dev/null || echo '{"settings":{},"portfolio":{"cash":1500,"starting_balance":1500,"total_value":1500,"total_pnl":0,"total_pnl_pct":0,"open_positions":[],"closed_positions":[],"trade_history":[]},"last_updated":null}' > "$DATA_DIR/portfolio.json"
fi

echo "Starting Stock Discovery Dashboard on port $PORT..."
echo "Open http://localhost:$PORT in your browser"
cd "$SCRIPT_DIR"
python3 server.py
