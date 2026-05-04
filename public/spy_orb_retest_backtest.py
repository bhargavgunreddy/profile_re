#!/usr/bin/env python3
"""
SPY ORB Retest Backtester (single-file)

Supports:
1) Backtest using your cached 15-minute candles (recommended, since you have 2 years)
   - Builds the "first 15 minutes range" from the first 15m bar of regular session (9:30-9:45 ET)
   - Detects breakout, retest, confirmation wick/candle, enters, stops, targets.

2) Optional: Download 5-minute data from yfinance BUT ONLY for last ~60 days
   - Yahoo limits 5m historical range.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import Optional, Tuple, List

import numpy as np
import pandas as pd

# Optional dependency
try:
    import yfinance as yf
except Exception:
    yf = None


# -----------------------
# Utilities
# -----------------------

NY_TZ = "America/New_York"


def _to_ny_datetime_index(df: pd.DataFrame, ts_col: Optional[str] = None) -> pd.DataFrame:
    """
    Ensure df has a tz-aware DatetimeIndex in America/New_York.
    Accepts either:
      - index already datetime-like
      - a timestamp column name
    """
    out = df.copy()

    if ts_col is not None:
        out[ts_col] = pd.to_datetime(out[ts_col], errors="coerce")
        out = out.set_index(ts_col)

    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")

    # Drop NaT
    out = out[~out.index.isna()]

    # Localize / convert to NY
    if out.index.tz is None:
        # Assume timestamps include offset or are NY local; safest: localize to NY
        out.index = out.index.tz_localize(NY_TZ)
    else:
        out.index = out.index.tz_convert(NY_TZ)

    return out.sort_index()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fix MultiIndex / tuple columns (yfinance sometimes returns them).
    """
    out = df.copy()

    if isinstance(out.columns, pd.MultiIndex):
        # Flatten by joining levels with "_"
        out.columns = ["_".join([str(x) for x in col if x is not None]).strip() for col in out.columns.values]
    else:
        new_cols = []
        for c in out.columns:
            if isinstance(c, tuple):
                new_cols.append("_".join(map(str, c)))
            else:
                new_cols.append(str(c))
        out.columns = new_cols

    # Standardize common OHLCV names
    rename_map = {}
    for c in out.columns:
        cl = c.lower()
        if cl in ["open", "o"]:
            rename_map[c] = "Open"
        elif cl in ["high", "h"]:
            rename_map[c] = "High"
        elif cl in ["low", "l"]:
            rename_map[c] = "Low"
        elif cl in ["close", "c", "adj close", "adjclose", "adj_close"]:
            rename_map[c] = "Close"
        elif cl in ["volume", "v"]:
            rename_map[c] = "Volume"
        # Sometimes yfinance gives "Close_SPY" etc — handle loosely
        elif "open" == cl.split("_")[0]:
            rename_map[c] = "Open"
        elif "high" == cl.split("_")[0]:
            rename_map[c] = "High"
        elif "low" == cl.split("_")[0]:
            rename_map[c] = "Low"
        elif "close" == cl.split("_")[0]:
            rename_map[c] = "Close"
        elif "volume" == cl.split("_")[0]:
            rename_map[c] = "Volume"

    out = out.rename(columns=rename_map)

    required = ["Open", "High", "Low", "Close"]
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Got columns: {list(out.columns)[:20]}")

    if "Volume" not in out.columns:
        out["Volume"] = np.nan

    return out[["Open", "High", "Low", "Close", "Volume"]]


def _is_regular_session(ts: pd.Timestamp) -> bool:
    # Regular session 9:30 to 16:00 ET (inclusive start, exclusive end)
    t = ts.tz_convert(NY_TZ).time()
    return (t >= datetime.strptime("09:30", "%H:%M").time()) and (t < datetime.strptime("16:00", "%H:%M").time())


def _session_date(ts: pd.Timestamp) -> date:
    return ts.tz_convert(NY_TZ).date()


# -----------------------
# yfinance 5m download (last 60 days only)
# -----------------------

