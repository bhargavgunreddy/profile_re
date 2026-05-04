import sys
import yfinance as yf
import pandas as pd
import numpy as np
import pytz
from datetime import timedelta, datetime
import os
import requests
try:
    from dateutil.relativedelta import relativedelta
except ImportError:
    # Fallback if dateutil not installed
    def relativedelta(years=0):
        class Delta:
            def __init__(self, years):
                self.years = years
        return Delta(years)
    
    # Simple implementation
    def add_years(date, years):
        try:
            return date.replace(year=date.year + years)
        except ValueError:
            # Handle leap year edge case
            return date.replace(year=date.year + years, day=28)

# ==========================
# CONFIG
# ==========================
SYMBOL = "SPY"
INTERVAL = "15m"        # ✅ CORRECT INTERVAL
EASTERN = pytz.timezone("US/Eastern")

# Polygon.io API key (optional - get free key at https://polygon.io)
# Set as environment variable: export POLYGON_API_KEY="your_key_here"
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", None)

# Reversal probability table (updated from 501 trading days of historical data)
REVERSAL_TABLE = {
    # Bear -> Bull reversal probabilities
    ("Bear", "Below VWAP", "Small"): 0.5600,   # Bear->Bull, Below VWAP, Small gap
    ("Bear", "Below VWAP", "Medium"): 0.4925,  # Bear->Bull, Below VWAP, Medium gap
    ("Bear", "Below VWAP", "Large"): 0.5660,   # Bear->Bull, Below VWAP, Large gap
    ("Bear", "Above VWAP", "Small"): 0.5385,   # Bear->Bull, Above VWAP, Small gap
    ("Bear", "Above VWAP", "Medium"): 0.5000,  # Bear->Bull, Above VWAP, Medium gap
    ("Bear", "Above VWAP", "Large"): 0.5833,   # Bear->Bull, Above VWAP, Large gap
    # Bull -> Bear reversal probabilities
    ("Bull", "Above VWAP", "Small"): 0.4952,   # Bull->Bear, Above VWAP, Small gap
    ("Bull", "Above VWAP", "Medium"): 0.4429, # Bull->Bear, Above VWAP, Medium gap
    ("Bull", "Above VWAP", "Large"): 0.4091,   # Bull->Bear, Above VWAP, Large gap
    ("Bull", "Below VWAP", "Small"): 0.0000,   # Bull->Bear, Below VWAP, Small gap (limited data)
    ("Bull", "Below VWAP", "Medium"): 0.3000, # Bull->Bear, Below VWAP, Medium gap
    ("Bull", "Below VWAP", "Large"): 0.0000,   # Bull->Bear, Below VWAP, Large gap (limited data)
}

# ==========================
# DYNAMIC (RECOMPUTED) PROBABILITY TABLE (RECOMMENDED)
# ==========================
# We learn a *directional* probability from your cached dataset and trade only "high-confidence" cells.
#
# Interpretation:
# - down_prob = P(Close_9:45 < Close_9:30) grouped by:
#       (Dir_930, VWAP_Pos_at_930, GapBucket)
#
# Trade mapping:
# - If down_prob is high -> trade PUT (bet down)
# - If down_prob is low  -> trade CALL (bet up)
# - Otherwise -> SKIP
#
# High-confidence filters:
MIN_CELL_COUNT = 50
DOWN_PROB_HIGH = 0.55
DOWN_PROB_LOW = 0.45

# Which horizon to *learn* probability for and base the trade decision on.
# Valid: "945", "1030", "CLOSE"
DECISION_TARGET = "1030"

ACTIVE_DOWN_TABLE = {}   # (dir_930, vwap_pos, gap_bucket) -> down_prob
ACTIVE_DOWN_COUNTS = {}  # (dir_930, vwap_pos, gap_bucket) -> n

# If enabled, probabilities are computed walk-forward (no lookahead) during the backtest loop.
WALK_FORWARD_MODE = True

# ==========================
# OPTIONS PROXY (EV MODEL) — 0DTE
# ==========================
# Simple EV-style proxy for 0DTE option trades:
# - Approximate an "ATM-ish" 0DTE contract premium as a small % of underlying.
# - Use p_win (derived from walk-forward probabilities) to compute expected value:
#     EV = premium * (p_win * WIN_PCT + (1-p_win) * LOSS_PCT) * 100 - fees
# - For realized PnL proxy, use WIN/LOSS outcome and apply the same pct move on premium.
#
# IMPORTANT: these are *assumptions* (not an option pricing model). Tune to your fills.
OPT_PREMIUM_PCT = 0.0030         # base premium ≈ 0.30% of underlying (SPY 680 -> ~$2.04)
OPT_PREMIUM_MIN = 1.50           # your typical fill range
OPT_PREMIUM_MAX = 3.00
OPT_CONTRACT_MULT = 100
OPT_FEES_ROUNDTRIP = 2.00        # $ per contract round trip

# TP/SL assumptions from your actual rules (apply consistently across horizons)
OPT_WIN_PCT_945 = 0.50
OPT_LOSS_PCT_945 = -0.35
OPT_WIN_PCT_1030 = 0.50
OPT_LOSS_PCT_1030 = -0.35
OPT_WIN_PCT_CLOSE = 0.50
OPT_LOSS_PCT_CLOSE = -0.35


def option_premium_proxy(underlying_price: float) -> float:
    prem = float(underlying_price) * OPT_PREMIUM_PCT
    prem = max(OPT_PREMIUM_MIN, prem)
    prem = min(OPT_PREMIUM_MAX, prem)
    return prem


def option_ev_proxy(p_win: float, premium: float, win_pct: float, loss_pct: float) -> float:
    # EV in dollars per contract
    expected_pct = (p_win * win_pct) + ((1.0 - p_win) * loss_pct)
    return (premium * expected_pct * OPT_CONTRACT_MULT) - OPT_FEES_ROUNDTRIP


def option_realized_pnl_proxy(result: str, premium: float, win_pct: float, loss_pct: float) -> float:
    if result == "WIN":
        return (premium * win_pct * OPT_CONTRACT_MULT) - OPT_FEES_ROUNDTRIP
    if result == "LOSS":
        return (premium * loss_pct * OPT_CONTRACT_MULT) - OPT_FEES_ROUNDTRIP
    return 0.0


def _direction(o: float, c: float) -> str:
    if c > o:
        return "Bull"
    if c < o:
        return "Bear"
    return "Doji"


def _gap_bucket_simple(g: float) -> str:
    if abs(g) < 0.2:
        return "Small"
    if abs(g) < 0.5:
        return "Medium"
    return "Large"


