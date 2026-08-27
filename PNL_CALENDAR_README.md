## Large Cap P/L Calendar (local “live link”)

This repo includes a lightweight, styled P/L calendar page that reads `gainsandlosses_enriched.csv`, aggregates by `close_date`, and renders net P/L per day for the **Large Cap** book.

### Files
- `large_cap_pnl.html`: the calendar UI (FullCalendar + PapaParse via CDN)
- `scripts/serve_pnl_calendar.py`: local server so the page can fetch the CSV

### Run
From the repo root:

```bash
python3 scripts/serve_pnl_calendar.py
```

Then open:
- `http://localhost:8000/large_cap_pnl.html`

### Notes
- If you open `large_cap_pnl.html` directly via `file://`, most browsers will block `fetch()` of the CSV. Use the local server above.
- You can also load a different CSV using the file picker in the top-right (expects a header row including `close_date` and `gain`).
- Default rebuild for this book: same-day trades from `2026-08-19` onward (`npm run orders:refresh`).