def download_yf_intraday(symbol: str, start: str, end: str, interval: str = "5m") -> pd.DataFrame:
    """
    Download intraday data using yfinance, respecting Yahoo limits.
    For 5m, Yahoo typically limits to last ~60 days.
    """
    if yf is None:
        raise RuntimeError("yfinance not installed. Run: python3 -m pip install yfinance")

    start_dt = pd.to_datetime(start).tz_localize(NY_TZ)
    end_dt = pd.to_datetime(end).tz_localize(NY_TZ)

    # Yahoo limit guard
    max_lookback_days = 60
    now_ny = pd.Timestamp.now(tz=NY_TZ)
    oldest_allowed = now_ny - pd.Timedelta(days=max_lookback_days)

    if start_dt < oldest_allowed:
        raise ValueError(
            f"Yahoo/yfinance limitation: {interval} data only available for ~last {max_lookback_days} days.\n"
            f"Your start={start_dt.date()} is older than allowed oldest ~{oldest_allowed.date()}.\n"
            f"Use your cached data or a paid data provider (Polygon/Alpaca/Tiingo/etc.) for historical intraday."
        )

    df = yf.download(
        symbol,
        start=start_dt.tz_convert("UTC").to_pydatetime(),
        end=end_dt.tz_convert("UTC").to_pydatetime(),
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=True,
        group_by="column",
    )

    if df is None or len(df) == 0:
        raise RuntimeError("yfinance returned no data. Try a shorter range (within last 60 days).")

    df = _normalize_columns(df)
    df = _to_ny_datetime_index(df)
    return df


# -----------------------
# Strategy / Backtest
# -----------------------

@dataclass
class Trade:
    day: date
    side: str                 # "LONG" or "SHORT"
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    exit_time: pd.Timestamp
    exit: float
    result_r: float           # R multiple
    reason: str               # "TP", "SL", "EOD"