def compute_reversal_table_from_cached_intraday(spy_all: pd.DataFrame, export_csv: str = "SPY_DOWN_TABLE_FROM_CACHE.csv"):
    """
    Build (probability, count) table from cached intraday data.

    down_prob = P(Close_target < Close_9:30) grouped by:
      (Dir_930, VWAP_Pos_at_930, GapBucket)
    """
    df = spy_all.copy()
    df = df.sort_values("Timestamp")
    if "Date" not in df.columns:
        df["Date"] = df["Timestamp"].dt.date
    if "Time" not in df.columns:
        df["Time"] = df["Timestamp"].dt.time

    # VWAP per day
    df["Typical"] = (df["High"] + df["Low"] + df["Close"]) / 3
    df["TPV"] = df["Typical"] * df["Volume"]
    df["CumTPV"] = df.groupby("Date")["TPV"].cumsum()
    df["CumVol"] = df.groupby("Date")["Volume"].cumsum()
    df["VWAP"] = df["CumTPV"] / df["CumVol"]

    t930 = pd.to_datetime("09:30").time()
    t945 = pd.to_datetime("09:45").time()
    t1545 = pd.to_datetime("15:45").time()

    b930 = df[df["Time"] == t930][["Date", "Open", "Close", "VWAP"]].copy()
    b945 = df[df["Time"] == t945][["Date", "Open", "Close"]].copy()

    # Regular close from 15:45 bar close
    close_1545 = df[df["Time"] == t1545][["Date", "Close"]].copy()
    close_1545 = close_1545.rename(columns={"Close": "Close_1545"})

    # Merge daily features
    daily = b930.merge(b945, on="Date", how="inner", suffixes=("_930", "_945"))
    daily = daily.merge(close_1545, on="Date", how="left")
    daily = daily.sort_values("Date").reset_index(drop=True)

    # If some days are missing 15:45 close (early close), fall back to last bar <= 16:00
    missing_close = daily["Close_1545"].isna()
    if missing_close.any():
        close_fallback = (
            df[df["Timestamp"].dt.time <= pd.to_datetime("16:00").time()]
            .sort_values("Timestamp")
            .groupby("Date")
            .tail(1)[["Date", "Close"]]
            .rename(columns={"Close": "Close_fallback"})
        )
        daily = daily.merge(close_fallback, on="Date", how="left")
        daily["Close_1545"] = daily["Close_1545"].fillna(daily["Close_fallback"])
        daily = daily.drop(columns=["Close_fallback"])

    # Prev close and gap%
    daily["Prev_Close"] = daily["Close_1545"].shift(1)
    daily["Gap_pct"] = (daily["Open_930"] - daily["Prev_Close"]) / daily["Prev_Close"] * 100.0
    daily = daily.dropna(subset=["Gap_pct", "VWAP"])

    daily["Dir_930"] = daily.apply(lambda x: _direction(float(x["Open_930"]), float(x["Close_930"])), axis=1)

    # Drop doji-at-9:30 days (no directional edge in feature)
    daily = daily[daily["Dir_930"].isin(["Bull", "Bear"])].copy()

    daily["VWAP_Pos"] = np.where(daily["Close_930"] > daily["VWAP"], "Above VWAP", "Below VWAP")
    daily["Gap_Bucket"] = daily["Gap_pct"].apply(_gap_bucket_simple)

    # Directional target: down at chosen horizon vs 9:30 close
    target_col = {"945": "Close_945", "1030": "Close_1030", "CLOSE": "Close_1545"}.get(DECISION_TARGET, "Close_945")
    daily["Delta_Target"] = daily[target_col] - daily["Close_930"]
    daily = daily.dropna(subset=["Delta_Target"]).copy()
    daily = daily[daily["Delta_Target"] != 0].copy()
    daily["Down_Target"] = np.where(daily["Delta_Target"] < 0, 1.0, 0.0)

    grouped = (
        daily.groupby(["Dir_930", "VWAP_Pos", "Gap_Bucket"])
        .agg(
            Down_Prob=("Down_Target", "mean"),
            Count=("Down_Target", "size"),
        )
        .reset_index()
        .sort_values(["Dir_930", "VWAP_Pos", "Gap_Bucket"])
    )

    grouped.to_csv(export_csv, index=False)
    table = {
        (r["Dir_930"], r["VWAP_Pos"], r["Gap_Bucket"]): float(r["Down_Prob"])
        for _, r in grouped.iterrows()
    }
    counts = {
        (r["Dir_930"], r["VWAP_Pos"], r["Gap_Bucket"]): int(r["Count"])
        for _, r in grouped.iterrows()
    }
    print(f"🧠 Recomputed down-prob table from cache -> {export_csv} (rows={len(grouped)})")
    return table, counts


def _build_daily_feature_df_from_intraday(spy_all: pd.DataFrame) -> pd.DataFrame:
    """
    Build a per-day feature table from intraday bars.
    Includes the columns needed for both training and evaluation.
    """
    df = spy_all.copy().sort_values("Timestamp")
    if "Date" not in df.columns:
        df["Date"] = df["Timestamp"].dt.date
    if "Time" not in df.columns:
        df["Time"] = df["Timestamp"].dt.time

    # VWAP per day
    df["Typical"] = (df["High"] + df["Low"] + df["Close"]) / 3
    df["TPV"] = df["Typical"] * df["Volume"]
    df["CumTPV"] = df.groupby("Date")["TPV"].cumsum()
    df["CumVol"] = df.groupby("Date")["Volume"].cumsum()
    df["VWAP"] = df["CumTPV"] / df["CumVol"]

    t930 = pd.to_datetime("09:30").time()
    t945 = pd.to_datetime("09:45").time()
    t1030 = pd.to_datetime("10:30").time()
    t1545 = pd.to_datetime("15:45").time()

    b930 = df[df["Time"] == t930][["Date", "Open", "Close", "VWAP"]].rename(
        columns={"Open": "Open_930", "Close": "Close_930", "VWAP": "VWAP_930"}
    )
    b945 = df[df["Time"] == t945][["Date", "Open", "Close"]].rename(
        columns={"Open": "Open_945", "Close": "Close_945"}
    )
    b1030 = df[df["Time"] == t1030][["Date", "Close"]].rename(columns={"Close": "Close_1030"})

    # Regular close: 15:45 bar close (fallback to last <= 16:00)
    c1545 = df[df["Time"] == t1545][["Date", "Close"]].rename(columns={"Close": "Close_1545"})
    c_fallback = (
        df[df["Timestamp"].dt.time <= pd.to_datetime("16:00").time()]
        .sort_values("Timestamp")
        .groupby("Date")
        .tail(1)[["Date", "Close"]]
        .rename(columns={"Close": "Close_1545_fallback"})
    )

    daily = b930.merge(b945, on="Date", how="inner")
    daily = daily.merge(b1030, on="Date", how="left")
    daily = daily.merge(c1545, on="Date", how="left").merge(c_fallback, on="Date", how="left")
    daily["Close_1545"] = daily["Close_1545"].fillna(daily["Close_1545_fallback"])
    daily = daily.drop(columns=["Close_1545_fallback"])

    daily = daily.sort_values("Date").reset_index(drop=True)
    daily["Prev_Close"] = daily["Close_1545"].shift(1)
    daily["Gap_pct"] = (daily["Open_930"] - daily["Prev_Close"]) / daily["Prev_Close"] * 100.0
    daily = daily.dropna(subset=["Gap_pct", "VWAP_930", "Close_1545"])

    daily["Dir_930"] = daily.apply(lambda x: _direction(float(x["Open_930"]), float(x["Close_930"])), axis=1)
    daily = daily[daily["Dir_930"].isin(["Bull", "Bear"])].copy()

    daily["VWAP_Pos"] = np.where(daily["Close_930"] > daily["VWAP_930"], "Above VWAP", "Below VWAP")
    daily["Gap_Bucket"] = daily["Gap_pct"].apply(_gap_bucket_simple)
    # Targets vs 9:30 close (NaN for missing bars or exact flats)
    def _mk_down(close_col: str, out_col: str):
        delta = daily[close_col] - daily["Close_930"]
        daily[out_col] = np.where(delta < 0, 1, np.where(delta > 0, 0, np.nan))

    _mk_down("Close_945", "Down_945")
    _mk_down("Close_1030", "Down_1030")
    _mk_down("Close_1545", "Down_Close")

    return daily


