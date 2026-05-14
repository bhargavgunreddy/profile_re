import pandas as pd
import numpy as np
from pathlib import Path
import argparse
from datetime import date as date_type
from dateutil.relativedelta import relativedelta

from polygon_cache_downloader import DownloadConfig, download_range_monthly

# -----------------------
# CONFIG
# -----------------------
FILE_5M = "data_5m.csv"
FILE_15M = "data_15m.csv"

TZ = "America/New_York"  # set to None to disable timezone localization/conversion
RTH_ONLY = True
RTH_START = "09:30"
RTH_END = "16:00"

# Opening range definition (changed from 15m to 5m per request)
# The opening range is defined by the first N minutes of RTH. For N=5, it's just the 09:30 5m candle.
OPENING_RANGE_MINUTES = 5

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
MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH = 3
DEFAULT_MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH = MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH

# entry fill model
ENTRY_AT = "range_line"  # "range_line" or "next_close"
EOD_EXIT = "last_close"  # "last_close" or "none" (if "none", ignore unresolved trades)

# only take the first valid trade per day
ONE_TRADE_PER_DAY = True

# -----------------------
# HIGH-IMPACT IMPROVEMENTS
# -----------------------
# 1) Relax pullback rule: pullback into a zone (instead of exact range-line touch)
# For LONG: accept pullback if Low is within [range_high - below_frac*range, range_high + zone_frac*range]
# For SHORT: accept pullback if High is within [range_low - zone_frac*range, range_low + below_frac*range]
PULLBACK_ZONE_FRAC = 0.25         # 25% of the 15m range
PULLBACK_BELOW_LINE_FRAC = 0.00   # allow undercut of the line by X% of the range (0 = must stay on the breakout side)

# 2) Trend confirmation filter at entry
# modes: "none", "vwap", "ema", "either"
TREND_FILTER_MODE = "either"
EMA_FAST = 9
EMA_SLOW = 21

# 3) Force a time cutoff for new entries
ENTRY_CUTOFF_TIME = "13:00"  # ET; no new entries after this time

# 6) Range acceptance filter (single highest-impact fix)
# Require N consecutive 5m CLOSES outside the 15m opening range before allowing pullback entries.
REQUIRE_RANGE_ACCEPTANCE = True
ACCEPTANCE_CONSEC_CLOSES = 2  # Long: 2 closes above range_high; Short: 2 closes below range_low


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
    # Support BOTH layouts:
    # 1) flat: <cache_root>/<TICKER>_5min_YYYY-MM.csv
    # 2) folder: <cache_root>/<TICKER>/<TICKER>_5min_YYYY-MM.csv
    files_flat = list(cache_dir.glob(f"{ticker}_5min_*.csv"))
    files_folder = list((cache_dir / ticker).glob(f"{ticker}_5min_*.csv"))
    files = sorted({*files_flat, *files_folder})
    if not files:
        raise FileNotFoundError(
            f"No 5m cache files found under {cache_dir} for {ticker}. "
            f"Expected {ticker}_5min_*.csv (flat) or {ticker}/{ticker}_5min_*.csv (folder)."
        )

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

