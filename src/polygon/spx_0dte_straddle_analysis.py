"""
SPX 0DTE Straddle Analysis

Strategy:
  At 9:31 AM ET each trading day, buy 1 ATM call + 1 ATM put (SPXW 0DTE)
  at the strike nearest $10 to SPX (SPY × 10 proxy at 9:31).

Checkpoints:
  - Entry:  9:31 AM
  - 10:00 AM
  - Midday: 12:00 PM
  - Afternoon: 3:30 PM
  - EOD:    3:50 PM

Data cache: data/spx_0dte_straddle/ (spy_1m/, spxw_options_1m/, analysis/)
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polygon_secrets import get_polygon_api_key

API_KEY = get_polygon_api_key()
BASE_URL = "https://api.polygon.io"
TZ = "America/New_York"

CALL_DELAY = 13  # seconds between API calls (free tier: 5/min)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data" / "spx_0dte_straddle"
SPY_CACHE_DIR = DATA_ROOT / "spy_1m"
OPTION_CACHE_DIR = DATA_ROOT / "spxw_options_1m"
ANALYSIS_DIR = DATA_ROOT / "analysis"

# Legacy cache (pre-folder migration)
LEGACY_CACHE_DIR = Path(__file__).resolve().parent / "data" / "spxw_straddle_cache"


def _ensure_data_dirs() -> None:
    for d in (SPY_CACHE_DIR, OPTION_CACHE_DIR, ANALYSIS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _migrate_legacy_cache() -> None:
    """Copy files from old cache location into data/spx_0dte_straddle/."""
    if not LEGACY_CACHE_DIR.exists():
        return
    _ensure_data_dirs()
    for src in LEGACY_CACHE_DIR.glob("*.csv"):
        if src.name.startswith("SPY_1m_"):
            dst = SPY_CACHE_DIR / src.name
        else:
            dst = OPTION_CACHE_DIR / src.name
        if not dst.exists():
            shutil.copy2(src, dst)


# ── HTTP / bars ───────────────────────────────────────────────────────

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


# ── Step 1: SPY 1m ────────────────────────────────────────────────────

def load_spy_1m_from_disk(start: date, end: date) -> pd.DataFrame | None:
    """Merge any overlapping SPY 1m cache files for the requested range."""
    frames: list[pd.DataFrame] = []
    for path in SPY_CACHE_DIR.glob("SPY_1m_*.csv"):
        try:
            df = pd.read_csv(path, parse_dates=["ts"])
        except Exception:
            continue
        if df.empty:
            continue
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
        df = df.set_index("ts").sort_index()
        frames.append(df)

    if not frames:
        return None

    merged = pd.concat(frames)
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged = merged[(merged.index.date >= start) & (merged.index.date <= end)]
    return merged if not merged.empty else None


def fetch_spy_1m(start: date, end: date) -> pd.DataFrame | None:
    """Load SPY 1m bars from cache when possible; download only if needed."""
    _ensure_data_dirs()
    cache_file = SPY_CACHE_DIR / f"SPY_1m_{start}_{end}.csv"

    if cache_file.exists():
        print(f"SPY 1m: using cache {cache_file.name}")
        df = pd.read_csv(cache_file, parse_dates=["ts"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
        return df.set_index("ts").sort_index()

    merged = load_spy_1m_from_disk(start, end)
    trading_days = get_trading_days(start, end)
    have_days = set()
    if merged is not None:
        have_days = {ts.date() for ts in merged.index}
    missing_days = [d for d in trading_days if d not in have_days]

    if merged is not None and not missing_days:
        print(f"SPY 1m: assembled {len(merged)} bars from existing cache (no download)")
        save = merged.reset_index()
        save["ts"] = save["ts"].dt.tz_convert("UTC")
        save.to_csv(cache_file, index=False)
        return merged

    if missing_days:
        fetch_start = min(missing_days)
        fetch_end = max(missing_days)
        print(f"SPY 1m: downloading {fetch_start} → {fetch_end} ({len(missing_days)} sessions missing)...")
    else:
        fetch_start, fetch_end = start, end
        print(f"SPY 1m: downloading {fetch_start} → {fetch_end}...")

    data = _get(
        f"{BASE_URL}/v2/aggs/ticker/SPY/range/1/minute/{fetch_start}/{fetch_end}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    df = _bars_df(data)
    if df is not None and not df.empty:
        if merged is not None:
            df = pd.concat([merged, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df[(df.index.date >= start) & (df.index.date <= end)]
        save = df.reset_index()
        save["ts"] = save["ts"].dt.tz_convert("UTC")
        save.to_csv(cache_file, index=False)
        df = save.set_index("ts").sort_index()
        df.index = df.index.tz_convert(TZ)
        print(f"  cached {len(df)} bars → {cache_file.relative_to(REPO_ROOT)}")
        return df

    if merged is not None:
        print(f"SPY 1m: download failed; using partial cache ({len(merged)} bars)")
        return merged
    return None


def get_spy_at_931(spy_df: pd.DataFrame, trade_date: date) -> float | None:
    day_data = spy_df[spy_df.index.date == trade_date]
    if day_data.empty:
        return None
    target = day_data.index[0].normalize() + pd.Timedelta(hours=9, minutes=31)
    mask = day_data.index >= target
    if mask.any():
        return float(day_data.loc[day_data.index[mask][0], "Close"])
    return None


def _spy_window(spy_df: pd.DataFrame, trade_date: date, start_h: int, start_m: int,
                end_h: int, end_m: int) -> pd.DataFrame:
    day_data = spy_df[spy_df.index.date == trade_date]
    if day_data.empty:
        return day_data
    base = day_data.index[0].normalize()
    start = base + pd.Timedelta(hours=start_h, minutes=start_m)
    end = base + pd.Timedelta(hours=end_h, minutes=end_m)
    return day_data[(day_data.index >= start) & (day_data.index <= end)]


def classify_day_regime_1030(spy_df: pd.DataFrame, trade_date: date) -> dict:
    """
    Classify RANGE vs TREND using SPY 1m bars from 9:31 → 10:30 only.

    Mirrors the repo's regime logic (VWAP slope, time on one side of VWAP,
    opening-range expansion) but on 1-minute SPY data × 10 as SPX proxy.

    Returns Day_Type_1030: RANGE | TREND_UP | TREND_DOWN
    """
    empty = {
        "Day_Type_1030": None,
        "SPX_Move_1030": None,
        "SPX_Range_1030": None,
        "Trend_Efficiency_1030": None,
        "Trend_Score_1030": None,
    }

    open_931 = get_spy_at_931(spy_df, trade_date)
    window = _spy_window(spy_df, trade_date, 9, 31, 10, 30)
    if open_931 is None or window.empty:
        return empty

    close_1030 = price_checkpoint(window, 10, 30, tol_min=5)
    if close_1030 is None:
        close_1030 = float(window.iloc[-1]["Close"])

    or_window = _spy_window(spy_df, trade_date, 9, 31, 10, 0)
    reg_window = window

    or_high = float(or_window["High"].max()) if not or_window.empty else float(reg_window["High"].max())
    or_low = float(or_window["Low"].min()) if not or_window.empty else float(reg_window["Low"].min())
    or_range = max(or_high - or_low, 1e-9)

    reg_high = float(reg_window["High"].max())
    reg_low = float(reg_window["Low"].min())
    reg_range = max(reg_high - reg_low, 1e-9)

    spx_move = (close_1030 - open_931) * 10
    spx_range = reg_range * 10
    efficiency = abs(spx_move) / spx_range if spx_range > 0 else 0.0

    # Session VWAP over 9:31-10:30
    tp = (reg_window["High"] + reg_window["Low"] + reg_window["Close"]) / 3.0
    vol = reg_window["Volume"].replace(0, np.nan)
    if vol.notna().any() and float(vol.sum()) > 0:
        vwap = (tp * vol).cumsum() / vol.cumsum()
        v0, vN = float(vwap.iloc[0]), float(vwap.iloc[-1])
        vwap_slope = (vN - v0) / max(abs(v0), 1e-9)
        time_above = float((reg_window["Close"] > vwap).mean())
    else:
        vwap_slope = (close_1030 - open_931) / max(abs(open_931), 1e-9)
        time_above = 1.0 if close_1030 >= open_931 else 0.0

    # 14-day SPY ATR proxy from prior closes (fallback: OR range)
    prior = spy_df[spy_df.index.date < trade_date].copy()
    daily = prior.resample("D")["Close"].last().dropna().tail(14)
    atr = float(daily.diff().abs().mean()) if len(daily) >= 5 else or_range
    atr = max(atr, 1e-9)

    or_atr_frac = or_range / atr
    range_expansion = reg_range / atr
    direction = 1 if spx_move >= 0 else -1

    # Scoring (adapted from SPY_15m_data/spy_full.py thresholds)
    or_gate = 1.0 if or_atr_frac >= 0.22 else 0.0
    slope_score = np.clip((abs(vwap_slope) - 0.00055) / 0.00055, -2, 2)
    vwap_side_score = np.clip((abs(time_above - 0.5) - 0.12) / 0.12, -2, 2)
    exp_score = np.clip((range_expansion - 1.15) / 1.15, -2, 2)
    move_score = np.clip((abs(spx_move) - 12.0) / 12.0, -2, 2)
    eff_score = np.clip((efficiency - 0.45) / 0.45, -2, 2)

    score = float(or_gate * (0.7 * slope_score + 0.7 * vwap_side_score + 0.6 * exp_score
                             + 0.8 * move_score + 0.5 * eff_score))

    if score >= 1.0 and abs(spx_move) >= 10:
        day_type = "TREND_UP" if direction > 0 else "TREND_DOWN"
    else:
        day_type = "RANGE"

    return {
        "Day_Type_1030": day_type,
        "SPX_Move_1030": round(spx_move, 1),
        "SPX_Range_1030": round(spx_range, 1),
        "Trend_Efficiency_1030": round(efficiency, 2),
        "Trend_Score_1030": round(score, 2),
    }


# ── Step 2: SPXW option 1m ────────────────────────────────────────────

def option_ticker(trade_date: date, strike: int, pc: str) -> str:
    d = trade_date.strftime("%y%m%d")
    return f"O:SPXW{d}{pc}{strike * 1000:08d}"


def option_cache_path(trade_date: date, strike: int, pc: str) -> Path:
    ticker = option_ticker(trade_date, strike, pc)
    return OPTION_CACHE_DIR / f"{ticker.replace(':', '_')}_{trade_date}.csv"


def load_option_1m_from_cache(trade_date: date, strike: int, pc: str) -> pd.DataFrame | None:
    cache_file = option_cache_path(trade_date, strike, pc)
    if not cache_file.exists():
        return None
    df = pd.read_csv(cache_file, parse_dates=["ts"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
    return df.set_index("ts").sort_index()


def download_option_1m(trade_date: date, strike: int, pc: str) -> pd.DataFrame | None:
    _ensure_data_dirs()
    cache_file = option_cache_path(trade_date, strike, pc)
    ticker = option_ticker(trade_date, strike, pc)
    ds = trade_date.isoformat()
    data = _get(
        f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{ds}/{ds}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    df = _bars_df(data)
    if df is not None and not df.empty:
        save = df.reset_index()
        save["ts"] = save["ts"].dt.tz_convert("UTC")
        save.to_csv(cache_file, index=False)
        df = save.set_index("ts").sort_index()
        df.index = df.index.tz_convert(TZ)
    return df


def fetch_option_1m(trade_date: date, strike: int, pc: str) -> pd.DataFrame | None:
    cached = load_option_1m_from_cache(trade_date, strike, pc)
    if cached is not None and not cached.empty:
        return cached
    return download_option_1m(trade_date, strike, pc)


# ── Price extraction ──────────────────────────────────────────────────

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


def price_checkpoint(df: pd.DataFrame | None, hour: int, minute: int, tol_min: int = 10) -> float | None:
    """Bar close at checkpoint; falls back to last bar within tol_min before target."""
    px = price_near(df, hour, minute, tol_min=tol_min)
    if px is not None or df is None or df.empty:
        return px
    target = df.index[0].normalize() + pd.Timedelta(hours=hour, minutes=minute)
    before = df.index[df.index <= target]
    if len(before):
        idx = before[-1]
        if (target - idx).total_seconds() <= tol_min * 60:
            return float(df.loc[idx, "Close"])
    return None


def straddle_pnl(cost: float | None, value: float | None) -> tuple[float | None, float | None]:
    if cost is None or value is None:
        return None, None
    pnl = (value - cost) * 100
    pct = ((value / cost) - 1) * 100 if cost else None
    return pnl, pct


# ── Main analysis ─────────────────────────────────────────────────────

def get_trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def run_analysis(start: date, end: date, strike_interval: int = 10) -> pd.DataFrame:
    _migrate_legacy_cache()
    trading_days = get_trading_days(start, end)
    print(f"Analyzing {len(trading_days)} trading days: {start} → {end}")
    print(f"Data folder: {DATA_ROOT.relative_to(REPO_ROOT)}/\n")

    spy_df = fetch_spy_1m(start, end)
    if spy_df is None or spy_df.empty:
        print("[ERROR] Could not fetch SPY 1m data. Check API key / rate limits.")
        return pd.DataFrame()

    day_info: list[dict] = []
    for d in trading_days:
        spy_931 = get_spy_at_931(spy_df, d)
        if spy_931 is None:
            print(f"  {d}: no SPY data at 9:31, skipping")
            continue
        spx_est = round(spy_931 * 10, 2)
        strike = int(round(spx_est / strike_interval) * strike_interval)
        day_info.append({"date": d, "spy_931": spy_931, "spx_est": spx_est, "strike": strike})
        print(f"  {d} ({d.strftime('%a')}): SPY@9:31 = {spy_931:.2f}  →  SPX ≈ {spx_est:.0f}  →  ATM = {strike}")

    print(f"\nFetching option data for {len(day_info)} days...")

    need_download = 0
    for info in day_info:
        d, strike = info["date"], info["strike"]
        for pc in ("C", "P"):
            if not option_cache_path(d, strike, pc).exists():
                need_download += 1
    est_min = need_download * CALL_DELAY // 60
    print(f"Cache hits expected: {len(day_info) * 2 - need_download}/{len(day_info) * 2} legs")
    if need_download:
        print(f"Downloads needed: {need_download} (~{est_min} min at {CALL_DELAY}s/call)\n")
    else:
        print("All option legs cached — no Polygon downloads needed.\n")

    rows = []
    downloads = 0
    cache_hits = 0
    for i, info in enumerate(day_info):
        d = info["date"]
        strike = info["strike"]
        print(f"[{i+1}/{len(day_info)}] {d} strike={strike}")

        if option_cache_path(d, strike, "C").exists():
            call_bars = load_option_1m_from_cache(d, strike, "C")
            cache_hits += 1
            print("  Call: [cache]")
        else:
            if downloads:
                time.sleep(CALL_DELAY)
            call_bars = download_option_1m(d, strike, "C")
            downloads += 1
            print("  Call: [downloaded]" if call_bars is not None else "  Call: [download failed]")

        if option_cache_path(d, strike, "P").exists():
            put_bars = load_option_1m_from_cache(d, strike, "P")
            cache_hits += 1
            print("  Put:  [cache]")
        else:
            if downloads:
                time.sleep(CALL_DELAY)
            put_bars = download_option_1m(d, strike, "P")
            downloads += 1
            print("  Put:  [downloaded]" if put_bars is not None else "  Put:  [download failed]")

        ce = price_checkpoint(call_bars, 9, 31)
        c10 = price_checkpoint(call_bars, 10, 0)
        cm = price_checkpoint(call_bars, 12, 0)
        c330 = price_checkpoint(call_bars, 15, 30)
        c350 = price_checkpoint(call_bars, 15, 50)

        pe = price_checkpoint(put_bars, 9, 31)
        p10 = price_checkpoint(put_bars, 10, 0)
        pm = price_checkpoint(put_bars, 12, 0)
        p330 = price_checkpoint(put_bars, 15, 30)
        p350 = price_checkpoint(put_bars, 15, 50)

        cost = (ce + pe) if (ce is not None and pe is not None) else None
        val_10 = (c10 + p10) if (c10 is not None and p10 is not None) else None
        noon_val = (cm + pm) if (cm is not None and pm is not None) else None
        val_330 = (c330 + p330) if (c330 is not None and p330 is not None) else None
        val_350 = (c350 + p350) if (c350 is not None and p350 is not None) else None

        pnl_10, pct_10 = straddle_pnl(cost, val_10)
        noon_pnl, noon_pct = straddle_pnl(cost, noon_val)
        pnl_330, pct_330 = straddle_pnl(cost, val_330)
        pnl_350, pct_350 = straddle_pnl(cost, val_350)

        regime = classify_day_regime_1030(spy_df, d)

        print(
            f"  Call {option_ticker(d, strike, 'C')}: "
            f"9:31=${ce}  10:00=${c10}  12:00=${cm}  3:30=${c330}  3:50=${c350}"
        )
        print(
            f"  Put  {option_ticker(d, strike, 'P')}: "
            f"9:31=${pe}  10:00=${p10}  12:00=${pm}  3:30=${p330}  3:50=${p350}"
        )

        rows.append({
            "SPX_931": info["spx_est"],
            "Date": d,
            "Day": d.strftime("%a"),
            "Strike": strike,
            "Call_931": ce,
            "Put_931": pe,
            "Straddle_Cost": cost,
            "Call_10am": c10,
            "Put_10am": p10,
            "Straddle_10am": val_10,
            "Combined_PnL_10am": pnl_10,
            "Combined_PnL_10am_%": pct_10,
            "Call_Noon": cm,
            "Put_Noon": pm,
            "Straddle_Noon": noon_val,
            "Combined_PnL_12pm": noon_pnl,
            "Combined_PnL_12pm_%": noon_pct,
            "Call_330": c330,
            "Put_330": p330,
            "Straddle_330": val_330,
            "Combined_PnL_330": pnl_330,
            "Combined_PnL_330_%": pct_330,
            "Call_350": c350,
            "Put_350": p350,
            "Straddle_350": val_350,
            "Combined_PnL_350": pnl_350,
            "Combined_PnL_350_%": pct_350,
            **regime,
        })

    print(f"\nOption legs: {cache_hits} cache hits, {downloads} downloads")
    return pd.DataFrame(rows)


PNL_DOLLAR_COLS = [
    "Combined_PnL_10am",
    "Combined_PnL_12pm",
    "Combined_PnL_330",
    "Combined_PnL_350",
]

PNL_PCT_COLS = [
    "Combined_PnL_10am_%",
    "Combined_PnL_12pm_%",
    "Combined_PnL_330_%",
    "Combined_PnL_350_%",
]


def append_grand_totals(df: pd.DataFrame) -> pd.DataFrame:
    """Append a GRAND TOTAL row summing dollar P/L columns."""
    if df.empty:
        return df

    data = df[df["Date"].astype(str) != "GRAND TOTAL"].copy()
    totals: dict = {
        "SPX_931": "",
        "Date": "GRAND TOTAL",
        "Day": "",
        "Strike": "",
    }

    if "Straddle_Cost" in data.columns:
        totals["Straddle_Cost"] = data["Straddle_Cost"].sum()

    for col in PNL_DOLLAR_COLS:
        if col in data.columns:
            totals[col] = data[col].sum(skipna=True)

    # Aggregate return % = total P/L ÷ total entry cost for days with data
    cost = data["Straddle_Cost"] if "Straddle_Cost" in data.columns else None
    for pnl_col, pct_col in zip(PNL_DOLLAR_COLS, PNL_PCT_COLS):
        if pnl_col not in data.columns or pct_col not in data.columns or cost is None:
            continue
        mask = data[pnl_col].notna() & cost.notna()
        if mask.any():
            totals[pct_col] = (data.loc[mask, pnl_col].sum() / cost.loc[mask].sum()) * 100

    for col in data.columns:
        totals.setdefault(col, "")

    return pd.concat([data, pd.DataFrame([totals])], ignore_index=True)


def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 90)
    print("  SPX 0DTE STRADDLE ANALYSIS")
    print("  Buy 1 ATM Call + 1 ATM Put at 9:31 AM each trading day")
    print("  Option multiplier: $100 per point")
    print("=" * 90)

    if df.empty:
        print("\nNo data.")
        return

    complete = df[df["Date"].astype(str) != "GRAND TOTAL"].dropna(subset=["Straddle_Cost"])
    print(f"\nTrading days analyzed: {len(complete)}")
    print(f"Days with complete straddle data: {len(complete)}")

    if complete.empty:
        print("\nNo complete straddle pricing data returned from Polygon.")
        return

    show = [
        "SPX_931", "Date", "Day", "Day_Type_1030", "SPX_Move_1030", "Strike", "Straddle_Cost",
        "Combined_PnL_10am", "Combined_PnL_12pm", "Combined_PnL_330", "Combined_PnL_350",
    ]
    display_df = df[[c for c in show if c in df.columns]]
    with pd.option_context("display.max_columns", 20, "display.width", 160,
                           "display.float_format", lambda x: f"{x:.2f}"):
        print(display_df.to_string(index=False))

    for label, pct_col, pnl_col in [
        ("10:00 AM", "Combined_PnL_10am_%", "Combined_PnL_10am"),
        ("12:00 PM", "Combined_PnL_12pm_%", "Combined_PnL_12pm"),
        ("3:30 PM", "Combined_PnL_330_%", "Combined_PnL_330"),
        ("3:50 PM", "Combined_PnL_350_%", "Combined_PnL_350"),
    ]:
        sub = complete.dropna(subset=[pct_col])
        if sub.empty:
            continue
        print(f"\n─── {label} Statistics ───")
        print(f"  Avg return:     {sub[pct_col].mean():+.2f}%")
        print(f"  Median return:  {sub[pct_col].median():+.2f}%")
        wins = (sub[pct_col] > 0).sum()
        print(f"  Win rate:       {wins}/{len(sub)} ({wins/len(sub)*100:.0f}%)")
        print(f"  Total P&L:      ${sub[pnl_col].sum():+.0f}")


def main():
    p = argparse.ArgumentParser(description="SPX 0DTE straddle analysis")
    p.add_argument("--start", default="2026-05-08")
    p.add_argument("--end", default="2026-06-04")
    p.add_argument("--strike-interval", type=int, default=10)
    p.add_argument("--out", default="")
    p.add_argument("--delay", type=int, default=13,
                   help="Seconds between API calls (free tier needs ~13)")
    args = p.parse_args()

    global CALL_DELAY
    CALL_DELAY = int(args.delay)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    df = run_analysis(start, end, int(args.strike_interval))
    df = append_grand_totals(df)
    print_summary(df)

    out_path = Path(args.out) if args.out else (
        ANALYSIS_DIR / f"spx_0dte_straddle_{start}_{end}.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, float_format="%.2f")
    print(f"\nSaved analysis: {out_path.relative_to(REPO_ROOT)}")
    print(f"Raw data:       {DATA_ROOT.relative_to(REPO_ROOT)}/")


if __name__ == "__main__":
    main()