def backtest_orb_retest(
    df: pd.DataFrame,
    *,
    orb_minutes: int = 15,
    confirm_wick: bool = True,
    rr: float = 2.0,
    max_entry_time: str = "11:00",
    require_break_close: bool = True,
    retest_tolerance: float = 0.0,  # dollars
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    ORB Retest:
      - Define ORB from first orb_minutes of regular session (default 15m).
      - Wait for breakout above ORB_high (long) or below ORB_low (short).
      - Wait for retest of broken level.
      - Confirmation: if confirm_wick=True, require a wick rejection candle around level.
      - Enter on confirmation candle close.
      - Stop: below confirmation candle low (long) / above confirmation candle high (short).
      - Target: fixed RR.
      - Exit: TP/SL or EOD (15:55 last bar before close).
    """

    df = _normalize_columns(df)
    df = _to_ny_datetime_index(df)

    # Keep regular session only
    df_rth = df[df.index.map(_is_regular_session)].copy()
    if df_rth.empty:
        raise ValueError("No regular-session candles found after filtering 9:30-16:00 ET.")

    # Group by session date
    trades: List[Trade] = []
    stats_rows = []

    max_entry_t = datetime.strptime(max_entry_time, "%H:%M").time()

    for d, day_df in df_rth.groupby(df_rth.index.map(_session_date)):
        day_df = day_df.copy()

        # Determine bar frequency in minutes (infer from median diff)
        diffs = day_df.index.to_series().diff().dropna()
        if diffs.empty:
            continue
        bar_minutes = int(round(diffs.median().total_seconds() / 60))
        if bar_minutes <= 0:
            continue

        # ORB bars count
        orb_bars = max(1, orb_minutes // bar_minutes)
        first_bars = day_df.between_time("09:30", "09:30").copy()
        # build ORB using first orb_bars starting 9:30
        start_slice = day_df.between_time("09:30", "16:00").iloc[:orb_bars]
        if len(start_slice) < orb_bars:
            continue

        orb_high = float(start_slice["High"].max())
        orb_low = float(start_slice["Low"].min())
        orb_end_time = start_slice.index[-1]

        # Scan for breakout after orb_end_time
        after_orb = day_df[day_df.index > orb_end_time].copy()
        if after_orb.empty:
            continue

        # limit entry time window
        after_orb = after_orb[after_orb.index.time <= max_entry_t]
        if after_orb.empty:
            continue

        state = "WAIT_BREAK"
        side = None
        break_level = None
        broke_time = None

        pending_retest = False

        # Helper: candle confirms rejection around level
        def confirms(candle: pd.Series, side_: str, level: float) -> bool:
            o, h, l, c = map(float, (candle["Open"], candle["High"], candle["Low"], candle["Close"]))
            if side_ == "LONG":
                # retest touch level (low <= level+tolerance) and close back above
                touched = l <= (level + retest_tolerance)
                if not touched:
                    return False
                if confirm_wick:
                    return c > level and (h - c) <= (c - l)  # hammer-ish / lower wick emphasis
                else:
                    return c > level
            else:
                touched = h >= (level - retest_tolerance)
                if not touched:
                    return False
                if confirm_wick:
                    return c < level and (c - l) <= (h - c)  # inverted hammer-ish / upper wick emphasis
                else:
                    return c < level

        # Determine last bar for EOD exit
        eod_bar = day_df.between_time("15:55", "16:00").iloc[:1]
        if eod_bar.empty:
            # fallback: last bar of day
            eod_time = day_df.index[-1]
        else:
            eod_time = eod_bar.index[0]

        entry_taken = False

        for ts, candle in after_orb.iterrows():
            o, h, l, c = map(float, (candle["Open"], candle["High"], candle["Low"], candle["Close"]))

            if not entry_taken and state == "WAIT_BREAK":
                # breakout detection
                if c > orb_high:
                    if (not require_break_close) or (c > orb_high):
                        side = "LONG"
                        break_level = orb_high
                        broke_time = ts
                        state = "WAIT_RETEST"
                elif c < orb_low:
                    if (not require_break_close) or (c < orb_low):
                        side = "SHORT"
                        break_level = orb_low
                        broke_time = ts
                        state = "WAIT_RETEST"

            elif not entry_taken and state == "WAIT_RETEST":
                # Retest + confirmation
                if confirms(candle, side, break_level):
                    entry_time = ts
                    entry = c  # enter at close of confirmation candle

                    if side == "LONG":
                        stop = l
                        risk = entry - stop
                        if risk <= 0:
                            continue
                        target = entry + rr * risk
                    else:
                        stop = h
                        risk = stop - entry
                        if risk <= 0:
                            continue
                        target = entry - rr * risk

                    # Now simulate forward for exit
                    forward = day_df[day_df.index > entry_time].copy()
                    if forward.empty:
                        exit_time = entry_time
                        exit_px = entry
                        reason = "EOD"
                    else:
                        exit_time = None
                        exit_px = None
                        reason = "EOD"

                        for ts2, c2 in forward.iterrows():
                            o2, h2, l2, cl2 = map(float, (c2["Open"], c2["High"], c2["Low"], c2["Close"]))

                            if side == "LONG":
                                # Assume worst-case ordering within bar: SL before TP
                                if l2 <= stop:
                                    exit_time, exit_px, reason = ts2, stop, "SL"
                                    break
                                if h2 >= target:
                                    exit_time, exit_px, reason = ts2, target, "TP"
                                    break
                            else:
                                if h2 >= stop:
                                    exit_time, exit_px, reason = ts2, stop, "SL"
                                    break
                                if l2 <= target:
                                    exit_time, exit_px, reason = ts2, target, "TP"
                                    break

                            if ts2 >= eod_time:
                                exit_time, exit_px, reason = ts2, cl2, "EOD"
                                break

                        if exit_time is None:
                            exit_time, exit_px, reason = forward.index[-1], float(forward["Close"].iloc[-1]), "EOD"

                    # R multiple
                    if side == "LONG":
                        result_r = (exit_px - entry) / (entry - stop)
                    else:
                        result_r = (entry - exit_px) / (stop - entry)

                    trades.append(
                        Trade(
                            day=d,
                            side=side,
                            entry_time=entry_time,
                            entry=entry,
                            stop=stop,
                            target=target,
                            exit_time=exit_time,
                            exit=exit_px,
                            result_r=float(result_r),
                            reason=reason,
                        )
                    )
                    entry_taken = True
                    break  # one trade/day

        # daily stats row
        stats_rows.append(
            {
                "day": d,
                "orb_high": orb_high,
                "orb_low": orb_low,
                "bars": len(day_df),
                "trade_taken": entry_taken,
            }
        )

    trades_df = pd.DataFrame([t.__dict__ for t in trades])
    stats_df = pd.DataFrame(stats_rows)

    if not trades_df.empty:
        trades_df = trades_df.sort_values(["day", "entry_time"]).reset_index(drop=True)

    return trades_df, stats_df


# -----------------------
# IO
# -----------------------

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # If the timestamp col name is "Timestamp" per your sample
    ts_col = None
    for candidate in ["Timestamp", "timestamp", "Date", "Datetime", "date", "datetime", "time"]:
        if candidate in df.columns:
            ts_col = candidate
            break
    if ts_col is None:
        # maybe it's the index in file
        ts_col = df.columns[0]
    df = _to_ny_datetime_index(df, ts_col=ts_col)
    df = _normalize_columns(df)
    return df


# -----------------------
# CLI
# -----------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="SPY", help="Symbol (default SPY)")
    p.add_argument("--csv", default=None, help="Path to your cached candles CSV (recommended)")
    p.add_argument("--interval", default="15m", choices=["1m", "5m", "15m"], help="Used only for yfinance download mode.")
    p.add_argument("--download", action="store_true", help="Download intraday via yfinance (only works for last ~60 days)")
    p.add_argument("--start", default=None, help="Start date YYYY-MM-DD (download mode)")
    p.add_argument("--end", default=None, help="End date YYYY-MM-DD (download mode)")
    p.add_argument("--orb-minutes", type=int, default=15, help="ORB window in minutes (default 15)")
    p.add_argument("--rr", type=float, default=2.0, help="Risk:Reward target (default 2.0)")
    p.add_argument("--max-entry-time", default="11:00", help="Latest time to allow entry HH:MM ET (default 11:00)")
    p.add_argument("--no-wick-confirm", action="store_true", help="Disable wick-style confirmation requirement")
    p.add_argument("--require-break-close", action="store_true", default=True, help="Require candle close beyond ORB level (default True)")
    p.add_argument("--retest-tolerance", type=float, default=0.0, help="Price tolerance for retest touch (default 0.0)")
    p.add_argument("--out", default="trades.csv", help="Output trades CSV")
    p.add_argument("--stats-out", default="stats.csv", help="Output daily stats CSV")
    args = p.parse_args()

    if args.csv is None and not args.download:
        print("ERROR: Provide --csv path to your cached data OR use --download (last ~60 days only).", file=sys.stderr)
        sys.exit(2)

    if args.download:
        if args.start is None or args.end is None:
            print("ERROR: --download requires --start and --end", file=sys.stderr)
            sys.exit(2)
        df = download_yf_intraday(args.symbol, args.start, args.end, interval=args.interval)
    else:
        df = load_csv(args.csv)

    trades_df, stats_df = backtest_orb_retest(
        df,
        orb_minutes=args.orb_minutes,
        confirm_wick=not args.no_wick_confirm,
        rr=args.rr,
        max_entry_time=args.max_entry_time,
        require_break_close=args.require_break_close,
        retest_tolerance=args.retest_tolerance,
    )

    trades_df.to_csv(args.out, index=False)
    stats_df.to_csv(args.stats_out, index=False)

    # Print summary
    print(f"Trades: {len(trades_df)}")
    if len(trades_df):
        winrate = (trades_df["reason"] == "TP").mean()
        avg_r = trades_df["result_r"].mean()
        med_r = trades_df["result_r"].median()
        total_r = trades_df["result_r"].sum()
        print(f"Win rate (TP): {winrate:.2%}")
        print(f"Avg R: {avg_r:.3f} | Median R: {med_r:.3f} | Total R: {total_r:.1f}")
        print(trades_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
