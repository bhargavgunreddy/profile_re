import pandas as pd
import numpy as np
from pathlib import Path
import argparse

# -----------------------
# CONFIG
# -----------------------
FILE_5M = "data_5m.csv"
FILE_15M = "data_15m.csv"

TZ = "America/New_York"  # set to None to disable timezone localization/conversion
RTH_ONLY = True
RTH_START = "09:30"
RTH_END = "16:00"

# "clean breakout" definition
BREAKOUT_ON = "close"  # "close" recommended. options: "close" or "highlow"
BREAKOUT_BUFFER = 0.0  # e.g. 0.05 for 5 cents buffer to avoid tiny breaches

# Require the breakout to be "clear" (avoid marginal range peeks that aren't real breakouts).
# Default is relative to the 15m opening range size.
CLEAR_BREAKOUT_MIN_RANGE_FRAC = 0.10  # breakout must exceed range line by >= 10% of (range_high-range_low)

# Optional: require a post-breakout confirmation candle before we even look for the pullback entry.
REQUIRE_BREAKOUT_CONFIRMATION = True

# Entry refinement: after the pullback touches the range line, wait for a confirmation candle in the breakout direction
# and enter at that candle's close (reduces chop / fake pullback entries).
WAIT_ENTRY_CONFIRMATION = True
ENTRY_CONFIRM_ON = "close"  # "close" or "highlow"
ENTRY_CONFIRM_BUFFER = 0.0  # buffer applied to confirm candle test (in price units)
# If True, require the confirmation candle to be "clean" (no wick back into the opening range).
# For LONG: Low must be above range_high (plus buffer). For SHORT: High must be below range_low (minus buffer).
ENTRY_CONFIRM_CLEAN = True
# If set, only allow the confirmation candle to appear within N 5m bars after the pullback touch.
# Example: 1 means: only the *next* 5m candle can confirm; otherwise skip the day.
MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH = 1

# entry fill model
ENTRY_AT = "range_line"  # "range_line" or "next_close"
EOD_EXIT = "last_close"  # "last_close" or "none" (if "none", ignore unresolved trades)

# only take the first valid trade per day
ONE_TRADE_PER_DAY = True


# -----------------------
# HELPERS
# -----------------------
def load_ohlc_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # expected columns: Timestamp, Open, High, Low, Close (Volume optional)
    # Normalize column names
    df.columns = [c.strip().capitalize() for c in df.columns]
    if "Timestamp" not in df.columns:
        raise ValueError(f"{path} must have a 'Timestamp' column.")
    for col in ["Open", "High", "Low", "Close"]:
        if col not in df.columns:
            raise ValueError(f"{path} missing required column: {col}")

    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    df = df.sort_values("Timestamp").set_index("Timestamp")

    # timezone handling (optional)
    if TZ:
        if df.index.tz is None:
            df.index = df.index.tz_localize(TZ)
        else:
            df.index = df.index.tz_convert(TZ)

    return df


def load_polygon_5m_cache_dir(cache_dir: str | Path, ticker: str = "SPY") -> pd.DataFrame:
    """
    Load cached Polygon 5-min CSVs like: SPY_5min_2025-01.csv
    Expected columns: Datetime, Open, High, Low, Close, Volume
    Datetime is tz-aware (UTC) in your cache.
    Returns a DataFrame indexed by Timestamp (DatetimeIndex).
    """
    cache_dir = Path(cache_dir)
    files = sorted(cache_dir.glob(f"{ticker}_5min_*.csv"))
    if not files:
        raise FileNotFoundError(f"No 5m cache files found in {cache_dir} matching {ticker}_5min_*.csv")

    dfs: list[pd.DataFrame] = []
    for fp in files:
        d = pd.read_csv(fp, parse_dates=["Datetime"])
        d["Datetime"] = pd.to_datetime(d["Datetime"], utc=True)
        d = d.rename(columns={"Datetime": "Timestamp"})
        d = d.set_index("Timestamp").sort_index()
        # normalize columns to match strategy expectations
        d.columns = [c.strip().capitalize() for c in d.columns]
        dfs.append(d)

    out = pd.concat(dfs).sort_index()
    out = out[~out.index.duplicated(keep="first")]

    # timezone handling (optional)
    if TZ:
        if out.index.tz is None:
            out.index = out.index.tz_localize(TZ)
        else:
            out.index = out.index.tz_convert(TZ)

    return out


