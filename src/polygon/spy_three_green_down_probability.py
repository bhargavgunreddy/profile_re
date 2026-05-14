"""
Probability study:
After N successive candles of a given color (green/red) on 5-minute bars,
how often does the next candle move in the opposite direction?

Uses existing cached Polygon 5m data (no downloads):
- Looks for cache files like:
    src/polygon/data/polygon/SPY_5min_YYYY-MM.csv
  OR folder style:
    src/polygon/data/polygon/SPY/SPY_5min_YYYY-MM.csv

Default:
- last 60 calendar days worth of bars (you can override)
- RTH only (09:30-16:00 America/New_York)
- green candle: Close > Open
- "down next": next candle is red (Close < Open)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta


TZ = "America/New_York"
RTH_START = "09:30"
RTH_END = "16:00"

MODES = ("next_candle", "fail_new_extreme", "vwap_revert", "mr_trade")


def _find_spy_cache_files(cache_root: Path) -> list[Path]:
    # folder style: .../SPY/SPY_5min_YYYY-MM.csv
    folder_files = sorted((cache_root / "SPY").glob("SPY_5min_*.csv"))
    if folder_files:
        return folder_files
    # flat style: .../SPY_5min_YYYY-MM.csv
    flat_files = sorted(cache_root.glob("SPY_5min_*.csv"))
    return flat_files


def load_spy_5m_cached(cache_root: Path) -> pd.DataFrame:
    files = _find_spy_cache_files(cache_root)
    if not files:
        raise FileNotFoundError(f"No SPY cache files found under {cache_root}")

    dfs: list[pd.DataFrame] = []
    for fp in files:
        d = pd.read_csv(fp, parse_dates=["Datetime"])
        d["Datetime"] = pd.to_datetime(d["Datetime"], utc=True)
        d = d.set_index("Datetime").sort_index()
        dfs.append(d)

    df = pd.concat(dfs).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def slice_last_days(df_utc: pd.DataFrame, days: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - relativedelta(days=days)
    return df_utc.loc[start:end].copy()

def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def add_context_columns(df_utc: pd.DataFrame) -> pd.DataFrame:
    """
    Adds:
    - date
    - daily VWAP (resets each day) if Volume exists
    - prior day high/low (RTH) as prev_day_high / prev_day_low
    - ATR14 (daily-reset, min_periods=1) as ATR14
    """
    d = df_utc.tz_convert(TZ).copy()
    d["date"] = d.index.date

    if "Volume" in d.columns:
        d["pv"] = d["Close"] * d["Volume"]
        d["cum_pv"] = d.groupby("date")["pv"].cumsum()
        d["cum_vol"] = d.groupby("date")["Volume"].cumsum()
        d["VWAP"] = d["cum_pv"] / d["cum_vol"]

    # ATR14 within each day (no overnight gap distortion)
    d["prev_close"] = d.groupby("date")["Close"].shift(1)
    tr = pd.concat(
        [
            (d["High"] - d["Low"]).abs().rename("tr1"),
            (d["High"] - d["prev_close"]).abs().rename("tr2"),
            (d["Low"] - d["prev_close"]).abs().rename("tr3"),
        ],
        axis=1,
    ).max(axis=1)
    d["TR"] = tr
    d["ATR14"] = (
        d.groupby("date")["TR"]
        .rolling(14, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )

    # prior day extremes (computed from full df provided; caller can pre-filter to RTH)
    day_ext = (
        d.groupby("date")
        .agg(day_high=("High", "max"), day_low=("Low", "min"))
        .reset_index()
        .sort_values("date")
    )
    day_ext["prev_day_high"] = day_ext["day_high"].shift(1)
    day_ext["prev_day_low"] = day_ext["day_low"].shift(1)
    day_ext = day_ext.set_index("date")[["prev_day_high", "prev_day_low"]]
    d = d.join(day_ext, on="date")
    return d


def _in_time_window(ts: pd.Timestamp, start_hhmm: str | None, end_hhmm: str | None) -> bool:
    if start_hhmm is None and end_hhmm is None:
        return True
    h = ts.hour
    m = ts.minute
    if start_hhmm:
        sh, sm = _parse_hhmm(start_hhmm)
        if (h, m) < (sh, sm):
            return False
    if end_hhmm:
        eh, em = _parse_hhmm(end_hhmm)
        if (h, m) > (eh, em):
            return False
    return True


def _range_extension_pct(g: pd.DataFrame, start_i: int, end_i: int) -> float:
    """
    Simple range extension measure for the sequence:
    abs( close[end] / open[start] - 1 )
    """
    o0 = float(g.iloc[start_i]["Open"])
    c1 = float(g.iloc[end_i]["Close"])
    if not o0:
        return 0.0
    return abs((c1 / o0) - 1.0)


def _near_level(px: float, level: float, tol_pct: float) -> bool:
    if level is None or np.isnan(level) or not level:
        return False
    return abs((px / level) - 1.0) <= tol_pct


def scan_sequences(
    g: pd.DataFrame,
    *,
    n: int,
    seq_color: str,
    non_overlapping: bool,
    tod_start: str | None,
    tod_end: str | None,
    min_range_ext_pct: float | None,
    vwap_dist_min: float | None,
    prior_level_tol_pct: float | None,
    use_prior_high_low: bool,
    use_vwap_dist: bool,
) -> list[int]:
    """
    Returns indices i of the END of each qualifying N-candle sequence in g (day data).
    Filters:
    - time of day window applied at i timestamp
    - optional range extension threshold
    - optional VWAP distance threshold (abs close/VWAP - 1 >= vwap_dist_min)
    - optional proximity to prior day high/low (within prior_level_tol_pct)
    """
    closes = g["Close"].to_numpy(float)
    opens = g["Open"].to_numpy(float)
    highs = g["High"].to_numpy(float)
    lows = g["Low"].to_numpy(float)
    idx = g.index

    if seq_color == "green":
        seq = closes > opens
    else:
        seq = closes < opens

    out: list[int] = []
    i = n - 1
    while i < len(g):
        if i >= len(g):
            break
        if seq[i - (n - 1) : i + 1].all():
            ts = idx[i]
            if not _in_time_window(ts, tod_start, tod_end):
                i += 1
                continue

            if min_range_ext_pct is not None:
                ext = _range_extension_pct(g, i - (n - 1), i)
                if ext < min_range_ext_pct:
                    i += 1
                    continue

            if use_vwap_dist and vwap_dist_min is not None:
                if "VWAP" not in g.columns:
                    i += 1
                    continue
                vwap = float(g.iloc[i].get("VWAP", np.nan))
                if np.isnan(vwap) or vwap == 0:
                    i += 1
                    continue
                dist = abs((closes[i] / vwap) - 1.0)
                if dist < vwap_dist_min:
                    i += 1
                    continue

            if use_prior_high_low and prior_level_tol_pct is not None:
                pH = float(g.iloc[i].get("prev_day_high", np.nan))
                pL = float(g.iloc[i].get("prev_day_low", np.nan))
                px = closes[i]
                if not (_near_level(px, pH, prior_level_tol_pct) or _near_level(px, pL, prior_level_tol_pct)):
                    i += 1
                    continue

            out.append(i)
            i = i + n if non_overlapping else i + 1
        else:
            i += 1
    return out


def compute_probabilities(
    df_utc: pd.DataFrame,
    *,
    n_green: int = 3,
    seq_color: str = "green",
    mode: str = "next_candle",
    lookahead: int = 2,
    # Context filters
    tod_start: str | None = None,
    tod_end: str | None = None,
    vwap_dist_min: float | None = None,
    prior_level_tol_pct: float | None = None,
    min_range_ext_pct: float | None = None,
    require_vwap_location: bool = False,
    require_prior_high_low: bool = False,
    rth_only: bool = True,
    non_overlapping: bool = False,
) -> dict:
    df = df_utc.tz_convert(TZ)
    if rth_only:
        df = df.between_time(RTH_START, RTH_END)

    df = df.copy()
    df["date"] = df.index.date

    seq_color = seq_color.lower().strip()
    if seq_color not in ("green", "red"):
        raise ValueError("seq_color must be 'green' or 'red'")
    mode = mode.lower().strip()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")

    # work in NY time for grouping and context
    df = df_utc.tz_convert(TZ)
    if rth_only:
        df = df.between_time(RTH_START, RTH_END)
    df = add_context_columns(df.tz_convert("UTC"))  # add_context expects UTC but returns NY-indexed df
    if rth_only:
        df = df.between_time(RTH_START, RTH_END)

    total_setups = 0
    metric_yes = 0
    next_returns = []  # store next return for summary (for next_candle & fail_new_extreme: next bar return; for vwap_revert: return to VWAP within horizon)

    by_hour_rows: list[dict] = []

    for _, g in df.groupby("date", sort=True):
        g = g.sort_index()
        if len(g) < n_green + 1:
            continue

        seq_end_idxs = scan_sequences(
            g,
            n=n_green,
            seq_color=seq_color,
            non_overlapping=non_overlapping,
            tod_start=tod_start,
            tod_end=tod_end,
            min_range_ext_pct=min_range_ext_pct,
            vwap_dist_min=vwap_dist_min,
            prior_level_tol_pct=prior_level_tol_pct,
            use_prior_high_low=require_prior_high_low,
            use_vwap_dist=require_vwap_location,
        )

        if not seq_end_idxs:
            continue

        closes = g["Close"].to_numpy(float)
        opens = g["Open"].to_numpy(float)
        highs = g["High"].to_numpy(float)
        lows = g["Low"].to_numpy(float)
        idx = g.index

        for i in seq_end_idxs:
            if i >= len(g) - 1:
                continue
            total_setups += 1

            if mode == "next_candle":
                j = i + 1
                if seq_color == "green":
                    is_opposite_color = closes[j] < opens[j]  # next is red
                    is_close_opposite = closes[j] < closes[i]  # next close down
                else:
                    is_opposite_color = closes[j] > opens[j]  # next is green
                    is_close_opposite = closes[j] > closes[i]  # next close up
                yes = bool(is_opposite_color)
                ret = (closes[j] / closes[i]) - 1.0
                metric_yes += int(yes)
                next_returns.append(ret)

                by_hour_rows.append(
                    {
                        "hour": idx[i].hour,
                        "setups": 1,
                        "metric_yes": int(yes),
                        "metric_alt": int(is_close_opposite),
                        "next_ret": ret,
                    }
                )

            elif mode == "fail_new_extreme":
                # Flip the question:
                # GREEN sequence -> fail to make a NEW HIGH in next K candles
                # RED sequence   -> fail to make a NEW LOW in next K candles
                k = max(1, int(lookahead))
                end = min(len(g) - 1, i + k)
                if seq_color == "green":
                    ref = highs[i]
                    future_max = float(np.max(highs[i + 1 : end + 1]))
                    yes = future_max <= ref
                else:
                    ref = lows[i]
                    future_min = float(np.min(lows[i + 1 : end + 1]))
                    yes = future_min >= ref
                metric_yes += int(bool(yes))
                # store next-bar return as a proxy, still useful for summary
                j = i + 1
                ret = (closes[j] / closes[i]) - 1.0
                next_returns.append(ret)

                by_hour_rows.append(
                    {
                        "hour": idx[i].hour,
                        "setups": 1,
                        "metric_yes": int(bool(yes)),
                        "metric_alt": 0,
                        "next_ret": ret,
                    }
                )

            else:  # vwap_revert
                if "VWAP" not in g.columns:
                    continue
                vwap_i = float(g.iloc[i].get("VWAP", np.nan))
                if np.isnan(vwap_i) or vwap_i == 0:
                    continue

                # Mean reversion: after GREEN sequence, test SHORT-to-VWAP;
                # after RED sequence, test LONG-to-VWAP.
                k = max(1, int(lookahead))
                end = min(len(g) - 1, i + k)
                future_highs = highs[i + 1 : end + 1]
                future_lows = lows[i + 1 : end + 1]

                if seq_color == "green":
                    # success if price touches VWAP (low <= vwap) within horizon
                    yes = bool(np.any(future_lows <= vwap_i))
                    # return measured as (entry_close - vwap)/entry_close (profit for short) if touched, else 0
                    entry = closes[i]
                    ret = ((entry - vwap_i) / entry) if entry else 0.0
                else:
                    yes = bool(np.any(future_highs >= vwap_i))
                    entry = closes[i]
                    ret = ((vwap_i - entry) / entry) if entry else 0.0

                metric_yes += int(yes)
                next_returns.append(ret if yes else 0.0)
                by_hour_rows.append(
                    {
                        "hour": idx[i].hour,
                        "setups": 1,
                        "metric_yes": int(yes),
                        "metric_alt": 0,
                        "next_ret": (ret if yes else 0.0),
                    }
                )

            # Actual trade simulation (mean reversion to VWAP with stop/time exits)
            # This mode is handled in a separate function below.

    by_hour = pd.DataFrame(by_hour_rows)
    if not by_hour.empty:
        hour = (
            by_hour.groupby("hour")
            .agg(
                setups=("setups", "sum"),
                metric_yes=("metric_yes", "sum"),
                metric_alt=("metric_alt", "sum"),
                avg_next_ret=("next_ret", "mean"),
            )
            .reset_index()
            .sort_values("hour")
        )
        hour["p_metric_yes"] = hour["metric_yes"] / hour["setups"]
        hour["p_metric_alt"] = hour["metric_alt"] / hour["setups"]
    else:
        hour = pd.DataFrame(
            columns=[
                "hour",
                "setups",
                "metric_yes",
                "metric_alt",
                "avg_next_ret",
                "p_metric_yes",
                "p_metric_alt",
            ]
        )

    next_returns = np.array(next_returns, dtype=float) if next_returns else np.array([], dtype=float)

    return {
        "total_setups": int(total_setups),
        "p_metric_yes": float(metric_yes / total_setups) if total_setups else np.nan,
        "avg_next_ret": float(np.nanmean(next_returns)) if next_returns.size else np.nan,
        "median_next_ret": float(np.nanmedian(next_returns)) if next_returns.size else np.nan,
        "by_hour": hour,
    }


def backtest_mr_trade(
    df_utc: pd.DataFrame,
    *,
    n: int,
    seq_color: str,
    # Context filters
    tod_start: str | None,
    tod_end: str | None,
    require_vwap_location: bool,
    vwap_dist_min: float | None,
    require_prior_high_low: bool,
    prior_level_tol_pct: float | None,
    min_range_ext_pct: float | None,
    non_overlapping: bool,
    one_trade_per_day: bool,
    # Trade rules
    stop_atr_mult: float = 0.8,
    max_hold_bars: int | None = 24,  # 24 bars = 2 hours on 5m
    force_eod_exit: bool = True,
    entry_at: str = "next_open",  # "next_open" or "next_close"
    rth_only: bool = True,
) -> pd.DataFrame:
    """
    Real strategy backtest:
    - Sequence filter defines a "stretched" move.
      seq_color == "green" => we SHORT for mean reversion to VWAP
      seq_color == "red"   => we LONG for mean reversion to VWAP
    - Entry at next bar open/close after the sequence end.
    - Target: VWAP touch (dynamic VWAP each bar). Fill at VWAP.
    - Stop: ATR-based (entry +/- stop_atr_mult * ATR14(entry)).
    - Exit: first of target/stop; else time_exit (max_hold_bars) or EOD.

    Returns a trades DataFrame with pnl_return and pnl_r (R-multiple).
    """
    seq_color = seq_color.lower().strip()
    if seq_color not in ("green", "red"):
        raise ValueError("seq_color must be 'green' or 'red'")
    entry_at = entry_at.lower().strip()
    if entry_at not in ("next_open", "next_close"):
        raise ValueError("entry_at must be 'next_open' or 'next_close'")

    # Build context on NY time
    df = df_utc.tz_convert(TZ)
    if rth_only:
        df = df.between_time(RTH_START, RTH_END)
    df = add_context_columns(df.tz_convert("UTC"))
    if rth_only:
        df = df.between_time(RTH_START, RTH_END)

    trades: list[dict] = []
    for day, g in df.groupby("date", sort=True):
        g = g.sort_index()
        if len(g) < n + 2:
            continue

        seq_end_idxs = scan_sequences(
            g,
            n=n,
            seq_color=seq_color,
            non_overlapping=non_overlapping,
            tod_start=tod_start,
            tod_end=tod_end,
            min_range_ext_pct=min_range_ext_pct,
            vwap_dist_min=vwap_dist_min,
            prior_level_tol_pct=prior_level_tol_pct,
            use_prior_high_low=require_prior_high_low,
            use_vwap_dist=require_vwap_location,
        )
        if not seq_end_idxs:
            continue

        # Prevent multiple trades/day by taking the first qualifying setup.
        if one_trade_per_day:
            seq_end_idxs = [seq_end_idxs[0]]

        for i in seq_end_idxs:
            if i + 1 >= len(g):
                continue
            entry_time = g.index[i + 1]
            entry_row = g.iloc[i + 1]
            entry_px = float(entry_row["Open"] if entry_at == "next_open" else entry_row["Close"])

            if "VWAP" not in g.columns or pd.isna(entry_row.get("VWAP")):
                continue
            atr = float(entry_row.get("ATR14", np.nan))
            if np.isnan(atr) or atr <= 0:
                continue

            side = "SHORT" if seq_color == "green" else "LONG"
            risk = stop_atr_mult * atr
            if risk <= 0:
                continue
            if side == "SHORT":
                stop_px = entry_px + risk
            else:
                stop_px = entry_px - risk

            # simulate forward
            after = g.iloc[i + 1 :].copy()
            if max_hold_bars is not None:
                after = after.iloc[: max_hold_bars + 1]

            outcome = "none"
            exit_time = None
            exit_px = np.nan

            for ts, bar in after.iterrows():
                hi = float(bar["High"])
                lo = float(bar["Low"])
                vwap = float(bar.get("VWAP", np.nan))
                if np.isnan(vwap):
                    continue

                if side == "SHORT":
                    tp_hit = lo <= vwap
                    sl_hit = hi >= stop_px
                    # conservative: stop wins ties
                    if sl_hit and tp_hit:
                        outcome = "stop_both"
                        exit_time = ts
                        exit_px = stop_px
                        break
                    if sl_hit:
                        outcome = "stop"
                        exit_time = ts
                        exit_px = stop_px
                        break
                    if tp_hit:
                        outcome = "target"
                        exit_time = ts
                        exit_px = vwap
                        break
                else:
                    tp_hit = hi >= vwap
                    sl_hit = lo <= stop_px
                    if sl_hit and tp_hit:
                        outcome = "stop_both"
                        exit_time = ts
                        exit_px = stop_px
                        break
                    if sl_hit:
                        outcome = "stop"
                        exit_time = ts
                        exit_px = stop_px
                        break
                    if tp_hit:
                        outcome = "target"
                        exit_time = ts
                        exit_px = vwap
                        break

            if exit_time is None:
                if len(after) > 0 and force_eod_exit:
                    outcome = "eod" if max_hold_bars is None else "time_exit"
                    exit_time = after.index[-1]
                    exit_px = float(after.iloc[-1]["Close"])
                else:
                    continue

            # returns and R
            if side == "SHORT":
                pnl_return = (entry_px - exit_px) / entry_px
                pnl_r = (entry_px - exit_px) / risk
            else:
                pnl_return = (exit_px - entry_px) / entry_px
                pnl_r = (exit_px - entry_px) / risk

            # context at sequence end (for analysis)
            seq_end_time = g.index[i]
            seq_end_close = float(g.iloc[i]["Close"])
            seq_end_vwap = float(g.iloc[i].get("VWAP", np.nan))
            vwap_dist = abs((seq_end_close / seq_end_vwap) - 1.0) if (not np.isnan(seq_end_vwap) and seq_end_vwap) else np.nan
            seq_ext = _range_extension_pct(g, i - (n - 1), i)

            trades.append(
                {
                    "date": pd.to_datetime(str(day)),
                    "side": side,
                    "seq_color": seq_color,
                    "n_seq": int(n),
                    "seq_end_time": seq_end_time,
                    "entry_time": entry_time,
                    "entry_px": entry_px,
                    "stop_px": stop_px,
                    "exit_time": exit_time,
                    "exit_px": exit_px,
                    "outcome": outcome,
                    "atr14": atr,
                    "risk": risk,
                    "pnl_return": pnl_return,
                    "pnl_r": pnl_r,
                    "seq_extension_pct": seq_ext,
                    "vwap_dist_at_seq_end": vwap_dist,
                }
            )

    return pd.DataFrame(trades).sort_values(["date", "entry_time"]).reset_index(drop=True)


def main():
    p = argparse.ArgumentParser(description="SPY: candle-sequence tests with context (VWAP, prior day levels, range extension)")
    p.add_argument(
        "--cache-root",
        default=str(Path(__file__).resolve().parent / "data" / "polygon"),
        help="Cache root containing SPY_5min_*.csv (flat or SPY/ subfolder).",
    )
    p.add_argument("--days", type=int, default=60, help="Lookback window in calendar days")
    p.add_argument("--n-green", type=int, default=3, help="Sequence length (works for green or red)")
    p.add_argument("--seq-color", choices=["green", "red"], default="green", help="Sequence candle color")
    p.add_argument("--mode", choices=list(MODES), default="next_candle", help="Test mode")
    p.add_argument("--lookahead", type=int, default=2, help="Lookahead bars for fail_new_extreme / vwap_revert")

    # Context filters
    p.add_argument("--tod-start", default="", help="Time-of-day start (HH:MM ET), e.g. 10:30")
    p.add_argument("--tod-end", default="", help="Time-of-day end (HH:MM ET), e.g. 14:30")
    p.add_argument("--require-vwap", action="store_true", help="Enable VWAP distance filter (requires Volume)")
    p.add_argument("--vwap-dist-min", type=float, default=0.0, help="Minimum abs distance from VWAP (e.g. 0.006 for 0.6%%)")
    p.add_argument("--require-prior-hilo", action="store_true", help="Enable filter: price near prior day high/low")
    p.add_argument("--prior-tol", type=float, default=0.001, help="Tolerance for prior day high/low proximity (pct, e.g. 0.001=0.1%%)")
    p.add_argument("--min-range-ext", type=float, default=0.0, help="Min sequence extension (pct, e.g. 0.003 for 0.3%%)")
    # Trade-sim params (mr_trade)
    p.add_argument("--stop-atr-mult", type=float, default=0.8, help="Stop size in ATR14 multiples (mr_trade)")
    p.add_argument("--max-hold-bars", type=int, default=24, help="Max holding bars after entry (mr_trade). 24=~2h")
    p.add_argument("--entry-at", choices=["next_open", "next_close"], default="next_open", help="Entry fill model (mr_trade)")
    p.add_argument("--one-trade-per-day", action="store_true", help="Take only the first setup per day (mr_trade)")
    p.add_argument("--out-csv", default="", help="Optional: save trades (mr_trade) to this CSV path")

    p.add_argument("--include-eth", action="store_true", help="Include extended hours (default is RTH only)")
    p.add_argument("--non-overlapping", action="store_true", help="Do not count overlapping sequences")
    p.add_argument("--show-by-hour", action="store_true", help="Print breakdown by hour")
    args = p.parse_args()

    df = load_spy_5m_cached(Path(args.cache_root))
    df = slice_last_days(df, days=int(args.days))

    tod_start = args.tod_start.strip() or None
    tod_end = args.tod_end.strip() or None
    vwap_dist_min = float(args.vwap_dist_min) if args.require_vwap else None
    prior_tol = float(args.prior_tol) if args.require_prior_hilo else None
    min_ext = float(args.min_range_ext) if args.min_range_ext and args.min_range_ext > 0 else None

    out = compute_probabilities(
        df,
        n_green=int(args.n_green),
        seq_color=str(args.seq_color),
        mode=str(args.mode),
        lookahead=int(args.lookahead),
        tod_start=tod_start,
        tod_end=tod_end,
        vwap_dist_min=vwap_dist_min,
        prior_level_tol_pct=prior_tol,
        min_range_ext_pct=min_ext,
        require_vwap_location=bool(args.require_vwap),
        require_prior_high_low=bool(args.require_prior_hilo),
        rth_only=not args.include_eth,
        non_overlapping=bool(args.non_overlapping),
    )

    label = f"{args.seq_color.upper()} x{int(args.n_green)}"
    print(f"\n=== SPY 5m: {args.mode} after {label} ===")
    print("Lookback days:", int(args.days))
    print("Sequence:", label)
    print("Session:", "RTH" if not args.include_eth else "ETH")
    print("Non-overlapping:", bool(args.non_overlapping))

    if args.mode != "mr_trade":
        print("Total setups:", out["total_setups"])
        print("P(metric_yes):", round(out["p_metric_yes"] * 100, 2), "%" if out["total_setups"] else "")
        print("Avg metric return:", round(out["avg_next_ret"] * 100, 4), "%" if out["total_setups"] else "")
        print("Median metric return:", round(out["median_next_ret"] * 100, 4), "%" if out["total_setups"] else "")
        if args.require_vwap:
            print("VWAP dist min:", float(args.vwap_dist_min))
        if args.require_prior_hilo:
            print("Prior high/low tol:", float(args.prior_tol))
        if tod_start or tod_end:
            print("Time window:", tod_start or "open", "→", tod_end or "close")
        if min_ext is not None:
            print("Min seq extension pct:", min_ext)
        if args.show_by_hour and len(out["by_hour"]) > 0:
            h = out["by_hour"].copy()
            h["p_metric_yes"] = (h["p_metric_yes"] * 100).round(2)
            h["avg_next_ret"] = (h["avg_next_ret"] * 100).round(4)
            print("\n=== By hour (ET) ===")
            print(h.to_string(index=False))
        return

    # mr_trade mode: run full trade simulation
    trades = backtest_mr_trade(
        df,
        n=int(args.n_green),
        seq_color=str(args.seq_color),
        tod_start=tod_start,
        tod_end=tod_end,
        require_vwap_location=bool(args.require_vwap),
        vwap_dist_min=vwap_dist_min,
        require_prior_high_low=bool(args.require_prior_hilo),
        prior_level_tol_pct=prior_tol,
        min_range_ext_pct=min_ext,
        non_overlapping=bool(args.non_overlapping),
        one_trade_per_day=bool(args.one_trade_per_day),
        stop_atr_mult=float(args.stop_atr_mult),
        max_hold_bars=(None if int(args.max_hold_bars) <= 0 else int(args.max_hold_bars)),
        entry_at=str(args.entry_at),
        rth_only=not args.include_eth,
    )
    if trades.empty:
        print("No trades matched the filters.")
        return

    wins = trades[trades["pnl_r"] > 0]
    losses = trades[trades["pnl_r"] <= 0]
    print("Trades:", len(trades))
    print("Win rate (pnl_r>0):", round(float(len(wins) / len(trades)) * 100, 2), "%")
    print("Avg R:", round(float(trades["pnl_r"].mean()), 3))
    print("Median R:", round(float(trades["pnl_r"].median()), 3))
    if len(wins):
        print("Avg win R:", round(float(wins["pnl_r"].mean()), 3))
    if len(losses):
        print("Avg loss R:", round(float(losses["pnl_r"].mean()), 3))
    print("Outcome counts:")
    print(trades["outcome"].value_counts().to_string())

    if args.out_csv:
        Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
        trades.to_csv(args.out_csv, index=False)
        print(f"Saved trades: {args.out_csv}")


if __name__ == "__main__":
    main()

