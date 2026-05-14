"""
Analyze the 10:31 & 10:32 AM same-color question using ONLY the 1-minute
CSV files already cached under SPY_1m_data/ (or SPX_1m_data/). No API calls.

Reports 30 / 60 / 90 day windows using the most recent analyzable days.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pytz

EASTERN = pytz.timezone("US/Eastern")
TARGET_TIMES = ("10:31", "10:32")


def candle_color(o: float, c: float) -> str:
    if c > o:
        return "green"
    if c < o:
        return "red"
    return "doji"


def analyze_dir(cache_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    files = sorted(cache_dir.glob("*_1m_*.csv"))
    for f in files:
        try:
            df = pd.read_csv(f, parse_dates=["Timestamp"])
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        if df.empty:
            continue
        if df["Timestamp"].dt.tz is None:
            df["Timestamp"] = df["Timestamp"].dt.tz_localize("UTC").dt.tz_convert(EASTERN)
        else:
            df["Timestamp"] = df["Timestamp"].dt.tz_convert(EASTERN)

        date_str = f.stem.split("_1m_")[-1]
        hm = df["Timestamp"].dt.strftime("%H:%M")
        c1_rows = df.loc[hm == TARGET_TIMES[0]]
        c2_rows = df.loc[hm == TARGET_TIMES[1]]
        if c1_rows.empty or c2_rows.empty:
            continue
        c1 = c1_rows.iloc[0]
        c2 = c2_rows.iloc[0]
        color1 = candle_color(float(c1["Open"]), float(c1["Close"]))
        color2 = candle_color(float(c2["Open"]), float(c2["Close"]))
        rows.append({
            "Date": date_str,
            "C1_Open": float(c1["Open"]),
            "C1_Close": float(c1["Close"]),
            "C1_Color": color1,
            "C2_Open": float(c2["Open"]),
            "C2_Close": float(c2["Close"]),
            "C2_Color": color2,
            "SameColor": color1 == color2 and color1 in ("green", "red"),
            "BothGreen": color1 == "green" and color2 == "green",
            "BothRed": color1 == "red" and color2 == "red",
            "HasDoji": "doji" in (color1, color2),
        })
    return pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)


def window_summary(df: pd.DataFrame, n: int) -> dict:
    sub = df.tail(n)
    total = len(sub)
    same = int(sub["SameColor"].sum())
    both_green = int(sub["BothGreen"].sum())
    both_red = int(sub["BothRed"].sum())
    doji = int(sub["HasDoji"].sum())
    opposite = total - same - doji
    pct = (same / total * 100.0) if total else 0.0
    return {
        "window_days": n,
        "trading_days_analyzed": total,
        "same_color": same,
        "same_color_pct": round(pct, 2),
        "both_green": both_green,
        "both_red": both_red,
        "opposite_colors": opposite,
        "had_doji": doji,
    }


def print_summary(title: str, s: dict) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"  Trading days analyzed : {s['trading_days_analyzed']}")
    print(f"  Same color matches    : {s['same_color']}  ({s['same_color_pct']}%)")
    print(f"    - Both GREEN        : {s['both_green']}")
    print(f"    - Both RED          : {s['both_red']}")
    print(f"  Opposite colors       : {s['opposite_colors']}")
    print(f"  Days with a doji      : {s['had_doji']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="SPY_1m_data",
                    help="Cache directory to read (default: SPY_1m_data)")
    args = ap.parse_args()
    cache = Path(__file__).resolve().parent / args.dir
    if not cache.exists():
        print(f"No cache dir: {cache}")
        return 1

    df = analyze_dir(cache)
    if df.empty:
        print("No analyzable days found in cache.")
        return 1

    print(f"Analyzable trading days in cache ({cache.name}): {len(df)}")
    print(f"Date range: {df['Date'].iloc[0]}  ->  {df['Date'].iloc[-1]}")

    print("\nMost recent 10 analyzed days:")
    print(df.tail(10).to_string(index=False))

    print("\n" + "=" * 60)
    print("RESULTS: 10:31 & 10:32 AM same-color candles")
    print("=" * 60)
    for n in (30, 60, 90):
        print_summary(f"Last {n} trading days (from cache)", window_summary(df, n))

    return 0


if __name__ == "__main__":
    sys.exit(main())