def walk_forward_backtest(spy_all: pd.DataFrame, eval_start=None, eval_end=None):
    """
    Walk-forward backtest:
    - For each day D, compute probability for that day using ONLY days < D.
    - Make trade decision for D.
    - After evaluation, update counts with day D outcome (so D affects future days only).
    """
    daily = _build_daily_feature_df_from_intraday(spy_all)
    target_down_col = {"945": "Down_945", "1030": "Down_1030", "CLOSE": "Down_Close"}.get(DECISION_TARGET, "Down_945")
    daily = daily.dropna(subset=[target_down_col]).copy()
    if eval_start is not None:
        eval_start = pd.to_datetime(eval_start).date()
    if eval_end is not None:
        eval_end = pd.to_datetime(eval_end).date()

    # incremental counts
    total = {}     # key -> n
    down = {}      # key -> sum(down_945)

    results = []
    for _, row in daily.iterrows():
        d = row["Date"]
        key = (row["Dir_930"], row["VWAP_Pos"], row["Gap_Bucket"])

        n = total.get(key, 0)
        p_down = (down.get(key, 0) / n) if n > 0 else None

        # decide trade using only history
        decision_mode = "SKIP"  # SKIP | UP | DOWN
        if n < MIN_CELL_COUNT or p_down is None:
            trade = "SKIP"
        else:
            if p_down >= DOWN_PROB_HIGH:
                trade = "PUT"
                decision_mode = "DOWN"
            elif p_down <= DOWN_PROB_LOW:
                trade = "CALL"
                decision_mode = "UP"
            else:
                trade = "SKIP"

        # only emit results within requested evaluation window
        in_window = True
        if eval_start and d < eval_start:
            in_window = False
        if eval_end and d > eval_end:
            in_window = False

        if in_window:
            entry = float(row["Close_930"])
            prem = option_premium_proxy(entry)

            # Probability that THIS trade wins, based on p_down = P(down move by 9:45)
            # - PUT wins when down  -> p_win = p_down
            # - CALL wins when up   -> p_win = 1 - p_down
            if trade == "SKIP" or p_down is None:
                p_win = None
            else:
                p_win = float(p_down) if trade == "PUT" else float(1.0 - p_down)

            def eval_exit(exit_price):
                if pd.isna(exit_price):
                    return None, None, None, None
                exit_price = float(exit_price)
                if trade == "CALL":
                    chg = exit_price - entry
                    res = "WIN" if chg > 0 else "LOSS"
                elif trade == "PUT":
                    chg = entry - exit_price
                    res = "WIN" if chg > 0 else "LOSS"
                else:
                    chg = exit_price - entry
                    res = "N/A"
                return exit_price, chg, (chg / entry) * 100.0, res

            ex945, chg945, pct945, res945 = eval_exit(row["Close_945"])
            ex1030, chg1030, pct1030, res1030 = eval_exit(row.get("Close_1030"))
            exclose, chgclose, pctclose, resclose = eval_exit(row.get("Close_1545"))

            # Options proxy EV + realized PnL
            if p_win is None:
                ev_945 = ev_1030 = ev_close = None
            else:
                ev_945 = option_ev_proxy(p_win, prem, OPT_WIN_PCT_945, OPT_LOSS_PCT_945)
                ev_1030 = option_ev_proxy(p_win, prem, OPT_WIN_PCT_1030, OPT_LOSS_PCT_1030)
                ev_close = option_ev_proxy(p_win, prem, OPT_WIN_PCT_CLOSE, OPT_LOSS_PCT_CLOSE)

            opt_pnl_945 = option_realized_pnl_proxy(res945, prem, OPT_WIN_PCT_945, OPT_LOSS_PCT_945) if trade != "SKIP" else 0.0
            opt_pnl_1030 = option_realized_pnl_proxy(res1030, prem, OPT_WIN_PCT_1030, OPT_LOSS_PCT_1030) if trade != "SKIP" else 0.0
            opt_pnl_close = option_realized_pnl_proxy(resclose, prem, OPT_WIN_PCT_CLOSE, OPT_LOSS_PCT_CLOSE) if trade != "SKIP" else 0.0

            results.append({
                "Date": pd.to_datetime(d).strftime("%Y-%m-%d"),
                "9:30 Open": float(row["Open_930"]),
                "9:30 Close": float(row["Close_930"]),
                "9:45 Close": float(row["Close_945"]),
                "10:30 Close": None if pd.isna(row.get("Close_1030")) else float(row["Close_1030"]),
                "Market Close": float(row["Close_1545"]),
                "Gap %": float(row["Gap_pct"]),
                "Gap Bucket": row["Gap_Bucket"],
                "Direction": row["Dir_930"],
                "VWAP Pos": row["VWAP_Pos"],
                "Down Prob": 0.0 if p_down is None else float(p_down),
                "Cell Count": int(n),
                "Trade": trade,
                "Decision Mode": decision_mode,
                "Decision Target": DECISION_TARGET,
                "Entry": entry,
                "Exit_945": ex945,
                "Price Chg_945": chg945,
                "Price Chg %_945": pct945,
                "Result_945": res945,
                "Exit_1030": ex1030,
                "Price Chg_1030": chg1030,
                "Price Chg %_1030": pct1030,
                "Result_1030": res1030,
                "Exit_Close": exclose,
                "Price Chg_Close": chgclose,
                "Price Chg %_Close": pctclose,
                "Result_Close": resclose,

                # Options proxy columns
                "Opt Prem": prem if trade != "SKIP" else None,
                "p_win": p_win,
                "Opt EV_945": ev_945,
                "Opt PnL_945": opt_pnl_945,
                "Opt EV_1030": ev_1030,
                "Opt PnL_1030": opt_pnl_1030,
                "Opt EV_Close": ev_close,
                "Opt PnL_Close": opt_pnl_close,
            })

        # update counts AFTER processing current day
        total[key] = n + 1
        down[key] = down.get(key, 0) + int(row[target_down_col])

    learned = []
    for key, n in sorted(total.items(), key=lambda x: (-x[1], x[0])):
        learned.append({
            "Dir_930": key[0],
            "VWAP_Pos": key[1],
            "Gap_Bucket": key[2],
            "Down_Prob": (down.get(key, 0) / n) if n else 0.0,
            "Count": n
        })
    learned_df = pd.DataFrame(learned)
    return pd.DataFrame(results), learned_df


