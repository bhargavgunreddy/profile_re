"""
5m Opening Range Breakout + Retest + Re-break Confirmation Strategy

Standalone implementation (does NOT modify or depend on other strategy files).

Rules (symmetric long/short):
1) Define initial range from the 09:30 5m candle: range_high / range_low
2) Wait for a breakout CLOSE outside the range (break1)
3) Wait for a retest: a candle CLOSE back INSIDE the range (retest)
4) Wait for a re-breakout CLOSE outside the range again in the breakout direction (rebreak1)
5) Wait for a confirmation candle that CLOSES cleanly outside the range (confirm)
   - clean means the candle does not wick back into the range:
       LONG: Low >= range_high + buffer
       SHORT: High <= range_low  - buffer
6) Enter at the confirmation candle CLOSE
7) Stop at the opposite side of the opening range (LONG stop=range_low, SHORT stop=range_high)
8) Target at 2R
9) If neither TP nor SL is hit, exit at end-of-day close (optional)

Evaluates BOTH long and short each day and takes whichever produces the earliest valid entry.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta

from polygon_cache_downloader import DownloadConfig, download_last_n_days
from polygon_secrets import debug_polygon_key

TZ_DEFAULT = "America/New_York"
RTH_START = "09:30"
RTH_END = "16:00"


@dataclass(frozen=True)
class StrategyConfig:
    tz: str = TZ_DEFAULT
    breakout_buffer: float = 0.0
    # Breakout/confirm strictness
    # - break1 requires a "wick-clean" breakout by default (prevents early fakeouts)
    break1_wick_clean: bool = True
    # - confirm requires CLOSE to be outside by at least this buffer (in price units)
    confirm_close_buffer: float = 0.05
    one_trade_per_day: bool = True
    eod_exit: bool = True
    # Stop placement:
    # - "opposite": stop at the opposite side of the opening range (wide stop)
    # - "breakout_line": stop at the breakout line (range boundary) + buffer (tighter, typical ORB retest)
    stop_mode: str = "breakout_line"
    stop_buffer: float = 0.10  # price units; for SPY, 0.10 ~= 10 cents
    rr: float = 1.5  # reward multiple (1.0R–1.5R recommended per request)


def _find_cache_files(cache_root: Path, ticker: str) -> list[Path]:
    """
    Supports BOTH layouts:
    - flat:   <cache_root>/<TICKER>_5min_YYYY-MM.csv
    - folder: <cache_root>/<TICKER>/<TICKER>_5min_YYYY-MM.csv
    """
    files_flat = list(cache_root.glob(f"{ticker}_5min_*.csv"))
    files_folder = list((cache_root / ticker).glob(f"{ticker}_5min_*.csv"))
    return sorted({*files_flat, *files_folder})


def load_cached_5m(cache_root: Path, ticker: str, *, tz: str) -> pd.DataFrame:
    files = _find_cache_files(cache_root, ticker)
    if not files:
        raise FileNotFoundError(
            f"No 5m cache files found under {cache_root} for {ticker}. "
            f"Expected {ticker}_5min_*.csv (flat) or {ticker}/{ticker}_5min_*.csv (folder)."
        )

    dfs: list[pd.DataFrame] = []
    for fp in files:
        d = pd.read_csv(fp, parse_dates=["Datetime"])
        d["Datetime"] = pd.to_datetime(d["Datetime"], utc=True)
        d = d.set_index("Datetime").sort_index()
        d.columns = [c.strip().capitalize() for c in d.columns]
        dfs.append(d)

    df = pd.concat(dfs).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.tz_convert(tz).between_time(RTH_START, RTH_END)
    df["date"] = df.index.date
    return df


def slice_last_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - relativedelta(days=days)
    return df.tz_convert("UTC").loc[start:end].tz_convert(df.index.tz).copy()


def print_coverage(df: pd.DataFrame, *, requested_days: int) -> None:
    """Print the actual backtest coverage based on what's in df (post-slicing)."""
    if df.empty:
        print("\n=== Backtest coverage ===\n<empty: no rows>\n")
        return
    start_ts = df.index.min()
    end_ts = df.index.max()
    sessions = int(df["date"].nunique()) if "date" in df.columns else None
    print("\n=== Backtest coverage ===")
    print("start:", start_ts)
    print("end:  ", end_ts)
    if sessions is not None:
        print("sessions:", sessions, f"(requested lookback_days={requested_days})")
    print()


