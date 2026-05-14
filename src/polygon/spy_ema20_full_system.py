# pip install requests pandas numpy pytz python-dateutil

import os, time, requests

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from pathlib import Path


# ================= DATA CACHE =================
DATA_DIR = Path("data/polygon")
DATA_DIR.mkdir(parents=True, exist_ok=True)

def cache_path(ticker, year, month):
    return DATA_DIR / f"{ticker}_{MULTIPLIER}min_{year}-{month:02d}.csv"


# ================= CONFIG =================
from polygon_secrets import get_polygon_api_key

POLYGON_API_KEY = get_polygon_api_key()

TICKER = "SPY"
MULTIPLIER = 5
TIMESPAN = "minute"
EMA_SPAN = 20
TZ = "America/New_York"
RTH_START = "09:30"
RTH_END = "16:00"

# OPTION ASSUMPTIONS (simple but realistic)
PROFIT_TARGET = 0.20   # +20%
STOP_LOSS = -0.12      # -12%

# BACKTEST CONTROLS (high ROI)
ATR_PERIOD = 14
USE_ATR_RISK = True
STOP_ATR_MULT = 0.6        # recommended starting point
TARGET_ATR_MULT = 1.1      # 1.0–1.2 ATR is a common first pass
FORCE_EOD_EXIT = True      # if neither stop nor target hit, exit end-of-day
MAX_BARS_IN_TRADE = None   # set to an int (e.g. 12) to force time-based exit instead of EOD

# Reclaim candle strength filter (kills marginal/choppy reclaims)
USE_RECLAIM_ATR_BUFFER = True
RECLAIM_ATR_BUFFER_MULT = 0.10   # Close - EMA20 >= 0.1 * ATR
RECLAIM_BPS_BUFFER = 0.0002      # fallback buffer: Close > EMA20 * (1 + 2 bps)

# Grid search toggles
RUN_PCT_GRID_SEARCH = False
RUN_ATR_GRID_SEARCH = False

# Debug
DEBUG_LAST_N_TRADES = 0  # set to e.g. 10 to print post-entry hi/lo vs target/stop for last N trades