# NOTE: day-type / opening-range classifier must be computed inside `analyze_date`.
def download_polygon_data(symbol, start_date, end_date, interval="15", missing_dates=None):
    """
    Download historical intraday data from Polygon.io
    Free tier: 5 API calls per minute, 2 years of historical data
    Saves each day's data as a CSV file for future use
    
    Parameters:
    -----------
    missing_dates : list, optional
        List of date strings (YYYY-MM-DD) to download. If None, downloads all dates in range.
    """
    if not POLYGON_API_KEY:
        return None
    
    # Convert dates to strings
    start_str = pd.to_datetime(start_date).strftime("%Y-%m-%d")
    end_str = pd.to_datetime(end_date).strftime("%Y-%m-%d")
    
    # Polygon.io uses different ticker format (SPY -> SPY)
    ticker = symbol
    
    # Create directory for storing CSV files
    data_dir = f"{symbol}_{interval}m_data"
    os.makedirs(data_dir, exist_ok=True)
    print(f"📁 Saving daily data to: {data_dir}/")
    
    # Download data day by day (Polygon free tier limit)
    all_data = []
    
    # If missing_dates is provided, only download those dates
    if missing_dates:
        dates_to_download = [pd.to_datetime(d).date() for d in missing_dates]
        print(f"Downloading {len(dates_to_download)} missing dates from Polygon.io...")
    else:
        # Download all dates in range
        current_date = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        dates_to_download = []
        while current_date <= end_dt:
            # Skip weekends
            if current_date.weekday() < 5:
                dates_to_download.append(current_date.date())
            current_date += timedelta(days=1)
        print(f"Downloading from Polygon.io (this may take a while for {len(dates_to_download)} days)...")
    
    for date_obj in dates_to_download:
        date_str = date_obj.strftime("%Y-%m-%d")
        
        # Check if CSV file already exists
        csv_filename = f"{data_dir}/{symbol}_{interval}m_{date_str}.csv"
        if os.path.exists(csv_filename):
            # Load from CSV instead of downloading
            try:
                df_day = pd.read_csv(csv_filename)
                df_day["Timestamp"] = pd.to_datetime(df_day["Timestamp"])
                if df_day["Timestamp"].dt.tz is None:
                    df_day["Timestamp"] = df_day["Timestamp"].dt.tz_localize("UTC")
                df_day["Timestamp"] = df_day["Timestamp"].dt.tz_convert(EASTERN)
                all_data.append(df_day)
                print(f"  Loaded from cache: {date_str} ({len(df_day)} bars)", end="\r")
                continue
            except Exception as e:
                print(f"\n  Error loading {date_str} from cache: {str(e)}, re-downloading...", end="\r")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{interval}/minute/{date_str}/{date_str}"
        params = {
            "adjusted": "true",
            "sort": "asc",
            "limit": 50000,
            "apiKey": POLYGON_API_KEY
        }
        
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("resultsCount", 0) > 0:
                    df = pd.DataFrame(data["results"])
                    df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
                    
                    # Rename columns to match yfinance format
                    df.rename(columns={
                        "timestamp": "Timestamp",
                        "o": "Open",
                        "h": "High",
                        "l": "Low",
                        "c": "Close",
                        "v": "Volume"
                    }, inplace=True)
                    
                    # Select only needed columns
                    df = df[["Timestamp", "Open", "High", "Low", "Close", "Volume"]].copy()
                    
                    # Convert timezone
                    if df["Timestamp"].dt.tz is None:
                        df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC")
                    df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)
                    
                    # Save to CSV
                    df.to_csv(csv_filename, index=False)
                    
                    all_data.append(df)
                    print(f"  Downloaded & saved {date_str} ({data['resultsCount']} bars) -> {csv_filename}", end="\r")
                else:
                    print(f"  No data for {date_str} (market holiday?)", end="\r")
            elif response.status_code == 429:
                print(f"\n  Rate limit hit, waiting 60 seconds...")
                import time
                time.sleep(60)
                continue
            else:
                print(f"\n  Error for {date_str}: {response.status_code}", end="\r")
        except Exception as e:
            print(f"\n  Error downloading {date_str}: {str(e)}", end="\r")
        
        # Rate limiting: free tier allows 5 calls per minute
        import time
        time.sleep(12)  # Wait 12 seconds between calls (5 per minute = 12 sec each)
    
    if not all_data:
        return None
    
    # Combine all data
    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.sort_values("Timestamp").reset_index(drop=True)
    
    print(f"\n✅ Downloaded {len(combined)} total bars from Polygon.io")
    print(f"📁 Daily CSV files saved in: {data_dir}/")
    return combined


def _cache_dir(symbol: str, interval: str = "15m") -> str:
    mins = interval.replace("m", "")
    return f"{symbol}_{mins}m_data"


def _cache_file(symbol: str, date_str: str, interval: str = "15m") -> str:
    mins = interval.replace("m", "")
    return os.path.join(_cache_dir(symbol, interval), f"{symbol}_{mins}m_{date_str}.csv")


def download_polygon_date(symbol: str, date_str: str, interval_mins: int = 15) -> str:
    """
    Download ONE day of {interval_mins}-minute candles from Polygon and save to cache folder.
    Returns the CSV path written.
    """
    if not POLYGON_API_KEY:
        raise RuntimeError("POLYGON_API_KEY not set. Export it first: export POLYGON_API_KEY='...'")

    data_dir = f"{symbol}_{interval_mins}m_data"
    os.makedirs(data_dir, exist_ok=True)
    out_csv = os.path.join(data_dir, f"{symbol}_{interval_mins}m_{date_str}.csv")

    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/{interval_mins}/minute/{date_str}/{date_str}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": POLYGON_API_KEY}
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 403:
        # Avoid printing the full URL (which may include apiKey in exception strings)
        raise RuntimeError(
            "Polygon API returned 403 Forbidden.\n"
            "- Your API key may be invalid/expired, or your Polygon plan may not include this endpoint.\n"
            "- Double-check POLYGON_API_KEY and your subscription permissions.\n"
            f"- Requested: {symbol} {interval_mins}m for {date_str}\n"
            "Security note: your API key appeared in terminal output; rotate it in Polygon and update your env var."
        )
    if resp.status_code == 401:
        raise RuntimeError(
            "Polygon API returned 401 Unauthorized.\n"
            "Check POLYGON_API_KEY (export POLYGON_API_KEY='...') and rotate it if needed."
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "Polygon API returned 429 Rate Limited.\n"
            "Wait a bit and retry. Free tier is ~5 requests/minute."
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Polygon API error HTTP {resp.status_code}.\n"
            f"Requested: {symbol} {interval_mins}m for {date_str}\n"
            f"Response snippet: {resp.text[:200]}"
        )
    data = resp.json()
    if data.get("resultsCount", 0) <= 0:
        raise RuntimeError(f"No Polygon data returned for {symbol} on {date_str} (holiday/closed?)")

    df = pd.DataFrame(data["results"])
    df["Timestamp"] = pd.to_datetime(df["t"], unit="ms")
    df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}, inplace=True)
    df = df[["Timestamp", "Open", "High", "Low", "Close", "Volume"]].copy()
    if df["Timestamp"].dt.tz is None:
        df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC")
    df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)
    df.to_csv(out_csv, index=False)
    return out_csv


def load_cached_day(symbol: str, date_obj, interval: str = "15m") -> pd.DataFrame | None:
    date_str = pd.to_datetime(date_obj).strftime("%Y-%m-%d")
    path = _cache_file(symbol, date_str, interval)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    if df["Timestamp"].dt.tz is None:
        df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC")
    df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)
    df["Date"] = df["Timestamp"].dt.date
    df["Time"] = df["Timestamp"].dt.time
    return df


def get_prev_cached_day(symbol: str, date_obj, interval: str = "15m"):
    """Find previous cached trading day < date_obj in the cache folder."""
    data_dir = _cache_dir(symbol, interval)
    if not os.path.exists(data_dir):
        return None
    mins = interval.replace("m", "")
    files = [f for f in os.listdir(data_dir) if f.startswith(f"{symbol}_{mins}m_") and f.endswith(".csv")]
    dates = []
    for f in files:
        ds = f.replace(f"{symbol}_{mins}m_", "").replace(".csv", "")
        try:
            dates.append(pd.to_datetime(ds).date())
        except Exception:
            continue
    dates = sorted(set(dates))
    d = pd.to_datetime(date_obj).date()
    prev = [x for x in dates if x < d]
    return prev[-1] if prev else None

def get_cached_dates(symbol, start_date, end_date, interval="15m"):
    """
    Get set of dates that have cached CSV files
    Returns set of date objects
    """
    data_dir = f"{symbol}_{interval.replace('m', '')}m_data"
    
    if not os.path.exists(data_dir):
        return set()
    
    # Get all CSV files in the directory
    csv_files = [f for f in os.listdir(data_dir) if f.endswith('.csv') and f.startswith(f"{symbol}_")]
    
    cached_dates = set()
    for csv_file in csv_files:
        # Extract date from filename: SPY_15m_2023-01-01.csv
        try:
            date_str = csv_file.replace(f"{symbol}_{interval.replace('m', '')}m_", "").replace(".csv", "")
            file_date = pd.to_datetime(date_str).date()
            cached_dates.add(file_date)
        except Exception:
            continue
    
    return cached_dates