def _fmt(v) -> str:
    if v is None:
        return "None"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def print_trade_logs(trades: pd.DataFrame, *, tail: int = 0, file_path: str = "") -> None:
    """
    Print one line per trade with timestamps for manual verification (TradingView-friendly).
    If tail > 0, only prints last N trades.
    If file_path is provided, writes the same lines to that file.
    """
    if trades is None or trades.empty:
        print("\n(no trades)\n")
        return

    df = trades.copy()
    if tail and tail > 0:
        df = df.tail(int(tail))

    cols = [
        "date",
        "direction",
        "break1_time",
        "retest_time",
        "rebreak1_time",
        "confirm_time",
        "entry_time",
        "exit_time",
        "outcome",
        "R_multiple",
        "entry_price",
        "stop_price",
        "target_price",
        "exit_price",
        "range_high",
        "range_low",
    ]

    lines: list[str] = []
    lines.append("\n=== Trade logs (one line per trade) ===")
    for _, r in df.iterrows():
        line = (
            f"{_fmt(r.get('date'))} | {str(r.get('direction')):5s} | "
            f"b1={_fmt(r.get('break1_time'))} ret={_fmt(r.get('retest_time'))} "
            f"rb={_fmt(r.get('rebreak1_time'))} conf={_fmt(r.get('confirm_time'))} "
            f"entry={_fmt(r.get('entry_time'))} exit={_fmt(r.get('exit_time'))} | "
            f"{_fmt(r.get('outcome'))} R={float(r.get('R_multiple')):.3f} | "
            f"px: entry={float(r.get('entry_price')):.2f} stop={float(r.get('stop_price')):.2f} "
            f"tp={float(r.get('target_price')):.2f} exit={float(r.get('exit_price')):.2f} | "
            f"OR: hi={float(r.get('range_high')):.2f} lo={float(r.get('range_low')):.2f}"
        )
        lines.append(line)

    text = "\n".join(lines) + "\n"
    print(text)
    if file_path:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"Saved log: {file_path}")


def simulate_equity_curve(
    trades: pd.DataFrame,
    *,
    start_equity: float,
    risk_frac: float = 0.01,
    risk_dollars: float = 0.0,
) -> pd.DataFrame:
    """
    Convert R-multiples into an equity curve.

    - If risk_dollars > 0: fixed-$ risk per trade, no compounding of risk size.
      pnl_$ = risk_dollars * R

    - Else: fixed-fractional risk per trade, compounding:
      pnl_$ = (equity * risk_frac) * R
    """
    if trades.empty:
        return pd.DataFrame(columns=["i", "date", "R_multiple", "equity", "pnl_dollars", "drawdown"])

    eq = float(start_equity)
    peak = float(start_equity)
    rows: list[dict] = []
    for i, (_, r) in enumerate(trades.reset_index(drop=True).iterrows(), start=1):
        R = float(r["R_multiple"])
        risk_amt = float(risk_dollars) if risk_dollars and risk_dollars > 0 else (eq * float(risk_frac))
        pnl = risk_amt * R
        eq = eq + pnl
        peak = max(peak, eq)
        dd = (eq / peak) - 1.0 if peak > 0 else 0.0
        rows.append(
            {
                "i": i,
                "date": r.get("date"),
                "R_multiple": R,
                "risk_dollars": risk_amt,
                "pnl_dollars": pnl,
                "equity": eq,
                "drawdown": dd,
            }
        )
    return pd.DataFrame(rows)


def _parse_float_list(s: str) -> list[float]:
    s = (s or "").strip()
    if not s:
        return []
    out: list[float] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(float(part))
    return out


def run_backtest(df: pd.DataFrame, *, cfg: StrategyConfig, run_day=None, debug_day=None) -> pd.DataFrame:
    trades: list[dict] = []
    for day, g in df.groupby("date", sort=True):
        if run_day is not None and day != run_day:
            continue
        gday = g.drop(columns=["date"], errors="ignore")
        if debug_day is not None and day == debug_day:
            print("\n=== DEBUG TRACE ===")
            print_debug_trace(debug_trace(gday, cfg=cfg))
        tr = backtest_day(gday, cfg=cfg)
        if tr:
            trades.append(tr)
    return pd.DataFrame(trades)


