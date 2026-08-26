#!/usr/bin/env bash
# Rebuild gains CSV + trade dashboard JSON from Orders.csv; optionally serve HTML dashboards.
#   bash scripts/refresh_orders_dashboard.sh           # rebuild + serve (same as npm run orders:live)
#   bash scripts/refresh_orders_dashboard.sh --no-serve   # rebuild only (same as npm run orders:refresh)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SERVE=1
for a in "$@"; do
  if [[ "$a" == "--no-serve" ]]; then SERVE=0; fi
done

echo "==> Building gainsandlosses_enriched.csv from Orders.csv (same-day trades from 2026-08-19)"
python3 src/polygon/build_gains_from_orders.py \
  --orders_csv Orders.csv \
  --out_csv gainsandlosses_enriched.csv \
  --same-day-only \
  --from-date 2026-08-19

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
echo "    Large Cap P/L: http://localhost:8000/large_cap_pnl.html"
echo "    Tradezilla:    http://localhost:8000/tradezilla_dashboard.html"
exec python3 scripts/serve_pnl_calendar.py