def load_data_from_csv(symbol, start_date, end_date, interval="15m"):
    """
    Load historical data from saved CSV files if they exist
    """
    data_dir = f"{symbol}_{interval.replace('m', '')}m_data"
    
    if not os.path.exists(data_dir):
        return None, set()
    
    # Get all CSV files in the directory
    csv_files = [f for f in os.listdir(data_dir) if f.endswith('.csv') and f.startswith(f"{symbol}_")]
    
    if not csv_files:
        return None, set()
    
    # Parse dates from filenames and filter by date range
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    
    all_data = []
    cached_dates = set()
    for csv_file in csv_files:
        # Extract date from filename: SPY_15m_2023-01-01.csv
        try:
            date_str = csv_file.replace(f"{symbol}_{interval.replace('m', '')}m_", "").replace(".csv", "")
            file_date = pd.to_datetime(date_str)
            file_date_obj = file_date.date()
            
            if start_dt <= file_date <= end_dt:
                df = pd.read_csv(os.path.join(data_dir, csv_file))
                df["Timestamp"] = pd.to_datetime(df["Timestamp"])
                if df["Timestamp"].dt.tz is None:
                    df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC")
                df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)
                all_data.append(df)
                cached_dates.add(file_date_obj)
        except Exception as e:
            continue
    
    if not all_data:
        return None, set()
    
    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.sort_values("Timestamp").reset_index(drop=True)
    
    print(f"📂 Loaded {len(combined)} bars from {len(all_data)} CSV files in {data_dir}/")
    return combined, cached_dates


def load_all_cached_data(symbol, interval="15m"):
    """
    OFFLINE helper: load ALL cached CSV files for the symbol/interval, regardless of gaps.
    Returns (combined_df, cached_dates).
    """
    data_dir = f"{symbol}_{interval.replace('m', '')}m_data"
    if not os.path.exists(data_dir):
        raise RuntimeError(f"Cache folder not found: {data_dir}/")

    csv_files = sorted(
        f for f in os.listdir(data_dir)
        if f.endswith(".csv") and f.startswith(f"{symbol}_{interval.replace('m', '')}m_")
    )
    if not csv_files:
        raise RuntimeError(f"No CSV files found in cache folder: {data_dir}/")

    all_data = []
    cached_dates = set()
    for f in csv_files:
        # SPY_15m_YYYY-MM-DD.csv
        date_str = f.replace(f"{symbol}_{interval.replace('m', '')}m_", "").replace(".csv", "")
        try:
            file_date = pd.to_datetime(date_str).date()
        except Exception:
            continue

        path = os.path.join(data_dir, f)
        df = pd.read_csv(path)
        df["Timestamp"] = pd.to_datetime(df["Timestamp"])
        if df["Timestamp"].dt.tz is None:
            df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC")
        df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)
        all_data.append(df)
        cached_dates.add(file_date)

    if not all_data:
        raise RuntimeError(f"Failed to load any data from {data_dir}/")

    combined = pd.concat(all_data, ignore_index=True).sort_values("Timestamp").reset_index(drop=True)
    combined["Date"] = combined["Timestamp"].dt.date
    combined["Time"] = combined["Timestamp"].dt.time
    print(f"📦 Loaded ALL cached data: {len(cached_dates)} days, {len(combined)} bars from {data_dir}/")
    return combined, cached_dates

def download_historical_data(symbol, start_date, end_date, interval="15m"):
    """
    OFFLINE MODE: Load historical intraday data ONLY from local CSV cache folder.

    - Expects files in: `{SYMBOL}_{INTERVAL}m_data/`, e.g. `SPY_15m_data/SPY_15m_2025-12-04.csv`
    - If any weekday in the requested range is missing from the cache, raises an error
      (so we never silently run partial datasets).
    """
    print("Checking local CSV cache...")
    cached_data, cached_dates = load_data_from_csv(symbol, start_date, end_date, interval)

    if cached_data is None or cached_data.empty:
        raise RuntimeError(
            f"No cached CSV data found for {symbol} in range {start_date} to {end_date}. "
            f"Expected folder: {symbol}_{interval.replace('m','')}m_data/"
        )

    # Expected weekdays in requested range (we don't know holidays here; handle by allowing missing if market holiday?)
    # We'll treat ALL weekdays as required unless the user hasn't downloaded them.
    start_dt = pd.to_datetime(start_date).date()
    end_dt = pd.to_datetime(end_date).date()

    expected_dates = set()
    cur = start_dt
    while cur <= end_dt:
        if cur.weekday() < 5:
            expected_dates.add(cur)
        cur += timedelta(days=1)

    missing_dates = sorted(expected_dates - cached_dates)
    if missing_dates:
        missing_preview = ", ".join([d.strftime("%Y-%m-%d") for d in missing_dates[:15]])
        more = "" if len(missing_dates) <= 15 else f" ... (+{len(missing_dates) - 15} more)"
        raise RuntimeError(
            f"Cache is missing {len(missing_dates)} weekday files in requested range.\n"
            f"Missing (first 15): {missing_preview}{more}\n"
            f"Download the missing days into {symbol}_{interval.replace('m','')}m_data/ and re-run."
        )

    print(f"✅ Cache complete for requested range ({len(cached_dates)} days). Using local data only.")
    return cached_data

