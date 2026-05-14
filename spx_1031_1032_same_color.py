"""
SPX 10:31 & 10:32 AM Candle Same-Color Research
================================================

Question:
  How often do the 10:31 AM and 10:32 AM (US/Eastern) 1-minute candles
  close the same color (both green or both red)?

Green candle  -> Close > Open
Red candle    -> Close < Open
Doji          -> Close == Open  (treated as "neutral" and EXCLUDED from the
                                 same-color match, but still shown as a
                                 separate bucket in the summary)

Reporting windows:
  - Last 30 trading days
  - Last 60 trading days
  - Last 90 trading days

Data source: Polygon.io 1-minute aggregates
  - Primary ticker:   I:SPX  (S&P 500 index)
  - Fallback ticker:  SPY    (ETF proxy) if index data is unavailable on
                              the current Polygon plan.

Data is cached on disk under ./SPX_1m_data/ (or ./SPY_1m_data/) so re-runs
don't re-hit the API.

Usage:
  python spx_1031_1032_same_color.py
  python spx_1031_1032_same_color.py --ticker SPY
  python spx_1031_1032_same_color.py --days 90 --sleep 0.2
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd
import pytz
import requests

REPO_ROOT = Path(__file__).resolve().parent
sys.path.append(str(REPO_ROOT / "src" / "polygon"))
from polygon_secrets import get_polygon_api_key  # noqa: E402

EASTERN = pytz.timezone("US/Eastern")
TARGET_TIMES = ("10:31", "10:32")  # HH:MM in US/Eastern
DEFAULT_DAYS = 90


def cache_dir_for(ticker: str) -> Path:
    """Cache directory per ticker (I:SPX -> SPX_1m_data, SPY -> SPY_1m_data)."""
    safe = ticker.replace("I:", "").replace(":", "_")
    d = REPO_ROOT / f"{safe}_1m_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(ticker: str, date_str: str) -> Path:
    safe = ticker.replace("I:", "").replace(":", "_")
    return cache_dir_for(ticker) / f"{safe}_1m_{date_str}.csv"


def fetch_1m_day(ticker: str, date_str: str, api_key: str,
                 max_429_retries: int = 4, rate_limit_wait_s: float = 65.0) -> pd.DataFrame | None:
    """Fetch 1-minute aggregates for a single day from Polygon.

    Handles 429 (rate limit) by sleeping ~65 seconds (Polygon free tier is
    5 calls/minute) and retrying up to `max_429_retries` times.
    """
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/{date_str}/{date_str}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": api_key,
    }

    for attempt in range(max_429_retries + 1):
        try:
            r = requests.get(url, params=params, timeout=20)
        except requests.exceptions.RequestException as e:
            print(f"  network error for {date_str}: {e}")
            return None

        if r.status_code == 429:
            if attempt >= max_429_retries:
                print(f"  429 rate limited for {ticker} on {date_str} (max retries exceeded)")
                return None
            wait = rate_limit_wait_s
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = max(wait, float(retry_after) + 2.0)
                except ValueError:
                    pass
            print(f"  429 rate limited; sleeping {wait:.0f}s then retrying {date_str} "
                  f"(attempt {attempt + 1}/{max_429_retries})")
            time.sleep(wait)
            continue

        if r.status_code == 403:
            print(f"  403 Forbidden for {ticker} on {date_str} (plan may not include this ticker)")
            return None
        if r.status_code != 200:
            print(f"  HTTP {r.status_code} for {ticker} on {date_str}: {r.text[:140]}")
            return None

        data = r.json()
        if data.get("status") not in ("OK", "DELAYED") or data.get("resultsCount", 0) == 0:
            return None

        df = pd.DataFrame(data["results"])
        df["Timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(EASTERN)
        df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}, inplace=True)
        df = df[["Timestamp", "Open", "High", "Low", "Close", "Volume"]].sort_values("Timestamp").reset_index(drop=True)
        return df

    return None


def load_day(ticker: str, date_str: str, api_key: str, sleep_s: float) -> pd.DataFrame | None:
    """Load a single day from cache; fall back to API and cache the result."""
    path = cache_path(ticker, date_str)
    if path.exists() and path.stat().st_size > 0:
        try:
            df = pd.read_csv(path, parse_dates=["Timestamp"])
            if df["Timestamp"].dt.tz is None:
                df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC").dt.tz_convert(EASTERN)
            else:
                df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)
            if not df.empty:
                return df
        except Exception as e:
            print(f"  cache read failed for {path.name}: {e}; refetching")

    df = fetch_1m_day(ticker, date_str, api_key)
    if df is not None and not df.empty:
        df.to_csv(path, index=False)
        time.sleep(sleep_s)
        return df
    time.sleep(sleep_s)
    return None


def recent_trading_dates(n_days: int, end_date: date | None = None,
                         newest_first: bool = False) -> list[str]:
    """Return the last n_days weekdays (Mon-Fri) up to and including end_date.

    By default the list is oldest-first (ascending). If newest_first=True the
    list is newest-first (descending), which is useful when you want the most
    recent trading days fetched/analyzed first under rate limits.
    Market holidays are filtered later because Polygon returns no data for them.
    """
    if end_date is None:
        end_date = datetime.now(EASTERN).date()
    out: list[str] = []
    d = end_date
    while len(out) < n_days:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    if newest_first:
        out.sort(reverse=True)
    else:
        out.sort()
    return out


def candle_color(row: pd.Series) -> str:
    o = float(row["Open"])
    c = float(row["Close"])
    if c > o:
        return "green"
    if c < o:
        return "red"
    return "doji"


def find_candle(df: pd.DataFrame, hhmm: str) -> pd.Series | None:
    """Find the 1-minute candle whose Timestamp matches HH:MM Eastern on that day.
    Polygon timestamps are the START of the bar, so the '10:31' candle covers
    10:31:00-10:31:59 Eastern.
    """
    mask = df["Timestamp"].dt.strftime("%H:%M") == hhmm
    sub = df.loc[mask]
    if sub.empty:
        return None
    return sub.iloc[0]


def analyze(ticker: str, days: int, sleep_s: float, api_key: str,
            newest_first: bool = True) -> pd.DataFrame:
    # Pull a generous buffer of weekdays so we still get `days` valid trading
    # days after holidays/empty returns. ~1.35x is plenty for a 90-day window.
    buffer = max(days + 15, int(days * 1.35))
    dates = recent_trading_dates(buffer, newest_first=newest_first)
    order = "newest -> oldest" if newest_first else "oldest -> newest"
    print(f"Scanning up to {len(dates)} weekdays ({order}) to collect {days} trading days of data...", flush=True)

    rows: list[dict] = []
    # Early-abort heuristic: if the first handful of uncached fetches all fail
    # with no data (likely a plan/permission issue), bail so we can fall back.
    consecutive_empty_fetches = 0
    early_abort_after = 8

    for ds in dates:
        was_cached = cache_path(ticker, ds).exists()
        df_day = load_day(ticker, ds, api_key, sleep_s)
        if df_day is None or df_day.empty:
            if not cache_path(ticker, ds).exists():
                consecutive_empty_fetches += 1
                if consecutive_empty_fetches >= early_abort_after and not rows:
                    print(f"  Aborting early: {consecutive_empty_fetches} consecutive empty fetches for {ticker}",
                          flush=True)
                    return pd.DataFrame()
            continue
        consecutive_empty_fetches = 0
        if not was_cached:
            print(f"  fetched {ds}: {len(df_day)} bars (total analyzed: {len(rows) + 1})", flush=True)

        c1 = find_candle(df_day, TARGET_TIMES[0])
        c2 = find_candle(df_day, TARGET_TIMES[1])
        if c1 is None or c2 is None:
            continue

        color1 = candle_color(c1)
        color2 = candle_color(c2)
        same_color = color1 == color2 and color1 in ("green", "red")

        rows.append({
            "Date": ds,
            "C1_Open": float(c1["Open"]),
            "C1_Close": float(c1["Close"]),
            "C1_Color": color1,
            "C2_Open": float(c2["Open"]),
            "C2_Close": float(c2["Close"]),
            "C2_Color": color2,
            "SameColor": same_color,
            "BothGreen": color1 == "green" and color2 == "green",
            "BothRed": color1 == "red" and color2 == "red",
            "HasDoji": "doji" in (color1, color2),
        })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    return out


def window_summary(df: pd.DataFrame, n: int) -> dict:
    """Summary for the most recent n trading days in df."""
    sub = df.tail(n)
    total = len(sub)
    same = int(sub["SameColor"].sum())
    both_green = int(sub["BothGreen"].sum())
    both_red = int(sub["BothRed"].sum())
    doji = int(sub["HasDoji"].sum())
    opposite = total - same - doji
    pct = (same / total * 100.0) if total else 0.0
    return {
        "window_days": n,
        "trading_days_analyzed": total,
        "same_color": same,
        "same_color_pct": round(pct, 2),
        "both_green": both_green,
        "both_red": both_red,
        "opposite_colors": opposite,
        "had_doji": doji,
    }


def print_summary(title: str, s: dict) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"  Trading days analyzed : {s['trading_days_analyzed']}")
    print(f"  Same color matches    : {s['same_color']}  ({s['same_color_pct']}%)")
    print(f"    - Both GREEN        : {s['both_green']}")
    print(f"    - Both RED          : {s['both_red']}")
    print(f"  Opposite colors       : {s['opposite_colors']}")
    print(f"  Days with a doji      : {s['had_doji']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="SPX 10:31 & 10:32 AM same-color candle research")
    parser.add_argument("--ticker", default="SPY",
                        help="Polygon ticker (default: SPY, because many plans do not include I:SPX). "
                             "Pass --ticker I:SPX to try the index; script will auto-fallback to SPY on failure.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="Number of recent trading days to cover (default: 90)")
    parser.add_argument("--sleep", type=float, default=0.15,
                        help="Seconds to sleep between Polygon API calls (default: 0.15)")
    parser.add_argument("--no-fallback", action="store_true",
                        help="Disable automatic SPY fallback when SPX fails")
    parser.add_argument("--oldest-first", action="store_true",
                        help="Fetch oldest -> newest instead of the default newest -> oldest")
    args = parser.parse_args()

    api_key = get_polygon_api_key()

    primary = args.ticker
    newest_first = not args.oldest_first
    print(f"Using ticker: {primary}")
    df = analyze(primary, args.days, args.sleep, api_key, newest_first=newest_first)

    effective_ticker = primary
    if df.empty and not args.no_fallback and primary.upper() in ("I:SPX", "SPX"):
        print("\nNo SPX 1-minute data available on this plan. Falling back to SPY...")
        effective_ticker = "SPY"
        df = analyze(effective_ticker, args.days, args.sleep, api_key, newest_first=newest_first)

    if df.empty:
        print("\nNo data collected. Aborting.")
        return 1

    # Save per-day detail
    safe = effective_ticker.replace("I:", "").replace(":", "_")
    detail_path = REPO_ROOT / f"{safe}_1031_1032_same_color_detail.csv"
    df.to_csv(detail_path, index=False)
    print(f"\nPer-day detail saved to: {detail_path.name}")

    # Print last 5 rows for sanity
    print("\nMost recent 5 analyzed days:")
    print(df.tail(5).to_string(index=False))

    # Summaries
    s30 = window_summary(df, 30)
    s60 = window_summary(df, 60)
    s90 = window_summary(df, 90)

    print("\n" + "=" * 60)
    print(f"RESULTS ({effective_ticker}): 10:31 & 10:32 AM same-color candles")
    print("=" * 60)
    print_summary("Last 30 trading days", s30)
    print_summary("Last 60 trading days", s60)
    print_summary("Last 90 trading days", s90)

    # Save combined summary
    summary_df = pd.DataFrame([s30, s60, s90])
    summary_df.insert(0, "ticker", effective_ticker)
    summary_path = REPO_ROOT / f"{safe}_1031_1032_same_color_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSummary saved to: {summary_path.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