def opening_range_0930(df_day: pd.DataFrame) -> tuple[float, float] | None:
    """Return (range_high, range_low) from the 09:30 5m candle."""
    if df_day.empty:
        return None
    t0 = df_day.index[0].normalize() + pd.Timedelta(hours=9, minutes=30)
    if t0 not in df_day.index:
        return None
    bar = df_day.loc[t0]
    if isinstance(bar, pd.DataFrame):
        bar = bar.iloc[0]
    return float(bar["High"]), float(bar["Low"])


def close_outside_range(close: float, range_high: float, range_low: float, direction: str, buffer: float) -> bool:
    if direction == "LONG":
        return close > (range_high + buffer)
    return close < (range_low - buffer)


def close_inside_range(close: float, range_high: float, range_low: float) -> bool:
    return range_low <= close <= range_high


def clean_confirm_ok(
    bar: pd.Series,
    range_high: float,
    range_low: float,
    direction: str,
    buffer: float,
    clean: bool,
) -> bool:
    c = float(bar["Close"])
    if not close_outside_range(c, range_high, range_low, direction, buffer):
        return False
    if not clean:
        return True
    if direction == "LONG":
        return float(bar["Low"]) >= (range_high + buffer)
    return float(bar["High"]) <= (range_low - buffer)

def break1_ok(
    bar: pd.Series,
    *,
    direction: str,
    range_high: float,
    range_low: float,
    breakout_buffer: float,
    wick_clean: bool,
) -> bool:
    """
    First breakout rule:
    - must CLOSE outside
    - optionally must be wick-clean (no wick back into the range)
    """
    c = float(bar["Close"])
    if not close_outside_range(c, range_high, range_low, direction, breakout_buffer):
        return False
    if not wick_clean:
        return True
    if direction == "LONG":
        return float(bar["Low"]) >= (range_high + breakout_buffer)
    return float(bar["High"]) <= (range_low - breakout_buffer)


def confirm_ok(
    bar: pd.Series,
    *,
    direction: str,
    range_high: float,
    range_low: float,
    close_buffer: float,
) -> bool:
    """
    Confirmation candle rule:
    - must CLOSE outside the range by at least close_buffer.
    (No wick-clean requirement; matches your 10:25 example.)
    """
    c = float(bar["Close"])
    if direction == "LONG":
        return c >= (range_high + close_buffer)
    return c <= (range_low - close_buffer)


def simulate_trade(
    df_day: pd.DataFrame,
    *,
    direction: str,
    entry_time: pd.Timestamp,
    entry_price: float,
    stop_price: float,
    target_price: float,
    eod_exit: bool,
) -> tuple[pd.Timestamp, float, str, float]:
    """Returns (exit_time, exit_price, outcome, R_multiple)."""
    trade_bars = df_day[df_day.index >= entry_time].copy()
    if trade_bars.empty:
        raise ValueError("No bars after entry_time")

    # IMPORTANT: do not evaluate TP/SL on the entry candle.
    # We enter at the confirmation candle close, so intra-bar path of that candle is not known.
    eval_bars = trade_bars.iloc[1:].copy()

    exit_time = None
    exit_price = None
    outcome = None

    for ts, bar in eval_bars.iterrows():
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
        if not eod_exit:
            raise ValueError("Unresolved trade and eod_exit disabled")
        exit_time = trade_bars.index[-1]
        exit_price = float(trade_bars.iloc[-1]["Close"])
        outcome = "EOD"

    if direction == "LONG":
        R = (exit_price - entry_price) / (entry_price - stop_price)
    else:
        R = (entry_price - exit_price) / (stop_price - entry_price)
    return exit_time, float(exit_price), str(outcome), float(R)


