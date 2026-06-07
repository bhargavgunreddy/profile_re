"""
SPX 0DTE ±offset strangle analysis.

At 9:31 AM each day:
  - ATM = nearest $10 to SPX (SPY × 10 at 9:31)
  - Buy 1 call @ ATM + offset, 1 put @ ATM - offset

Checkpoints: 12:00 PM and 3:30 PM combined P/L.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spx_0dte_straddle_analysis import (  # noqa: E402
    ANALYSIS_DIR,
    CALL_DELAY,
    REPO_ROOT,
    append_grand_totals,
    download_option_1m,
    fetch_spy_1m,
    get_spy_at_931,
    load_option_1m_from_cache,
    option_cache_path,
    option_ticker,
    price_checkpoint,
    straddle_pnl,
    _migrate_legacy_cache,
    get_trading_days,
)


def last_n_trading_days(n: int, end: date) -> tuple[date, date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    return days[0], days[-1]


def _load_leg(trade_date: date, strike: int, pc: str, downloads: list[int]) -> pd.DataFrame | None:
    if option_cache_path(trade_date, strike, pc).exists():
        return load_option_1m_from_cache(trade_date, strike, pc)
    if downloads[0]:
        time.sleep(CALL_DELAY)
    bars = download_option_1m(trade_date, strike, pc)
    downloads[0] += 1
    return bars


def run_strangle_analysis(
    start: date,
    end: date,
    offset: int,
    strike_interval: int = 10,
) -> pd.DataFrame:
    _migrate_legacy_cache()
    trading_days = get_trading_days(start, end)
    print(f"\n±{offset} STRANGLE — {start} → {end} ({len(trading_days)} weekdays)")

    spy_df = fetch_spy_1m(start, end)
    if spy_df is None or spy_df.empty:
        print("[ERROR] No SPY data")
        return pd.DataFrame()

    day_info: list[dict] = []
    for d in trading_days:
        spy_931 = get_spy_at_931(spy_df, d)
        if spy_931 is None:
            continue
        spx_est = round(spy_931 * 10, 2)
        atm = int(round(spx_est / strike_interval) * strike_interval)
        day_info.append({
            "date": d,
            "spx_est": spx_est,
            "atm": atm,
            "call_strike": atm + offset,
            "put_strike": atm - offset,
        })

    need = sum(
        1 for info in day_info
        for strike, pc in ((info["call_strike"], "C"), (info["put_strike"], "P"))
        if not option_cache_path(info["date"], strike, pc).exists()
    )
    print(f"Cache hits expected: {len(day_info) * 2 - need}/{len(day_info) * 2} legs")
    if need:
        print(f"Downloads needed: {need} (~{need * CALL_DELAY // 60} min)\n")

    rows = []
    downloads = [0]
    for i, info in enumerate(day_info):
        d = info["date"]
        cs, ps = info["call_strike"], info["put_strike"]
        print(f"[{i+1}/{len(day_info)}] {d}  call={cs}  put={ps}")

        call_bars = _load_leg(d, cs, "C", downloads)
        put_bars = _load_leg(d, ps, "P", downloads)

        ce = price_checkpoint(call_bars, 9, 31)
        cm = price_checkpoint(call_bars, 12, 0)
        c330 = price_checkpoint(call_bars, 15, 30)

        pe = price_checkpoint(put_bars, 9, 31)
        pm = price_checkpoint(put_bars, 12, 0)
        p330 = price_checkpoint(put_bars, 15, 30)

        cost = (ce + pe) if (ce is not None and pe is not None) else None
        noon_val = (cm + pm) if (cm is not None and pm is not None) else None
        val_330 = (c330 + p330) if (c330 is not None and p330 is not None) else None

        noon_pnl, noon_pct = straddle_pnl(cost, noon_val)
        pnl_330, pct_330 = straddle_pnl(cost, val_330)

        print(f"  C {option_ticker(d, cs, 'C')}: 9:31=${ce} 12=${cm} 3:30=${c330}")
        print(f"  P {option_ticker(d, ps, 'P')}: 9:31=${pe} 12=${pm} 3:30=${p330}")

        rows.append({
            "SPX_931": info["spx_est"],
            "Date": d,
            "Day": d.strftime("%a"),
            "ATM_Strike": info["atm"],
            "Call_Strike": cs,
            "Put_Strike": ps,
            "Offset": offset,
            "Call_931": ce,
            "Put_931": pe,
            "Strangle_Cost": cost,
            "Call_Noon": cm,
            "Put_Noon": pm,
            "Strangle_Noon": noon_val,
            "Combined_PnL_12pm": noon_pnl,
            "Combined_PnL_12pm_%": noon_pct,
            "Call_330": c330,
            "Put_330": p330,
            "Strangle_330": val_330,
            "Combined_PnL_330": pnl_330,
            "Combined_PnL_330_%": pct_330,
        })

    print(f"Downloads: {downloads[0]}")
    return pd.DataFrame(rows)


def append_strangle_totals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    data = df[df["Date"].astype(str) != "GRAND TOTAL"].copy()
    totals: dict = {
        "SPX_931": "",
        "Date": "GRAND TOTAL",
        "Day": "",
        "ATM_Strike": "",
        "Call_Strike": "",
        "Put_Strike": "",
        "Offset": "",
    }
    if "Strangle_Cost" in data.columns:
        totals["Strangle_Cost"] = data["Strangle_Cost"].sum()
    for col in ("Combined_PnL_12pm", "Combined_PnL_330"):
        if col in data.columns:
            totals[col] = data[col].sum(skipna=True)
    cost = data["Strangle_Cost"]
    for pnl_col, pct_col in (
        ("Combined_PnL_12pm", "Combined_PnL_12pm_%"),
        ("Combined_PnL_330", "Combined_PnL_330_%"),
    ):
        mask = data[pnl_col].notna() & cost.notna()
        if mask.any():
            totals[pct_col] = (data.loc[mask, pnl_col].sum() / cost.loc[mask].sum()) * 100
    for col in data.columns:
        totals.setdefault(col, "")
    return pd.concat([data, pd.DataFrame([totals])], ignore_index=True)


def main() -> None:
    p = argparse.ArgumentParser(description="SPX 0DTE ±offset strangle analysis")
    p.add_argument("--end", default="2026-06-04")
    p.add_argument("--trading-days", type=int, default=20)
    p.add_argument("--offsets", default="10,20")
    p.add_argument("--delay", type=int, default=13)
    args = p.parse_args()

    import spx_0dte_straddle_analysis as sa
    sa.CALL_DELAY = int(args.delay)

    end = date.fromisoformat(args.end)
    start, _ = last_n_trading_days(int(args.trading_days), end)
    offsets = [int(x.strip()) for x in args.offsets.split(",") if x.strip()]

    summary_cols = [
        "SPX_931", "Date", "Day", "ATM_Strike", "Call_Strike", "Put_Strike",
        "Strangle_Cost", "Combined_PnL_12pm", "Combined_PnL_330",
    ]

    for offset in offsets:
        df = run_strangle_analysis(start, end, offset)
        df = append_strangle_totals(df)
        out = ANALYSIS_DIR / f"strangle_pm{offset}_last_{args.trading_days}d.csv"
        df.to_csv(out, index=False, float_format="%.2f")
        print(f"Saved: {out.relative_to(REPO_ROOT)}")
        show = df[[c for c in summary_cols if c in df.columns]]
        print(show.to_string(index=False, float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
