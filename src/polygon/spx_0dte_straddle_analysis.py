"""
SPX 0DTE Straddle Analysis — May 2026

Strategy:
  At 9:31 AM ET each trading day, buy 1 ATM call + 1 ATM put (straddle)
  at the strike nearest to where SPX opens.

Checkpoints:
  - Entry:  9:31 AM option prices (straddle cost)
  - Midday: 12:00 PM option prices (straddle value)
  - EOD:    last available bar ~15:55 PM (straddle value)

Data: Polygon.io — uses SPY 1m bars × 10 to estimate SPX level,
      then fetches SPXW option 1m bars for the 0DTE ATM straddle.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polygon_secrets import get_polygon_api_key

API_KEY = get_polygon_api_key()
BASE_URL = "https://api.polygon.io"
TZ = "America/New_York"

CALL_DELAY = 13  # seconds between API calls (free tier: 5/min)
CACHE_DIR = Path(__file__).resolve().parent / "data" / "spxw_straddle_cache"


def _get(url: str, params: dict) -> dict | None:
    params = {**params, "apiKey": API_KEY}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                print("  [rate-limited] waiting 20s...")
                time.sleep(20)
                continue
            if r.status_code >= 400:
                return None
            return r.json()
        except Exception:
            if attempt < 2:
                time.sleep(3)
    return None


def _bars_df(data: dict | None) -> pd.DataFrame | None:
    if not data or "results" not in data or not data["results"]:
        return None
    df = pd.DataFrame(data["results"])
    df["ts"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(TZ)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    return df.set_index("ts").sort_index()


# ── Step 1: Get SPY 1m data for full date range ───────────────────────

def fetch_spy_1m(start: date, end: date) -> pd.DataFrame | None:
    """Fetch SPY 1-minute bars for the whole date range (single API call)."""
    cache_file = CACHE_DIR / f"SPY_1m_{start}_{end}.csv"
    if cache_file.exists():
        df = pd.read_csv(cache_file, parse_dates=["ts"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
        return df.set_index("ts").sort_index()

    print(f"Fetching SPY 1m data {start} → {end}...")
    data = _get(
        f"{BASE_URL}/v2/aggs/ticker/SPY/range/1/minute/{start}/{end}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    df = _bars_df(data)
    if df is not None and not df.empty:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        save = df.reset_index()
        save["ts"] = save["ts"].dt.tz_convert("UTC")
        save.to_csv(cache_file, index=False)
        df = save.set_index("ts").sort_index()
        df.index = df.index.tz_convert(TZ)
        print(f"  cached {len(df)} bars")
    return df


def get_spy_at_931(spy_df: pd.DataFrame, trade_date: date) -> float | None:
    """Extract SPY close price at the 9:31 bar for a given date."""
    day_str = str(trade_date)
    day_data = spy_df[spy_df.index.date == trade_date]
    if day_data.empty:
        return None
    target = day_data.index[0].normalize() + pd.Timedelta(hours=9, minutes=31)
    mask = day_data.index >= target
    if mask.any():
        return float(day_data.loc[day_data.index[mask][0], "Close"])
    return None


# ── Step 2: Fetch SPXW option 1m bars ─────────────────────────────────

def option_ticker(trade_date: date, strike: int, pc: str) -> str:
    d = trade_date.strftime("%y%m%d")
    return f"O:SPXW{d}{pc}{strike * 1000:08d}"


def fetch_option_1m(trade_date: date, strike: int, pc: str) -> pd.DataFrame | None:
    """Fetch 1m bars for a SPXW option. Caches to disk."""
    ticker = option_ticker(trade_date, strike, pc)
    cache_file = CACHE_DIR / f"{ticker.replace(':', '_')}_{trade_date}.csv"

    if cache_file.exists():
        df = pd.read_csv(cache_file, parse_dates=["ts"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
        return df.set_index("ts").sort_index()

    ds = trade_date.isoformat()
    data = _get(
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{ds}/{ds}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    df = _bars_df(data)
    if df is not None and not df.empty:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        save = df.reset_index()
        save["ts"] = save["ts"].dt.tz_convert("UTC")
        save.to_csv(cache_file, index=False)
        df = save.set_index("ts").sort_index()
        df.index = df.index.tz_convert(TZ)
    return df


# ── Price extraction helpers ──────────────────────────────────────────

def price_near(df: pd.DataFrame | None, hour: int, minute: int, tol_min: int = 5) -> float | None:
    if df is None or df.empty:
        return None
    target = df.index[0].normalize() + pd.Timedelta(hours=hour, minutes=minute)
    after = df.index[df.index >= target]
    if len(after):
        idx = after[0]
        if (idx - target).total_seconds() <= tol_min * 60:
            return float(df.loc[idx, "Close"])
    before = df.index[df.index <= target]
    if len(before):
        idx = before[-1]
        if (target - idx).total_seconds() <= tol_min * 60:
            return float(df.loc[idx, "Close"])
    return None


def eod_price(df: pd.DataFrame | None) -> float | None:
    if df is None or df.empty:
        return None
    cutoff = df.index[0].normalize() + pd.Timedelta(hours=15, minutes=50)
    late = df[df.index >= cutoff]
    if not late.empty:
        return float(late.iloc[-1]["Close"])
    return float(df.iloc[-1]["Close"])


# ── Main analysis ─────────────────────────────────────────────────────

def get_trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def run_analysis(start: date, end: date, strike_interval: int = 5, may13_strike: int = 7410):
    trading_days = get_trading_days(start, end)
    print(f"Analyzing {len(trading_days)} trading days: {start} → {end}\n")

    # Step 1: Fetch SPY data (single API call for full range)
    spy_df = fetch_spy_1m(start, end)
    if spy_df is None or spy_df.empty:
        print("[ERROR] Could not fetch SPY 1m data. Check API key / rate limits.")
        return pd.DataFrame()

    # Determine ATM strikes for each day
    day_info: list[dict] = []
    for d in trading_days:
        spy_931 = get_spy_at_931(spy_df, d)
        if spy_931 is None:
            print(f"  {d}: no SPY data at 9:31, skipping")
            continue
        spx_est = round(spy_931 * 10, 2)
        strike = int(round(spx_est / strike_interval) * strike_interval)
        # User-specified override for May 13
        if d == date(2026, 5, 13):
            strike = may13_strike
        day_info.append({"date": d, "spy_931": spy_931, "spx_est": spx_est, "strike": strike})
        print(f"  {d} ({d.strftime('%a')}): SPY@9:31 = {spy_931:.2f}  →  SPX ≈ {spx_est:.0f}  →  ATM = {strike}")

    print(f"\nFetching option data for {len(day_info)} days...")
    print(f"(Rate limit: ~{CALL_DELAY}s between calls, est. {len(day_info) * 2 * CALL_DELAY // 60}min)\n")

    # Step 2: Fetch options for each day
    rows = []
    for i, info in enumerate(day_info):
        d = info["date"]
        strike = info["strike"]
        print(f"[{i+1}/{len(day_info)}] {d} strike={strike}")

        call_bars = fetch_option_1m(d, strike, "C")
        cached_call = call_bars is not None
        if not cached_call:
            time.sleep(CALL_DELAY)
            call_bars = fetch_option_1m(d, strike, "C")

        time.sleep(CALL_DELAY if not cached_call else 0.1)

        put_bars = fetch_option_1m(d, strike, "P")
        cached_put = put_bars is not None
        if not cached_put:
            time.sleep(CALL_DELAY)

        ce = price_near(call_bars, 9, 31)
        cm = price_near(call_bars, 12, 0)
        cx = eod_price(call_bars)

        pe = price_near(put_bars, 9, 31)
        pm = price_near(put_bars, 12, 0)
        px = eod_price(put_bars)

        c_bars = len(call_bars) if call_bars is not None else 0
        p_bars = len(put_bars) if put_bars is not None else 0
        print(f"  Call {option_ticker(d, strike, 'C')}: {c_bars} bars  |  9:31=${ce}  12:00=${cm}  EOD=${cx}")
        print(f"  Put  {option_ticker(d, strike, 'P')}: {p_bars} bars  |  9:31=${pe}  12:00=${pm}  EOD=${px}")

        cost = (ce + pe) if (ce is not None and pe is not None) else None
        mid_val = (cm + pm) if (cm is not None and pm is not None) else None
        eod_val = (cx + px) if (cx is not None and px is not None) else None

        mid_pnl = ((mid_val - cost) * 100) if (cost and mid_val) else None
        mid_pct = (((mid_val / cost) - 1) * 100) if (cost and mid_val) else None
        eod_pnl = ((eod_val - cost) * 100) if (cost and eod_val) else None
        eod_pct = (((eod_val / cost) - 1) * 100) if (cost and eod_val) else None

        rows.append({
            "Date": d,
            "Day": d.strftime("%a"),
            "SPY_931": info["spy_931"],
            "SPX_est": info["spx_est"],
            "Strike": strike,
            "Call_931": ce,
            "Put_931": pe,
            "Straddle_Cost": cost,
            "Call_Noon": cm,
            "Put_Noon": pm,
            "Straddle_Noon": mid_val,
            "Noon_PnL_$": mid_pnl,
            "Noon_PnL_%": mid_pct,
            "Call_EOD": cx,
            "Put_EOD": px,
            "Straddle_EOD": eod_val,
            "EOD_PnL_$": eod_pnl,
            "EOD_PnL_%": eod_pct,
        })

    return pd.DataFrame(rows)


def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 90)
    print("  SPX 0DTE STRADDLE ANALYSIS — MAY 2026")
    print("  Buy 1 ATM Call + 1 ATM Put at 9:31 AM each trading day")
    print("  Option multiplier: $100 per point")
    print("=" * 90)

    if df.empty:
        print("\nNo data.")
        return

    complete = df.dropna(subset=["Straddle_Cost"])
    print(f"\nTrading days analyzed: {len(df)}")
    print(f"Days with complete straddle data: {len(complete)}")

    if complete.empty:
        print("\nNo complete straddle pricing data returned from Polygon.")
        return

    print("\n─── Per-Day Breakdown ───")
    show = ["Date", "Day", "SPX_est", "Strike",
            "Call_931", "Put_931", "Straddle_Cost",
            "Straddle_Noon", "Noon_PnL_%",
            "Straddle_EOD", "EOD_PnL_%"]
    with pd.option_context("display.max_columns", 20, "display.width", 160,
                           "display.float_format", lambda x: f"{x:.2f}"):
        print(complete[[c for c in show if c in complete.columns]].to_string(index=False))

    mid = complete.dropna(subset=["Noon_PnL_%"])
    eod = complete.dropna(subset=["EOD_PnL_%"])

    if not mid.empty:
        print("\n─── Midday (12:00 PM) Statistics ───")
        print(f"  Avg return:     {mid['Noon_PnL_%'].mean():+.2f}%")
        print(f"  Median return:  {mid['Noon_PnL_%'].median():+.2f}%")
        print(f"  Best day:       {mid['Noon_PnL_%'].max():+.2f}%  ({mid.loc[mid['Noon_PnL_%'].idxmax(), 'Date']})")
        print(f"  Worst day:      {mid['Noon_PnL_%'].min():+.2f}%  ({mid.loc[mid['Noon_PnL_%'].idxmin(), 'Date']})")
        wins = (mid['Noon_PnL_%'] > 0).sum()
        print(f"  Win rate:       {wins}/{len(mid)} ({wins/len(mid)*100:.0f}%)")
        print(f"  Avg P&L/trade:  ${mid['Noon_PnL_$'].mean():+.0f}")
        print(f"  Total P&L:      ${mid['Noon_PnL_$'].sum():+.0f}")

    if not eod.empty:
        print("\n─── End of Day (~15:55 PM) Statistics ───")
        print(f"  Avg return:     {eod['EOD_PnL_%'].mean():+.2f}%")
        print(f"  Median return:  {eod['EOD_PnL_%'].median():+.2f}%")
        print(f"  Best day:       {eod['EOD_PnL_%'].max():+.2f}%  ({eod.loc[eod['EOD_PnL_%'].idxmax(), 'Date']})")
        print(f"  Worst day:      {eod['EOD_PnL_%'].min():+.2f}%  ({eod.loc[eod['EOD_PnL_%'].idxmin(), 'Date']})")
        wins = (eod['EOD_PnL_%'] > 0).sum()
        print(f"  Win rate:       {wins}/{len(eod)} ({wins/len(eod)*100:.0f}%)")
        print(f"  Avg P&L/trade:  ${eod['EOD_PnL_$'].mean():+.0f}")
        print(f"  Total P&L:      ${eod['EOD_PnL_$'].sum():+.0f}")

        print("\n─── Capital Summary ───")
        avg_cost = complete["Straddle_Cost"].mean()
        print(f"  Avg straddle cost:  ${avg_cost:.2f} × 100 = ${avg_cost * 100:,.0f} per trade")
        print(f"  Cumulative EOD P&L: ${eod['EOD_PnL_$'].sum():+,.0f}")


def main():
    p = argparse.ArgumentParser(description="SPX 0DTE straddle analysis (May 2026)")
    p.add_argument("--start", default="2026-05-01")
    p.add_argument("--end", default="2026-05-12",
                   help="End date (today May 13 not available on free Polygon tier)")
    p.add_argument("--strike-interval", type=int, default=5)
    p.add_argument("--out", default="")
    p.add_argument("--delay", type=int, default=13,
                   help="Seconds between API calls (free tier needs ~13)")
    args = p.parse_args()

    global CALL_DELAY
    CALL_DELAY = int(args.delay)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    df = run_analysis(start, end, int(args.strike_interval))
    print_summary(df)

    out_path = args.out or str(
        Path(__file__).resolve().parent.parent.parent / "spx_0dte_straddle_may2026.csv"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