def load_15m_folder(folder: str | Path, ticker: str = "SPY") -> pd.DataFrame:
    """
    Load per-day 15m CSVs like: SPY_15m_2025-01-02.csv
    Expected columns: Timestamp, Open, High, Low, Close (Volume optional)
    Timestamp usually includes an offset (tz-aware) in your files.
    Returns a DataFrame indexed by Timestamp (DatetimeIndex).
    """
    folder = Path(folder)
    files = sorted(folder.glob(f"{ticker}_15m_*.csv"))
    if not files:
        raise FileNotFoundError(f"No 15m files found in {folder} matching {ticker}_15m_*.csv")

    dfs: list[pd.DataFrame] = []
    for fp in files:
        d = pd.read_csv(fp)
        d.columns = [c.strip().capitalize() for c in d.columns]
        if "Timestamp" not in d.columns:
            raise ValueError(f"{fp} must have a 'Timestamp' column.")
        d["Timestamp"] = pd.to_datetime(d["Timestamp"], errors="coerce")
        if d["Timestamp"].isna().any():
            raise ValueError(f"{fp} has unparsable Timestamp values.")
        d = d.sort_values("Timestamp").set_index("Timestamp")

        # Normalize timezone per-file BEFORE concatenation to avoid mixed-tz object Index.
        if TZ:
            if isinstance(d.index, pd.DatetimeIndex) and d.index.tz is None:
                d.index = d.index.tz_localize(TZ)
            else:
                # tz-aware (possibly fixed offset) -> convert to named TZ
                d.index = pd.DatetimeIndex(d.index).tz_convert(TZ)
        dfs.append(d)

    out = pd.concat(dfs).sort_index()
    out = out[~out.index.duplicated(keep="first")]

    # Ensure we have a real DatetimeIndex (not object Index)
    out.index = pd.DatetimeIndex(out.index)

    return out


def filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    if not RTH_ONLY:
        return df
    return df.between_time(RTH_START, RTH_END).copy()


def get_first_15m_range(df15_day: pd.DataFrame):
    # first 15m candle of the day
    first = df15_day.iloc[0]
    return float(first["High"]), float(first["Low"]), df15_day.index[0]


def is_breakout_long(bar, range_high):
    if BREAKOUT_ON == "close":
        return bar["Close"] > (range_high + BREAKOUT_BUFFER)
    else:
        return bar["High"] > (range_high + BREAKOUT_BUFFER)


def is_breakout_short(bar, range_low):
    if BREAKOUT_ON == "close":
        return bar["Close"] < (range_low - BREAKOUT_BUFFER)
    else:
        return bar["Low"] < (range_low - BREAKOUT_BUFFER)

def breakout_strength_ok(bar, range_high: float, range_low: float, direction: str) -> bool:
    """
    Filter out marginal breaches of the range.
    We require the breakout to exceed the range line by at least a fraction of the opening range size.
    """
    rng = float(range_high - range_low)
    if rng <= 0:
        return False
    min_move = CLEAR_BREAKOUT_MIN_RANGE_FRAC * rng

    if direction == "LONG":
        if BREAKOUT_ON == "close":
            move = float(bar["Close"]) - (range_high + BREAKOUT_BUFFER)
        else:
            move = float(bar["High"]) - (range_high + BREAKOUT_BUFFER)
        return move >= min_move
    else:
        if BREAKOUT_ON == "close":
            move = (range_low - BREAKOUT_BUFFER) - float(bar["Close"])
        else:
            move = (range_low - BREAKOUT_BUFFER) - float(bar["Low"])
        return move >= min_move