# =========================================
def fetch_polygon_month_cached(ticker, year, month):
    """
    Fetch one calendar month of Polygon data.
    Uses disk cache if available.
    """
    file_path = cache_path(ticker, year, month)

    # -------- LOAD FROM CACHE --------
    if file_path.exists():
        print(f"Loading cached data: {file_path.name}")
        df = pd.read_csv(file_path, parse_dates=["Datetime"])
        df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
        return df.set_index("Datetime")

    # -------- DOWNLOAD FROM POLYGON --------
    start_date = datetime(year, month, 1).date()
    end_date = (start_date + relativedelta(months=1))

    print(f"Downloading {ticker} {year}-{month:02d} from Polygon")

    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{MULTIPLIER}/{TIMESPAN}/{start_date}/{end_date}"
    r = requests.get(url, params={
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": POLYGON_API_KEY
    })

    if r.status_code == 429:
        time.sleep(15)
        return fetch_polygon_month_cached(ticker, year, month)

    r.raise_for_status()
    data = r.json()

    if "results" not in data:
        return pd.DataFrame()

    df = pd.DataFrame(data["results"])
    df["Datetime"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(columns={
        "o": "Open",
        "h": "High",
        "l": "Low",
        "c": "Close",
        "v": "Volume"
    })

    df = df[["Datetime", "Open", "High", "Low", "Close", "Volume"]]

    # -------- SAVE TO CACHE --------
    df.to_csv(file_path, index=False)
    print(f"Saved to cache: {file_path.name}")

    return df.set_index("Datetime")

def fetch_last_year_cached():
    end = datetime.now(timezone.utc).date()
    start = end - relativedelta(years=1)

    dfs = []
    current = start.replace(day=1)

    while current < end:
        year = current.year
        month = current.month

        df_month = fetch_polygon_month_cached(TICKER, year, month)
        if not df_month.empty:
            dfs.append(df_month)

        current = current + relativedelta(months=1)
        time.sleep(0.5)  # polite

    df = pd.concat(dfs).sort_index()
    # Polygon month ranges can overlap at boundaries (inclusive end), creating duplicate timestamps.
    # That breaks DatetimeIndex.get_loc(), which can return a slice for duplicate labels.
    df = df[~df.index.duplicated(keep="first")]
    return df

    end = datetime.now(timezone.utc).date()
    start = end - relativedelta(years=1)
    dfs = []
    cur = start
    while cur < end:
        nxt = min(cur + relativedelta(months=1), end)
        print(f"Downloading {cur} → {nxt}")
        d = fetch_polygon_month(cur, nxt)
        if not d.empty:
            dfs.append(d)
        cur = nxt
        time.sleep(1)
    return pd.concat(dfs).sort_index()

def simulate_underlying_trade(g, entry_time, target_pct=0.0020, stop_pct=0.0010):
    """
    Simulate a simple long trade on underlying after entry_time.
    target_pct: +0.20% move hits target
    stop_pct:   -0.10% move hits stop

    Returns: outcome ("target"/"stop"/"none"), exit_time
    """
    entry_px = g.loc[entry_time, "Close"]
    # Defensive: if duplicate timestamps exist, .loc can return a Series.
    if isinstance(entry_px, pd.Series):
        entry_px = float(entry_px.iloc[0])
    target_px = entry_px * (1 + target_pct)
    stop_px   = entry_px * (1 - stop_pct)

    after = g.loc[entry_time:]

    # Use High/Low to detect touches realistically
    hit_target = after["High"] >= target_px
    hit_stop   = after["Low"]  <= stop_px

    # find first hit in time order
    first_target_time = hit_target.idxmax() if hit_target.any() else None
    first_stop_time   = hit_stop.idxmax()   if hit_stop.any() else None

    if first_target_time and first_stop_time:
        if first_target_time <= first_stop_time:
            return "target", first_target_time
        else:
            return "stop", first_stop_time
    elif first_target_time:
        return "target", first_target_time
    elif first_stop_time:
        return "stop", first_stop_time
    else:
        return "none", None


def simulate_underlying_trade_risk(
    g,
    entry_time,
    *,
    mode="atr",
    target_pct=0.0020,
    stop_pct=0.0010,
    target_atr_mult=1.1,
    stop_atr_mult=0.6,
    atr_col="ATR14",
    force_eod_exit=True,
    max_bars=None,
):
    """
    Simulate a simple long trade on underlying after entry_time.

    mode:
      - "pct": target/stop are entry_px * (1 +/- pct)
      - "atr": target/stop are entry_px +/- (mult * ATR(entry_time))

    If neither target nor stop hits:
      - if max_bars is not None: exit on the last bar of that window ("time_exit")
      - elif force_eod_exit: exit on the last bar of the day ("eod")
      - else: outcome "none"

    Returns dict with:
      outcome, exit_time, entry_px, exit_px, pnl_return, pnl_r, target_px, stop_px
    """
    entry_px = g.loc[entry_time, "Close"]
    if isinstance(entry_px, pd.Series):
        entry_px = float(entry_px.iloc[0])
    else:
        entry_px = float(entry_px)

    atr_val = None
    if mode == "atr":
        atr_val = g.loc[entry_time, atr_col]
        if isinstance(atr_val, pd.Series):
            atr_val = float(atr_val.iloc[0])
        else:
            atr_val = float(atr_val)
        # If ATR isn't available for this bar, fall back to percent-based levels.
        if np.isnan(atr_val):
            mode = "pct"
            atr_val = np.nan

        target_px = entry_px + (target_atr_mult * atr_val)
        stop_px = entry_px - (stop_atr_mult * atr_val)
        risk_frac = (stop_atr_mult * atr_val) / entry_px
    else:
        target_px = entry_px * (1 + target_pct)
        stop_px = entry_px * (1 - stop_pct)
        risk_frac = stop_pct

    after = g.loc[entry_time:]
    if max_bars is not None:
        after = after.iloc[: max_bars + 1]

    hit_target = after["High"] >= target_px
    hit_stop = after["Low"] <= stop_px

    first_target_time = hit_target.idxmax() if hit_target.any() else None
    first_stop_time = hit_stop.idxmax() if hit_stop.any() else None

    # Stop/target hits use the exact level as the fill price.
    if first_target_time is not None and first_stop_time is not None:
        if first_target_time <= first_stop_time:
            exit_time = first_target_time
            exit_px = target_px
            outcome = "target"
        else:
            exit_time = first_stop_time
            exit_px = stop_px
            outcome = "stop"
    elif first_target_time is not None:
        exit_time = first_target_time
        exit_px = target_px
        outcome = "target"
    elif first_stop_time is not None:
        exit_time = first_stop_time
        exit_px = stop_px
        outcome = "stop"
    else:
        if max_bars is not None and len(after) > 0:
            exit_time = after.index[-1]
            exit_px = float(after.iloc[-1]["Close"])
            outcome = "time_exit"
        elif force_eod_exit and len(after) > 0:
            exit_time = after.index[-1]
            exit_px = float(after.iloc[-1]["Close"])
            outcome = "eod"
        else:
            return {
                "outcome": "none",
                "exit_time": None,
                "entry_px": entry_px,
                "exit_px": None,
                "pnl_return": 0.0,
                "pnl_r": 0.0,
                "atr": atr_val,
                "target_px": target_px,
                "stop_px": stop_px,
            }

    pnl_return = (exit_px - entry_px) / entry_px
    pnl_r = pnl_return / risk_frac if risk_frac and risk_frac > 0 else 0.0

    return {
        "outcome": outcome,
        "exit_time": exit_time,
        "entry_px": entry_px,
        "exit_px": exit_px,
        "pnl_return": pnl_return,
        "pnl_r": pnl_r,
        "atr": atr_val,
        "target_px": target_px,
        "stop_px": stop_px,
    }


# ================= ANALYSIS =================
df = fetch_last_year_cached()
df = df.tz_convert(TZ).between_time(RTH_START, RTH_END)

# Indicators
df["date"] = df.index.date
df["EMA20"] = df["Close"].ewm(span=EMA_SPAN, adjust=False).mean()

# Daily VWAP (resets each day)
df["pv"] = df["Close"] * df["Volume"]
df["cum_pv"] = df.groupby("date")["pv"].cumsum()
df["cum_vol"] = df.groupby("date")["Volume"].cumsum()
df["VWAP"] = df["cum_pv"] / df["cum_vol"]

# ATR(14) on 5-min bars (computed within each day to avoid overnight gap distortion)
df["prev_close"] = df.groupby("date")["Close"].shift(1)
tr_components = pd.concat(
    [
        (df["High"] - df["Low"]).abs().rename("tr1"),
        (df["High"] - df["prev_close"]).abs().rename("tr2"),
        (df["Low"] - df["prev_close"]).abs().rename("tr3"),
    ],
    axis=1,
)
df["TR"] = tr_components.max(axis=1)
df["ATR14"] = (
    df.groupby("date")["TR"]
    # Use min_periods=1 so ATR is defined early in the session and we don't produce lots of NaN "none" trades.
    .rolling(ATR_PERIOD, min_periods=1)
    .mean()
    .reset_index(level=0, drop=True)
)

results = []

for day, g in df.groupby("date"):
    close, ema = g["Close"], g["EMA20"]

    below = close < ema
    if not below.any():
        continue

    t0 = g.index[below.argmax()]
    after = g.loc[t0:]

    reclaim = after[after["Close"] > after["EMA20"]]
    if reclaim.empty:
        continue

    reclaim_time = reclaim.index[0]
    minutes = (reclaim_time - t0).total_seconds() / 60

    # ---- HOLD FILTER (3 candles) ----
    hold_ok = True
    reclaim_idx = g.index.get_loc(reclaim_time)
    # If the index label occurs multiple times, pandas returns a slice/bool mask/array.
    if isinstance(reclaim_idx, slice):
        reclaim_idx = reclaim_idx.start
    elif isinstance(reclaim_idx, np.ndarray):
        if reclaim_idx.dtype == bool:
            reclaim_idx = int(np.flatnonzero(reclaim_idx)[0])
        else:
            reclaim_idx = int(reclaim_idx[0])
    else:
        reclaim_idx = int(reclaim_idx)
    for i in range(1, 4):
        if reclaim_idx + i >= len(g):
            hold_ok = False
            break
        if g.iloc[reclaim_idx + i]["Close"] <= g.iloc[reclaim_idx + i]["EMA20"]:
            hold_ok = False
            break

    # ---- VWAP FILTER ----
    vwap_ok = g.loc[reclaim_time]["Close"] >= g.loc[reclaim_time]["VWAP"]

    # ---- RECLAIM CANDLE STRENGTH FILTER ----
    reclaim_close = g.loc[reclaim_time, "Close"]
    reclaim_ema = g.loc[reclaim_time, "EMA20"]
    reclaim_atr = g.loc[reclaim_time, "ATR14"]
    if isinstance(reclaim_close, pd.Series):
        reclaim_close = float(reclaim_close.iloc[0])
    else:
        reclaim_close = float(reclaim_close)
    if isinstance(reclaim_ema, pd.Series):
        reclaim_ema = float(reclaim_ema.iloc[0])
    else:
        reclaim_ema = float(reclaim_ema)
    if isinstance(reclaim_atr, pd.Series):
        reclaim_atr = float(reclaim_atr.iloc[0])
    else:
        reclaim_atr = float(reclaim_atr)

    if USE_RECLAIM_ATR_BUFFER and not np.isnan(reclaim_atr):
        reclaim_strength_ok = (reclaim_close - reclaim_ema) >= (RECLAIM_ATR_BUFFER_MULT * reclaim_atr)
    else:
        reclaim_strength_ok = reclaim_close > (reclaim_ema * (1 + RECLAIM_BPS_BUFFER))

    # ---- DAY CLASSIFIER ----
    if minutes <= 30:
        regime = "FAST"
    elif minutes <= 60:
        regime = "NEUTRAL"
    else:
        regime = "TREND"

    # ---- ENTRY GATING + UNDERLYING OUTCOME SIM ----
    trade_taken = (regime == "FAST") and hold_ok and vwap_ok and reclaim_strength_ok

    outcome = "no_trade"
    pnl_r = 0.0
    pnl_return = 0.0
    entry_px = np.nan
    exit_px = np.nan
    exit_time = None
    target_px = np.nan
    stop_px = np.nan

    if trade_taken:
        sim = simulate_underlying_trade_risk(
            g,
            reclaim_time,
            mode="atr" if USE_ATR_RISK else "pct",
            target_pct=0.0020,
            stop_pct=0.0010,
            target_atr_mult=TARGET_ATR_MULT,
            stop_atr_mult=STOP_ATR_MULT,
            force_eod_exit=FORCE_EOD_EXIT,
            max_bars=MAX_BARS_IN_TRADE,
        )
        outcome = sim["outcome"]
        exit_time = sim["exit_time"]
        entry_px = sim["entry_px"]
        exit_px = sim["exit_px"] if sim["exit_px"] is not None else np.nan
        pnl_return = sim["pnl_return"]
        pnl_r = sim["pnl_r"]
        target_px = sim["target_px"]
        stop_px = sim["stop_px"]

    results.append({
        "date": day,
        "minutes_to_reclaim": minutes,
        "regime": regime,
        "hold_ok": hold_ok,
        "vwap_ok": vwap_ok,
        "reclaim_strength_ok": reclaim_strength_ok,
        "trade_taken": trade_taken,
        "outcome": outcome,
        "entry_time": reclaim_time,
        "exit_time": exit_time,
        "entry_px": entry_px,
        "exit_px": exit_px,
        "target_px": target_px,
        "stop_px": stop_px,
        "atr14": reclaim_atr,
        "pnl_return": pnl_return,
        "pnl_r": pnl_r,
    })

res = pd.DataFrame(results)

# ================= OUTPUT =================
print("\n=== FULL SYSTEM RESULTS (1 YEAR) ===")
print(res["regime"].value_counts())

trades = res[res["trade_taken"]]
print("\nTrades taken:", len(trades))
print("Win rate (pnl_r > 0):", round((trades["pnl_r"] > 0).mean() * 100, 2), "%")
print("Avg pnl (R):", round(trades["pnl_r"].mean(), 3))
print(trades["outcome"].value_counts())

if DEBUG_LAST_N_TRADES and len(trades) > 0:
    sample = trades.tail(int(DEBUG_LAST_N_TRADES))
    print(f"\n=== DEBUG: last {len(sample)} trades (post-entry hi/lo vs target/stop) ===")
    for _, r in sample.iterrows():
        gday = df[df["date"] == r["date"]]
        after = gday.loc[r["entry_time"]:]
        hi = float(after["High"].max()) if len(after) else np.nan
        lo = float(after["Low"].min()) if len(after) else np.nan
        print(
            r["date"],
            "entry", round(float(r["entry_px"]), 4),
            "target", round(float(r["target_px"]), 4),
            "stop", round(float(r["stop_px"]), 4),
            "post-entry hi", round(hi, 4),
            "lo", round(lo, 4),
            "outcome", r["outcome"],
        )

if RUN_PCT_GRID_SEARCH and len(trades) > 0:
    stop_grid = [0.0008, 0.0010, 0.0012, 0.0015, 0.0020]
    target_grid = [0.0015, 0.0020, 0.0025, 0.0030, 0.0040]

    day_groups = {d: gg for d, gg in df.groupby("date")}
    grid_rows = []

    for sp in stop_grid:
        for tp in target_grid:
            pnls = []
            outcomes = []
            for _, tr in trades.iterrows():
                gg = day_groups.get(tr["date"])
                if gg is None:
                    continue
                sim = simulate_underlying_trade_risk(
                    gg,
                    tr["entry_time"],
                    mode="pct",
                    target_pct=tp,
                    stop_pct=sp,
                    force_eod_exit=True,
                    max_bars=None,
                )
                pnls.append(sim["pnl_r"])
                outcomes.append(sim["outcome"])

            if not pnls:
                continue
            out = pd.Series(outcomes).value_counts()
            grid_rows.append(
                {
                    "stop_pct": sp,
                    "target_pct": tp,
                    "trades": len(pnls),
                    "avg_R": float(np.mean(pnls)),
                    "win_rate": float(np.mean(np.array(pnls) > 0)),
                    "target_hits": int(out.get("target", 0)),
                    "stop_hits": int(out.get("stop", 0)),
                    "eod_or_time_exit": int(out.get("eod", 0) + out.get("time_exit", 0)),
                }
            )

    grid = pd.DataFrame(grid_rows).sort_values(["avg_R", "trades"], ascending=[False, False])
    print("\n=== GRID SEARCH (PCT) — top 15 by Avg R ===")
    print(grid.head(15).to_string(index=False))

if RUN_ATR_GRID_SEARCH and len(trades) > 0:
    stop_mult_grid = [0.6, 0.8, 1.0]
    target_mult_grid = [1.0, 1.2, 1.4]

    day_groups = {d: gg for d, gg in df.groupby("date")}
    grid_rows = []

    for sm in stop_mult_grid:
        for tm in target_mult_grid:
            pnls = []
            outcomes = []
            for _, tr in trades.iterrows():
                gg = day_groups.get(tr["date"])
                if gg is None:
                    continue
                sim = simulate_underlying_trade_risk(
                    gg,
                    tr["entry_time"],
                    mode="atr",
                    stop_atr_mult=sm,
                    target_atr_mult=tm,
                    force_eod_exit=True,
                    max_bars=None,
                )
                pnls.append(sim["pnl_r"])
                outcomes.append(sim["outcome"])

            if not pnls:
                continue
            out = pd.Series(outcomes).value_counts()
            grid_rows.append(
                {
                    "stop_atr_mult": sm,
                    "target_atr_mult": tm,
                    "trades": len(pnls),
                    "avg_R": float(np.mean(pnls)),
                    "win_rate": float(np.mean(np.array(pnls) > 0)),
                    "target_hits": int(out.get("target", 0)),
                    "stop_hits": int(out.get("stop", 0)),
                    "eod_or_time_exit": int(out.get("eod", 0) + out.get("time_exit", 0)),
                }
            )

    grid = pd.DataFrame(grid_rows).sort_values(["avg_R", "trades"], ascending=[False, False])
    print("\n=== GRID SEARCH (ATR) — top 15 by Avg R ===")
    print(grid.head(15).to_string(index=False))

res.to_csv("SPY_FULL_EMA20_SYSTEM_1Y.csv", index=False)
print("\nSaved: SPY_FULL_EMA20_SYSTEM_1Y.csv")
