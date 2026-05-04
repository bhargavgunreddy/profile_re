import pandas as pd
import numpy as np
import os
from pathlib import Path

# =========================
# 1. LOAD ALL DATA FROM SPY_15m_data FOLDER
# =========================
print("Loading SPY 15-minute data from SPY_15m_data folder...")

data_dir = "SPY_15m_data"

if not os.path.exists(data_dir):
    raise RuntimeError(f"Directory {data_dir} not found!")

# Get all CSV files
csv_files = sorted([f for f in os.listdir(data_dir) if f.endswith('.csv') and f.startswith('SPY_')])

if not csv_files:
    raise RuntimeError(f"No CSV files found in {data_dir}!")

print(f"Found {len(csv_files)} CSV files")

# Load and combine all CSV files
all_data = []
for i, csv_file in enumerate(csv_files, 1):
    file_path = os.path.join(data_dir, csv_file)
    try:
        df = pd.read_csv(file_path)
        df["Timestamp"] = pd.to_datetime(df["Timestamp"])
        
        # Handle timezone if needed
        if df["Timestamp"].dt.tz is None:
            df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC")
        df["Timestamp"] = df["Timestamp"].dt.tz_convert("US/Eastern")
        
        all_data.append(df)
        if i % 50 == 0 or i == len(csv_files):
            print(f"  Loaded {i}/{len(csv_files)} files...", end="\r")
    except Exception as e:
        print(f"\n  Warning: Error loading {csv_file}: {e}")
        continue

if not all_data:
    raise RuntimeError("No data loaded from CSV files!")

# Combine all dataframes
spy = pd.concat(all_data, ignore_index=True)
spy = spy.sort_values("Timestamp").reset_index(drop=True)

# Rename Timestamp to Datetime for consistency
spy.rename(columns={"Timestamp": "Datetime"}, inplace=True)

# Extract Date and Time
spy["Date"] = spy["Datetime"].dt.date
spy["Time"] = spy["Datetime"].dt.strftime("%H:%M")

print(f"\n✅ Loaded {len(spy)} total rows from {len(all_data)} files")
print(f"   Date range: {spy['Date'].min()} to {spy['Date'].max()}")
print(f"   Trading days: {spy['Date'].nunique()}")

# =========================
# 2. VWAP (INTRADAY)
# =========================
spy["Typical"] = (spy["High"] + spy["Low"] + spy["Close"]) / 3
# spy["CumVol"] = spy.groupby("Date")["Volume"].cumsum()
# spy["CumTPV"] = (spy["Typical"] * spy["Volume"]).groupby(spy["Date"]).cumsum()
# spy["VWAP"] = spy["CumTPV"] / spy["CumVol"]

# =========================
# 2. VWAP (SAFE METHOD)
# =========================
spy["Typical"] = (spy["High"] + spy["Low"] + spy["Close"]) / 3

spy["TPV"] = spy["Typical"] * spy["Volume"]

spy["CumVol"] = spy.groupby("Date", group_keys=False)["Volume"].cumsum()
spy["CumTPV"] = spy.groupby("Date", group_keys=False)["TPV"].cumsum()

spy["VWAP"] = spy["CumTPV"] / spy["CumVol"]


# =========================
# 3. 9:30 & 9:45 CANDLES
# =========================
c930 = spy[spy["Time"] == "09:30"][["Date", "Open", "Close"]]
c945 = spy[spy["Time"] == "09:45"][["Date", "Open", "Close"]]

merged = pd.merge(
    c930,
    c945,
    on="Date",
    suffixes=("_930", "_945")
)

# =========================
# 4. DIRECTION
# =========================
def direction(o, c):
    if c > o:
        return "Bull"
    elif c < o:
        return "Bear"
    else:
        return "Doji"

merged["Dir_930"] = merged.apply(
    lambda x: direction(x["Open_930"], x["Close_930"]), axis=1
)
merged["Dir_945"] = merged.apply(
    lambda x: direction(x["Open_945"], x["Close_945"]), axis=1
)

# =========================
# 5. VWAP POSITION (9:30)
# =========================
vwap_930 = spy[spy["Time"] == "09:30"][["Date", "Close", "VWAP"]]
vwap_930["VWAP_Pos"] = np.where(
    vwap_930["Close"] > vwap_930["VWAP"], "Above VWAP", "Below VWAP"
)

merged = pd.merge(
    merged,
    vwap_930[["Date", "VWAP_Pos"]],
    on="Date"
)

# =========================
# 6. GAP %
# =========================
daily_close = spy.groupby("Date").tail(1)[["Date", "Close"]]
daily_close["Prev_Close"] = daily_close["Close"].shift(1)

daily_open = spy[spy["Time"] == "09:30"][["Date", "Open"]]

gap = pd.merge(daily_open, daily_close[["Date", "Prev_Close"]], on="Date")
gap["Gap_pct"] = (gap["Open"] - gap["Prev_Close"]) / gap["Prev_Close"] * 100

merged = pd.merge(merged, gap[["Date", "Gap_pct"]], on="Date")

# =========================
# 7. GAP BUCKETS
# =========================
def gap_bucket(g):
    if abs(g) < 0.2:
        return "Small (<0.2%)"
    elif abs(g) < 0.5:
        return "Medium (0.2–0.5%)"
    else:
        return "Large (>0.5%)"

merged["Gap_Bucket"] = merged["Gap_pct"].apply(gap_bucket)

# =========================
# 8. PROBABILITIES
# =========================
bull = merged[merged["Dir_930"] == "Bull"]
bear = merged[merged["Dir_930"] == "Bear"]

bull_to_bear = (
    bull.groupby(["VWAP_Pos", "Gap_Bucket"])["Dir_945"]
    .apply(lambda x: (x == "Bear").mean())
    .reset_index(name="Bull_to_Bear_Prob")
)

bear_to_bull = (
    bear.groupby(["VWAP_Pos", "Gap_Bucket"])["Dir_945"]
    .apply(lambda x: (x == "Bull").mean())
    .reset_index(name="Bear_to_Bull_Prob")
)

# =========================
# 9. OUTPUT
# =========================
print(f"\nSPY – ALL HISTORICAL DATA ({spy['Date'].nunique()} trading days)\n")

print("Bull -> Bear Reversal Probability")
print(bull_to_bear.to_string(index=False))

print("\nBear -> Bull Reversal Probability")
print(bear_to_bull.to_string(index=False))

# =========================
# 10. EXPORT
# =========================
output_file = "SPY_ALL_930_945_VWAP_GAP.csv"
merged.to_csv(output_file, index=False)
print(f"\nCSV exported: {output_file}")
print(f"Total days analyzed: {len(merged)}")
