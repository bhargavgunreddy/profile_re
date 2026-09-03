#!/usr/bin/env bash
# Rebuild gains CSV + trade dashboard JSON from Orders.csv; optionally serve HTML dashboards.
#   bash scripts/refresh_orders_dashboard.sh           # rebuild + serve (same as npm run orders:live)
#   bash scripts/refresh_orders_dashboard.sh --no-serve --min_close_date 2026-08-19
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SERVE=1
BUILD_ARGS=()
for a in "$@"; do
  if [[ "$a" == "--no-serve" ]]; then
    SERVE=0
  else
    BUILD_ARGS+=("$a")
  fi
done

echo "==> Building gainsandlosses_enriched.csv from Orders.csv"
python3 src/polygon/build_gains_from_orders.py --orders_csv Orders.csv --out_csv gainsandlosses_enriched.csv "${BUILD_ARGS[@]}"

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