def add_5m_indicators(df5: pd.DataFrame) -> pd.DataFrame:
    """
    Adds VWAP (daily-reset) and EMA9/EMA21 to a 5m dataframe.
    Assumes df5 is already tz-aligned and filtered to RTH if desired.
    """
    d = df5.copy()
    d["date"] = d.index.date

    # EMA trend filter
    d["EMA9"] = d["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    d["EMA21"] = d["Close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # VWAP trend filter (requires Volume)
    if "Volume" in d.columns:
        d["pv"] = d["Close"] * d["Volume"]
        d["cum_pv"] = d.groupby("date")["pv"].cumsum()
        d["cum_vol"] = d.groupby("date")["Volume"].cumsum()
        d["VWAP"] = d["cum_pv"] / d["cum_vol"]
    return d


def get_opening_range_from_5m(df5_day: pd.DataFrame):
    """
    Opening range is defined by the first OPENING_RANGE_MINUTES of RTH.
    With 5m bars and OPENING_RANGE_MINUTES=5, this is simply the first 5m candle (09:30).
    For completeness, if OPENING_RANGE_MINUTES > 5, we aggregate all 5m bars in [09:30, 09:30+N).
    """
    if df5_day.empty:
        return None
    day_start = df5_day.index[0].normalize()
    t0 = day_start + pd.Timedelta(hours=9, minutes=30)
    t1 = t0 + pd.Timedelta(minutes=int(OPENING_RANGE_MINUTES))
    window = df5_day[(df5_day.index >= t0) & (df5_day.index < t1)]
    if window.empty:
        return None
    return float(window["High"].max()), float(window["Low"].min()), t0

def get_first_15m_range_from_5m(df5_day: pd.DataFrame):
    """
    Derive the first 15m opening range (09:30-09:45 ET) from 5m bars.
    Uses all 5m bars with timestamps in [09:30, 09:45).
    """
    day_start = df5_day.index[0].normalize()
    t0 = day_start + pd.Timedelta(hours=9, minutes=30)
    t1 = day_start + pd.Timedelta(hours=9, minutes=45)
    window = df5_day[(df5_day.index >= t0) & (df5_day.index < t1)]
    if window.empty:
        return None
    return float(window["High"].max()), float(window["Low"].min()), t0

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


def find_range_acceptance_time(df5: pd.DataFrame, *, direction: str, range_high: float, range_low: float) -> pd.Timestamp | None:
    """
    Returns the timestamp of the Nth consecutive close outside the range (acceptance),
    or None if acceptance never occurs.
    """
    consec = 0
    for ts, bar in df5.iterrows():
        c = float(bar["Close"])
        if direction == "LONG":
            ok = c > (range_high + BREAKOUT_BUFFER)
        else:
            ok = c < (range_low - BREAKOUT_BUFFER)
        if ok:
            consec += 1
            if consec >= ACCEPTANCE_CONSEC_CLOSES:
                return ts
        else:
            consec = 0
    return None


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

    # after the opening range closes:
    # With OPENING_RANGE_MINUTES=5, the opening range covers 09:30-09:35,
    # so trading starts evaluating from the first bar with timestamp >= 09:35.
    day_start = df5_day.index[0].normalize()
    t_trade_start = day_start + pd.Timedelta(hours=9, minutes=30 + int(OPENING_RANGE_MINUTES))
    t_entry_cutoff = pd.Timestamp(str(day_start.date()) + " " + ENTRY_CUTOFF_TIME, tz=df5_day.index.tz)

    df = df5_day[df5_day.index >= t_trade_start].copy()
    if df.empty:
        return None

    def _backtest_direction(direction: str):
        """
        Backtest one direction (LONG or SHORT) without committing to it.
        Returns trade dict or None.
        """
        breakout_time = None
        # 1) find first breakout in this direction
        for ts, bar in df.iterrows():
            if direction == "LONG":
                ok = is_breakout_long(bar, range_high) and breakout_strength_ok(bar, range_high, range_low, "LONG")
            else:
                ok = is_breakout_short(bar, range_low) and breakout_strength_ok(bar, range_high, range_low, "SHORT")
            if ok:
                breakout_time = ts
                break
        if breakout_time is None:
            return None

        after_breakout = df[df.index > breakout_time]
        if after_breakout.empty:
            return None

        acceptance_time = None
        if REQUIRE_RANGE_ACCEPTANCE:
            acceptance_time = find_range_acceptance_time(
                after_breakout, direction=direction, range_high=range_high, range_low=range_low
            )
            if acceptance_time is None:
                return None
            after_breakout = after_breakout[after_breakout.index > acceptance_time]
            if after_breakout.empty:
                return None

        confirm_time = None
        if REQUIRE_BREAKOUT_CONFIRMATION:
            if direction == "LONG":
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

        rng = float(range_high - range_low)
        if rng <= 0:
            return None

        if direction == "LONG":
            zone_top = range_high + (PULLBACK_ZONE_FRAC * rng)
            zone_bot = range_high - (PULLBACK_BELOW_LINE_FRAC * rng)
            touches = after_breakout[(after_breakout["Low"] <= zone_top) & (after_breakout["Low"] >= zone_bot)]
        else:
            zone_bot = range_low - (PULLBACK_ZONE_FRAC * rng)
            zone_top = range_low + (PULLBACK_BELOW_LINE_FRAC * rng)
            touches = after_breakout[(after_breakout["High"] >= zone_bot) & (after_breakout["High"] <= zone_top)]
        if touches.empty:
            return None

        touch_time = touches.index[0]
        if WAIT_ENTRY_CONFIRMATION:
            after_touch = after_breakout[after_breakout.index > touch_time]
            if MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH is not None:
                after_touch = after_touch.iloc[:MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH]
            if after_touch.empty:
                return None
            if direction == "LONG":
                conf2 = after_touch[after_touch.apply(lambda r: confirms_long(r, range_high), axis=1)]
            else:
                conf2 = after_touch[after_touch.apply(lambda r: confirms_short(r, range_low), axis=1)]
            if conf2.empty:
                return None
            entry_time = conf2.index[0]
            entry_price = float(after_breakout.loc[entry_time, "Close"])
        else:
            if ENTRY_AT == "range_line":
                entry_time = touch_time
                entry_price = range_high if direction == "LONG" else range_low
            else:
                idx = after_breakout.index.get_indexer([touch_time])[0]
                if idx + 1 >= len(after_breakout):
                    return None
                entry_time = after_breakout.index[idx + 1]
                entry_price = float(after_breakout.loc[entry_time, "Close"])

        if entry_time is None or entry_time > t_entry_cutoff:
            return None

        if TREND_FILTER_MODE != "none":
            row = df5_day.loc[entry_time]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            close = float(row["Close"])
            has_vwap = ("VWAP" in row.index) and (not pd.isna(row.get("VWAP")))
            has_ema = ("EMA9" in row.index) and ("EMA21" in row.index)
            if direction == "LONG":
                vwap_ok = has_vwap and (close >= float(row["VWAP"]))
                ema_ok = has_ema and (float(row["EMA9"]) > float(row["EMA21"]))
            else:
                vwap_ok = has_vwap and (close <= float(row["VWAP"]))
                ema_ok = has_ema and (float(row["EMA9"]) < float(row["EMA21"]))
            if TREND_FILTER_MODE == "vwap" and not vwap_ok:
                return None
            if TREND_FILTER_MODE == "ema" and not ema_ok:
                return None
            if TREND_FILTER_MODE == "either" and not (vwap_ok or ema_ok):
                return None

        if direction == "LONG":
            stop_price = range_low
            risk = entry_price - stop_price
            if risk <= 0:
                return None
            target_price = entry_price + 2.0 * risk
        else:
            stop_price = range_high
            risk = stop_price - entry_price
            if risk <= 0:
                return None
            target_price = entry_price - 2.0 * risk

        trade_bars = df[df.index >= entry_time].copy()
        if trade_bars.empty:
            return None

        exit_time = None
        exit_price = None
        outcome = None  # "TP" / "SL" / "EOD"

        for ts, bar in trade_bars.iterrows():
            hi = float(bar["High"])
            lo = float(bar["Low"])

            if direction == "LONG":
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

        if direction == "LONG":
            R = (exit_price - entry_price) / (entry_price - stop_price)
        else:
            R = (entry_price - exit_price) / (stop_price - entry_price)

        return {
            "date": str(entry_time.date()),
            "direction": direction,
            "range_high": range_high,
            "range_low": range_low,
            "breakout_time": breakout_time,
            "acceptance_time": acceptance_time,
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

    long_trade = _backtest_direction("LONG")
    short_trade = _backtest_direction("SHORT")

    if long_trade is None and short_trade is None:
        return None
    if long_trade is None:
        return short_trade
    if short_trade is None:
        return long_trade

    # If both directions produce valid trades, take the one that triggers first.
    if pd.Timestamp(long_trade["entry_time"]) <= pd.Timestamp(short_trade["entry_time"]):
        return long_trade
    return short_trade

def diagnose_day(df5_day: pd.DataFrame, range_high: float, range_low: float) -> str:
    """
    Returns a short reason string for why the day produced no trade under current rules.
    This is meant for debugging when you see 'No trades found...'.
    """
    if df5_day.empty or len(df5_day) < 5:
        return "too_few_bars"

    day_start = df5_day.index[0].normalize()
    t_trade_start = day_start + pd.Timedelta(hours=9, minutes=30 + int(OPENING_RANGE_MINUTES))
    t_entry_cutoff = pd.Timestamp(str(day_start.date()) + " " + ENTRY_CUTOFF_TIME, tz=df5_day.index.tz)

    df = df5_day[df5_day.index >= t_trade_start].copy()
    if df.empty:
        return "no_bars_after_opening_range"

    # breakout
    breakout_dir = None
    breakout_time = None
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
        return "no_clear_breakout"

    after_breakout = df[df.index > breakout_time]
    if after_breakout.empty:
        return "no_bars_after_breakout"

    if REQUIRE_RANGE_ACCEPTANCE:
        acceptance_time = find_range_acceptance_time(
            after_breakout, direction=breakout_dir, range_high=range_high, range_low=range_low
        )
        if acceptance_time is None:
            return "no_range_acceptance"
        after_breakout = after_breakout[after_breakout.index > acceptance_time]
        if after_breakout.empty:
            return "no_bars_after_acceptance"

    if REQUIRE_BREAKOUT_CONFIRMATION:
        if breakout_dir == "LONG":
            conf = after_breakout[after_breakout.apply(lambda r: is_breakout_long(r, range_high), axis=1)]
        else:
            conf = after_breakout[after_breakout.apply(lambda r: is_breakout_short(r, range_low), axis=1)]
        if conf.empty:
            return "no_breakout_confirm_candle"

    rng = float(range_high - range_low)
    if rng <= 0:
        return "bad_range"

    if breakout_dir == "LONG":
        zone_top = range_high + (PULLBACK_ZONE_FRAC * rng)
        zone_bot = range_high - (PULLBACK_BELOW_LINE_FRAC * rng)
        touches = after_breakout[(after_breakout["Low"] <= zone_top) & (after_breakout["Low"] >= zone_bot)]
    else:
        zone_bot = range_low - (PULLBACK_ZONE_FRAC * rng)
        zone_top = range_low + (PULLBACK_BELOW_LINE_FRAC * rng)
        touches = after_breakout[(after_breakout["High"] >= zone_bot) & (after_breakout["High"] <= zone_top)]
    if touches.empty:
        return "no_pullback_touch"

    touch_time = touches.index[0]
    if WAIT_ENTRY_CONFIRMATION:
        after_touch = after_breakout[after_breakout.index > touch_time]
        if MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH is not None:
            after_touch = after_touch.iloc[:MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH]
        if after_touch.empty:
            return "no_bars_after_touch"
        if breakout_dir == "LONG":
            conf2 = after_touch[after_touch.apply(lambda r: confirms_long(r, range_high), axis=1)]
        else:
            conf2 = after_touch[after_touch.apply(lambda r: confirms_short(r, range_low), axis=1)]
        if conf2.empty:
            return "no_entry_confirm"
        entry_time = conf2.index[0]
    else:
        entry_time = touch_time

    if entry_time > t_entry_cutoff:
        return "entry_after_cutoff"

    # trend filter
    if TREND_FILTER_MODE != "none":
        row = df5_day.loc[entry_time]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        close = float(row["Close"])
        vwap_ok = ("VWAP" in row.index) and (not pd.isna(row.get("VWAP")))
        ema_ok = ("EMA9" in row.index) and ("EMA21" in row.index)
        if breakout_dir == "LONG":
            vwap_ok = vwap_ok and (close >= float(row["VWAP"]))
            ema_ok = ema_ok and (float(row["EMA9"]) > float(row["EMA21"]))
        else:
            vwap_ok = vwap_ok and (close <= float(row["VWAP"]))
            ema_ok = ema_ok and (float(row["EMA9"]) < float(row["EMA21"]))
        if TREND_FILTER_MODE == "vwap" and not vwap_ok:
            return "trend_filter_vwap"
        if TREND_FILTER_MODE == "ema" and not ema_ok:
            return "trend_filter_ema"
        if TREND_FILTER_MODE == "either" and not (vwap_ok or ema_ok):
            return "trend_filter_either"

    return "unknown"


def debug_trace_day(df5_day: pd.DataFrame, range_high: float, range_low: float) -> dict:
    """
    Detailed trace for a single day (both directions).
    Returns a JSON-serializable dict (timestamps are ISO strings).
    """
    def _ts(x):
        return None if x is None else str(pd.Timestamp(x))

    def _trace_direction(direction: str) -> dict:
        t: dict = {
            "direction": direction,
            "reason": None,
            "breakout_time": None,
            "acceptance_time": None,
            "confirm_time": None,
            "touch_time": None,
            "entry_time": None,
            "trend_vwap_ok": None,
            "trend_ema_ok": None,
        }
        if df5_day.empty or len(df5_day) < 5:
            t["reason"] = "too_few_bars"
            return t

        day_start = df5_day.index[0].normalize()
        t_trade_start = day_start + pd.Timedelta(hours=9, minutes=30 + int(OPENING_RANGE_MINUTES))
        t_entry_cutoff = pd.Timestamp(str(day_start.date()) + " " + ENTRY_CUTOFF_TIME, tz=df5_day.index.tz)
        df_local = df5_day[df5_day.index >= t_trade_start].copy()
        if df_local.empty:
            t["reason"] = "no_bars_after_opening_range"
            return t

        breakout_time = None
        for ts, bar in df_local.iterrows():
            if direction == "LONG":
                ok = is_breakout_long(bar, range_high) and breakout_strength_ok(bar, range_high, range_low, "LONG")
            else:
                ok = is_breakout_short(bar, range_low) and breakout_strength_ok(bar, range_high, range_low, "SHORT")
            if ok:
                breakout_time = ts
                break
        t["breakout_time"] = _ts(breakout_time)
        if breakout_time is None:
            t["reason"] = "no_clear_breakout"
            return t

        after_breakout = df_local[df_local.index > breakout_time]
        if after_breakout.empty:
            t["reason"] = "no_bars_after_breakout"
            return t

        if REQUIRE_RANGE_ACCEPTANCE:
            acceptance_time = find_range_acceptance_time(
                after_breakout, direction=direction, range_high=range_high, range_low=range_low
            )
            t["acceptance_time"] = _ts(acceptance_time)
            if acceptance_time is None:
                t["reason"] = "no_range_acceptance"
                return t
            after_breakout = after_breakout[after_breakout.index > acceptance_time]
            if after_breakout.empty:
                t["reason"] = "no_bars_after_acceptance"
                return t

        if REQUIRE_BREAKOUT_CONFIRMATION:
            if direction == "LONG":
                conf = after_breakout[after_breakout.apply(lambda r: is_breakout_long(r, range_high), axis=1)]
            else:
                conf = after_breakout[after_breakout.apply(lambda r: is_breakout_short(r, range_low), axis=1)]
            confirm_time = conf.index[0] if not conf.empty else None
            t["confirm_time"] = _ts(confirm_time)
            if confirm_time is None:
                t["reason"] = "no_breakout_confirm_candle"
                return t

        rng = float(range_high - range_low)
        if rng <= 0:
            t["reason"] = "bad_range"
            return t

        if direction == "LONG":
            zone_top = range_high + (PULLBACK_ZONE_FRAC * rng)
            zone_bot = range_high - (PULLBACK_BELOW_LINE_FRAC * rng)
            touches = after_breakout[(after_breakout["Low"] <= zone_top) & (after_breakout["Low"] >= zone_bot)]
        else:
            zone_bot = range_low - (PULLBACK_ZONE_FRAC * rng)
            zone_top = range_low + (PULLBACK_BELOW_LINE_FRAC * rng)
            touches = after_breakout[(after_breakout["High"] >= zone_bot) & (after_breakout["High"] <= zone_top)]
        if touches.empty:
            t["reason"] = "no_pullback_touch"
            return t
        touch_time = touches.index[0]
        t["touch_time"] = _ts(touch_time)

        if WAIT_ENTRY_CONFIRMATION:
            after_touch = after_breakout[after_breakout.index > touch_time]
            if MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH is not None:
                after_touch = after_touch.iloc[:MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH]
            if after_touch.empty:
                t["reason"] = "no_bars_after_touch"
                return t
            if direction == "LONG":
                conf2 = after_touch[after_touch.apply(lambda r: confirms_long(r, range_high), axis=1)]
            else:
                conf2 = after_touch[after_touch.apply(lambda r: confirms_short(r, range_low), axis=1)]
            entry_time = conf2.index[0] if not conf2.empty else None
            t["entry_time"] = _ts(entry_time)
            if entry_time is None:
                t["reason"] = "no_entry_confirm"
                return t
        else:
            t["entry_time"] = _ts(touch_time)

        entry_time = pd.Timestamp(t["entry_time"])
        if entry_time > t_entry_cutoff:
            t["reason"] = "entry_after_cutoff"
            return t

        if TREND_FILTER_MODE != "none":
            row = df5_day.loc[entry_time]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            close = float(row["Close"])
            has_vwap = ("VWAP" in row.index) and (not pd.isna(row.get("VWAP")))
            has_ema = ("EMA9" in row.index) and ("EMA21" in row.index)
            if direction == "LONG":
                vwap_ok = has_vwap and (close >= float(row["VWAP"]))
                ema_ok = has_ema and (float(row["EMA9"]) > float(row["EMA21"]))
            else:
                vwap_ok = has_vwap and (close <= float(row["VWAP"]))
                ema_ok = has_ema and (float(row["EMA9"]) < float(row["EMA21"]))
            t["trend_vwap_ok"] = bool(vwap_ok)
            t["trend_ema_ok"] = bool(ema_ok)
            if TREND_FILTER_MODE == "vwap" and not vwap_ok:
                t["reason"] = "trend_filter_vwap"
                return t
            if TREND_FILTER_MODE == "ema" and not ema_ok:
                t["reason"] = "trend_filter_ema"
                return t
            if TREND_FILTER_MODE == "either" and not (vwap_ok or ema_ok):
                t["reason"] = "trend_filter_either"
                return t

        t["reason"] = "TAKE_TRADE"
        return t

    out: dict = {
        "date": str(df5_day.index[0].date()) if not df5_day.empty else None,
        "opening_range_minutes": int(OPENING_RANGE_MINUTES),
        "range_high": float(range_high),
        "range_low": float(range_low),
        "trend_filter_mode": TREND_FILTER_MODE,
        "t_entry_cutoff": str(pd.Timestamp(str(df5_day.index[0].date()) + " " + ENTRY_CUTOFF_TIME, tz=df5_day.index.tz))
        if not df5_day.empty
        else None,
        "LONG": _trace_direction("LONG"),
        "SHORT": _trace_direction("SHORT"),
        "picked_direction": None,
        "picked_entry_time": None,
    }

    # Determine which direction would be picked (earliest valid entry)
    candidates = []
    for d in ("LONG", "SHORT"):
        tr = out[d]
        if tr.get("reason") == "TAKE_TRADE" and tr.get("entry_time"):
            candidates.append((pd.Timestamp(tr["entry_time"]), d))
    if candidates:
        candidates.sort()
        out["picked_entry_time"] = str(candidates[0][0])
        out["picked_direction"] = candidates[0][1]

    return out

def main():
    global TZ, MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH
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
    ap.add_argument(
        "--use-5m-for-15m-range",
        action="store_true",
        help="Derive the first-15m opening range from 5m data (no 15m files required).",
    )
    ap.add_argument("--run-date", default="", help="Run for a specific date (YYYY-MM-DD). Auto-downloads missing month if needed.")
    ap.add_argument(
        "--force-refresh-month",
        action="store_true",
        help="If --run-date is set, force re-download of that month even if a cache file exists.",
    )
    ap.add_argument("--debug-date", default="", help="Print a single-day skip reason trace (YYYY-MM-DD).")
    ap.add_argument(
        "--max-entry-confirm-bars",
        type=int,
        default=DEFAULT_MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH if DEFAULT_MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH is not None else 0,
        help="How many 5m bars after pullback touch can confirm entry (0 disables the limit).",
    )
    args = ap.parse_args()

    TZ = args.tz.strip() or None
    MAX_ENTRY_CONFIRM_BARS_AFTER_TOUCH = None if int(args.max_entry_confirm_bars) <= 0 else int(args.max_entry_confirm_bars)

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

    # We no longer need 15m data for the opening range; it is derived from 5m bars.
    # Keep 15m loading path only for backward compatibility / inspection use.
    df15 = None
    if not args.use_5m_for_15m_range:
        if spy_15m_dir and spy_15m_dir.exists():
            df15 = load_15m_folder(spy_15m_dir, ticker=args.ticker)
        elif Path(args.file_15m).exists():
            df15 = load_ohlc_csv(args.file_15m)

    df5 = filter_rth(df5)
    if df15 is not None:
        df15 = filter_rth(df15)

    # group by day
    df5 = add_5m_indicators(df5)
    if df15 is not None:
        df15["date"] = df15.index.date

    run_day: date_type | None = None
    if args.run_date:
        run_day = pd.to_datetime(args.run_date).date()
        # If requested day is not in cache, auto-download the month containing it.
        if run_day not in set(df5.index.date):
            # download to the cache root (supports per-ticker folder layout)
            cache_root = polygon_5m_dir if polygon_5m_dir is not None else Path(default_polygon_5m_dir)
            # month range containing run_day
            start = pd.Timestamp(run_day.replace(day=1)).date()
            end = (start + relativedelta(months=1) - relativedelta(days=1))
            print(f"Cache missing {run_day}. Downloading {args.ticker} month {start} → {end} ...")
            cfg = DownloadConfig(multiplier=5)
            download_range_monthly(
                str(args.ticker).upper(),
                start_date=str(start),
                end_date=str(end),
                base_dir=Path(cache_root),
                cfg=cfg,
                force_download=bool(args.force_refresh_month),
            )
            # reload
            df5 = load_polygon_5m_cache_dir(cache_root, ticker=str(args.ticker).upper())
            df5 = filter_rth(df5)
            df5 = add_5m_indicators(df5)

    trades = []
    skip_counts: dict[str, int] = {}
    debug_day = pd.to_datetime(args.debug_date).date() if args.debug_date else None
    # Opening range always derived from 5m bars.
    for day, df5_day_full in df5.groupby("date"):
        if run_day is not None and day != run_day:
            continue
        if debug_day is not None and day != debug_day:
            continue
        df5_day = df5_day_full.drop(columns=["date"], errors="ignore")
        first = get_opening_range_from_5m(df5_day)
        if not first:
            skip_counts["no_opening_range"] = skip_counts.get("no_opening_range", 0) + 1
            continue
        range_high, range_low, _ = first

        if debug_day is not None:
            trace = debug_trace_day(df5_day, range_high, range_low)
            print("\n=== DEBUG TRACE ===")
            print(f"date: {trace.get('date')}")
            print(f"opening_range_minutes: {trace.get('opening_range_minutes')}")
            print(f"range_high: {trace.get('range_high')}")
            print(f"range_low: {trace.get('range_low')}")
            print(f"t_entry_cutoff: {trace.get('t_entry_cutoff')}")
            print(f"picked_direction: {trace.get('picked_direction')}")
            print(f"picked_entry_time: {trace.get('picked_entry_time')}")
            print("\nLONG trace:")
            for k in ["breakout_time","acceptance_time","confirm_time","touch_time","entry_time","trend_vwap_ok","trend_ema_ok","reason"]:
                print(f"  {k}: {trace['LONG'].get(k)}")
            print("\nSHORT trace:")
            for k in ["breakout_time","acceptance_time","confirm_time","touch_time","entry_time","trend_vwap_ok","trend_ema_ok","reason"]:
                print(f"  {k}: {trace['SHORT'].get(k)}")
            # still allow it to attempt trade creation for that day

        trade = backtest_day(df5_day, range_high, range_low)
        if trade:
            trades.append(trade)
        else:
            reason = diagnose_day(df5_day, range_high, range_low)
            skip_counts[reason] = skip_counts.get(reason, 0) + 1

    if not trades:
        print("No trades found with the current rules/parameters.")
        if skip_counts:
            print("\nTop skip reasons:")
            for k, v in sorted(skip_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"- {k}: {v}")
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
