"""
Simple example: Get 15-minute candle data from Polygon.io API

Requirements:
1. Get a free API key from https://polygon.io
2. Set environment variable: export POLYGON_API_KEY="your_key_here"
3. Or set it directly in the script (not recommended for production)
"""

import os
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta
from pathlib import Path
import sys

# allow importing helpers from src/polygon when running from repo root
sys.path.append(str(Path(__file__).resolve().parent / "src" / "polygon"))
from polygon_secrets import get_polygon_api_key

# Configuration
API_KEY = get_polygon_api_key()
SYMBOL = "SPY"  # Ticker symbol
EASTERN = pytz.timezone("US/Eastern")

def get_polygon_15m_data(ticker, date, api_key):
    """
    Get 15-minute candle data from Polygon.io for a specific date
    
    Parameters:
    -----------
    ticker : str
        Stock ticker symbol (e.g., "SPY", "AAPL")
    date : str
        Date in YYYY-MM-DD format (e.g., "2024-03-15")
    api_key : str
        Your Polygon.io API key
    
    Returns:
    --------
    pandas.DataFrame
        DataFrame with columns: Timestamp, Open, High, Low, Close, Volume
    """
    
    # Polygon.io API endpoint for aggregates (candles)
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/15/minute/{date}/{date}"
    
    params = {
        "adjusted": "true",  # Adjusted for splits and dividends
        "sort": "asc",       # Sort ascending by time
        "limit": 50000,      # Max number of results (should be enough for 1 day)
        "apiKey": api_key
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()  # Raise an error for bad status codes
        
        data = response.json()
        
        if data.get("status") == "OK" and data.get("resultsCount", 0) > 0:
            # Convert results to DataFrame
            df = pd.DataFrame(data["results"])
            
            # Convert timestamp (milliseconds) to datetime
            df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
            
            # Rename columns to standard names
            df.rename(columns={
                "timestamp": "Timestamp",
                "o": "Open",
                "h": "High",
                "l": "Low",
                "c": "Close",
                "v": "Volume",
                "vw": "VWAP",  # Volume weighted average price (optional)
                "n": "Count"   # Number of transactions (optional)
            }, inplace=True)
            
            # Select only the columns we need
            df = df[["Timestamp", "Open", "High", "Low", "Close", "Volume"]].copy()
            
            # Convert timezone to Eastern
            if df["Timestamp"].dt.tz is None:
                df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC")
            df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)
            
            # Sort by timestamp
            df = df.sort_values("Timestamp").reset_index(drop=True)
            
            return df
        else:
            print(f"No data returned for {ticker} on {date}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Error making API request: {e}")
        return None
    except Exception as e:
        print(f"Error processing data: {e}")
        return None


def get_multiple_days(ticker, start_date, end_date, api_key):
    """
    Get 15-minute data for multiple days
    
    Parameters:
    -----------
    ticker : str
        Stock ticker symbol
    start_date : str
        Start date in YYYY-MM-DD format
    end_date : str
        End date in YYYY-MM-DD format
    api_key : str
        Polygon.io API key
    
    Returns:
    --------
    pandas.DataFrame
        Combined DataFrame for all days
    """
    
    all_data = []
    current_date = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    
    print(f"Downloading {ticker} 15-minute data from {start_date} to {end_date}...")
    
    while current_date <= end_dt:
        date_str = current_date.strftime("%Y-%m-%d")
        
        # Skip weekends
        if current_date.weekday() >= 5:
            print(f"Skipping weekend: {date_str}")
            current_date += timedelta(days=1)
            continue
        
        print(f"Downloading {date_str}...", end=" ")
        df_day = get_polygon_15m_data(ticker, date_str, api_key)
        
        if df_day is not None and not df_day.empty:
            all_data.append(df_day)
            print(f"✅ {len(df_day)} bars")
        else:
            print("❌ No data")
        
        current_date += timedelta(days=1)
        
        # Rate limiting: Free tier allows 5 calls per minute
        # Wait 12 seconds between calls to stay within limit
        import time
        time.sleep(12)
    
    if not all_data:
        print("No data downloaded")
        return None
    
    # Combine all days
    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.sort_values("Timestamp").reset_index(drop=True)
    
    print(f"\n✅ Total: {len(combined)} bars downloaded")
    return combined


# Example usage
if __name__ == "__main__":
    # Check if API key is set
    if API_KEY == "YOUR_API_KEY_HERE" or not API_KEY:
        print("❌ ERROR: Please set your Polygon.io API key")
        print("   Option 1: Set environment variable")
        print("   export POLYGON_API_KEY='your_key_here'")
        print("\n   Option 2: Edit this script and set API_KEY directly")
        print("\n   Get free API key at: https://polygon.io")
        exit(1)
    
    # Example 1: Get data for a single day
    print("=" * 60)
    print("Example 1: Get data for a single day")
    print("=" * 60)
    single_day_data = get_polygon_15m_data("SPY", "2024-03-15", API_KEY)
    
    if single_day_data is not None:
        print(f"\nDownloaded {len(single_day_data)} bars")
        print("\nFirst few rows:")
        print(single_day_data.head())
        print("\nLast few rows:")
        print(single_day_data.tail())
        
        # Save to CSV
        single_day_data.to_csv("SPY_15m_2024-03-15.csv", index=False)
        print("\n✅ Saved to: SPY_15m_2024-03-15.csv")
    
    # Example 2: Get data for multiple days (commented out to avoid using all API calls)
    # print("\n" + "=" * 60)
    # print("Example 2: Get data for multiple days")
    # print("=" * 60)
    # multi_day_data = get_multiple_days("SPY", "2024-03-10", "2024-03-15", API_KEY)
    # 
    # if multi_day_data is not None:
    #     multi_day_data.to_csv("SPY_15m_multiple_days.csv", index=False)
    #     print("✅ Saved to: SPY_15m_multiple_days.csv")