def find_setup(
    df_day: pd.DataFrame,
    *,
    direction: str,
    range_high: float,
    range_low: float,
    cfg: StrategyConfig,
) -> dict | None:
    """Return a setup dict or None."""
    if df_day.empty:
        return None

    # Start evaluating after 09:35
    t_start = df_day.index[0].normalize() + pd.Timedelta(hours=9, minutes=35)
    df = df_day[df_day.index >= t_start].copy()
    if df.empty:
        return None

    # 1) break1: first breakout (close outside; optionally wick-clean)
    break1 = None
    for ts, bar in df.iterrows():
        if break1_ok(
            bar,
            direction=direction,
            range_high=range_high,
            range_low=range_low,
            breakout_buffer=cfg.breakout_buffer,
            wick_clean=cfg.break1_wick_clean,
        ):
            break1 = ts
            break
    if break1 is None:
        return None

    # 2) retest: first close back inside AFTER break1
    after_break1 = df[df.index > break1]
    if after_break1.empty:
        return None
    retest = None
    for ts, bar in after_break1.iterrows():
        if close_inside_range(float(bar["Close"]), range_high, range_low):
            retest = ts
            break
    if retest is None:
        return None

    # 3) rebreak1: first close outside again AFTER retest
    after_retest = df[df.index > retest]
    if after_retest.empty:
        return None
    rebreak1 = None
    for ts, bar in after_retest.iterrows():
        if close_outside_range(float(bar["Close"]), range_high, range_low, direction, cfg.breakout_buffer):
            rebreak1 = ts
            break
    if rebreak1 is None:
        return None

    # 4) confirm: first CLOSE clearly outside AFTER rebreak1
    after_rebreak1 = df[df.index > rebreak1]
    if after_rebreak1.empty:
        return None
    confirm = None
    for ts, bar in after_rebreak1.iterrows():
        if confirm_ok(
            bar,
            direction=direction,
            range_high=range_high,
            range_low=range_low,
            close_buffer=cfg.confirm_close_buffer,
        ):
            confirm = ts
            break
    if confirm is None:
        return None

    entry_time = confirm
    row = df.loc[confirm]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    entry_price = float(row["Close"])

    # stop placement
    if direction == "LONG":
        if cfg.stop_mode == "opposite":
            stop_price = range_low
        else:
            # breakout line for LONG is range_high (stop just below it)
            stop_price = range_high - cfg.stop_buffer
        risk = entry_price - stop_price
        if risk <= 0:
            return None
        target_price = entry_price + float(cfg.rr) * risk
    else:
        if cfg.stop_mode == "opposite":
            stop_price = range_high
        else:
            # breakout line for SHORT is range_low (stop just above it)
            stop_price = range_low + cfg.stop_buffer
        risk = stop_price - entry_price
        if risk <= 0:
            return None
        target_price = entry_price - float(cfg.rr) * risk

    return {
        "direction": direction,
        "range_high": float(range_high),
        "range_low": float(range_low),
        "break1_time": break1,
        "retest_time": retest,
        "rebreak1_time": rebreak1,
        "confirm_time": confirm,
        "entry_time": entry_time,
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "target_price": float(target_price),
    }


