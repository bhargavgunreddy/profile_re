# pip install requests pandas numpy pytz

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# ----------------------------
# CONFIG
# ----------------------------
from polygon_secrets import get_polygon_api_key

POLYGON_API_KEY = get_polygon_api_key()

TICKER = "SPY"
MULTIPLIER = 5
TIMESPAN = "minute"  # Polygon uses 'minute', 'hour', 'day', etc.
DAYS_BACK = 90       # requested
ADJUSTED = True      # adjust for splits (Polygon default is adjusted in many clients)
SORT = "asc"
LIMIT = 50000        # max supported by aggregates endpoint per Polygon docs :contentReference[oaicite:2]{index=2}

TZ = "America/New_York"
RTH_START = "09:30"
RTH_END = "16:00"

EMA_SPAN = 20
SAVE_CSV = True

# ----------------------------
# POLYGON AGGS DOWNLOAD
# ----------------------------
def polygon_aggs_url(ticker: str, multiplier: int, timespan: str, date_from: str, date_to: str) -> str:
    return f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{date_from}/{date_to}"

def fetch_polygon_aggs(
    ticker: str,
    multiplier: int,
    timespan: str,
    date_from: str,
    date_to: str,
    adjusted: bool = True,
    sort: str = "asc",
    limit: int = 50000,
    api_key: str = "",
    sleep_on_429: float = 15.0,
) -> pd.DataFrame:
    """
    Fetches aggregate bars from Polygon and returns a DataFrame indexed by UTC datetime.
    Handles pagination via next_url if present.
    """
    url = polygon_aggs_url(ticker, multiplier, timespan, date_from, date_to)
    params = {
        "adjusted": "true" if adjusted else "false",
        "sort": sort,
        "limit": limit,
        "apiKey": api_key,
    }

    all_results = []
    next_url = None

    while True:
        if next_url:
            # next_url typically already includes apiKey; but to be safe, append if missing
            url_to_get = next_url
            if "apiKey=" not in url_to_get and api_key:
                joiner = "&" if "?" in url_to_get else "?"
                url_to_get = f"{url_to_get}{joiner}apiKey={api_key}"
            resp = requests.get(url_to_get, timeout=30)
        else:
            resp = requests.get(url, params=params, timeout=30)

        if resp.status_code == 429:
            # Polygon free tier can be low rate limit (commonly 5 req/min cited by integrators) :contentReference[oaicite:3]{index=3}
            print(f"Rate limited (429). Sleeping {sleep_on_429}s...")
            time.sleep(sleep_on_429)
            continue

        resp.raise_for_status()
        data = resp.json()

        if data.get("status") not in ("OK", "DELAYED"):
            raise RuntimeError(f"Polygon returned non-OK status: {data.get('status')} | {data}")

        results = data.get("results") or []
        all_results.extend(results)

        next_url = data.get("next_url")
        if not next_url:
            break

        # Be polite to API
        time.sleep(0.25)

    if not all_results:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "VWAP", "Trades"])

    # Polygon aggs fields: t,o,h,l,c,v,vw,n
    df = pd.DataFrame(all_results)
    df = df.rename(
        columns={
            "t": "timestamp_ms",
            "o": "Open",
            "h": "High",
            "l": "Low",
            "c": "Close",
            "v": "Volume",
            "vw": "VWAP",
            "n": "Trades",
        }
    )

    df["Datetime"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.set_index("Datetime").sort_index()

    # Keep only what we need
    keep_cols = ["Open", "High", "Low", "Close", "Volume", "VWAP", "Trades"]
    df = df[[c for c in keep_cols if c in df.columns]].copy()

    return df

# ----------------------------
# ANALYSIS: EMA + TIME-TO-RECLAIM
# ----------------------------
def add_ema(df: pd.DataFrame, span: int = 20) -> pd.DataFrame:
    out = df.copy()
    out["EMA20"] = out["Close"].ewm(span=span, adjust=False).mean()
    return out

def time_to_reclaim_minutes(group: pd.DataFrame) -> float | None:
    """
    Minutes between:
      - first bar where Close < EMA20
      - first later bar where Close > EMA20
    within the group (a single trading day).
    """
    g = group.sort_index()
    close = g["Close"]
    ema = g["EMA20"]

    below = close < ema
    if not below.any():
        return None

    first_below_time = g.index[below.argmax()]

    after = g.loc[first_below_time:]
    reclaim = after[after["Close"] > after["EMA20"]]
    if reclaim.empty:
        return None

    first_reclaim_time = reclaim.index[0]
    minutes = (first_reclaim_time - first_below_time).total_seconds() / 60.0
    return float(minutes)

def main():
    # Date range (Polygon accepts YYYY-MM-DD for aggs ranges commonly) :contentReference[oaicite:4]{index=4}
    end_date = datetime.now(timezone.utc).date()
    start_date = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).date()

    date_from = start_date.strftime("%Y-%m-%d")
    date_to = end_date.strftime("%Y-%m-%d")

    print(f"Downloading {TICKER} {MULTIPLIER}{TIMESPAN} bars from {date_from} to {date_to} via Polygon...")

    df = fetch_polygon_aggs(
        ticker=TICKER,
        multiplier=MULTIPLIER,
        timespan=TIMESPAN,
        date_from=date_from,
        date_to=date_to,
        adjusted=ADJUSTED,
        sort=SORT,
        limit=LIMIT,
        api_key=POLYGON_API_KEY,
    )

    if df.empty:
        print("No data returned.")
        return

    # Convert to NY time for RTH filtering and day boundaries
    df = df.tz_convert(TZ)

    # Filter Regular Trading Hours
    df = df.between_time(RTH_START, RTH_END).copy()

    # EMA
    df = add_ema(df, span=EMA_SPAN)

    # Group per day
    df["date"] = df.index.date
    timing = (
        df.groupby("date", sort=True)
          .apply(time_to_reclaim_minutes)
          .reset_index(name="minutes_to_reclaim")
    )

    # Drop days with no reclaim measurement
    timing = timing.dropna().copy()

    # Summary
    print(f"\nSYMBOL={TICKER} | interval={MULTIPLIER} {TIMESPAN} | EMA={EMA_SPAN} | RTH={RTH_START}-{RTH_END} {TZ}")
    print("Definition: minutes from first Close<EMA20 to first later Close>EMA20 (same day)\n")

    print("Days measured:", len(timing))
    print("Average minutes:", round(timing["minutes_to_reclaim"].mean(), 2))
    print("Median minutes:", round(timing["minutes_to_reclaim"].median(), 2))
    print("Min minutes:", round(timing["minutes_to_reclaim"].min(), 2))
    print("Max minutes:", round(timing["minutes_to_reclaim"].max(), 2))

    print("\nDistribution:")
    print(timing["minutes_to_reclaim"].describe())

    # Buckets
    bins = [0, 15, 30, 60, 120, 10_000]
    labels = ["≤15m", "15–30m", "30–60m", "60–120m", "120m+"]

    timing["bucket"] = pd.cut(
        timing["minutes_to_reclaim"],
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=True
    )

    print("\nBucket counts:")
    print(timing["bucket"].value_counts().sort_index())

    print("\nMost recent 15 rows:")
    print(timing.tail(15).to_string(index=False))

    if SAVE_CSV:
        out_name = f"{TICKER}_polygon_{MULTIPLIER}{TIMESPAN}_ema{EMA_SPAN}_reclaim_{DAYS_BACK}d.csv"
        timing.to_csv(out_name, index=False)
        print(f"\nSaved CSV: {out_name}")

if __name__ == "__main__":
    main()