def confirms_long(bar, range_high: float) -> bool:
    if ENTRY_CONFIRM_ON == "close":
        ok = float(bar["Close"]) > (range_high + ENTRY_CONFIRM_BUFFER)
    else:
        ok = float(bar["High"]) > (range_high + ENTRY_CONFIRM_BUFFER)
    if not ok:
        return False
    if ENTRY_CONFIRM_CLEAN:
        return float(bar["Low"]) >= (range_high + ENTRY_CONFIRM_BUFFER)
    return True


def confirms_short(bar, range_low: float) -> bool:
    if ENTRY_CONFIRM_ON == "close":
        ok = float(bar["Close"]) < (range_low - ENTRY_CONFIRM_BUFFER)
    else:
        ok = float(bar["Low"]) < (range_low - ENTRY_CONFIRM_BUFFER)
    if not ok:
        return False
    if ENTRY_CONFIRM_CLEAN:
        return float(bar["High"]) <= (range_low - ENTRY_CONFIRM_BUFFER)
    return True


def backtest_day(df5_day: pd.DataFrame, range_high: float, range_low: float):
    """
    Returns a dict describing the trade for that day, or None if no trade.
    Strategy:
      - detect first breakout (either direction)
      - after breakout, wait for pullback touch to range line
      - enter at range line (or next close)
      - stop at opposite side of 15m range
      - target at 2R
      - determine which hits first
    """
    if df5_day.empty or len(df5_day) < 5:
        return None

    # after the first 15m candle closes:
    # first 15m candle covers 9:30-9:45, so trading starts after 9:45 bar opens.
    # With 5m bars, that means start evaluating from the first bar with timestamp >= 09:45.
    day_start = df5_day.index[0].normalize()
    t_trade_start = day_start + pd.Timedelta(hours=9, minutes=45)

    df = df5_day[df5_day.index >= t_trade_start].copy()
    if df.empty:
        return None

    breakout_dir = None
    breakout_time = None
    confirm_time = None

    # 1) find first breakout
    for ts, bar in df.iterrows():
        if is_breakout_long(bar, range_high) and breakout_strength_ok(bar, range_high, range_low, "LONG"):
            breakout_dir = "LONG"
            breakout_time = ts
            break
        if is_breakout_short(bar, range_low) and breakout_strength_ok(bar, range_high, range_low, "SHORT"):
            breakout_dir = "SHORT"
            breakout_time = ts
            break

    if breakout_dir is None:
        return None

    # Optional: require a post-breakout confirmation candle (but do NOT shift the window;
    # we still want to allow a pullback touch that happens before the confirmation candle).
    after_breakout = df[df.index > breakout_time]
    if REQUIRE_BREAKOUT_CONFIRMATION:
        if after_breakout.empty:
            return None
        if breakout_dir == "LONG":
            conf = after_breakout[after_breakout.apply(lambda r: is_breakout_long(r, range_high), axis=1)]
        else:
            conf = after_breakout[after_breakout.apply(lambda r: is_breakout_short(r, range_low), axis=1)]
        if conf.empty:
            return None
        confirm_time = conf.index[0]

    entry_time = None
    entry_price = None
    stop_price = None
    target_price = None

    if breakout_dir == "LONG":
        # touch range_high again: candle low <= range_high
        touches = after_breakout[after_breakout["Low"] <= range_high]
        if touches.empty:
            return None

        touch_time = touches.index[0]
        if WAIT_ENTRY_CONFIRMATION:
            after_touch = after_breakout[after_breakout.index > touch_time]
            if MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH is not None:
                after_touch = after_touch.iloc[:MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH]
            if after_touch.empty:
                return None
            conf = after_touch[after_touch.apply(lambda r: confirms_long(r, range_high), axis=1)]
            if conf.empty:
                return None
            entry_time = conf.index[0]
            entry_price = float(after_breakout.loc[entry_time, "Close"])
        else:
            if ENTRY_AT == "range_line":
                entry_time = touch_time
                entry_price = range_high
            else:
                # enter at next candle close
                idx = after_breakout.index.get_indexer([touch_time])[0]
                if idx + 1 >= len(after_breakout):
                    return None
                entry_time = after_breakout.index[idx + 1]
                entry_price = float(after_breakout.loc[entry_time, "Close"])

        stop_price = range_low
        risk = entry_price - stop_price
        if risk <= 0:
            return None
        target_price = entry_price + 2.0 * risk

    else:  # SHORT
        # touch range_low again: candle high >= range_low
        touches = after_breakout[after_breakout["High"] >= range_low]
        if touches.empty:
            return None

        touch_time = touches.index[0]
        if WAIT_ENTRY_CONFIRMATION:
            after_touch = after_breakout[after_breakout.index > touch_time]
            if MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH is not None:
                after_touch = after_touch.iloc[:MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH]
            if after_touch.empty:
                return None
            conf = after_touch[after_touch.apply(lambda r: confirms_short(r, range_low), axis=1)]
            if conf.empty:
                return None
            entry_time = conf.index[0]
            entry_price = float(after_breakout.loc[entry_time, "Close"])
        else:
            if ENTRY_AT == "range_line":
                entry_time = touch_time
                entry_price = range_low
            else:
                idx = after_breakout.index.get_indexer([touch_time])[0]
                if idx + 1 >= len(after_breakout):
                    return None
                entry_time = after_breakout.index[idx + 1]
                entry_price = float(after_breakout.loc[entry_time, "Close"])

        stop_price = range_high
        risk = stop_price - entry_price
        if risk <= 0:
            return None
        target_price = entry_price - 2.0 * risk

    # 3) walk forward candle-by-candle to see which hits first
    trade_bars = df[df.index >= entry_time].copy()
    if trade_bars.empty:
        return None

    exit_time = None
    exit_price = None
    outcome = None  # "TP" / "SL" / "EOD"

    for ts, bar in trade_bars.iterrows():
        hi = float(bar["High"])
        lo = float(bar["Low"])

        if breakout_dir == "LONG":
            # Intra-bar ordering is unknown; choose conservative (stop hits first if both hit).
            sl_hit = lo <= stop_price
            tp_hit = hi >= target_price
            if sl_hit and tp_hit:
                exit_time, exit_price, outcome = ts, stop_price, "SL_both"
                break
            if sl_hit:
                exit_time, exit_price, outcome = ts, stop_price, "SL"
                break
            if tp_hit:
                exit_time, exit_price, outcome = ts, target_price, "TP"
                break
        else:
            sl_hit = hi >= stop_price
            tp_hit = lo <= target_price
            if sl_hit and tp_hit:
                exit_time, exit_price, outcome = ts, stop_price, "SL_both"
                break
            if sl_hit:
                exit_time, exit_price, outcome = ts, stop_price, "SL"
                break
            if tp_hit:
                exit_time, exit_price, outcome = ts, target_price, "TP"
                break

    if exit_time is None:
        if EOD_EXIT == "none":
            return None
        exit_time = trade_bars.index[-1]
        exit_price = float(trade_bars.iloc[-1]["Close"])
        outcome = "EOD"

    # compute R-multiple
    if breakout_dir == "LONG":
        R = (exit_price - entry_price) / (entry_price - stop_price)
    else:
        R = (entry_price - exit_price) / (stop_price - entry_price)

    return {
        "date": str(entry_time.date()),
        "direction": breakout_dir,
        "range_high": range_high,
        "range_low": range_low,
        "breakout_time": breakout_time,
        "confirm_time": confirm_time,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "outcome": outcome,
        "R_multiple": float(R),
    }


