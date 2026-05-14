# pip install requests pandas numpy pytz

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta

# ============================
# CONFIG
# ============================
from polygon_secrets import get_polygon_api_key

POLYGON_API_KEY = get_polygon_api_key()

TICKER = "SPY"
MULTIPLIER = 5
TIMESPAN = "minute"
EMA_SPAN = 20
TZ = "America/New_York"
RTH_START = "09:30"
RTH_END = "16:00"
SAVE_CSV = True

# ============================
# POLYGON FETCH (MONTHLY)
# ============================
def fetch_polygon_month(ticker, start_date, end_date):
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{MULTIPLIER}/{TIMESPAN}/{start_date}/{end_date}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": POLYGON_API_KEY,
    }

    r = requests.get(url, params=params, timeout=30)
    if r.status_code == 429:
        time.sleep(15)
        return fetch_polygon_month(ticker, start_date, end_date)

    r.raise_for_status()
    data = r.json()

    if "results" not in data:
        return pd.DataFrame()

    df = pd.DataFrame(data["results"])
    df = df.rename(columns={
        "t": "timestamp",
        "o": "Open",
        "h": "High",
        "l": "Low",
        "c": "Close",
        "v": "Volume"
    })

    df["Datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("Datetime")
    return df[["Open", "High", "Low", "Close", "Volume"]]

def fetch_last_year():
    end = datetime.now(timezone.utc).date()
    start = end - relativedelta(years=1)

    dfs = []
    current = start

    while current < end:
        month_end = min(current + relativedelta(months=1), end)
        print(f"Downloading {current} → {month_end}")
        df_month = fetch_polygon_month(TICKER, current, month_end)
        if not df_month.empty:
            dfs.append(df_month)
        current = month_end
        time.sleep(1)  # be polite to Polygon

    return pd.concat(dfs).sort_index()

# ============================
# ANALYSIS
# ============================
def time_to_reclaim_minutes(group):
    close = group["Close"]
    ema = group["EMA20"]

    below = close < ema
    if not below.any():
        return None

    t0 = group.index[below.argmax()]
    after = group.loc[t0:]
    reclaim = after[after["Close"] > after["EMA20"]]
    if reclaim.empty:
        return None

    return (reclaim.index[0] - t0).total_seconds() / 60

# ============================
# MAIN
# ============================
df = fetch_last_year()

df = df.tz_convert(TZ)
df = df.between_time(RTH_START, RTH_END)

df["EMA20"] = df["Close"].ewm(span=EMA_SPAN, adjust=False).mean()
df["date"] = df.index.date

timing = (
    df.groupby("date")
      .apply(time_to_reclaim_minutes)
      .dropna()
      .reset_index(name="minutes_to_reclaim")
)

print("\n=== SPY EMA20 RECLAIM – LAST 1 YEAR ===")
print("Days measured:", len(timing))
print("Average minutes:", round(timing["minutes_to_reclaim"].mean(), 2))
print("Median minutes:", round(timing["minutes_to_reclaim"].median(), 2))
print("Min:", timing["minutes_to_reclaim"].min())
print("Max:", timing["minutes_to_reclaim"].max())

bins = [0, 15, 30, 60, 120, 10000]
labels = ["≤15m", "15–30m", "30–60m", "60–120m", "120m+"]

timing["bucket"] = pd.cut(timing["minutes_to_reclaim"], bins=bins, labels=labels)
print("\nBucket counts:")
print(timing["bucket"].value_counts().sort_index())

if SAVE_CSV:
    timing.to_csv("SPY_ema20_reclaim_1year.csv", index=False)
    print("\nSaved: SPY_ema20_reclaim_1year.csv")
