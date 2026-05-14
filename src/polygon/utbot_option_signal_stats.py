"""
UTBot signal statistics (SPY options proxy backtest).

What it does:
- Reads TradingView/UTBot alerts from CSV (timestamp + BUY/SELL side).
- Reads SPY OHLCV bars from cached polygon CSV files.
- Simulates:
    BUY signal  -> buy CALL
    SELL signal -> buy PUT
- Exits when target option return is hit (e.g. +10% or +20%), or time-stop
  after N minutes (default 30).

Important:
- This uses a simple delta-based option proxy model (no IV/smile/spread).
- Good for directional signal quality / success-ratio estimation.
- For exact fills, replace with real option minute bars per contract.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    tz: str = "America/New_York"
    max_hold_minutes: int = 30
    call_delta: float = 0.45
    put_delta: float = 0.45
    entry_option_price: float = 1.00
    stop_loss_pct: float = -100.0  # disabled by default
    targets_pct: tuple[float, ...] = (10.0, 20.0)
    entry_mode: str = "same_bar_close"  # or next_bar_open


def _find_spy_files(cache_root: Path) -> list[Path]:
    files = list((cache_root / "SPY").glob("SPY_5min_*.csv"))
    if not files:
        files = list(cache_root.glob("SPY_5min_*.csv"))
    return sorted(files)


def load_spy_5m(cache_root: Path, tz: str) -> pd.DataFrame:
    files = _find_spy_files(cache_root)
    if not files:
        raise FileNotFoundError(
            f"No SPY 5m files under {cache_root}. Expected SPY_5min_*.csv or SPY/SPY_5min_*.csv"
        )

    chunks: list[pd.DataFrame] = []
    for fp in files:
        d = pd.read_csv(fp, parse_dates=["Datetime"])
        d["Datetime"] = pd.to_datetime(d["Datetime"], utc=True)
        d = d.set_index("Datetime").sort_index()
        d.columns = [c.strip().capitalize() for c in d.columns]
        chunks.append(d[["Open", "High", "Low", "Close"]])

    df = pd.concat(chunks).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.tz_convert(tz)
    return df


def _normalize_signal(x: str) -> str | None:
    s = str(x).strip().lower()
    if s in {"buy", "long", "bull", "bullish", "call"}:
        return "BUY"
    if s in {"sell", "short", "bear", "bearish", "put"}:
        return "SELL"
    return None


def load_alerts(alerts_csv: Path, tz: str) -> pd.DataFrame:
    raw = pd.read_csv(alerts_csv)
    cols_lower = {c.lower().strip(): c for c in raw.columns}

    time_col = None
    side_col = None

    for cand in ["timestamp", "time", "datetime", "alert_time", "date"]:
        if cand in cols_lower:
            time_col = cols_lower[cand]
            break
    for cand in ["signal", "side", "action", "alert", "direction"]:
        if cand in cols_lower:
            side_col = cols_lower[cand]
            break

    if not time_col or not side_col:
        raise ValueError(
            "Could not detect required columns in alerts CSV. Need timestamp/time + signal/side."
        )

    df = raw[[time_col, side_col]].copy()
    df.columns = ["timestamp", "signal"]
    df["signal"] = df["signal"].map(_normalize_signal)
    df = df.dropna(subset=["signal"])

    ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    # If source strings are timezone-naive, treat as NY time
    if ts.isna().any():
        ts_local = pd.to_datetime(df["timestamp"], errors="coerce")
        ts = ts_local.dt.tz_localize(tz).dt.tz_convert("UTC")
    df["timestamp"] = ts.dt.tz_convert(tz)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def _nearest_or_next_bar(ts: pd.Timestamp, bars: pd.DataFrame) -> pd.Timestamp | None:
    idx = bars.index.searchsorted(ts, side="left")
    if idx >= len(bars.index):
        return None
    return bars.index[idx]


def _option_pnl_pct(
    side: str,
    underlying_px: float,
    entry_underlying_px: float,
    *,
    entry_option_price: float,
    call_delta: float,
    put_delta: float,
) -> float:
    move = underlying_px - entry_underlying_px
    delta = call_delta if side == "BUY" else put_delta
    signed_move = move if side == "BUY" else -move
    est_option_px = entry_option_price + (delta * signed_move)
    # prevent nonsense negative premium
    est_option_px = max(0.01, est_option_px)
    return ((est_option_px / entry_option_price) - 1.0) * 100.0


def simulate_target(
    alerts: pd.DataFrame,
    bars: pd.DataFrame,
    *,
    cfg: BacktestConfig,
    target_pct: float,
) -> pd.DataFrame:
    rows: list[dict] = []
    hold_bars = max(1, int(cfg.max_hold_minutes // 5))

    for _, a in alerts.iterrows():
        alert_ts: pd.Timestamp = a["timestamp"]
        side: str = a["signal"]

        bar_ts = _nearest_or_next_bar(alert_ts, bars)
        if bar_ts is None:
            continue
        start_i = bars.index.get_loc(bar_ts)

        if cfg.entry_mode == "next_bar_open":
            start_i += 1
            if start_i >= len(bars):
                continue
            entry_ts = bars.index[start_i]
            entry_px = float(bars.iloc[start_i]["Open"])
        else:
            entry_ts = bar_ts
            entry_px = float(bars.iloc[start_i]["Close"])

        last_i = min(start_i + hold_bars, len(bars) - 1)
        exit_ts = bars.index[last_i]
        exit_reason = "TIME_STOP"
        exit_option_pnl_pct = None
        exit_underlying_px = float(bars.iloc[last_i]["Close"])

        # walk forward bars to find first target/stop touch
        for i in range(start_i, last_i + 1):
            b = bars.iloc[i]
            ts_i = bars.index[i]
            hi = float(b["High"])
            lo = float(b["Low"])
            cl = float(b["Close"])

            pnl_best = _option_pnl_pct(
                side,
                hi if side == "BUY" else lo,  # favorable underlying extreme
                entry_px,
                entry_option_price=cfg.entry_option_price,
                call_delta=cfg.call_delta,
                put_delta=cfg.put_delta,
            )
            pnl_worst = _option_pnl_pct(
                side,
                lo if side == "BUY" else hi,  # adverse underlying extreme
                entry_px,
                entry_option_price=cfg.entry_option_price,
                call_delta=cfg.call_delta,
                put_delta=cfg.put_delta,
            )
            pnl_close = _option_pnl_pct(
                side,
                cl,
                entry_px,
                entry_option_price=cfg.entry_option_price,
                call_delta=cfg.call_delta,
                put_delta=cfg.put_delta,
            )

            # conservative ordering on same bar:
            # if stop exists and both hit, treat as stop first.
            if cfg.stop_loss_pct > -100 and pnl_worst <= cfg.stop_loss_pct:
                exit_ts = ts_i
                exit_reason = "STOP_LOSS"
                exit_option_pnl_pct = cfg.stop_loss_pct
                exit_underlying_px = cl
                break
            if pnl_best >= target_pct:
                exit_ts = ts_i
                exit_reason = "TARGET_HIT"
                exit_option_pnl_pct = target_pct
                exit_underlying_px = cl
                break

            # keep timeout-close candidate
            if i == last_i:
                exit_option_pnl_pct = pnl_close
                exit_underlying_px = cl

        assert exit_option_pnl_pct is not None

        rows.append(
            {
                "alert_time": alert_ts,
                "entry_time": entry_ts,
                "exit_time": exit_ts,
                "side": side,
                "entry_underlying": entry_px,
                "exit_underlying": exit_underlying_px,
                "target_pct": target_pct,
                "exit_reason": exit_reason,
                "option_pnl_pct": exit_option_pnl_pct,
                "win": 1 if exit_reason == "TARGET_HIT" else 0,
            }
        )

    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "avg_option_pnl_pct": 0.0,
            "median_option_pnl_pct": 0.0,
            "target_hits": 0,
            "time_stops": 0,
            "stop_losses": 0,
        }
    return {
        "trades": int(len(trades)),
        "win_rate_pct": float(trades["win"].mean() * 100.0),
        "avg_option_pnl_pct": float(trades["option_pnl_pct"].mean()),
        "median_option_pnl_pct": float(trades["option_pnl_pct"].median()),
        "target_hits": int((trades["exit_reason"] == "TARGET_HIT").sum()),
        "time_stops": int((trades["exit_reason"] == "TIME_STOP").sum()),
        "stop_losses": int((trades["exit_reason"] == "STOP_LOSS").sum()),
    }


def _parse_targets(s: str) -> tuple[float, ...]:
    vals: list[float] = []
    for p in s.split(","):
        p = p.strip()
        if not p:
            continue
        vals.append(float(p))
    if not vals:
        raise ValueError("No target values provided")
    return tuple(vals)


def _print_summary(name: str, sm: dict) -> None:
    print(f"\n=== {name} ===")
    print(f"trades:               {sm['trades']}")
    print(f"win rate (%):         {sm['win_rate_pct']:.2f}")
    print(f"avg option pnl (%):   {sm['avg_option_pnl_pct']:.2f}")
    print(f"median option pnl (%):{sm['median_option_pnl_pct']:.2f}")
    print(f"target hits:          {sm['target_hits']}")
    print(f"time stops:           {sm['time_stops']}")
    print(f"stop losses:          {sm['stop_losses']}")


def _iter_targets(targets: Iterable[float]) -> Iterable[float]:
    for t in targets:
        if t <= 0:
            continue
        yield float(t)


def main() -> None:
    ap = argparse.ArgumentParser(description="UTBot buy/sell option success-ratio stats")
    ap.add_argument("--alerts_csv", required=True, help="TradingView alert CSV path")
    ap.add_argument(
        "--spy_cache_root",
        default="src/polygon/data/polygon",
        help="Path containing SPY_5min csv cache",
    )
    ap.add_argument("--tz", default="America/New_York")
    ap.add_argument("--max_hold_minutes", type=int, default=30)
    ap.add_argument("--targets_pct", default="10,20", help="Comma-separated targets in %%")
    ap.add_argument("--stop_loss_pct", type=float, default=-100.0, help="e.g. -20 for -20%% option SL")
    ap.add_argument("--call_delta", type=float, default=0.45)
    ap.add_argument("--put_delta", type=float, default=0.45)
    ap.add_argument("--entry_option_price", type=float, default=1.00)
    ap.add_argument(
        "--entry_mode",
        default="same_bar_close",
        choices=["same_bar_close", "next_bar_open"],
    )
    ap.add_argument("--out_prefix", default="utbot_option_stats")
    args = ap.parse_args()

    cfg = BacktestConfig(
        tz=args.tz,
        max_hold_minutes=args.max_hold_minutes,
        call_delta=args.call_delta,
        put_delta=args.put_delta,
        entry_option_price=args.entry_option_price,
        stop_loss_pct=args.stop_loss_pct,
        targets_pct=_parse_targets(args.targets_pct),
        entry_mode=args.entry_mode,
    )

    alerts = load_alerts(Path(args.alerts_csv), tz=cfg.tz)
    bars = load_spy_5m(Path(args.spy_cache_root), tz=cfg.tz)

    # Keep bars on the alert date range to speed up lookups
    if not alerts.empty:
        start = alerts["timestamp"].min().floor("D")
        end = alerts["timestamp"].max().ceil("D")
        bars = bars.loc[start:end]

    if alerts.empty or bars.empty:
        print("No data to backtest (alerts or bars empty).")
        return

    summary_rows: list[dict] = []
    for t in _iter_targets(cfg.targets_pct):
        trades = simulate_target(alerts, bars, cfg=cfg, target_pct=t)
        sm = summarize(trades)
        _print_summary(f"Target {t:.1f}%", sm)
        out_trades = f"{args.out_prefix}_target_{int(t)}.csv"
        trades.to_csv(out_trades, index=False)
        print(f"saved trades: {out_trades}")
        summary_rows.append({"target_pct": t, **sm})

    out_summary = f"{args.out_prefix}_summary.csv"
    pd.DataFrame(summary_rows).to_csv(out_summary, index=False)
    print(f"\nsaved summary: {out_summary}")


if __name__ == "__main__":
    main()

