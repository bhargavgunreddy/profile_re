#!/usr/bin/env bash
# Merge a daily orders export into Orders.csv and rebuild dashboards.
#
# Usage:
#   bash scripts/merge_daily_orders.sh Orders_may_7.csv          # merge + serve
#   bash scripts/merge_daily_orders.sh Orders_may_7.csv --no-serve  # merge only
#
# What it does:
#   1. Prepends new data rows from the daily file into Orders.csv (skips duplicates)
#   2. Updates the Orders.csv header timestamp
#   3. Runs the full orders:live pipeline (build_gains → dashboard JSON → serve)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/merge_daily_orders.sh <daily_orders.csv> [--no-serve]"
  echo "Example: bash scripts/merge_daily_orders.sh Orders_may_7.csv"
  exit 1
fi

DAILY="$1"
shift

SERVE=1
for a in "$@"; do
  if [[ "$a" == "--no-serve" ]]; then SERVE=0; fi
done

if [[ ! -f "$DAILY" ]]; then
  echo "ERROR: File not found: $DAILY"
  exit 1
fi

if [[ ! -f "Orders.csv" ]]; then
  echo "ERROR: Orders.csv not found in repo root"
  exit 1
fi

echo "==> Merging $DAILY into Orders.csv"
python3 scripts/merge_daily_orders.py "$DAILY" Orders.csv

echo "==> Building gainsandlosses_enriched.csv from Orders.csv"
python3 src/polygon/build_gains_from_orders.py --orders_csv Orders.csv --out_csv gainsandlosses_enriched.csv

echo "==> Building trade_dashboard_data.json from gainsandlosses_enriched.csv"
python3 src/polygon/build_trade_dashboard_data.py \
  --trades_csv gainsandlosses_enriched.csv \
  --spy_cache_root src/polygon/data/polygon \
  --out trade_dashboard_data.json

if [[ "$SERVE" -eq 0 ]]; then
  echo "==> Done (no server). Open dashboards with: npm run pnl:serve"
  exit 0
fi

echo "==> Starting static server (Ctrl+C to stop)"
echo "    P/L calendar:  http://localhost:8000/pnl_calendar.html"
echo "    Tradezilla:    http://localhost:8000/tradezilla_dashboard.html"
exec python3 scripts/serve_pnl_calendar.py