def main():
    global TZ
    repo_root = Path(__file__).resolve().parents[2]
    default_polygon_5m_dir = str(Path(__file__).parent / "data" / "polygon")
    default_spy_15m_dir = str(repo_root / "SPY_15m_data")

    ap = argparse.ArgumentParser(description="SPY pullback-to-range strategy (5m entries, 15m first-candle range)")
    ap.add_argument("--ticker", default="SPY")
    ap.add_argument(
        "--polygon-5m-dir",
        default=default_polygon_5m_dir,
        help="Directory containing cached Polygon 5m files like SPY_5min_2025-01.csv",
    )
    ap.add_argument(
        "--spy-15m-dir",
        default=default_spy_15m_dir,
        help="Directory containing per-day 15m files like SPY_15m_2025-01-02.csv",
    )
    ap.add_argument("--file-5m", default=FILE_5M, help="Fallback single 5m CSV (expects a Timestamp column)")
    ap.add_argument("--file-15m", default=FILE_15M, help="Fallback single 15m CSV (expects a Timestamp column)")
    ap.add_argument("--tz", default=TZ if TZ else "", help="Timezone for alignment, e.g. America/New_York (blank disables)")
    args = ap.parse_args()

    TZ = args.tz.strip() or None

    polygon_5m_dir = Path(args.polygon_5m_dir) if args.polygon_5m_dir else None
    spy_15m_dir = Path(args.spy_15m_dir) if args.spy_15m_dir else None

    if polygon_5m_dir and polygon_5m_dir.exists():
        df5 = load_polygon_5m_cache_dir(polygon_5m_dir, ticker=args.ticker)
    elif Path(args.file_5m).exists():
        df5 = load_ohlc_csv(args.file_5m)
    else:
        raise FileNotFoundError(
            "Could not find 5m input.\n"
            f"- Tried cache dir: {polygon_5m_dir}\n"
            f"- Tried file: {args.file_5m}\n"
            "Fix: pass --polygon-5m-dir /path/to/src/polygon/data/polygon (recommended)\n"
            "  or pass --file-5m /path/to/data_5m.csv"
        )

    if spy_15m_dir and spy_15m_dir.exists():
        df15 = load_15m_folder(spy_15m_dir, ticker=args.ticker)
    elif Path(args.file_15m).exists():
        df15 = load_ohlc_csv(args.file_15m)
    else:
        raise FileNotFoundError(
            "Could not find 15m input.\n"
            f"- Tried 15m dir: {spy_15m_dir}\n"
            f"- Tried file: {args.file_15m}\n"
            "Fix: pass --spy-15m-dir /path/to/SPY_15m_data (recommended)\n"
            "  or pass --file-15m /path/to/data_15m.csv"
        )

    df5 = filter_rth(df5)
    df15 = filter_rth(df15)

    # group by day
    df5["date"] = df5.index.date
    df15["date"] = df15.index.date

    trades = []
    for day, df15_day in df15.groupby("date"):
        if len(df15_day) < 1:
            continue

        # Require matching 5m data for the day
        df5_day = df5[df5["date"] == day].drop(columns=["date"], errors="ignore")
        if df5_day.empty:
            continue

        range_high, range_low, first15_ts = get_first_15m_range(df15_day)

        trade = backtest_day(df5_day.drop(columns=["date"], errors="ignore"), range_high, range_low)
        if trade:
            trades.append(trade)
            if ONE_TRADE_PER_DAY:
                pass

    if not trades:
        print("No trades found with the current rules/parameters.")
        return

    res = pd.DataFrame(trades)

    # Summary stats
    total = len(res)
    win = (res["outcome"] == "TP").sum()
    loss = res["outcome"].str.contains("SL").sum()
    eod = (res["outcome"] == "EOD").sum()

    print("\n=== Strategy Results ===")
    print(f"Trades: {total}")
    print(f"Wins (TP): {win} ({win/total:.1%})")
    print(f"Losses (SL): {loss} ({loss/total:.1%})")
    print(f"EOD exits: {eod} ({eod/total:.1%})")
    print(f"Avg R: {res['R_multiple'].mean():.3f}")
    print(f"Median R: {res['R_multiple'].median():.3f}")
    print(f"Total R (sum): {res['R_multiple'].sum():.3f}")

    # Optional: save trades
    res.to_csv("backtest_results.csv", index=False)
    print("\nSaved detailed trades to backtest_results.csv")

    # Show top 20 best/worst days
    print("\nTop 10 R days:")
    print(res.sort_values("R_multiple", ascending=False).head(10)[
        ["date","direction","R_multiple","outcome","entry_time","exit_time"]
    ].to_string(index=False))

    print("\nWorst 10 R days:")
    print(res.sort_values("R_multiple", ascending=True).head(10)[
        ["date","direction","R_multiple","outcome","entry_time","exit_time"]
    ].to_string(index=False))


if __name__ == "__main__":
    main()