def find_setup_trace(
    df_day: pd.DataFrame,
    *,
    direction: str,
    range_high: float,
    range_low: float,
    cfg: StrategyConfig,
) -> dict:
    """
    Debug helper: returns a dict with partial progress + a reason for failure.
    Mirrors find_setup() but never returns None.
    """
    out: dict = {"direction": direction}
    if df_day.empty:
        out["reason"] = "empty_day"
        return out

    # Start evaluating after 09:35
    t_start = df_day.index[0].normalize() + pd.Timedelta(hours=9, minutes=35)
    df = df_day[df_day.index >= t_start].copy()
    if df.empty:
        out["reason"] = "no_bars_after_0935"
        return out

    # 1) break1
    break1 = None
    for ts, bar in df.iterrows():
        if break1_ok(
            bar,
            direction=direction,
            range_high=range_high,
            range_low=range_low,
            breakout_buffer=cfg.breakout_buffer,
            wick_clean=cfg.break1_wick_clean,
        ):
            break1 = ts
            break
    out["break1_time"] = break1
    if break1 is None:
        out["reason"] = "no_break1"
        return out

    # 2) retest
    after_break1 = df[df.index > break1]
    if after_break1.empty:
        out["reason"] = "no_bars_after_break1"
        return out
    retest = None
    for ts, bar in after_break1.iterrows():
        if close_inside_range(float(bar["Close"]), range_high, range_low):
            retest = ts
            break
    out["retest_time"] = retest
    if retest is None:
        out["reason"] = "no_retest_close_inside"
        return out

    # 3) rebreak1
    after_retest = df[df.index > retest]
    if after_retest.empty:
        out["reason"] = "no_bars_after_retest"
        return out
    rebreak1 = None
    for ts, bar in after_retest.iterrows():
        if close_outside_range(float(bar["Close"]), range_high, range_low, direction, cfg.breakout_buffer):
            rebreak1 = ts
            break
    out["rebreak1_time"] = rebreak1
    if rebreak1 is None:
        out["reason"] = "no_rebreak1_close_outside"
        return out

    # 4) confirm
    after_rebreak1 = df[df.index > rebreak1]
    if after_rebreak1.empty:
        out["reason"] = "no_bars_after_rebreak1"
        return out
    confirm = None
    for ts, bar in after_rebreak1.iterrows():
        if confirm_ok(
            bar,
            direction=direction,
            range_high=range_high,
            range_low=range_low,
            close_buffer=cfg.confirm_close_buffer,
        ):
            confirm = ts
            break
    out["confirm_time"] = confirm
    if confirm is None:
        out["reason"] = "no_confirm_close_buffer"
        return out

    # entry/px
    entry_time = confirm
    row = df.loc[confirm]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    entry_price = float(row["Close"])
    out["entry_time"] = entry_time
    out["entry_price"] = entry_price

    # stop/target sanity
    if direction == "LONG":
        stop_price = (range_low if cfg.stop_mode == "opposite" else (range_high - cfg.stop_buffer))
        risk = entry_price - stop_price
    else:
        stop_price = (range_high if cfg.stop_mode == "opposite" else (range_low + cfg.stop_buffer))
        risk = stop_price - entry_price
    out["stop_price"] = float(stop_price)
    out["risk"] = float(risk)
    if risk <= 0:
        out["reason"] = "risk_non_positive"
        return out

    out["reason"] = "ok"
    return out


def backtest_day(df_day: pd.DataFrame, *, cfg: StrategyConfig) -> dict | None:
    orng = opening_range_0930(df_day)
    if not orng:
        return None
    range_high, range_low = orng

    long_setup = find_setup(df_day, direction="LONG", range_high=range_high, range_low=range_low, cfg=cfg)
    short_setup = find_setup(df_day, direction="SHORT", range_high=range_high, range_low=range_low, cfg=cfg)

    if long_setup is None and short_setup is None:
        return None
    if long_setup is None:
        chosen = short_setup
    elif short_setup is None:
        chosen = long_setup
    else:
        chosen = long_setup if long_setup["entry_time"] <= short_setup["entry_time"] else short_setup

    exit_time, exit_price, outcome, R = simulate_trade(
        df_day,
        direction=chosen["direction"],
        entry_time=chosen["entry_time"],
        entry_price=chosen["entry_price"],
        stop_price=chosen["stop_price"],
        target_price=chosen["target_price"],
        eod_exit=cfg.eod_exit,
    )

    return {
        "date": str(df_day.index[0].date()),
        **chosen,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "outcome": outcome,
        "R_multiple": float(R),
    }


def debug_trace(df_day: pd.DataFrame, *, cfg: StrategyConfig) -> dict:
    orng = opening_range_0930(df_day)
    out = {
        "date": str(df_day.index[0].date()) if not df_day.empty else None,
        "range_high": None,
        "range_low": None,
        "LONG": None,
        "SHORT": None,
        "picked_direction": None,
        "picked_entry_time": None,
    }
    if not orng:
        out["reason"] = "no_opening_range"
        return out
    range_high, range_low = orng
    out["range_high"], out["range_low"] = float(range_high), float(range_low)

    for direction in ("LONG", "SHORT"):
        trace = find_setup_trace(df_day, direction=direction, range_high=range_high, range_low=range_low, cfg=cfg)
        out[direction] = {k: (str(v) if isinstance(v, pd.Timestamp) else v) for k, v in trace.items()}

    candidates = []
    for d in ("LONG", "SHORT"):
        if out[d] and out[d].get("entry_time"):
            candidates.append((pd.Timestamp(out[d]["entry_time"]), d))
    if candidates:
        candidates.sort()
        out["picked_entry_time"] = str(candidates[0][0])
        out["picked_direction"] = candidates[0][1]
    return out


