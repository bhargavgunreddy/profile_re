"""
SPY VWAP-context mean reversion strategy (standalone backtest).

This file is intentionally separate from other experiments.
It uses EXISTING cached Polygon 5m data (no downloads).

Core idea (configurable):
- After N consecutive green candles AND price is >= X% above VWAP during a time window,
  SHORT and take profit on VWAP touch, stop = ATR multiple, time/EOD exit otherwise.

Symmetric long version:
- After N consecutive red candles AND price is >= X% below VWAP, LONG to VWAP.

Outputs:
- Summary stats (win rate, Avg R, expectancy)
- Optional trades CSV
- Optional grid search across key parameters
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta


TZ = "America/New_York"
RTH_START = "09:30"
RTH_END = "16:00"


@dataclass(frozen=True)
class StrategyConfig:
    # signal
    n_seq: int = 4
    seq_color: str = "green"  # "green" -> short MR; "red" -> long MR
    vwap_dist_min: float = 0.006  # 0.6%
    min_seq_extension_pct: float = 0.003  # 0.3% move over the sequence
    tod_start: str = "10:30"
    tod_end: str = "14:30"

    # trade
    entry_at: str = "next_open"  # "next_open" or "next_close"
    stop_atr_mult: float = 1.0
    max_hold_bars: int = 48  # 48*5m = 4h
    force_eod_exit: bool = True
    one_trade_per_day: bool = True

    # costs (simple)
    slippage_bps: float = 0.5  # applied on entry and exit (each side)


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _in_time_window(ts: pd.Timestamp, start_hhmm: str, end_hhmm: str) -> bool:
    sh, sm = _parse_hhmm(start_hhmm)
    eh, em = _parse_hhmm(end_hhmm)
    return (sh, sm) <= (ts.hour, ts.minute) <= (eh, em)


def _find_cache_files(cache_root: Path, ticker: str) -> list[Path]:
    # folder style: .../<TICKER>/<TICKER>_5min_YYYY-MM.csv
    folder_files = sorted((cache_root / ticker).glob(f"{ticker}_5min_*.csv"))
    if folder_files:
        return folder_files
    # flat style: .../<TICKER>_5min_YYYY-MM.csv
    return sorted(cache_root.glob(f"{ticker}_5min_*.csv"))


def load_cached_5m(cache_root: Path, ticker: str) -> pd.DataFrame:
    files = _find_cache_files(cache_root, ticker)
    if not files:
        raise FileNotFoundError(f"No cache files found for {ticker} under {cache_root}")

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


def add_indicators(df_utc: pd.DataFrame) -> pd.DataFrame:
    """
    Adds daily VWAP and ATR14 (daily-reset).
    Returns NY-tz indexed dataframe with a `date` column.
    """
    d = df_utc.tz_convert(TZ).copy()
    d = d.between_time(RTH_START, RTH_END)
    d["date"] = d.index.date

    # VWAP
    if "Volume" not in d.columns:
        raise ValueError("Volume column required for VWAP.")
    d["pv"] = d["Close"] * d["Volume"]
    d["cum_pv"] = d.groupby("date")["pv"].cumsum()
    d["cum_vol"] = d.groupby("date")["Volume"].cumsum()
    d["VWAP"] = d["cum_pv"] / d["cum_vol"]

    # ATR14 within each day
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
        d.groupby("date")["TR"].rolling(14, min_periods=1).mean().reset_index(level=0, drop=True)
    )

    return d


def _seq_extension_pct(g: pd.DataFrame, start_i: int, end_i: int) -> float:
    o0 = float(g.iloc[start_i]["Open"])
    c1 = float(g.iloc[end_i]["Close"])
    if not o0:
        return 0.0
    return abs((c1 / o0) - 1.0)


def _scan_sequences(g: pd.DataFrame, *, n: int, seq_color: str, cfg: StrategyConfig) -> list[int]:
    """
    Returns indices i (sequence END index) matching:
    - N consecutive green/red candles
    - time window filter
    - VWAP distance filter (>= cfg.vwap_dist_min)
    - min sequence extension (>= cfg.min_seq_extension_pct)
    """
    closes = g["Close"].to_numpy(float)
    opens = g["Open"].to_numpy(float)
    idx = g.index

    seq_color = seq_color.lower().strip()
    if seq_color == "green":
        seq = closes > opens
        side = "SHORT"
    else:
        seq = closes < opens
        side = "LONG"

    out: list[int] = []
    i = n - 1
    while i < len(g) - 1:
        if seq[i - (n - 1) : i + 1].all():
            ts = idx[i]
            if not _in_time_window(ts, cfg.tod_start, cfg.tod_end):
                i += 1
                continue

            # VWAP distance at sequence end (directional)
            vwap = float(g.iloc[i]["VWAP"])
            if not vwap:
                i += 1
                continue
            dist = (closes[i] / vwap) - 1.0
            if side == "SHORT":
                ok = dist >= cfg.vwap_dist_min
            else:
                ok = (-dist) >= cfg.vwap_dist_min
            if not ok:
                i += 1
                continue

            # extension filter
            ext = _seq_extension_pct(g, i - (n - 1), i)
            if ext < cfg.min_seq_extension_pct:
                i += 1
                continue

            out.append(i)
            if cfg.one_trade_per_day:
                break
            i += 1
        else:
            i += 1

    return out


def backtest(df_utc: pd.DataFrame, *, cfg: StrategyConfig) -> pd.DataFrame:
    """
    Returns trades DataFrame.
    """
    df = add_indicators(df_utc)

    seq_color = cfg.seq_color.lower().strip()
    if seq_color not in ("green", "red"):
        raise ValueError("seq_color must be green or red")
    side = "SHORT" if seq_color == "green" else "LONG"

    trades: list[dict] = []
    for day, g in df.groupby("date", sort=True):
        g = g.sort_index()
        if len(g) < cfg.n_seq + 2:
            continue

        seq_end_idxs = _scan_sequences(g, n=cfg.n_seq, seq_color=seq_color, cfg=cfg)
        if not seq_end_idxs:
            continue

        for i in seq_end_idxs:
            entry_i = i + 1
            if entry_i >= len(g):
                continue
            entry_time = g.index[entry_i]
            entry_row = g.iloc[entry_i]
            entry_px = float(entry_row["Open"] if cfg.entry_at == "next_open" else entry_row["Close"])
            atr = float(entry_row["ATR14"])
            if np.isnan(atr) or atr <= 0:
                continue

            risk = cfg.stop_atr_mult * atr
            if side == "SHORT":
                stop_px = entry_px + risk
            else:
                stop_px = entry_px - risk

            after = g.iloc[entry_i:].copy()
            if cfg.max_hold_bars is not None and cfg.max_hold_bars > 0:
                after = after.iloc[: cfg.max_hold_bars + 1]

            outcome = "none"
            exit_time = None
            exit_px = np.nan

            for ts, bar in after.iterrows():
                hi = float(bar["High"])
                lo = float(bar["Low"])
                vwap = float(bar["VWAP"])

                if side == "SHORT":
                    tp_hit = lo <= vwap
                    sl_hit = hi >= stop_px
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
                if len(after) > 0 and cfg.force_eod_exit:
                    outcome = "time_exit" if (cfg.max_hold_bars and cfg.max_hold_bars > 0) else "eod"
                    exit_time = after.index[-1]
                    exit_px = float(after.iloc[-1]["Close"])
                else:
                    continue

            # slippage: apply bps on entry and exit (worsen result)
            slip = (cfg.slippage_bps / 10_000.0)
            if side == "SHORT":
                entry_eff = entry_px * (1 - slip)  # worse for short (sell lower)
                exit_eff = float(exit_px) * (1 + slip)  # buy back higher
                pnl_return = (entry_eff - exit_eff) / entry_eff
                pnl_r = (entry_eff - exit_eff) / risk
            else:
                entry_eff = entry_px * (1 + slip)  # buy higher
                exit_eff = float(exit_px) * (1 - slip)  # sell lower
                pnl_return = (exit_eff - entry_eff) / entry_eff
                pnl_r = (exit_eff - entry_eff) / risk

            vwap_dist = abs((float(g.iloc[i]["Close"]) / float(g.iloc[i]["VWAP"])) - 1.0)
            ext = _seq_extension_pct(g, i - (cfg.n_seq - 1), i)

            trades.append(
                {
                    "date": pd.to_datetime(str(day)),
                    "side": side,
                    "seq_color": seq_color,
                    "n_seq": cfg.n_seq,
                    "seq_end_time": g.index[i],
                    "entry_time": entry_time,
                    "entry_px": entry_px,
                    "stop_px": stop_px,
                    "exit_time": exit_time,
                    "exit_px": float(exit_px),
                    "outcome": outcome,
                    "atr14": atr,
                    "risk": risk,
                    "vwap_dist_at_seq_end": vwap_dist,
                    "seq_extension_pct": ext,
                    "pnl_return": float(pnl_return),
                    "pnl_r": float(pnl_r),
                }
            )

    return pd.DataFrame(trades).sort_values(["date", "entry_time"]).reset_index(drop=True)


def summarize(trades: pd.DataFrame) -> None:
    print("\n=== VWAP Context Mean Reversion Strategy ===")
    print("Trades:", len(trades))
    if trades.empty:
        return
    wins = trades[trades["pnl_r"] > 0]
    losses = trades[trades["pnl_r"] <= 0]
    print("Win rate (pnl_r>0):", round(float(len(wins) / len(trades)) * 100, 2), "%")
    print("Avg R:", round(float(trades["pnl_r"].mean()), 3))
    print("Median R:", round(float(trades["pnl_r"].median()), 3))
    if len(wins):
        print("Avg win R:", round(float(wins["pnl_r"].mean()), 3))
    if len(losses):
        print("Avg loss R:", round(float(losses["pnl_r"].mean()), 3))
    print("Outcome counts:")
    print(trades["outcome"].value_counts().to_string())


def run_grid(df_utc: pd.DataFrame, *, base_cfg: StrategyConfig) -> pd.DataFrame:
    """
    Small grid search over the highest-leverage parameters.
    """
    vwaps = [0.004, 0.006, 0.008]  # 0.4%, 0.6%, 0.8%
    stops = [0.8, 1.0, 1.2]
    holds = [24, 36, 48]  # 2h, 3h, 4h
    exts = [0.002, 0.003, 0.004]  # 0.2%, 0.3%, 0.4%

    rows: list[dict] = []
    for vd in vwaps:
        for st in stops:
            for mh in holds:
                for ext in exts:
                    cfg = StrategyConfig(
                        **{
                            **base_cfg.__dict__,
                            "vwap_dist_min": float(vd),
                            "stop_atr_mult": float(st),
                            "max_hold_bars": int(mh),
                            "min_seq_extension_pct": float(ext),
                        }
                    )
                    t = backtest(df_utc, cfg=cfg)
                    if t.empty:
                        continue
                    rows.append(
                        {
                            "trades": int(len(t)),
                            "win_rate": float((t["pnl_r"] > 0).mean()),
                            "avg_R": float(t["pnl_r"].mean()),
                            "median_R": float(t["pnl_r"].median()),
                            "vwap_dist_min": vd,
                            "stop_atr_mult": st,
                            "max_hold_bars": mh,
                            "min_seq_ext": ext,
                        }
                    )

    grid = pd.DataFrame(rows)
    if not grid.empty:
        grid = grid.sort_values(["avg_R", "trades"], ascending=[False, False]).reset_index(drop=True)
    return grid


def main():
    p = argparse.ArgumentParser(description="SPY VWAP-context mean reversion strategy (cached data)")
    p.add_argument("--cache-root", default=str(Path(__file__).resolve().parent / "data" / "polygon"))
    p.add_argument("--ticker", default="SPY")
    p.add_argument("--days", type=int, default=160)

    p.add_argument("--n-seq", type=int, default=4)
    p.add_argument("--seq-color", choices=["green", "red"], default="green")
    p.add_argument("--vwap-dist-min", type=float, default=0.006)
    p.add_argument("--min-seq-ext", type=float, default=0.003)
    p.add_argument("--tod-start", default="10:30")
    p.add_argument("--tod-end", default="14:30")

    p.add_argument("--entry-at", choices=["next_open", "next_close"], default="next_open")
    p.add_argument("--stop-atr-mult", type=float, default=1.0)
    p.add_argument("--max-hold-bars", type=int, default=48)
    p.add_argument("--no-eod-exit", action="store_true")
    p.add_argument("--one-trade-per-day", action="store_true")
    p.add_argument("--slippage-bps", type=float, default=0.5)

    p.add_argument("--out-csv", default="", help="Optional output CSV for trades")
    p.add_argument("--grid", action="store_true", help="Run small grid search")
    args = p.parse_args()

    df = load_cached_5m(Path(args.cache_root), ticker=str(args.ticker).upper())
    df = slice_last_days(df, days=int(args.days))

    cfg = StrategyConfig(
        n_seq=int(args.n_seq),
        seq_color=str(args.seq_color),
        vwap_dist_min=float(args.vwap_dist_min),
        min_seq_extension_pct=float(args.min_seq_ext),
        tod_start=str(args.tod_start),
        tod_end=str(args.tod_end),
        entry_at=str(args.entry_at),
        stop_atr_mult=float(args.stop_atr_mult),
        max_hold_bars=int(args.max_hold_bars),
        force_eod_exit=not bool(args.no_eod_exit),
        one_trade_per_day=bool(args.one_trade_per_day),
        slippage_bps=float(args.slippage_bps),
    )

    if args.grid:
        grid = run_grid(df, base_cfg=cfg)
        print("\n=== GRID (top 20 by Avg R) ===")
        if grid.empty:
            print("(no trades for any grid combo)")
        else:
            g = grid.head(20).copy()
            g["win_rate"] = (g["win_rate"] * 100).round(2)
            print(g.to_string(index=False))

    trades = backtest(df, cfg=cfg)
    summarize(trades)

    if args.out_csv:
        Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
        trades.to_csv(args.out_csv, index=False)
        print(f"\nSaved: {args.out_csv}")


if __name__ == "__main__":
    main()

