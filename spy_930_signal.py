import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date

# ============================
# USER CONFIG
# ============================
PROB_THRESHOLD = 0.60   # Minimum probability to take trade

# ============================
# REVERSAL PROBABILITY TABLE
# ============================
# Based on your analysis (LAST 60 DAYS)
REVERSAL_PROBS = {
    # Bull → Bear
    ("Bull", "Above VWAP", "Large"): 0.125,
    ("Bull", "Above VWAP", "Medium"): 0.625,
    ("Bull", "Above VWAP", "Small"): 0.545,
    ("Bull", "Below VWAP", "Large"): 0.000,
    ("Bull", "Below VWAP", "Medium"): 0.000,
    ("Bull", "Below VWAP", "Small"): 0.000,

    # Bear → Bull
    ("Bear", "Below VWAP", "Large"): 0.428,
    ("Bear", "Below VWAP", "Medium"): 0.667,
    ("Bear", "Below VWAP", "Small"): 0.000,
    ("Bear", "Above VWAP", "Small"): 0.000,
}

# ============================
# 1. DAILY DATA → GAP
# ============================
daily = yf.download(
    "SPY",
    period="5d",
    interval="1d",
    auto_adjust=False,
    progress=False
).reset_index()

if isinstance(daily.columns, pd.MultiIndex):
    daily.columns = daily.columns.get_level_values(0)

daily["Prev_Close"] = daily["Close"].shift(1)
daily["Gap_Pct"] = (daily["Open"] - daily["Prev_Close"]) / daily["Prev_Close"] * 100

# def gap_bucket(g):
#     if abs(g) < 0.2:
#         return "Small"
#     elif abs(g) < 0.5:
#         return "Medium"
#     else:
#         return "Large"

def gap_bucket(g):
    g = abs(g)
    if g < 0.2:
        return "Small (<0.2%)"
    elif g < 0.5:
        return "Medium (0.2–0.5%)"
    else:
        return "Large (>0.5%)"

daily["Gap_Bucket"] = daily["Gap_Pct"].apply(gap_bucket)
gap_bucket_today = daily.iloc[-1]["Gap_Bucket"]

# ============================
# 2. INTRADAY DATA (15m)
# ============================
intraday = yf.download(
    "SPY",
    interval="15m",
    period="1d",
    auto_adjust=False,
    progress=False
).reset_index()

if isinstance(intraday.columns, pd.MultiIndex):
    intraday.columns = intraday.columns.get_level_values(0)

intraday["Datetime"] = pd.to_datetime(intraday["Datetime"], utc=True).dt.tz_convert("US/Eastern")
intraday["Time"] = intraday["Datetime"].dt.strftime("%H:%M")
intraday["Date"] = intraday["Datetime"].dt.date

today = date.today()
intraday = intraday[intraday["Date"] == today]

# ============================
# 3. VWAP
# ============================
intraday["Typical"] = (intraday["High"] + intraday["Low"] + intraday["Close"]) / 3
intraday["TPV"] = intraday["Typical"] * intraday["Volume"]

intraday["CumVol"] = intraday["Volume"].cumsum()
intraday["CumTPV"] = intraday["TPV"].cumsum()
intraday["VWAP"] = intraday["CumTPV"] / intraday["CumVol"]

# ============================
# 4. 9:30 CANDLE
# ============================
candle_930 = intraday[intraday["Time"] == "09:30"].iloc[0]

close_930 = candle_930["Close"]
vwap_930 = candle_930["VWAP"]

dir_930 = "Bull" if close_930 > vwap_930 else "Bear"
vwap_pos = "Above VWAP" if close_930 > vwap_930 else "Below VWAP"

# ============================
# 5. DECISION ENGINE
# ============================
prob = REVERSAL_PROBS.get(
    (dir_930, vwap_pos, gap_bucket_today),
    0
)

if prob >= PROB_THRESHOLD:
    trade = "PUT" if dir_930 == "Bull" else "CALL"
else:
    trade = "SKIP"

# ============================
# 6. OUTPUT
# ============================
print("\n📊 SPY 9:30 TRADE SIGNAL\n")
print(f"Gap Bucket        : {gap_bucket_today}")
print(f"9:30 Close        : {close_930:.2f}")
print(f"9:30 VWAP         : {vwap_930:.2f}")
print(f"Direction @ 9:30  : {dir_930}")
print(f"VWAP Position     : {vwap_pos}")
print(f"Reversal Prob     : {prob:.2f}")
print("\n==========================")
print(f"🔥 TRADE DECISION: {trade}")
print("==========================\n")
