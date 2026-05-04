import sys
import yfinance as yf
import pandas as pd
import pytz
from datetime import datetime

# ==========================
# CONFIG
# ==========================
SYMBOL = "SPY"
INTERVAL = "15m"
EASTERN = pytz.timezone("US/Eastern")

# Reversal probability table (from your research)
REVERSAL_TABLE = {
    ("Bear", "Below VWAP", "Small"): 0.5833,
    ("Bear", "Below VWAP", "Medium"): 0.6667,
    ("Bear", "Below VWAP", "Large"): 0.50,
    ("Bull", "Above VWAP", "Small"): 0.545,
    ("Bull", "Above VWAP", "Medium"): 0.625,
    ("Bull", "Above VWAP", "Large"): 0.125,
}

T930 = pd.to_datetime("09:30").time()
T945 = pd.to_datetime("09:45").time()

def _flatten_cols(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def _to_eastern(ts: pd.Series) -> pd.Series:
    # SAFE timezone handling for yfinance inconsistencies
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    return ts.dt.tz_convert(EASTERN)

def gap_bucket(gap_pct: float) -> str:
    g = abs(gap_pct)
    if g < 0.2:
        return "Small"
    elif g < 0.5:
        return "Medium"
    return "Large"

def analyze_date(test_date_str, spy_data=None, daily_data=None):
    """
    Analyze one date using ONLY that day's intraday data and the prior daily close.
    Returns dict on success, or dict with {'Date':..., 'Error':...} on failure.
    """
    test_day = pd.to_datetime(test_date_str).date()

    # ---------- Intraday data ----------
    if spy_data is None:
        start = pd.to_datetime(test_date_str) - pd.Timedelta(days=3)
        end = pd.to_datetime(test_date_str) + pd.Timedelta(days=1)

        spy = yf.download(
            SYMBOL,
            start=start,
            end=end,
            interval=INTERVAL,
            auto_adjust=True,
            progress=False
        )
        if spy.empty:
            return {"Date": test_date_str, "Error": "No intraday data (Yahoo limit / holiday)"}

        spy = _flatten_cols(spy).reset_index()
        if "Datetime" in spy.columns:
            spy.rename(columns={"Datetime": "Timestamp"}, inplace=True)

        spy["Timestamp"] = _to_eastern(spy["Timestamp"])
        spy["Date"] = spy["Timestamp"].dt.date
        spy["Time"] = spy["Timestamp"].dt.time
    else:
        spy = spy_data.copy()

    day_df = spy[spy["Date"] == test_day].copy()
    if day_df.empty:
        return {"Date": test_date_str, "Error": "No bars for that date in intraday dataset"}

    # ---------- Daily data (must be relative to test_day!) ----------
    if daily_data is None:
        d_start = pd.to_datetime(test_date_str) - pd.Timedelta(days=30)
        d_end = pd.to_datetime(test_date_str) + pd.Timedelta(days=1)

        daily = yf.download(
            SYMBOL,
            start=d_start,
            end=d_end,
            interval="1d",
            auto_adjust=True,
            progress=False
        )
        if daily.empty:
            return {"Date": test_date_str, "Error": "No daily data"}

        daily = _flatten_cols(daily).reset_index()
    else:
        daily = daily_data.copy()

    # Normalize daily date column
    if "Date" not in daily.columns:
        # yfinance daily reset_index gives 'Date' usually
        if "Datetime" in daily.columns:
            daily.rename(columns={"Datetime": "Date"}, inplace=True)

    daily["Date"] = pd.to_datetime(daily["Date"]).dt.date
    prev_rows = daily[daily["Date"] < test_day]
    if prev_rows.empty:
        return {"Date": test_date_str, "Error": "No previous daily close available for gap"}

    prev_close = float(prev_rows.iloc[-1]["Close"])

    # ---------- VWAP calculation (session only) ----------
    day_df = day_df.sort_values("Timestamp")
    day_df["Typical"] = (day_df["High"] + day_df["Low"] + day_df["Close"]) / 3.0
    day_df["TPV"] = day_df["Typical"] * day_df["Volume"]
    day_df["CumTPV"] = day_df["TPV"].cumsum()
    day_df["CumVol"] = day_df["Volume"].cumsum()
    day_df["VWAP"] = day_df["CumTPV"] / day_df["CumVol"]

    # ---------- Get 9:30 and 9:45 candles ----------
    bar_930_df = day_df[day_df["Time"] == T930]
    if bar_930_df.empty:
        return {"Date": test_date_str, "Error": "Missing 9:30 candle"}

    bar_930 = bar_930_df.iloc[0]
    open_930 = float(bar_930["Open"])
    close_930 = float(bar_930["Close"])
    vwap_930 = float(bar_930["VWAP"])

    bar_945_df = day_df[day_df["Time"] == T945]
    close_945 = float(bar_945_df.iloc[0]["Close"]) if not bar_945_df.empty else None

    # ---------- Gap ----------
    gap_pct = float((open_930 - prev_close) / prev_close * 100.0)
    gap_cat = gap_bucket(gap_pct)

    # ---------- Signal variables ----------
    dir_930 = "Bull" if close_930 > open_930 else "Bear"
    vwap_pos = "Above VWAP" if close_930 > vwap_930 else "Below VWAP"

    # ---------- Probability lookup ----------
    reversal_prob = float(REVERSAL_TABLE.get((dir_930, vwap_pos, gap_cat), 0.0))

    # ---------- Decision (your current rule) ----------
    if reversal_prob >= 0.55:
        trade = "CALL" if dir_930 == "Bear" else "PUT"
    elif reversal_prob <= 0.35:
        trade = "PUT" if dir_930 == "Bear" else "CALL"
    else:
        trade = "SKIP"

    # ---------- Outcome for THIS strategy: use 9:45 candle close ----------
    # (You can change to 10:00/10:15 later, but do NOT use market close here.)
    entry = close_930
    outcome_price = close_945
    result = None
    price_chg = None
    price_chg_pct = None

    if outcome_price is not None:
        if trade == "CALL":
            price_chg = outcome_price - entry
            result = "WIN" if price_chg > 0 else "LOSS"
        elif trade == "PUT":
            price_chg = entry - outcome_price
            result = "WIN" if price_chg > 0 else "LOSS"
        else:
            result = "N/A"
            price_chg = outcome_price - entry

        price_chg_pct = (price_chg / entry) * 100.0

    return {
        "Date": test_date_str,
        "Prev Close": prev_close,
        "9:30 Open": open_930,
        "9:30 Close": close_930,
        "9:45 Close": close_945,
        "Gap %": gap_pct,
        "Gap Bucket": gap_cat,
        "Direction": dir_930,
        "VWAP Pos": vwap_pos,
        "Rev Prob": reversal_prob,
        "Trade": trade,
        "Entry": entry,
        "Exit(9:45)": outcome_price,
        "Price Chg": price_chg,
        "Price Chg %": price_chg_pct,
        "Result": result
    }

def print_single_result(r):
    if "Error" in r:
        print(f"\n{r['Date']} -> ERROR: {r['Error']}\n")
        return

    print("\n📊 SPY 9:30 TRADE SIGNAL\n")
    print(f"Test Date        : {r['Date']}")
    print(f"Prev Close       : ${r['Prev Close']:.2f}")
    print(f"9:30 Open        : ${r['9:30 Open']:.2f}")
    print(f"9:30 Close       : ${r['9:30 Close']:.2f}")
    if r["9:45 Close"] is not None:
        print(f"9:45 Close       : ${r['9:45 Close']:.2f}")
    print(f"Gap %            : {r['Gap %']:.2f}%")
    print(f"Gap Bucket       : {r['Gap Bucket']}")
    print(f"9:30 Direction   : {r['Direction']}")
    print(f"VWAP Position    : {r['VWAP Pos']}")
    print(f"Reversal Prob    : {r['Rev Prob']:.2f}")

    print("\n==========================")
    print(f"🔥 TRADE DECISION: {r['Trade']}")
    print("==========================\n")

    if r["Exit(9:45)"] is not None:
        print("📈 OUTCOME (9:30 → 9:45 window)")
        print(f"Entry (9:30 close): ${r['Entry']:.2f}")
        print(f"Exit (9:45 close) : ${r['Exit(9:45)']:.2f}")
        if r["Price Chg"] is not None:
            print(f"Move              : ${r['Price Chg']:+.2f} ({r['Price Chg %']:+.2f}%)")
        print(f"Result            : {r['Result']}\n")

# ==========================
# MAIN
# ==========================
if len(sys.argv) < 2:
    print("Usage:")
    print("  Single date: python spy_trading_engine_test_for_date.py YYYY-MM-DD")
    print("  Last 60 days: python spy_trading_engine_test_for_date.py --last-60")
    sys.exit(1)

if sys.argv[1] == "--last-60":
    print("Downloading SPY 15m data for last 60 days...")

    spy_all = yf.download(
        SYMBOL,
        period="60d",
        interval=INTERVAL,
        auto_adjust=True,
        progress=False
    )
    if spy_all.empty:
        print("No intraday data returned.")
        sys.exit(1)

    spy_all = _flatten_cols(spy_all).reset_index()
    if "Datetime" in spy_all.columns:
        spy_all.rename(columns={"Datetime": "Timestamp"}, inplace=True)

    spy_all["Timestamp"] = _to_eastern(spy_all["Timestamp"])
    spy_all["Date"] = spy_all["Timestamp"].dt.date
    spy_all["Time"] = spy_all["Timestamp"].dt.time

    # daily for this same window + buffer (used for correct prev_close per date)
    daily_all = yf.download(
        SYMBOL,
        period="120d",
        interval="1d",
        auto_adjust=True,
        progress=False
    )
    daily_all = _flatten_cols(daily_all).reset_index()

    # identify trading days with a 9:30 candle
    days = spy_all[spy_all["Time"] == T930]["Date"].unique()
    days = sorted(days)[-60:]

    results = []
    errors = {}

    for d in days:
        d_str = pd.to_datetime(d).strftime("%Y-%m-%d")
        r = analyze_date(d_str, spy_data=spy_all, daily_data=daily_all)
        results.append(r)
        if "Error" in r:
            errors[r["Error"]] = errors.get(r["Error"], 0) + 1

    df = pd.DataFrame(results)

    ok = df[df["Error"].isna()] if "Error" in df.columns else df
    print(f"\nAnalyzed: {len(df)} days | Successful: {len(ok)}")

    if errors:
        print("\nCommon failure reasons:")
        for k, v in sorted(errors.items(), key=lambda x: -x[1]):
            print(f"  {v}x  {k}")

    # Summary win rate on taken trades (CALL/PUT)
    if "Result" in ok.columns:
        taken = ok[ok["Trade"].isin(["CALL", "PUT"])].copy()
        if len(taken) > 0:
            wins = (taken["Result"] == "WIN").sum()
            losses = (taken["Result"] == "LOSS").sum()
            print(f"\nTrades Taken: {len(taken)} | Wins: {wins} | Losses: {losses} | WinRate: {wins/(wins+losses)*100:.1f}%")

    # Export raw results
    out = f"spy_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(out, index=False)
    print(f"\n💾 Exported: {out}")

else:
    date_str = sys.argv[1]
    r = analyze_date(date_str)
    print_single_result(r)
