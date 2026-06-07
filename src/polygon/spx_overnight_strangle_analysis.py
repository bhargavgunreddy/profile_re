"""
SPX overnight ±30 strangle — "grandfather effect" test on options.

Entry (day T, ~3:59 PM ET):
  SPX ≈ SPY × 10 at 3:58 PM
  Buy 1 call @ SPX+30 and 1 put @ SPX−30 (strikes rounded to $5)
  Options expire next trading day (1 DTE)

Exit checkpoints (day T+1):
  9:40 AM, 12:00 PM, 3:30 PM combined strangle P/L

Reuses data/spx_0dte_straddle/ cache; downloads only missing legs/ranges.
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
    BASE_URL,
    CALL_DELAY,
    DATA_ROOT,
    OPTION_CACHE_DIR,
    REPO_ROOT,
    TZ,
    _bars_df,
    _ensure_data_dirs,
    _get,
    _migrate_legacy_cache,
    append_grand_totals,
    fetch_spy_1m,
    get_trading_days,
    load_option_1m_from_cache,
    load_spy_1m_from_disk,
    option_cache_path,
    option_ticker,
    price_checkpoint,
    straddle_pnl,
)

OVERNIGHT_DIR = DATA_ROOT / "overnight_options_1m"

PNL_COLS = ["Combined_PnL_940", "Combined_PnL_12pm", "Combined_PnL_330"]
PNL_PCT_COLS = ["Combined_PnL_940_%", "Combined_PnL_12pm_%", "Combined_PnL_330_%"]


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def last_n_entry_days(n: int, end: date) -> list[date]:
    """Last N weekdays that have a following trading day on or before `end`."""
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            nxt = next_trading_day(d)
            if nxt <= end:
                days.append(d)
        d -= timedelta(days=1)
    days.sort()
    return days


def strike_from_spx(spx: float, offset: float) -> int:
    return int(round((spx + offset) / 5) * 5)


def get_spy_at_time(spy_df: pd.DataFrame, trade_date: date, hour: int, minute: int) -> float | None:
    day_data = spy_df[spy_df.index.date == trade_date]
    if day_data.empty:
        return None
    target = day_data.index[0].normalize() + pd.Timedelta(hours=hour, minutes=minute)
    mask = day_data.index >= target
    if mask.any():
        return float(day_data.loc[day_data.index[mask][0], "Close"])
    before = day_data.index[day_data.index <= target]
    if len(before):
        return float(day_data.loc[before[-1], "Close"])
    return None


def overnight_cache_path(entry_date: date, expiry_date: date, strike: int, pc: str) -> Path:
    ticker = option_ticker(expiry_date, strike, pc).replace(":", "_")
    return OVERNIGHT_DIR / f"{ticker}_{entry_date}_to_{expiry_date}.csv"


def _read_option_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["ts"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
    return df.set_index("ts").sort_index()


def _save_option_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    save = df.reset_index()
    save["ts"] = save["ts"].dt.tz_convert("UTC")
    save.to_csv(path, index=False)


def download_option_range(
    entry_date: date,
    expiry_date: date,
    strike: int,
    pc: str,
) -> pd.DataFrame | None:
    ticker = option_ticker(expiry_date, strike, pc)
    data = _get(
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{entry_date}/{expiry_date}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    df = _bars_df(data)
    if df is not None and not df.empty:
        cache = overnight_cache_path(entry_date, expiry_date, strike, pc)
        _save_option_csv(cache, df)
    return df


def fetch_overnight_option_bars(
    entry_date: date,
    expiry_date: date,
    strike: int,
    pc: str,
    downloads: list[int],
) -> pd.DataFrame | None:
    """Load overnight range from cache, or merge single-day caches, or download."""
    _ensure_data_dirs()
    OVERNIGHT_DIR.mkdir(parents=True, exist_ok=True)

    range_path = overnight_cache_path(entry_date, expiry_date, strike, pc)
    cached = _read_option_csv(range_path)
    if cached is not None and not cached.empty:
        return cached

    parts: list[pd.DataFrame] = []

    # Exit-day bars often exist from 0DTE straddle/strangle runs (expiry date file)
    exit_df = load_option_1m_from_cache(expiry_date, strike, pc)
    if exit_df is not None and not exit_df.empty:
        parts.append(exit_df)

    # Entry-day afternoon may exist if someone cached entry_date file for this ticker
    entry_single = OPTION_CACHE_DIR / (
        f"{option_ticker(expiry_date, strike, pc).replace(':', '_')}_{entry_date}.csv"
    )
    entry_df = _read_option_csv(entry_single)
    if entry_df is not None and not entry_df.empty:
        parts.append(entry_df)

    if parts:
        merged = pd.concat(parts)
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        has_entry = any(ts.date() == entry_date for ts in merged.index)
        has_exit = any(ts.date() == expiry_date for ts in merged.index)
        if has_entry and has_exit:
            _save_option_csv(range_path, merged)
            return merged

    if downloads[0]:
        time.sleep(CALL_DELAY)
    df = download_option_range(entry_date, expiry_date, strike, pc)
    downloads[0] += 1
    return df


def price_on_date(df: pd.DataFrame | None, on_date: date, hour: int, minute: int) -> float | None:
    if df is None or df.empty:
        return None
    day = df[df.index.date == on_date]
    return price_checkpoint(day, hour, minute)


def run_overnight_analysis(
    entry_days: list[date],
    offset: int = 30,
    spy_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    _migrate_legacy_cache()
    if not entry_days:
        return pd.DataFrame()

    start = min(entry_days)
    end = max(next_trading_day(d) for d in entry_days)

    if spy_df is None:
        spy_df = fetch_spy_1m(start, end)
    if spy_df is None or spy_df.empty:
        print("[ERROR] No SPY data")
        return pd.DataFrame()

    day_info: list[dict] = []
    for d in entry_days:
        spy_358 = get_spy_at_time(spy_df, d, 15, 58)
        if spy_358 is None:
            print(f"  {d}: no SPY at 3:58 PM, skipping")
            continue
        spx = round(spy_358 * 10, 2)
        expiry = next_trading_day(d)
        day_info.append({
            "entry_date": d,
            "exit_date": expiry,
            "spx_358": spx,
            "call_strike": strike_from_spx(spx, offset),
            "put_strike": strike_from_spx(spx, -offset),
        })

    need = 0
    for info in day_info:
        for strike, pc in ((info["call_strike"], "C"), (info["put_strike"], "P")):
            if not overnight_cache_path(
                info["entry_date"], info["exit_date"], strike, pc
            ).exists():
                exit_cached = option_cache_path(info["exit_date"], strike, pc).exists()
                if not exit_cached:
                    need += 1
                else:
                    need += 1  # still need entry afternoon unless range cache exists
    print(f"Overnight legs: {len(day_info) * 2} total")
    print(f"Range caches expected misses: ~{need} (may merge exit-day 0DTE cache)\n")

    rows = []
    downloads = [0]
    for i, info in enumerate(day_info):
        d, exp = info["entry_date"], info["exit_date"]
        cs, ps = info["call_strike"], info["put_strike"]
        print(f"[{i+1}/{len(day_info)}] entry={d} exit={exp}  call={cs}  put={ps}")

        call_bars = fetch_overnight_option_bars(d, exp, cs, "C", downloads)
        put_bars = fetch_overnight_option_bars(d, exp, ps, "P", downloads)

        ce = price_on_date(call_bars, d, 15, 59)
        pe = price_on_date(put_bars, d, 15, 59)

        c940 = price_on_date(call_bars, exp, 9, 40)
        c12 = price_on_date(call_bars, exp, 12, 0)
        c330 = price_on_date(call_bars, exp, 15, 30)

        p940 = price_on_date(put_bars, exp, 9, 40)
        p12 = price_on_date(put_bars, exp, 12, 0)
        p330 = price_on_date(put_bars, exp, 15, 30)

        cost = (ce + pe) if (ce is not None and pe is not None) else None
        val_940 = (c940 + p940) if (c940 is not None and p940 is not None) else None
        val_12 = (c12 + p12) if (c12 is not None and p12 is not None) else None
        val_330 = (c330 + p330) if (c330 is not None and p330 is not None) else None

        pnl_940, pct_940 = straddle_pnl(cost, val_940)
        pnl_12, pct_12 = straddle_pnl(cost, val_12)
        pnl_330, pct_330 = straddle_pnl(cost, val_330)

        spy_exit = get_spy_at_time(spy_df, exp, 9, 40)
        spx_move = round((spy_exit * 10 - info["spx_358"]), 1) if spy_exit else None

        print(
            f"  Call {option_ticker(exp, cs, 'C')}: "
            f"entry=${ce}  9:40=${c940}  12=${c12}  3:30=${c330}"
        )
        print(
            f"  Put  {option_ticker(exp, ps, 'P')}: "
            f"entry=${pe}  9:40=${p940}  12=${p12}  3:30=${p330}"
        )

        rows.append({
            "Entry_Date": d,
            "Exit_Date": exp,
            "Day": d.strftime("%a"),
            "SPX_358": info["spx_358"],
            "SPX_Move_to_940": spx_move,
            "Call_Strike": cs,
            "Put_Strike": ps,
            "Call_Entry_359": ce,
            "Put_Entry_359": pe,
            "Strangle_Cost": cost,
            "Combined_PnL_940": pnl_940,
            "Combined_PnL_940_%": pct_940,
            "Combined_PnL_12pm": pnl_12,
            "Combined_PnL_12pm_%": pct_12,
            "Combined_PnL_330": pnl_330,
            "Combined_PnL_330_%": pct_330,
        })

    print(f"\nDownloads: {downloads[0]}")
    return pd.DataFrame(rows)


def append_overnight_totals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    data = df[df["Entry_Date"].astype(str) != "GRAND TOTAL"].copy()
    totals: dict = {"Entry_Date": "GRAND TOTAL", "Exit_Date": "", "Day": ""}
    if "Strangle_Cost" in data.columns:
        totals["Strangle_Cost"] = data["Strangle_Cost"].sum()
    for col in PNL_COLS:
        if col in data.columns:
            totals[col] = data[col].sum(skipna=True)
    cost = data["Strangle_Cost"]
    for pnl_col, pct_col in zip(PNL_COLS, PNL_PCT_COLS):
        if pnl_col not in data.columns:
            continue
        mask = data[pnl_col].notna() & cost.notna()
        if mask.any():
            totals[pct_col] = (data.loc[mask, pnl_col].sum() / cost.loc[mask].sum()) * 100
    for col in data.columns:
        totals.setdefault(col, "")
    return pd.concat([data, pd.DataFrame([totals])], ignore_index=True)


def main():
    p = argparse.ArgumentParser(description="SPX overnight ±offset strangle analysis")
    p.add_argument("--end", default="2026-06-04")
    p.add_argument("--trading-days", type=int, default=20)
    p.add_argument("--offset", type=int, default=30)
    p.add_argument("--delay", type=int, default=13)
    p.add_argument("--out", default="")
    args = p.parse_args()

    global CALL_DELAY
    CALL_DELAY = int(args.delay)

    end = date.fromisoformat(args.end)
    entry_days = last_n_entry_days(int(args.trading_days), end)
    print(f"SPX OVERNIGHT ±{args.offset} STRANGLE")
    print(f"Entry ~3:59 PM · Exit next day 9:40 / 12:00 / 3:30")
    print(f"{len(entry_days)} holds: {entry_days[0]} → {entry_days[-1]}\n")

    df = run_overnight_analysis(entry_days, offset=int(args.offset))
    df = append_overnight_totals(df)

    show = [
        "Entry_Date", "Exit_Date", "Day", "SPX_358", "Call_Strike", "Put_Strike",
        "Strangle_Cost", "Combined_PnL_940", "Combined_PnL_12pm", "Combined_PnL_330",
    ]
    with pd.option_context("display.max_columns", 12, "display.width", 140,
                           "display.float_format", lambda x: f"{x:.2f}"):
        print("\n" + df[[c for c in show if c in df.columns]].to_string(index=False))

    out = Path(args.out) if args.out else (
        ANALYSIS_DIR / f"overnight_pm{args.offset}_last_{args.trading_days}d.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, float_format="%.2f")
    print(f"\nSaved: {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
