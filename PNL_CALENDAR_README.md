## P/L Calendar (local “live link”)

This repo includes a lightweight, styled P/L calendar page that reads `gainsandlosses.csv`, aggregates by `close_date`, and renders net P/L per day.

### Files
- `pnl_calendar.html`: the calendar UI (FullCalendar + PapaParse via CDN)
- `scripts/serve_pnl_calendar.py`: local server so the page can fetch `gainsandlosses.csv`

### Refresh from `Orders.csv`
Standard pipeline (see `.cursor/rules/orders-dashboard-refresh.mdc`):

```bash
python3 src/polygon/build_gains_from_orders.py --orders_csv Orders.csv --out_csv gainsandlosses_enriched.csv
python3 src/polygon/build_trade_dashboard_data.py --trades_csv gainsandlosses_enriched.csv --out trade_dashboard_data.json
python3 scripts/serve_pnl_calendar.py
```

Shortcut: `npm run orders:refresh` (rebuild) or `npm run orders:live` (rebuild + serve).

To reconstruct only closes on/after a date (example: Aug 19, 2026):

```bash
bash scripts/refresh_orders_dashboard.sh --no-serve --min_close_date 2026-08-19
```

The calendar page also accepts `?from=2026-08-19` (and `?to=YYYY-MM-DD`) to pre-fill the date filter.

### Run
From the repo root:

```bash
python3 scripts/serve_pnl_calendar.py
```

Then open:
- `http://localhost:8000/pnl_calendar.html`
- `http://localhost:8000/pnl_calendar.html?from=2026-08-19`

### Notes
- If you open `pnl_calendar.html` directly via `file://`, most browsers will block `fetch()` of the CSV. Use the local server above.
- You can also load a different CSV using the file picker in the top-right (expects a header row including `close_date` and `gain`).
- Default dataset is `gainsandlosses_enriched.csv`.

