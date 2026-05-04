import yfinance as yf
import pandas as pd

# Download last few days of daily data
spy = yf.download(
    "SPY",
    period="5d",
    interval="1d",
    progress=False,
    auto_adjust=False
)

# Reset index
spy = spy.reset_index()

# ✅ FIX: Flatten columns if MultiIndex
if isinstance(spy.columns, pd.MultiIndex):
    spy.columns = spy.columns.get_level_values(0)

# Previous close
spy["Prev_Close"] = spy["Close"].shift(1)

# Gap %
spy["Gap_Pct"] = (spy["Open"] - spy["Prev_Close"]) / spy["Prev_Close"] * 100

# Gap bucket
def gap_bucket(g):
    if pd.isna(g):
        return None
    if abs(g) < 0.2:
        return "Small (<0.2%)"
    elif abs(g) < 0.5:
        return "Medium (0.2–0.5%)"
    else:
        return "Large (>0.5%)"

spy["Gap_Bucket"] = spy["Gap_Pct"].apply(gap_bucket)

# Show results
print(spy[["Date", "Open", "Prev_Close", "Gap_Pct", "Gap_Bucket"]])