def print_debug_trace(trace: dict) -> None:
    """Pretty-print debug trace with one key/value per line."""
    print(f"date: {trace.get('date')}")
    print(f"range_high: {trace.get('range_high')}")
    print(f"range_low: {trace.get('range_low')}")
    print(f"picked_direction: {trace.get('picked_direction')}")
    print(f"picked_entry_time: {trace.get('picked_entry_time')}")

    def _print_side(name: str):
        side = trace.get(name) or {}
        print(f"\n{name} trace:")
        keys = [
            "reason",
            "break1_time",
            "retest_time",
            "rebreak1_time",
            "confirm_time",
            "entry_time",
            "entry_price",
            "stop_price",
            "target_price",
        ]
        for k in keys:
            if k in side:
                print(f"  {k}: {side.get(k)}")

    _print_side("LONG")
    _print_side("SHORT")


def summarize(trades: pd.DataFrame) -> None:
    print("\n=== 5m ORB Retest Re-break Strategy ===")
    print("Trades:", len(trades))
    if trades.empty:
        return
    win = int((trades["outcome"] == "TP").sum())
    loss = int(trades["outcome"].astype(str).str.contains("SL").sum())
    eod = int((trades["outcome"] == "EOD").sum())
    total = len(trades)
    print(f"Wins (TP): {win} ({win/total:.1%})")
    print(f"Losses (SL): {loss} ({loss/total:.1%})")
    print(f"EOD exits: {eod} ({eod/total:.1%})")
    print(f"Avg R: {trades['R_multiple'].mean():.3f}")
    print(f"Median R: {trades['R_multiple'].median():.3f}")
    print(f"Total R (sum): {trades['R_multiple'].sum():.3f}")


