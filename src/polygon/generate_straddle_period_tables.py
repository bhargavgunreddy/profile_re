"""
Generate SPX 0DTE straddle tables for multiple lookback windows.

Produces two CSVs per period:
  - summary_last_{N}d.csv  — SPX, date, strike, cost, combined P/L at each checkpoint
  - detail_last_{N}d.csv   — full leg prices + P/L (includes GRAND TOTAL row)

Uses cached Polygon data in data/spx_0dte_straddle/ when available.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spx_0dte_straddle_analysis import (  # noqa: E402
    ANALYSIS_DIR,
    REPO_ROOT,
    append_grand_totals,
    run_analysis,
)

SUMMARY_COLS = [
    "SPX_931",
    "Date",
    "Day",
    "Day_Type_1030",
    "SPX_Move_1030",
    "SPX_Range_1030",
    "Trend_Efficiency_1030",
    "Strike",
    "Straddle_Cost",
    "Combined_PnL_10am",
    "Combined_PnL_12pm",
    "Combined_PnL_330",
    "Combined_PnL_350",
]


def last_n_trading_days(n: int, end: date) -> tuple[date, date, list[date]]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    return days[0], days[-1], days


def export_tables(df: pd.DataFrame, n: int, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"summary_last_{n}d.csv"
    detail_path = out_dir / f"detail_last_{n}d.csv"

    detail_cols = [c for c in df.columns]
    df.to_csv(detail_path, index=False, float_format="%.2f")

    summary_cols = [c for c in SUMMARY_COLS if c in df.columns]
    df[summary_cols].to_csv(summary_path, index=False, float_format="%.2f")

    return summary_path, detail_path


def grand_total_row(df: pd.DataFrame) -> pd.Series | None:
    sub = df[df["Date"].astype(str) == "GRAND TOTAL"]
    return sub.iloc[0] if not sub.empty else None


def run_period(n: int, end: date, strike_interval: int = 10) -> pd.DataFrame:
    start, _, _ = last_n_trading_days(n, end)
    print(f"\n{'=' * 70}")
    print(f"  LAST {n} TRADING DAYS  ({start} → {end})")
    print(f"{'=' * 70}")
    df = run_analysis(start, end, strike_interval)
    return append_grand_totals(df)


def main() -> None:
    p = argparse.ArgumentParser(description="Generate straddle summary + detail tables")
    p.add_argument("--end", default="2026-06-04")
    p.add_argument("--periods", default="20,50,100", help="Comma-separated trading-day counts")
    p.add_argument("--strike-interval", type=int, default=10)
    p.add_argument("--delay", type=int, default=13)
    p.add_argument("--out-dir", default=str(ANALYSIS_DIR))
    args = p.parse_args()

    import spx_0dte_straddle_analysis as sa

    sa.CALL_DELAY = int(args.delay)

    end = date.fromisoformat(args.end)
    out_dir = Path(args.out_dir)
    periods = [int(x.strip()) for x in args.periods.split(",") if x.strip()]

    results: list[tuple[int, Path, Path, pd.Series | None, int]] = []
    for n in periods:
        df = run_period(n, end, int(args.strike_interval))
        summary_path, detail_path = export_tables(df, n, out_dir)
        gt = grand_total_row(df)
        sessions = len(df[df["Date"].astype(str) != "GRAND TOTAL"])
        results.append((n, summary_path, detail_path, gt, sessions))

    print(f"\n{'=' * 70}")
    print("  OUTPUT FILES")
    print(f"{'=' * 70}")
    for n, sp, dp, gt, sessions in results:
        print(f"\nLast {n}d ({sessions} sessions):")
        print(f"  Summary: {sp.relative_to(REPO_ROOT)}")
        print(f"  Detail:  {dp.relative_to(REPO_ROOT)}")
        if gt is not None:
            print(
                f"  GRAND TOTAL P/L: 10am=${gt['Combined_PnL_10am']:+.0f}  "
                f"12pm=${gt['Combined_PnL_12pm']:+.0f}  "
                f"3:30=${gt['Combined_PnL_330']:+.0f}  "
                f"3:50=${gt['Combined_PnL_350']:+.0f}"
            )


if __name__ == "__main__":
    main()