def analyze_date(test_date_str, spy_data=None, daily_data=None):
    """Analyze a single date and return results as a dictionary"""
    test_day = pd.to_datetime(test_date_str).date()
    
    # OFFLINE single-date: if spy_data not provided, load that day + previous cached day (for prev_close)
    if spy_data is None:
        day_df = load_cached_day(SYMBOL, test_day, INTERVAL)
        if day_df is None:
            return None

        prev_day = get_prev_cached_day(SYMBOL, test_day, INTERVAL)
        prev_df = load_cached_day(SYMBOL, prev_day, INTERVAL) if prev_day else None

        if prev_df is not None:
            spy = pd.concat([prev_df, day_df], ignore_index=True)
        else:
            spy = day_df.copy()
    else:
        spy = spy_data.copy()
    
    # Filter to only the test date
    spy_test_date = spy[spy["Date"] == test_day].copy()
    
    if spy_test_date.empty:
        return None
    
    # VWAP CALCULATION
    spy_test_date = spy_test_date.sort_values("Timestamp")
    spy_test_date["Typical"] = (spy_test_date["High"] + spy_test_date["Low"] + spy_test_date["Close"]) / 3
    spy_test_date["TPV"] = spy_test_date["Typical"] * spy_test_date["Volume"]
    spy_test_date["CumTPV"] = spy_test_date["TPV"].cumsum()
    spy_test_date["CumVol"] = spy_test_date["Volume"].cumsum()
    spy_test_date["VWAP"] = spy_test_date["CumTPV"] / spy_test_date["CumVol"]
    
    # Get 9:30 candle
    bar_930 = spy_test_date[spy_test_date["Time"] == pd.to_datetime("09:30").time()]
    
    if bar_930.empty:
        return None
    
    bar_930 = bar_930.iloc[0]
    
    # Get 9:45 candle
    bar_945 = spy_test_date[spy_test_date["Time"] == pd.to_datetime("09:45").time()]
    close_945 = None
    if not bar_945.empty:
        bar_945 = bar_945.iloc[0]
        close_945_val = bar_945["Close"]
        close_945 = float(close_945_val.iloc[0]) if isinstance(close_945_val, pd.Series) else float(close_945_val)
    
    # Get 10:30 candle
    bar_1030 = spy_test_date[spy_test_date["Time"] == pd.to_datetime("10:30").time()]
    close_1030 = None
    if not bar_1030.empty:
        bar_1030 = bar_1030.iloc[0]
        close_1030_val = bar_1030["Close"]
        close_1030 = float(close_1030_val.iloc[0]) if isinstance(close_1030_val, pd.Series) else float(close_1030_val)
    
    # Get regular market close (4:00pm ET) for 15m bars -> 15:45 bar close
    bar_1545 = spy_test_date[spy_test_date["Time"] == pd.to_datetime("15:45").time()]
    close_1600 = None
    if not bar_1545.empty:
        bar_1545 = bar_1545.iloc[0]
        close_1600_val = bar_1545["Close"]
        close_1600 = float(close_1600_val.iloc[0]) if isinstance(close_1600_val, pd.Series) else float(close_1600_val)
    else:
        # Fallback: last bar at/before 16:00 (handles early close edge cases imperfectly)
        bars_upto_close = spy_test_date[spy_test_date["Timestamp"].dt.time <= pd.to_datetime("16:00").time()]
        if not bars_upto_close.empty:
            last_bar_close = bars_upto_close.sort_values("Timestamp").iloc[-1]
            close_1600_val = last_bar_close["Close"]
            close_1600 = float(close_1600_val.iloc[0]) if isinstance(close_1600_val, pd.Series) else float(close_1600_val)

    # GAP CALCULATION - Use previous trading day's close from intraday data
    # Get all dates from spy data, find previous trading day
    if spy_data is not None:
        all_dates = sorted(spy_data["Date"].unique())
        test_date_idx = None
        for i, date in enumerate(all_dates):
            if date == test_day:
                test_date_idx = i
                break
        
        if test_date_idx is not None and test_date_idx > 0:
            # Get previous trading day
            prev_trading_day = all_dates[test_date_idx - 1]
            prev_day_data = spy_data[spy_data["Date"] == prev_trading_day]
            if not prev_day_data.empty:
                # Get the last bar (market close) of previous trading day
                prev_day_data = prev_day_data.sort_values("Timestamp")
                prev_last_bar = prev_day_data.iloc[-1]
                prev_close_val = prev_last_bar["Close"]
                prev_close = float(prev_close_val.iloc[0]) if isinstance(prev_close_val, pd.Series) else float(prev_close_val)
            else:
                prev_close = None
        else:
            # OFFLINE: if the prior trading day isn't in the intraday cache, we cannot compute gap safely.
            prev_close = None
    else:
        # OFFLINE: without intraday cache, we don't compute gap.
        prev_close = None
    
    # Extract values
    open_930_val = bar_930["Open"]
    open_930 = float(open_930_val.iloc[0]) if isinstance(open_930_val, pd.Series) else float(open_930_val)
    
    close_930_val = bar_930["Close"]
    close_930 = float(close_930_val.iloc[0]) if isinstance(close_930_val, pd.Series) else float(close_930_val)
    
    # Calculate gap percentage (only if we have previous close)
    if prev_close is not None and prev_close > 0:
        gap_pct = float((open_930 - prev_close) / prev_close * 100.0)
    else:
        gap_pct = None
        return None  # Can't calculate gap without previous close
    
    if abs(gap_pct) < 0.2:
        gap_bucket = "Small"
    elif abs(gap_pct) < 0.5:
        gap_bucket = "Medium"
    else:
        gap_bucket = "Large"

    # VWAP
    vwap_930_val = bar_930["VWAP"]
    vwap_930 = float(vwap_930_val.iloc[0]) if isinstance(vwap_930_val, pd.Series) else float(vwap_930_val)
    
    # SIGNAL VARIABLES
    dir_930 = "Bull" if close_930 > open_930 else "Bear"
    vwap_pos = "Above VWAP" if close_930 > vwap_930 else "Below VWAP"
    
    # DOWN PROB (dynamic table if computed)
    key = (dir_930, vwap_pos, gap_bucket)
    down_prob = ACTIVE_DOWN_TABLE.get(key, None)
    cell_count = ACTIVE_DOWN_COUNTS.get(key, 0)
    
    # MARKET CLOSE (keep for reference, but we'll use 9:45 close for evaluation)
    if not spy_test_date.empty:
        last_bar = spy_test_date.iloc[-1]
        market_close_val = last_bar["Close"]
        market_close = float(market_close_val.iloc[0]) if isinstance(market_close_val, pd.Series) else float(market_close_val)
    else:
        market_close = None
    
    # TRADE DECISION (trade only high-confidence cells)
    if cell_count < MIN_CELL_COUNT:
        trade = "SKIP"
    else:
        # down_prob = P(down by 9:45 vs 9:30 close)
        if down_prob is None:
            trade = "SKIP"
        elif down_prob >= DOWN_PROB_HIGH:
            trade = "PUT"
        elif down_prob <= DOWN_PROB_LOW:
            trade = "CALL"
        else:
            trade = "SKIP"
    
    # TRADE RESULT - Evaluate against 9:45 close
    entry_price = close_930
    trade_result_945 = None
    price_change_945 = None
    price_change_pct_945 = None
    exit_price_945 = close_945  # Use 9:45 close for evaluation
    
    if exit_price_945 is not None:
        if trade == "CALL":
            price_change_945 = exit_price_945 - entry_price
            trade_result_945 = "WIN" if price_change_945 > 0 else "LOSS"
            price_change_pct_945 = (price_change_945 / entry_price) * 100
        elif trade == "PUT":
            price_change_945 = entry_price - exit_price_945
            trade_result_945 = "WIN" if price_change_945 > 0 else "LOSS"
            price_change_pct_945 = (price_change_945 / entry_price) * 100
        else:
            trade_result_945 = "N/A"
            price_change_945 = exit_price_945 - entry_price
            price_change_pct_945 = (price_change_945 / entry_price) * 100
    
    # TRADE RESULT - Evaluate against 10:30 close
    trade_result_1030 = None
    price_change_1030 = None
    price_change_pct_1030 = None
    exit_price_1030 = close_1030  # Use 10:30 close for evaluation
    
    if exit_price_1030 is not None:
        if trade == "CALL":
            price_change_1030 = exit_price_1030 - entry_price
            trade_result_1030 = "WIN" if price_change_1030 > 0 else "LOSS"
            price_change_pct_1030 = (price_change_1030 / entry_price) * 100
        elif trade == "PUT":
            price_change_1030 = entry_price - exit_price_1030
            trade_result_1030 = "WIN" if price_change_1030 > 0 else "LOSS"
            price_change_pct_1030 = (price_change_1030 / entry_price) * 100
        else:
            trade_result_1030 = "N/A"
            price_change_1030 = exit_price_1030 - entry_price
            price_change_pct_1030 = (price_change_1030 / entry_price) * 100
    
    # TRADE RESULT - Evaluate against regular market close (4:00pm ET)
    trade_result_close = None
    price_change_close = None
    price_change_pct_close = None
    exit_price_close = close_1600

    if exit_price_close is not None:
        if trade == "CALL":
            price_change_close = exit_price_close - entry_price
            trade_result_close = "WIN" if price_change_close > 0 else "LOSS"
            price_change_pct_close = (price_change_close / entry_price) * 100
        elif trade == "PUT":
            price_change_close = entry_price - exit_price_close
            trade_result_close = "WIN" if price_change_close > 0 else "LOSS"
            price_change_pct_close = (price_change_close / entry_price) * 100
        else:
            trade_result_close = "N/A"
            price_change_close = exit_price_close - entry_price
            price_change_pct_close = (price_change_close / entry_price) * 100

    return {
        "Date": test_date_str,
        "9:30 Open": open_930,
        "9:30 Close": close_930,
        "9:45 Close": close_945,
        "10:30 Close": close_1030,
        "Market Close": close_1600,  # regular close (4:00pm ET)
        "Gap %": gap_pct,
        "Gap Bucket": gap_bucket,
        "Direction": dir_930,
        "VWAP Pos": vwap_pos,
        "Down Prob": None if down_prob is None else float(down_prob),
        "Cell Count": cell_count,
        "Trade": trade,
        "Entry": entry_price,
        "Exit_945": exit_price_945,  # 9:45 close (used for evaluation)
        "Price Chg_945": price_change_945,
        "Price Chg %_945": price_change_pct_945,
        "Result_945": trade_result_945,
        "Exit_1030": exit_price_1030,  # 10:30 close (used for evaluation)
        "Price Chg_1030": price_change_1030,
        "Price Chg %_1030": price_change_pct_1030,
        "Result_1030": trade_result_1030,
        "Exit_Close": exit_price_close,  # regular close (same as Market Close)
        "Price Chg_Close": price_change_close,
        "Price Chg %_Close": price_change_pct_close,
        "Result_Close": trade_result_close,
    }

