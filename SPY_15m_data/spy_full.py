"""
SPY 15-min Intraday Regime + Strategy Backtester (Single File)
-------------------------------------------------------------
What this does:
- Reads 15-min OHLCV CSV (timezone-aware timestamps are supported)
- Builds a per-day regime classifier (TREND vs RANGE) using:
    * OR range vs ATR
    * VWAP slope (9:30->10:30)
    * Time above/below VWAP (9:30->10:30)
    * Early range expansion (9:30->10:30 range / ATR)
    * Pullback depth (trend health proxy)
- Trades:
    * TREND module: OR breakout with "acceptance" (2 consecutive closes beyond OR) + VWAP side
    * RANGE module: false-break mean reversion (break OR then close back inside within N bars) + VWAP reclaim
- Risk:
    * Stop distance = min(ATR*stop_atr_mult, OR_range*stop_cap_or_mult)  (SPY tuned)
    * Target distance = ATR*target_atr_mult
    * One trade per day per direction/module by default

Usage:
  python spy_engine.py --csv spy_15m.csv --out results.csv

CSV columns required:
  Timestamp,Open,High,Low,Close,Volume
Timestamp must be parseable by pandas; timezone offset is fine, e.g. 2025-12-11 09:30:00-05:00

Notes:
- This is a research/backtest harness, not trading advice.
- For options 0DTE execution, you'd add an options mapping layer (not included).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# -------------------------
# Config
# -------------------------

@dataclass
class EngineConfig:
    # Session / bars
    market_open: str = "09:30"
    market_close: str = "16:00"
    bar_minutes: int = 15

    # OR window (classic: first 30 minutes = 9:30-10:00 on 15m bars -> 2 bars)
    or_bars: int = 2

    # Regime metrics window (first 60 minutes = 9:30-10:30 on 15m bars -> 4 bars)
    regime_bars: int = 4

    # ATR
    atr_lookback_days: int = 14

    # SPY-tuned regime thresholds
    min_or_range_atr_frac: float = 0.22
    vwap_slope_thresh: float = 0.00055
    time_above_vwap_thresh: float = 0.62
    pullback_max_atr_frac: float = 0.55
    range_expansion_thresh: float = 1.15  # NEW

    # TREND module
    or_break_hold_bars: int = 2  # require acceptance
    max_entry_time: str = "11:30"  # don't enter too late (avoid noon chop)

    # RANGE module
    false_break_max_bars: int = 3
    vwap_reclaim_bars: int = 2

    # Risk / exits (SPY-tuned)
    stop_atr_mult: float = 1.0
    target_atr_mult: float = 1.4
    stop_cap_or_mult: float = 0.80  # NEW: cap stop by OR_range * this

    # Execution assumptions
    slippage_bps: float = 0.0     # basis points slippage per fill (set 1-3 bps if desired)
    commission_per_trade: float = 0.0

    # Limits
    one_trade_per_day: bool = True


# -------------------------
# Indicators / utilities
# -------------------------

def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    needed = {"timestamp", "open", "high", "low", "close", "volume"}
    cols = {c.lower(): c for c in df.columns}
    missing = [c for c in needed if c not in cols]
    if missing:
        raise ValueError(f"Missing required columns (case-insensitive): {missing}. Found: {list(df.columns)}")
    out = df.rename(columns={cols["timestamp"]: "timestamp",
                             cols["open"]: "open",
                             cols["high"]: "high",
                             cols["low"]: "low",
                             cols["close"]: "close",
                             cols["volume"]: "volume"}).copy()
    return out


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = _ensure_columns(df)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False)
    df = df.sort_values("timestamp").reset_index(drop=True)
    # If timestamps are timezone-aware, keep them; if naive, assume America/New_York-like session times
    return df


def add_day_key(df: pd.DataFrame) -> pd.DataFrame:
    # Day key in local timestamp's date
    df = df.copy()
    df["day"] = df["timestamp"].dt.date.astype(str)
    return df


def true_range(high: np.ndarray, low: np.ndarray, prev_close: np.ndarray) -> np.ndarray:
    return np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))


def compute_daily_atr(daily: pd.DataFrame, lookback: int) -> pd.Series:
    """
    daily has columns: day, high, low, close (daily)
    Returns ATR per day aligned with daily index.
    """
    h = daily["high"].to_numpy()
    l = daily["low"].to_numpy()
    c = daily["close"].to_numpy()
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = true_range(h, l, prev_c)
    atr = pd.Series(tr).rolling(lookback, min_periods=lookback).mean()
    atr.index = daily.index
    return atr


def vwap_for_window(dd: pd.DataFrame) -> pd.Series:
    """
    VWAP on intraday bars, cumulative within the provided window.
    """
    pv = (dd["close"] * dd["volume"]).cumsum()
    vv = dd["volume"].cumsum().replace(0, np.nan)
    return pv / vv


def parse_hhmm(ts: pd.Timestamp) -> str:
    return ts.strftime("%H:%M")


def within_time(ts: pd.Timestamp, start_hhmm: str, end_hhmm: str) -> bool:
    hhmm = parse_hhmm(ts)
    return start_hhmm <= hhmm <= end_hhmm


def bps_to_frac(bps: float) -> float:
    return bps / 10000.0


# -------------------------
# Regime classification
# -------------------------

def classify_day_regime(day_df: pd.DataFrame, atr: float, cfg: EngineConfig) -> Tuple[str, Dict]:
    """
    Uses only early-window info (9:30->10:30) to decide TREND vs RANGE.
    Returns (regime, diagnostics)
    """
    dd = day_df.copy()

    # Basic sanity
    if len(dd) < cfg.regime_bars:
        return "SKIP", {"reason": "insufficient_bars"}

    # Opening range (first cfg.or_bars bars starting at 9:30)
    or_df = dd.iloc[:cfg.or_bars]
    or_high = float(or_df["high"].max())
    or_low = float(or_df["low"].min())
    or_range = max(1e-9, or_high - or_low)

    # Early window for regime metrics (first cfg.regime_bars bars)
    reg_df = dd.iloc[:cfg.regime_bars]
    vwap = vwap_for_window(reg_df)

    # VWAP slope: (last vwap - first vwap) / first vwap
    v0 = float(vwap.iloc[0])
    vN = float(vwap.iloc[-1])
    vwap_slope = (vN - v0) / max(1e-9, v0)

    # Time above VWAP fraction (close > vwap)
    above = (reg_df["close"].to_numpy() > vwap.to_numpy()).astype(float)
    time_above = float(above.mean())

    # Early range expansion (9:30-10:30)
    range_early = float(reg_df["high"].max() - reg_df["low"].min())
    range_expansion = (range_early / atr) if atr > 0 else 0.0

    # Pullback depth proxy:
    # If price trends, pullbacks should be relatively shallow vs ATR in first hour.
    # Compute max adverse excursion from a simple direction guess (close direction from first to last bar).
    direction = 1 if float(reg_df["close"].iloc[-1]) >= float(reg_df["close"].iloc[0]) else -1
    closes = reg_df["close"].to_numpy()
    highs = reg_df["high"].to_numpy()
    lows = reg_df["low"].to_numpy()

    if direction == 1:
        # Up attempt: pullback depth = max peak-to-trough within window
        peak = np.maximum.accumulate(highs)
        drawdown = peak - lows
        pullback = float(np.max(drawdown))
    else:
        trough = np.minimum.accumulate(lows)
        drawup = highs - trough
        pullback = float(np.max(drawup))

    pullback_atr_frac = (pullback / atr) if atr > 0 else 0.0

    # Gates / scores
    or_atr_frac = (or_range / atr) if atr > 0 else 0.0
    or_gate = 1.0 if or_atr_frac >= cfg.min_or_range_atr_frac else 0.0

    slope_score = np.clip((abs(vwap_slope) - cfg.vwap_slope_thresh) / cfg.vwap_slope_thresh, -2, 2)
    vwap_side_score = np.clip((abs(time_above - 0.5) - (cfg.time_above_vwap_thresh - 0.5)) / (cfg.time_above_vwap_thresh - 0.5), -2, 2)
    exp_score = np.clip((range_expansion - cfg.range_expansion_thresh) / cfg.range_expansion_thresh, -2, 2)

    # Pullback score: smaller pullback is better for trend
    pullback_score = np.clip((cfg.pullback_max_atr_frac - pullback_atr_frac) / max(1e-9, cfg.pullback_max_atr_frac), -2, 2)

    score = float(or_gate * (0.8 * slope_score + 0.8 * vwap_side_score + 0.7 * exp_score + 0.5 * pullback_score))
    regime = "TREND" if score >= 1.25 else "RANGE"

    diag = {
        "or_high": or_high,
        "or_low": or_low,
        "or_range": float(or_range),
        "atr": float(atr),
        "or_atr_frac": float(or_atr_frac),
        "vwap_slope": float(vwap_slope),
        "time_above_vwap": float(time_above),
        "range_early": float(range_early),
        "range_expansion": float(range_expansion),
        "pullback": float(pullback),
        "pullback_atr_frac": float(pullback_atr_frac),
        "score": float(score),
        "direction_guess": "UP" if direction == 1 else "DOWN",
    }
    return regime, diag


# -------------------------
# Trade simulation
# -------------------------

@dataclass
class Trade:
    day: str
    regime: str
    side: str                 # LONG / SHORT
    entry_time: pd.Timestamp
    entry_px: float
    exit_time: pd.Timestamp
    exit_px: float
    exit_reason: str          # STOP / TARGET / EOD / TIME
    pnl_r: float              # R-multiple
    pnl_abs: float            # absolute pnl in price units
    or_high: float
    or_low: float
    atr: float


def _apply_slippage(px: float, side: str, bps: float, is_entry: bool) -> float:
    # Conservative: pay slippage on both entry and exit.
    frac = bps_to_frac(bps)
    if frac <= 0:
        return px
    if side == "LONG":
        return px * (1 + frac) if is_entry else px * (1 - frac)
    else:
        return px * (1 - frac) if is_entry else px * (1 + frac)


def simulate_day(day_df: pd.DataFrame, day: str, regime: str, diag: Dict, cfg: EngineConfig) -> List[Trade]:
    dd = day_df.copy()
    trades: List[Trade] = []

    if regime == "SKIP":
        return trades

    atr = float(diag["atr"])
    or_high = float(diag["or_high"])
    or_low = float(diag["or_low"])
    or_range = max(1e-9, float(diag["or_range"]))

    # Capped stop distance (SPY tuned)
    stop_dist = cfg.stop_atr_mult * atr
    stop_dist = min(stop_dist, cfg.stop_cap_or_mult * or_range)
    tgt_dist = cfg.target_atr_mult * atr

    # VWAP for full day (cumulative)
    dd["vwap"] = vwap_for_window(dd)

    # Cutoff time for new entries
    max_entry_time = cfg.max_entry_time

    taken = False

    if regime == "TREND":
        # Need acceptance: or_break_hold_bars consecutive closes beyond OR in direction,
        # and close on correct VWAP side at signal bar.
        closes = dd["close"].to_numpy()
        times = dd["timestamp"].to_numpy()
        vwap = dd["vwap"].to_numpy()

        # We start checking after OR is formed
        start_idx = cfg.or_bars
        for i in range(start_idx, len(dd) - cfg.or_break_hold_bars + 1):
            ts = dd["timestamp"].iloc[i]
            if parse_hhmm(ts) > max_entry_time:
                break
            if cfg.one_trade_per_day and taken:
                break

            window = closes[i:i + cfg.or_break_hold_bars]
            window_vwap = vwap[i:i + cfg.or_break_hold_bars]

            # Long acceptance
            if np.all(window > or_high) and window[-1] > window_vwap[-1]:
                entry_idx = i + cfg.or_break_hold_bars - 1
                entry_ts = dd["timestamp"].iloc[entry_idx]
                entry_px = float(dd["close"].iloc[entry_idx])
                entry_px = _apply_slippage(entry_px, "LONG", cfg.slippage_bps, True)

                stop_px = entry_px - stop_dist
                tgt_px = entry_px + tgt_dist

                trade = _simulate_trade(dd, day, regime, "LONG", entry_ts, entry_idx,
                                        entry_px, stop_px, tgt_px, cfg)
                trade.or_high = or_high
                trade.or_low = or_low
                trade.atr = atr
                trades.append(trade)
                taken = True
                break

            # Short acceptance
            if np.all(window < or_low) and window[-1] < window_vwap[-1]:
                entry_idx = i + cfg.or_break_hold_bars - 1
                entry_ts = dd["timestamp"].iloc[entry_idx]
                entry_px = float(dd["close"].iloc[entry_idx])
                entry_px = _apply_slippage(entry_px, "SHORT", cfg.slippage_bps, True)

                stop_px = entry_px + stop_dist
                tgt_px = entry_px - tgt_dist

                trade = _simulate_trade(dd, day, regime, "SHORT", entry_ts, entry_idx,
                                        entry_px, stop_px, tgt_px, cfg)
                trade.or_high = or_high
                trade.or_low = or_low
                trade.atr = atr
                trades.append(trade)
                taken = True
                break

    else:  # RANGE
        # False break: price breaks OR then closes back inside within N bars
        # Then require VWAP reclaim for LONG (close > vwap for vwap_reclaim_bars),
        # or VWAP reject for SHORT (close < vwap for vwap_reclaim_bars).
        highs = dd["high"].to_numpy()
        lows = dd["low"].to_numpy()
        closes = dd["close"].to_numpy()
        vwap = dd["vwap"].to_numpy()

        start_idx = cfg.or_bars
        i = start_idx
        while i < len(dd) - cfg.vwap_reclaim_bars:
            ts = dd["timestamp"].iloc[i]
            if parse_hhmm(ts) > max_entry_time:
                break
            if cfg.one_trade_per_day and taken:
                break

            # Upside false break -> mean revert SHORT
            if highs[i] > or_high:
                # Look for close back inside (<= or_high) within next false_break_max_bars
                j_end = min(len(dd) - 1, i + cfg.false_break_max_bars)
                j_inside = None
                for j in range(i, j_end + 1):
                    if closes[j] <= or_high:
                        j_inside = j
                        break
                if j_inside is not None:
                    # Require VWAP reject: consecutive closes < vwap
                    k = j_inside
                    if k + cfg.vwap_reclaim_bars - 1 < len(dd):
                        cond = np.all(closes[k:k + cfg.vwap_reclaim_bars] < vwap[k:k + cfg.vwap_reclaim_bars])
                        if cond:
                            entry_idx = k + cfg.vwap_reclaim_bars - 1
                            entry_ts = dd["timestamp"].iloc[entry_idx]
                            entry_px = float(dd["close"].iloc[entry_idx])
                            entry_px = _apply_slippage(entry_px, "SHORT", cfg.slippage_bps, True)

                            stop_px = entry_px + stop_dist
                            tgt_px = entry_px - tgt_dist

                            trade = _simulate_trade(dd, day, regime, "SHORT", entry_ts, entry_idx,
                                                    entry_px, stop_px, tgt_px, cfg)
                            trade.or_high = or_high
                            trade.or_low = or_low
                            trade.atr = atr
                            trades.append(trade)
                            taken = True
                            break
                i += 1
                continue

            # Downside false break -> mean revert LONG
            if lows[i] < or_low:
                j_end = min(len(dd) - 1, i + cfg.false_break_max_bars)
                j_inside = None
                for j in range(i, j_end + 1):
                    if closes[j] >= or_low:
                        j_inside = j
                        break
                if j_inside is not None:
                    k = j_inside
                    if k + cfg.vwap_reclaim_bars - 1 < len(dd):
                        cond = np.all(closes[k:k + cfg.vwap_reclaim_bars] > vwap[k:k + cfg.vwap_reclaim_bars])
                        if cond:
                            entry_idx = k + cfg.vwap_reclaim_bars - 1
                            entry_ts = dd["timestamp"].iloc[entry_idx]
                            entry_px = float(dd["close"].iloc[entry_idx])
                            entry_px = _apply_slippage(entry_px, "LONG", cfg.slippage_bps, True)

                            stop_px = entry_px - stop_dist
                            tgt_px = entry_px + tgt_dist

                            trade = _simulate_trade(dd, day, regime, "LONG", entry_ts, entry_idx,
                                                    entry_px, stop_px, tgt_px, cfg)
                            trade.or_high = or_high
                            trade.or_low = or_low
                            trade.atr = atr
                            trades.append(trade)
                            taken = True
                            break
                i += 1
                continue

            i += 1

    return trades


def _simulate_trade(dd: pd.DataFrame,
                    day: str,
                    regime: str,
                    side: str,
                    entry_ts: pd.Timestamp,
                    entry_idx: int,
                    entry_px: float,
                    stop_px: float,
                    tgt_px: float,
                    cfg: EngineConfig) -> Trade:
    """
    Simulate bar-by-bar exits.
    Uses intrabar stop/target checks:
      - For LONG: if low <= stop -> stop, else if high >= tgt -> target
      - For SHORT: if high >= stop -> stop, else if low <= tgt -> target
    If both occur in same bar: assume STOP first (conservative).
    Exit at EOD if neither hit.
    """
    highs = dd["high"].to_numpy()
    lows = dd["low"].to_numpy()
    times = dd["timestamp"].to_numpy()

    exit_reason = "EOD"
    exit_idx = len(dd) - 1
    exit_px = float(dd["close"].iloc[-1])

    for i in range(entry_idx + 1, len(dd)):
        h = float(highs[i])
        l = float(lows[i])

        if side == "LONG":
            if l <= stop_px:
                exit_reason = "STOP"
                exit_idx = i
                exit_px = stop_px