def main():
    p = argparse.ArgumentParser(description="5m opening-range retest re-break strategy (cached Polygon 5m)")
    p.add_argument("--ticker", default="SPY")
    p.add_argument(
        "--cache-root",
        default=str(Path(__file__).resolve().parent / "data" / "polygon"),
        help="Cache root containing <TICKER>_5min_*.csv or <TICKER>/<TICKER>_5min_*.csv",
    )
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--tz", default=TZ_DEFAULT)
    p.add_argument(
        "--auto-download",
        action="store_true",
        help="Ensure cache has the requested lookback window by downloading from Polygon before running.",
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="With --auto-download, force re-download (overwrite) for the covered months.",
    )
    p.add_argument(
        "--check-key",
        action="store_true",
        help="Print where POLYGON_API_KEY is being loaded from (does not print the key).",
    )

    p.add_argument("--breakout-buffer", type=float, default=0.0)
    p.add_argument("--stop-mode", choices=["breakout_line", "opposite"], default="breakout_line")
    p.add_argument("--stop-buffer", type=float, default=0.10, help="Stop buffer in price units (used with stop-mode=breakout_line)")
    p.add_argument("--no-break1-wick-clean", action="store_true", help="Allow first breakout candle to wick back into range")
    p.add_argument("--confirm-close-buffer", type=float, default=0.05, help="Min close distance outside range for confirmation (price units)")
    p.add_argument("--rr", type=float, default=1.5, help="Reward multiple (e.g., 1.0 or 1.5)")
    p.add_argument("--no-eod-exit", action="store_true")
    p.add_argument("--one-trade-per-day", action="store_true")

    p.add_argument("--run-date", default="", help="Run only for YYYY-MM-DD")
    p.add_argument("--debug-date", default="", help="Print debug trace for YYYY-MM-DD")
    p.add_argument("--out", default="", help="Optional output CSV")
    p.add_argument("--print-trades", action="store_true", help="Print per-trade timestamps for verification")
    p.add_argument("--print-trades-tail", type=int, default=0, help="Only print last N trades (0 = all)")
    p.add_argument("--log-file", default="", help="Optional path to write the printed trade log (text)")
    p.add_argument("--start-equity", type=float, default=1000.0, help="Starting account size for equity simulation")
    p.add_argument("--risk-frac", type=float, default=0.01, help="Fraction of equity risked per trade (used if --risk-dollars=0)")
    p.add_argument("--risk-dollars", type=float, default=0.0, help="Fixed $ risk per trade (overrides --risk-frac if > 0)")
    p.add_argument("--print-equity", action="store_true", help="Print equity summary using R-multiples")

    # Grid search / walk-forward
    p.add_argument("--grid-search", action="store_true", help="Run a small grid search over params and print ranked results")
    p.add_argument("--grid-stop-buffers", default="0.10,0.20", help="Comma list, e.g. 0.10,0.15,0.20")
    p.add_argument("--grid-confirm-buffers", default="0.05,0.20", help="Comma list, e.g. 0.05,0.10,0.20")
    p.add_argument("--grid-rrs", default="1.5,2.0", help="Comma list, e.g. 1.5,2.0")
    p.add_argument("--wf-test-sessions", type=int, default=60, help="Walk-forward test window size (sessions)")
    p.add_argument("--grid-top", type=int, default=15, help="How many grid rows to print")
    p.add_argument("--grid-only", action="store_true", help="If set, only print grid results (skip the single-run backtest)")
    p.add_argument("--grid-use-best", action="store_true", help="If set, run the single-run backtest using the best grid row")
    args = p.parse_args()

    cfg = StrategyConfig(
        tz=str(args.tz),
        breakout_buffer=float(args.breakout_buffer),
        break1_wick_clean=not bool(args.no_break1_wick_clean),
        confirm_close_buffer=float(args.confirm_close_buffer),
        one_trade_per_day=bool(args.one_trade_per_day),
        eod_exit=not bool(args.no_eod_exit),
        stop_mode=str(args.stop_mode),
        stop_buffer=float(args.stop_buffer),
        rr=float(args.rr),
    )

    cache_root = Path(args.cache_root)
    ticker = str(args.ticker).upper()

    if args.check_key:
        d = debug_polygon_key()
        print("\n=== Polygon key diagnostics (does not print secrets) ===")
        print("cwd:", d["cwd"])
        print("repo_root:", d["repo_root"])
        print("env_has_key:", d["env_has_key"])
        print("checked:")
        for pth in d["checked"]:
            print("  -", pth)
        print()

    if args.auto_download:
        # Download last N days into cache_root/<TICKER>/... (monthly files)
        cfg_dl = DownloadConfig(multiplier=5)
        try:
            download_last_n_days(
                ticker,
                days=int(args.days),
                base_dir=cache_root,
                cfg=cfg_dl,
                force_download=bool(args.force_refresh),
            )
        except Exception as e:
            # If download fails (e.g., 403), still allow running on whatever is already cached.
            print(f"\n[WARN] Auto-download failed: {e}\nProceeding with cached data if available...\n")

    df = load_cached_5m(cache_root, ticker, tz=cfg.tz)
    df = slice_last_days(df, int(args.days))
    print_coverage(df, requested_days=int(args.days))

    run_day = pd.to_datetime(args.run_date).date() if args.run_date else None
    debug_day = pd.to_datetime(args.debug_date).date() if args.debug_date else None

    # Helpful guardrail: if user requests a specific date that isn't present, say so explicitly.
    if run_day is not None and not df.empty and "date" in df.columns:
        available_dates = set(df["date"].unique())
        if run_day not in available_dates:
            try:
                min_d = min(available_dates)
                max_d = max(available_dates)
                n_sess = len(available_dates)
                print(
                    f"\n[WARN] Requested --run-date {run_day} is not present in the loaded cache/API data.\n"
                    f"       Available sessions in this run: {min_d} → {max_d} (sessions={n_sess}).\n"
                    f"       Try increasing --days, re-running with --auto-download/--force-refresh, "
                    f"or run the latest available date: --run-date {max_d}\n"
                )
            except Exception:
                print(f"\n[WARN] Requested --run-date {run_day} is not present in the loaded data.\n")

    # Optional grid search (walk-forward on last wf_test_sessions sessions)
    if bool(args.grid_search):
        stop_grid = _parse_float_list(str(args.grid_stop_buffers))
        conf_grid = _parse_float_list(str(args.grid_confirm_buffers))
        rr_grid = _parse_float_list(str(args.grid_rrs))
        if not stop_grid or not conf_grid or not rr_grid:
            raise ValueError("Grid lists are empty; provide --grid-stop-buffers/--grid-confirm-buffers/--grid-rrs")

        sessions = sorted(df["date"].unique())
        wf_test = int(args.wf_test_sessions)
        if wf_test <= 0 or wf_test >= len(sessions):
            raise ValueError(f"--wf-test-sessions must be between 1 and {len(sessions)-1}")
        train_dates = set(sessions[:-wf_test])
        test_dates = set(sessions[-wf_test:])

        rows = []
        for sb in stop_grid:
            for cb in conf_grid:
                for rr in rr_grid:
                    cfg2 = StrategyConfig(
                        tz=cfg.tz,
                        breakout_buffer=cfg.breakout_buffer,
                        break1_wick_clean=cfg.break1_wick_clean,
                        confirm_close_buffer=float(cb),
                        one_trade_per_day=cfg.one_trade_per_day,
                        eod_exit=cfg.eod_exit,
                        stop_mode=cfg.stop_mode,
                        stop_buffer=float(sb),
                        rr=float(rr),
                    )

                    df_train = df[df["date"].isin(train_dates)]
                    df_test = df[df["date"].isin(test_dates)]
                    tr_train = run_backtest(df_train, cfg=cfg2)
                    tr_test = run_backtest(df_test, cfg=cfg2)

                    def _stats(tr: pd.DataFrame) -> tuple[int, float, float]:
                        if tr.empty:
                            return 0, 0.0, 0.0
                        return int(len(tr)), float(tr["R_multiple"].mean()), float(tr["R_multiple"].sum())

                    ntr, avgR_tr, sumR_tr = _stats(tr_train)
                    nte, avgR_te, sumR_te = _stats(tr_test)
                    rows.append(
                        {
                            "stop_buffer": sb,
                            "confirm_close_buffer": cb,
                            "rr": rr,
                            "train_trades": ntr,
                            "train_avgR": avgR_tr,
                            "train_sumR": sumR_tr,
                            "test_trades": nte,
                            "test_avgR": avgR_te,
                            "test_sumR": sumR_te,
                        }
                    )

        grid = pd.DataFrame(rows)
        grid = grid.sort_values(["test_sumR", "test_avgR", "train_sumR"], ascending=False)
        topn = int(args.grid_top)
        print("\n=== Grid search (walk-forward) ===")
        print(f"train_sessions={len(train_dates)} test_sessions={len(test_dates)} (test=last {wf_test} sessions)")
        with pd.option_context("display.max_rows", topn, "display.max_columns", 99, "display.width", 160):
            print(grid.head(topn).to_string(index=False))

        if bool(args.grid_use_best) and not grid.empty:
            best = grid.iloc[0].to_dict()
            cfg = StrategyConfig(
                tz=cfg.tz,
                breakout_buffer=cfg.breakout_buffer,
                break1_wick_clean=cfg.break1_wick_clean,
                confirm_close_buffer=float(best["confirm_close_buffer"]),
                one_trade_per_day=cfg.one_trade_per_day,
                eod_exit=cfg.eod_exit,
                stop_mode=cfg.stop_mode,
                stop_buffer=float(best["stop_buffer"]),
                rr=float(best["rr"]),
            )
            print("\n=== Using best grid params for single-run backtest ===")
            print(
                f"stop_buffer={cfg.stop_buffer} confirm_close_buffer={cfg.confirm_close_buffer} rr={cfg.rr} "
                f"(ranked #1 by test_sumR/test_avgR)"
            )
            print()
        elif bool(args.grid_only):
            return

    res = run_backtest(df, cfg=cfg, run_day=run_day, debug_day=debug_day)
    summarize(res)
    if bool(args.print_equity) and not res.empty:
        curve = simulate_equity_curve(
            res,
            start_equity=float(args.start_equity),
            risk_frac=float(args.risk_frac),
            risk_dollars=float(args.risk_dollars),
        )
        end_eq = float(curve["equity"].iloc[-1])
        ret_pct = (end_eq / float(args.start_equity) - 1.0) * 100.0
        max_dd = float(curve["drawdown"].min()) * 100.0
        mode = f"fixed_${args.risk_dollars:.2f}" if float(args.risk_dollars) > 0 else f"fixed_frac={float(args.risk_frac):.3f}"
        print("\n=== Equity simulation ===")
        print("mode:", mode)
        print(f"start_equity: {float(args.start_equity):.2f}")
        print(f"end_equity:   {end_eq:.2f}")
        print(f"return:       {ret_pct:.2f}%")
        print(f"max_drawdown: {max_dd:.2f}%")

    if bool(args.print_trades):
        print_trade_logs(res, tail=int(args.print_trades_tail), file_path=str(args.log_file))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        res.to_csv(args.out, index=False)
        print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()

