import yfinance as yf
import pandas as pd
import numpy as np

# =========================
# 1. DOWNLOAD LAST 60 DAYS
# =========================
print("Downloading SPY 15-minute data (last 60 days)...")

spy = yf.download(
    "SPY",
    interval="15m",
    period="60d",
    auto_adjust=False,
    progress=False
)

if spy.empty:
    raise RuntimeError("No data returned from Yahoo")

spy = spy.reset_index()

# Flatten columns if multi-index (Yahoo sometimes returns multi-index)
if isinstance(spy.columns, pd.MultiIndex):
    spy.columns = spy.columns.get_level_values(0)

# Convert timezone
spy["Datetime"] = pd.to_datetime(spy["Datetime"], utc=True).dt.tz_convert("US/Eastern")
spy["Date"] = spy["Datetime"].dt.date
spy["Time"] = spy["Datetime"].dt.strftime("%H:%M")

print(f"Rows downloaded: {len(spy)}")

# =========================
# 2. VWAP CALCULATION
# =========================
spy["Typical"] = (spy["High"] + spy["Low"] + spy["Close"]) / 3
spy["TPV"] = spy["Typical"] * spy["Volume"]

spy["CumVol"] = spy.groupby("Date")["Volume"].cumsum()
spy["CumTPV"] = spy.groupby("Date")["TPV"].cumsum()
spy["VWAP"] = spy["CumTPV"] / spy["CumVol"]

# =========================
# 3. DAILY GAP AND GAP BUCKET
# =========================
daily_close = spy.groupby("Date").tail(1)[["Date", "Close"]].copy()
daily_close["Prev_Close"] = daily_close["Close"].shift(1)

daily_open = spy[spy["Time"] == "09:30"][["Date", "Open"]].copy()
gap = pd.merge(daily_open, daily_close[["Date", "Prev_Close"]], on="Date")
gap["Gap_Pct"] = (gap["Open"] - gap["Prev_Close"]) / gap["Prev_Close"] * 100

def gap_bucket(g):
    if abs(g) < 0.2:
        return "Small (<0.2%)"
    elif abs(g) < 0.5:
        return "Medium (0.2–0.5%)"
    else:
        return "Large (>0.5%)"

gap["Gap_Bucket"] = gap["Gap_Pct"].apply(gap_bucket)

# Merge gap info back into spy
spy = pd.merge(spy, gap[["Date", "Gap_Pct", "Gap_Bucket"]], on="Date", how="left")

# =========================
# 4. 9:30 & 9:45 CANDLES
# =========================
open_930 = spy[spy["Time"] == "09:30"][["Date", "Close", "VWAP", "Gap_Pct", "Gap_Bucket", "Volume"]].copy()
open_945 = spy[spy["Time"] == "09:45"][["Date", "Close"]].copy()

open_930.rename(columns={"Close": "Close_930", "VWAP": "VWAP_930"}, inplace=True)
open_945.rename(columns={"Close": "Close_945"}, inplace=True)

# =========================
# 5. DIRECTION LOGIC
# =========================
open_930["Dir_930"] = np.where(open_930["Close_930"] > open_930["VWAP_930"], "Bull",
                               np.where(open_930["Close_930"] < open_930["VWAP_930"], "Bear", "Doji"))

open_945["Dir_945"] = np.where(open_945["Close_945"] > open_930.set_index("Date").loc[open_945["Date"], "Close_930"].values,
                               "Bull",
                               np.where(open_945["Close_945"] < open_930.set_index("Date").loc[open_945["Date"], "Close_930"].values,
                                        "Bear", "Doji"))

# =========================
# 6. MERGE 9:30 AND 9:45
# =========================
merged = pd.merge(open_930, open_945[["Date", "Dir_945"]], on="Date", how="inner")

# =========================
# 7. VOLUME SPIKE DETECTION
# =========================
vol_930 = spy[spy["Time"] == "09:30"][["Date", "Volume"]].copy()
vol_930["AvgVol20"] = vol_930["Volume"].rolling(20).mean()
vol_930["Vol_Ratio"] = vol_930["Volume"] / vol_930["AvgVol20"]
vol_930["Vol_Spike"] = np.where(vol_930["Vol_Ratio"] >= 1.5, "High Volume", "Normal Volume")

merged = pd.merge(merged, vol_930[["Date", "Vol_Spike"]], on="Date", how="left")

# =========================
# 8. PROBABILITIES + SAMPLE SIZE
# =========================
bull = merged[merged["Dir_930"] == "Bull"]
bear = merged[merged["Dir_930"] == "Bear"]

bull_to_bear = (
    bull.groupby(["VWAP_930", "Gap_Bucket", "Vol_Spike"])
    .agg(
        Probability=("Dir_945", lambda x: (x == "Bear").mean()),
        Samples=("Dir_945", "count")
    )
    .reset_index()
)

bear_to_bull = (
    bear.groupby(["VWAP_930", "Gap_Bucket", "Vol_Spike"])
    .agg(
        Probability=("Dir_945", lambda x: (x == "Bull").mean()),
        Samples=("Dir_945", "count")
    )
    .reset_index()
)

# =========================
# 9. OUTPUT
# =========================
print("\nSPY – LAST 60 DAYS\n")

print("Bull -> Bear Reversal Probability")
print(bull_to_bear.to_string(index=False))

print("\nBear -> Bull Reversal Probability")
print(bear_to_bull.to_string(index=False))

# =========================
# 10. EXPORT
# =========================
merged.to_csv("SPY_60D_930_945_VWAP_GAP_VOL.csv", index=False)
print("\nCSV exported: SPY_60D_930_945_VWAP_GAP_VOL.csv")
