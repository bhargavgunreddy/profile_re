import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz

# ===============================
# CONFIG
# ===============================
SYMBOL = "SPY"
ACCOUNT_SIZE = 100_000
EV_THRESHOLD = 0.0
ATR_LOOKBACK = 14

# ===============================
# PROBABILITY TABLES (FROM YOUR RESEARCH)
# ===============================
BULL_TO_BEAR = {
    ("Above VWAP", "Small (<0.2%)"): 0.500,
    ("Above VWAP", "Medium (0.2–0.5%)"): 0.625,
    ("Above VWAP", "Large (>0.5%)"): 0.225,
}

BEAR_TO_BULL = {
    ("Below VWAP", "Small (<0.2%)"): 0.583,
    ("Below VWAP", "Medium (0.2–0.5%)"): 0.667,
    ("Below VWAP", "Large (>0.5%)"): 0.50,
}

# ===============================
# HELPERS
# ===============================
def gap_bucket(g):
    g = abs(g)
    if g < 0.2:
        return "Small (<0.2%)"
    elif g < 0.5:
        return "Medium (0.2–0.5%)"
    else:
        return "Large (>0.5%)"

def calc_atr(df):
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum(
            abs(df["High"] - df["Close"].shift()),
            abs(df["Low"] - df["Close"].shift())
        )
    )
    return tr.rolling(ATR_LOOKBACK).mean()

# ===============================
# DOWNLOAD DATA
# ===============================
print("Downloading SPY 15-minute data (last 60 days)...")

spy = yf.download(
    SYMBOL,
    interval="15m",
    period="60d",
    auto_adjust=True,
    progress=False
)

spy = yf.download(
    SYMBOL,
    interval="15m",
    period="60d",
    auto_adjust=True,
    progress=False
)

# ---- FIX MULTIINDEX COLUMNS ----
if isinstance(spy.columns, pd.MultiIndex):
    spy.columns = spy.columns.get_level_values(0)

spy = spy.reset_index()
spy["Datetime"] = spy["Datetime"].dt.tz_convert("US/Eastern")
spy["Date"] = spy["Datetime"].dt.date
spy["Time"] = spy["Datetime"].dt.time

# ===============================
# VWAP
# ===============================
spy["Typical"] = (spy["High"] + spy["Low"] + spy["Close"]) / 3
spy["TPV"] = spy["Typical"] * spy["Volume"]
spy["CumTPV"] = spy.groupby("Date")["TPV"].cumsum()
spy["CumVol"] = spy.groupby("Date")["Volume"].cumsum()
spy["VWAP"] = spy["CumTPV"] / spy["CumVol"]

# ===============================
# ATR
# ===============================
spy["ATR"] = calc_atr(spy)

# ===============================
# TODAY DATA
# ===============================
today = spy["Date"].iloc[-1]
today_df = spy[spy["Date"] == today]

open_930 = today_df[today_df["Time"] == datetime.strptime("09:30", "%H:%M").time()].iloc[0]
open_945 = today_df[today_df["Time"] == datetime.strptime("09:45", "%H:%M").time()].iloc[0]

# ===============================
# CORE VARIABLES
# ===============================
dir_930 = "Bull" if open_930["Close"] > open_930["Open"] else "Bear"
vwap_pos = "Above VWAP" if open_930["Close"] > open_930["VWAP"] else "Below VWAP"

prev_close = spy[spy["Date"] < today].iloc[-1]["Close"]
gap_pct = (open_930["Open"] - prev_close) / prev_close * 100
gap_cat = gap_bucket(gap_pct)

# ===============================
# DAY TYPE CLASSIFIER
# ===============================
range_930_10 = today_df[
    (today_df["Time"] >= datetime.strptime("09:30", "%H:%M").time()) &
    (today_df["Time"] <= datetime.strptime("10:00", "%H:%M").time())
]

range_pct = (range_930_10["High"].max() - range_930_10["Low"].min()) / prev_close
atr_today = today_df["ATR"].iloc[-1]

vwap_slope = today_df["VWAP"].iloc[-1] - today_df["VWAP"].iloc[0]

# if abs(vwap_slope) > 0.1 and range_pct > 0.004:
#     day_type = "TREND"
# else:
#     day_type = "RANGE"

below_vwap_pct = (range_930_10["Close"] < range_930_10["VWAP"]).mean()

if (
    below_vwap_pct > 0.7 and
    vwap_slope < -0.1 and
    range_pct > 0.003
):
    day_type = "TREND"
else:
    day_type = "RANGE"
# ===============================
# REVERSAL MODULE
# ===============================

if day_type == "TREND":
    reversal_prob = 0
    expected = None
    
if day_type == "RANGE":
    if dir_930 == "Bull":
        reversal_prob = BULL_TO_BEAR.get((vwap_pos, gap_cat), 0)
        expected = "PUT"
    else:
        reversal_prob = BEAR_TO_BULL.get((vwap_pos, gap_cat), 0)
        expected = "CALL"

# ===============================
# CONTINUATION MODULE
# ===============================
continuation_score = 0

if day_type == "TREND":
    if dir_930 == "Bear" and vwap_pos == "Below VWAP":
        continuation_score += 3
    if abs(vwap_slope) > 0.15:
        continuation_score += 2
    if abs(gap_pct) > 0.2:
        continuation_score += 1
    if today_df["Volume"].iloc[0] > today_df["Volume"].rolling(20).mean().iloc[-1]:
        continuation_score += 2

# ===============================
# EXPECTED VALUE
# ===============================
avg_win = 1.8
avg_loss = 1.0

if day_type == "RANGE":
    ev = (reversal_prob * avg_win) - ((1 - reversal_prob) * avg_loss)
else:
    ev = (continuation_score / 7) * avg_win - ((1 - continuation_score / 7) * avg_loss)

if day_type == "RANGE":
    ev *= 0.7   # realism penalty
# ===============================
# TRADE DECISION
# ===============================
if ev > EV_THRESHOLD:
    size = ACCOUNT_SIZE * min(ev, 0.02)
    trade = expected if day_type == "RANGE" else ("PUT" if dir_930 == "Bear" else "CALL")
else:
    size = 0
    trade = "SKIP"

# ===============================
# OUTPUT
# ===============================
print("\n📊 SPY TRADE ENGINE\n")
print(f"Day Type        : {day_type}")
print(f"Gap Bucket      : {gap_cat}")
print(f"9:30 Direction  : {dir_930}")
print(f"VWAP Position   : {vwap_pos}")
print(f"Expected Value  : {round(ev, 3)}")
print("\n==========================")
print(f"🔥 TRADE DECISION: {trade}")
print(f"💰 POSITION SIZE: ${round(size, 2)}")
print("==========================")

# ===============================
# JOURNAL
# ===============================
log = pd.DataFrame([{
    "Date": today,
    "Day_Type": day_type,
    "Direction_930": dir_930,
    "VWAP_Pos": vwap_pos,
    "Gap_Bucket": gap_cat,
    "EV": ev,
    "Trade": trade,
    "Size": size
}])

log.to_csv("spy_trade_journal.csv", mode="a", header=not pd.io.common.file_exists("spy_trade_journal.csv"), index=False)