def print_single_result(result):
    """Print detailed result for a single date"""
    print("\n📊 SPY 9:30 TRADE SIGNAL\n")
    print(f"Test Date        : {result['Date']}")
    print(f"9:30 Open        : ${result['9:30 Open']:.2f}")
    print(f"9:30 Close       : ${result['9:30 Close']:.2f}")
    if result['9:45 Close'] is not None:
        print(f"9:45 Close       : ${result['9:45 Close']:.2f}")
    if result['10:30 Close'] is not None:
        print(f"10:30 Close      : ${result['10:30 Close']:.2f}")
    print(f"Gap %            : {result['Gap %']:.2f}%")
    print(f"Gap Bucket       : {result['Gap Bucket']}")
    print(f"9:30 Direction   : {result['Direction']}")
    print(f"VWAP Position    : {result['VWAP Pos']}")
    if result.get("Down Prob") is not None:
        print(f"Down Prob        : {result['Down Prob']:.2f}")
    
    print("\n==========================")
    print(f"🔥 TRADE DECISION: {result['Trade']}")
    print("==========================")
    
    if result['Exit_945'] is not None:
        print(f"\n📈 RESULTS AT 9:45 CLOSE:")
        print(f"Entry Price      : ${result['Entry']:.2f} (9:30 Close)")
        print(f"Exit Price       : ${result['Exit_945']:.2f} (9:45 Close)")
        if result['Price Chg_945'] is not None:
            print(f"Price Change     : ${result['Price Chg_945']:+.2f} ({result['Price Chg %_945']:+.2f}%)")
        if result['Trade'] != "SKIP" and result['Result_945'] is not None:
            print(f"Trade Result     : {result['Result_945']}")
        print()
    
    if result['Exit_1030'] is not None:
        print(f"\n📈 RESULTS AT 10:30 CLOSE:")
        print(f"Entry Price      : ${result['Entry']:.2f} (9:30 Close)")
        print(f"Exit Price       : ${result['Exit_1030']:.2f} (10:30 Close)")
        if result['Price Chg_1030'] is not None:
            print(f"Price Change     : ${result['Price Chg_1030']:+.2f} ({result['Price Chg %_1030']:+.2f}%)")
        if result['Trade'] != "SKIP" and result['Result_1030'] is not None:
            print(f"Trade Result     : {result['Result_1030']}")
        print()
    elif result['Market Close'] is not None:
        print(f"\n⚠️  10:30 close not available, showing market close for reference:")
        print(f"Entry Price      : ${result['Entry']:.2f} (9:30 Close)")
        print(f"Market Close     : ${result['Market Close']:.2f}")
        print()

# ==========================
# MAIN
# ==========================
if len(sys.argv) < 2:
    print("Usage:")
    print("  Download one date to cache: python spy_trading_engine_test_for_date.py --download-date YYYY-MM-DD")
    print("  Single date: python spy_trading_engine_test_for_date.py YYYY-MM-DD")
    print("  Last 60 days: python spy_trading_engine_test_for_date.py --last-60")
    print("  All cached:  python spy_trading_engine_test_for_date.py --all-cached")
    print("  Date range: python spy_trading_engine_test_for_date.py --range START_DATE END_DATE")
    print("  Last N years: python spy_trading_engine_test_for_date.py --years N")
    print("  Walk-forward (no lookahead): default for batch runs")
    print("  Static (LOOKAHEAD, for quick analysis only): add --static to any batch run")
    print("\nNote: For 2+ years of data, set POLYGON_API_KEY environment variable")
    print("      Get free API key at: https://polygon.io")
    print("      export POLYGON_API_KEY='your_key_here'")
    sys.exit(1)

# Batch runs default to walk-forward (no lookahead). Use --static to disable.
WALK_FORWARD_MODE = ("--static" not in sys.argv)

# Determine date range
if sys.argv[1] == "--download-date" and len(sys.argv) >= 3:
    d = sys.argv[2]
    out = download_polygon_date(SYMBOL, d, interval_mins=int(INTERVAL.replace("m", "")))
    print(f"✅ Downloaded {SYMBOL} {INTERVAL} for {d} -> {out}")
    sys.exit(0)
if sys.argv[1] == "--last-60":
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=60)
    title_suffix = "LAST 60 TRADING DAYS"
elif sys.argv[1] == "--all-cached":
    # Special offline mode: use whatever is in the CSV cache folder (even if there are gaps)
    title_suffix = "ALL CACHED DAYS"
    start_date = None
    end_date = None
elif sys.argv[1] == "--range" and len(sys.argv) >= 4:
    start_date = pd.to_datetime(sys.argv[2]).date()
    end_date = pd.to_datetime(sys.argv[3]).date()
    title_suffix = f"{sys.argv[2]} TO {sys.argv[3]}"
elif sys.argv[1] == "--years" and len(sys.argv) >= 3:
    years = int(sys.argv[2])
    end_date = datetime.now().date()
    try:
        from dateutil.relativedelta import relativedelta
        start_date = end_date - relativedelta(years=years)
    except ImportError:
        # Simple fallback: approximate years as days
        start_date = end_date - timedelta(days=years * 365)
    title_suffix = f"LAST {years} YEARS"
else:
    # Single date mode - handled later
    title_suffix = None
    start_date = None
    end_date = None

