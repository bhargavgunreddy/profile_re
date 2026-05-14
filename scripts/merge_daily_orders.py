"""
Merge a daily E*TRADE orders export into the main Orders.csv.

- Prepends new rows (daily file is newest-first, so they go at the top)
- Deduplicates by comparing the raw CSV line text
- Updates the account header line with the daily file's timestamp
- Backs up Orders.csv to Orders.csv.bak before writing

Usage:
    python3 scripts/merge_daily_orders.py Orders_may_7.csv Orders.csv
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def parse_orders_file(path: Path) -> tuple[str, str, list[str]]:
    """Return (header_line, column_line, data_lines) from an E*TRADE orders CSV."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError(f"{path} has fewer than 2 lines")
    return lines[0], lines[1], lines[2:]


def normalize(line: str) -> str:
    """Strip whitespace for dedup comparison."""
    return line.strip()


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/merge_daily_orders.py <daily.csv> <orders.csv>")
        sys.exit(1)

    daily_path = Path(sys.argv[1])
    orders_path = Path(sys.argv[2])

    if not daily_path.exists():
        print(f"ERROR: {daily_path} not found")
        sys.exit(1)
    if not orders_path.exists():
        print(f"ERROR: {orders_path} not found")
        sys.exit(1)

    daily_header, daily_cols, daily_rows = parse_orders_file(daily_path)
    orders_header, orders_cols, orders_rows = parse_orders_file(orders_path)

    existing_set = {normalize(r) for r in orders_rows}

    new_rows = []
    dupes = 0
    for row in daily_rows:
        if not row.strip():
            continue
        if normalize(row) in existing_set:
            dupes += 1
        else:
            new_rows.append(row)

    if not new_rows:
        print(f"No new rows to merge from {daily_path.name} ({dupes} duplicates skipped)")
        return

    backup = orders_path.with_suffix(".csv.bak")
    shutil.copy2(orders_path, backup)
    print(f"Backed up {orders_path} -> {backup.name}")

    merged_rows = new_rows + orders_rows

    output_lines = [daily_header, daily_cols] + merged_rows
    orders_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")

    print(f"Merged {len(new_rows)} new rows into {orders_path.name} ({dupes} duplicates skipped)")
    print(f"Orders.csv now has {len(merged_rows)} data rows")


if __name__ == "__main__":
    main()