if title_suffix:
    # Batch mode with date range
    if sys.argv[1] == "--all-cached":
        print("Loading ALL cached SPY 15m data from local folder...")
        spy_all, _cached_dates = load_all_cached_data(SYMBOL, INTERVAL)
    else:
        print(f"Loading SPY 15m data from {start_date} to {end_date} (local cache only)...")
        spy_all = download_historical_data(SYMBOL, start_date, end_date, INTERVAL)
    
    if spy_all is None or spy_all.empty:
        print("No data available")
        sys.exit(1)

    if WALK_FORWARD_MODE:
        # Run walk-forward backtest (no lookahead)
        eval_start = start_date if sys.argv[1] != "--all-cached" else None
        eval_end = end_date if sys.argv[1] != "--all-cached" else None
        df, learned_df = walk_forward_backtest(spy_all, eval_start=eval_start, eval_end=eval_end)

        learned_path = f"{SYMBOL}_DOWN_TABLE_WALK_FORWARD_LEARNED_{DECISION_TARGET}.csv"
        learned_df.to_csv(learned_path, index=False)
        print(f"🧠 Walk-forward learned table exported: {learned_path}")

        results = df.to_dict("records")
    else:
        # Recompute probability table from the same cached dataset (LOOKAHEAD — for quick analysis only)
        ACTIVE_DOWN_TABLE, ACTIVE_DOWN_COUNTS = compute_reversal_table_from_cached_intraday(
            spy_all,
            export_csv=f"{SYMBOL}_DOWN_TABLE_FROM_CACHE_{DECISION_TARGET}.csv"
        )
    
    # Ensure Date and Time columns exist
    if "Date" not in spy_all.columns:
        spy_all["Date"] = spy_all["Timestamp"].dt.date
    if "Time" not in spy_all.columns:
        spy_all["Time"] = spy_all["Timestamp"].dt.time
    
    # Get unique trading days (days with 9:30 candle)
    trading_days = spy_all[spy_all["Time"] == pd.to_datetime("09:30").time()]["Date"].unique()
    trading_days = sorted(trading_days)
    if sys.argv[1] == "--last-60":
        trading_days = trading_days[-60:]
    
    print(f"Found {len(trading_days)} trading days with 9:30 data")
    print("Analyzing each day...\n")
    
    # OFFLINE: no daily download. prev_close is computed from cached intraday data.
    daily_all = None
    
    if not WALK_FORWARD_MODE:
        # Analyze each date (static probability table)
        results = []
        for i, date in enumerate(trading_days, 1):
            date_str = date.strftime("%Y-%m-%d")
            print(f"Processing {i}/{len(trading_days)}: {date_str}...", end="\r")
            result = analyze_date(date_str, spy_data=spy_all, daily_data=daily_all)
            if result:
                results.append(result)
        print(f"\n\nAnalyzed {len(results)} days successfully\n")
        df = pd.DataFrame(results)
    else:
        # walk_forward already produced df/results
        print(f"\n\nAnalyzed {len(results)} days successfully (walk-forward)\n")
    
    # Format columns for display
    display_df = df.copy()
    display_df["9:30 Open"] = display_df["9:30 Open"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["9:30 Close"] = display_df["9:30 Close"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["9:45 Close"] = display_df["9:45 Close"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["10:30 Close"] = display_df["10:30 Close"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["Market Close"] = display_df["Market Close"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["Gap %"] = display_df["Gap %"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "N/A")
    display_df["Entry"] = display_df["Entry"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["Exit_945"] = display_df["Exit_945"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["Price Chg_945"] = display_df["Price Chg_945"].apply(lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A")
    display_df["Price Chg %_945"] = display_df["Price Chg %_945"].apply(lambda x: f"{x:+.2f}%" if pd.notna(x) else "N/A")
    display_df["Exit_1030"] = display_df["Exit_1030"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["Price Chg_1030"] = display_df["Price Chg_1030"].apply(lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A")
    display_df["Price Chg %_1030"] = display_df["Price Chg %_1030"].apply(lambda x: f"{x:+.2f}%" if pd.notna(x) else "N/A")
    display_df["Exit_Close"] = display_df["Exit_Close"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    display_df["Price Chg_Close"] = display_df["Price Chg_Close"].apply(lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A")
    display_df["Price Chg %_Close"] = display_df["Price Chg %_Close"].apply(lambda x: f"{x:+.2f}%" if pd.notna(x) else "N/A")
    display_df["Down Prob"] = display_df["Down Prob"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "N/A")
    display_df["Cell Count"] = display_df["Cell Count"].apply(lambda x: f"{int(x)}" if pd.notna(x) else "0")
    if "Opt Prem" in display_df.columns:
        display_df["Opt Prem"] = display_df["Opt Prem"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
    if "p_win" in display_df.columns:
        display_df["p_win"] = display_df["p_win"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "N/A")
    for col in ["Opt EV_945", "Opt PnL_945", "Opt EV_1030", "Opt PnL_1030", "Opt EV_Close", "Opt PnL_Close"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A")
    
    # Select columns for display
    display_cols = ["Date", "9:30 Open", "9:30 Close", "9:45 Close", "10:30 Close", "Market Close",
                     "Gap %", "Gap Bucket",
                     "Direction", "VWAP Pos", "Down Prob", "Cell Count", "Trade", "Entry",
                     "Exit_945", "Price Chg_945", "Price Chg %_945", "Result_945",
                     "Exit_1030", "Price Chg_1030", "Price Chg %_1030", "Result_1030",
                     "Exit_Close", "Price Chg_Close", "Price Chg %_Close", "Result_Close"]

    # If walk-forward columns exist, include options proxy metrics
    if "Opt Prem" in df.columns:
        display_cols += ["Decision Mode", "Decision Target", "Opt Prem", "p_win",
                         "Opt EV_945", "Opt PnL_945",
                         "Opt EV_1030", "Opt PnL_1030",
                         "Opt EV_Close", "Opt PnL_Close"]
    
    print("=" * 200)
    print(f"SPY 9:30 TRADE SIGNAL - {title_suffix}")
    print("=" * 200)
    print(display_df[display_cols].to_string(index=False))
    print("=" * 200)
    
    # Export to CSV (with formatted values for Google Sheets)
    csv_df = display_df[display_cols].copy()  # Use already formatted display_df
    
    csv_filename = f"spy_trading_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_df.to_csv(csv_filename, index=False)
    print(f"\n💾 Data exported to: {csv_filename}")
    print("   You can import this CSV file into Google Sheets or Excel")
    
    # Summary statistics
    if len(results) > 0:
        # 9:45 results
        trade_results_945 = [r["Result_945"] for r in results if r["Result_945"] in ["WIN", "LOSS"]]
        wins_945 = trade_results_945.count("WIN") if trade_results_945 else 0
        losses_945 = trade_results_945.count("LOSS") if trade_results_945 else 0
        win_rate_945 = (wins_945 / (wins_945 + losses_945) * 100) if (wins_945 + losses_945) > 0 else 0
        
        # 10:30 results
        trade_results_1030 = [r["Result_1030"] for r in results if r["Result_1030"] in ["WIN", "LOSS"]]
        wins_1030 = trade_results_1030.count("WIN") if trade_results_1030 else 0
        losses_1030 = trade_results_1030.count("LOSS") if trade_results_1030 else 0
        win_rate_1030 = (wins_1030 / (wins_1030 + losses_1030) * 100) if (wins_1030 + losses_1030) > 0 else 0

        # Market close results (4:00pm ET)
        trade_results_close = [r["Result_Close"] for r in results if r.get("Result_Close") in ["WIN", "LOSS"]]
        wins_close = trade_results_close.count("WIN") if trade_results_close else 0
        losses_close = trade_results_close.count("LOSS") if trade_results_close else 0
        win_rate_close = (wins_close / (wins_close + losses_close) * 100) if (wins_close + losses_close) > 0 else 0
        
        print(f"\n📊 SUMMARY:")
        print(f"Total Days Analyzed: {len(results)}")
        print(f"Skipped: {len([r for r in results if r['Trade'] == 'SKIP'])}")
        print(f"\n📈 RESULTS AT 9:45 CLOSE:")
        print(f"  Trades Taken: {wins_945 + losses_945}")
        print(f"  Wins: {wins_945}")
        print(f"  Losses: {losses_945}")
        print(f"  Win Rate: {win_rate_945:.1f}%")
        print(f"\n📈 RESULTS AT 10:30 CLOSE:")
        print(f"  Trades Taken: {wins_1030 + losses_1030}")
        print(f"  Wins: {wins_1030}")
        print(f"  Losses: {losses_1030}")
        print(f"  Win Rate: {win_rate_1030:.1f}%")

        print(f"\n📈 RESULTS AT MARKET CLOSE (4:00pm ET):")
        print(f"  Trades Taken: {wins_close + losses_close}")
        print(f"  Wins: {wins_close}")
        print(f"  Losses: {losses_close}")
        print(f"  Win Rate: {win_rate_close:.1f}%")

        # Options proxy EV / realized PnL (only if columns exist)
        if "Opt EV_945" in df.columns:
            taken = df[df["Trade"] != "SKIP"].copy()
            if not taken.empty:
                def _mean(col):
                    return float(pd.to_numeric(taken[col], errors="coerce").dropna().mean())

                print(f"\n🧾 OPTIONS PROXY (per contract, using premium≈{OPT_PREMIUM_PCT*100:.2f}% of underlying, fees=${OPT_FEES_ROUNDTRIP:.2f}):")
                print(f"  Avg EV @9:45     : ${_mean('Opt EV_945'):+.2f} | Avg PnL @9:45     : ${_mean('Opt PnL_945'):+.2f}")
                print(f"  Avg EV @10:30    : ${_mean('Opt EV_1030'):+.2f} | Avg PnL @10:30    : ${_mean('Opt PnL_1030'):+.2f}")
                print(f"  Avg EV @Close    : ${_mean('Opt EV_Close'):+.2f} | Avg PnL @Close    : ${_mean('Opt PnL_Close'):+.2f}")
    
else:
    # Single date mode
    TEST_DATE = sys.argv[1]
    result = analyze_date(TEST_DATE)
    
    if result is None:
        print(f"No data available for {TEST_DATE}")
        sys.exit(1)
    
    print_single_result(result)
