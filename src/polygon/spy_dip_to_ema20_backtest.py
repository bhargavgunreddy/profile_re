import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from daily_ema_position_report import build_report_from_cache


@dataclass(frozen=True)
class DipToEMAConfig:
    ticker: str = "SPY"
    tz: str = "America/New_York"
    rth_start: str = "09:30"
    rth_end: str = "16:00"
    ema_span: int = 20

    atr_period: int = 14

    # Entry: buy when Close is meaningfully below EMA20 (a "dip")
    dip_entry_atr_mult: float = 0.20  # entry threshold = EMA - 0.2*ATR

    # Risk: ATR-based stop. Reward: exit at EMA20 touch (dynamic target each bar).
    stop_atr_mult: float = 0.80

    # Exits
    force_eod_exit: bool = True
    max_bars_in_trade: int | None = None  # e.g. 12 for 60 minutes (12x5m)

    # Open-stretch filters derived from daily report (quantified from your table)
    open_stretch_quantile_min: float = 0.60
    open_stretch_quantile_max: float = 0.95

    # When both stop and target happen in the same bar, be conservative:
    stop_wins_ties: bool = True


def load_cached_5min_ohlcv(cache_dir: str | Path, ticker: str = "SPY") -> pd.DataFrame:
    """
    Loads cached monthly Polygon CSVs like: SPY_5min_2025-01.csv
    Expected columns: Datetime, Open, High, Low, Close, Volume
    Datetime in the cache is tz-aware (UTC).
    """
    cache_dir = Path(cache_dir)
    files = sorted(cache_dir.glob(f"{ticker}_5min_*.csv"))
    if not files:
        raise FileNotFoundError(f"No cache files found in {cache_dir} matching {ticker}_5min_*.csv")

    dfs: list[pd.DataFrame] = []
    for fp in files:
        d = pd.read_csv(fp, parse_dates=["Datetime"])
        d["Datetime"] = pd.to_datetime(d["Datetime"], utc=True)
        d = d.set_index("Datetime")
        dfs.append(d)

    df = pd.concat(dfs).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def add_indicators(df: pd.DataFrame, *, cfg: DipToEMAConfig) -> pd.DataFrame:
    d = df.copy()
    d = d.tz_convert(cfg.tz).between_time(cfg.rth_start, cfg.rth_end)
    d["date"] = d.index.date
    d["EMA20"] = d["Close"].ewm(span=cfg.ema_span, adjust=False).mean()

    # ATR(14) on 5-min bars computed within each day (no overnight gap distortion)
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
        .rolling(cfg.atr_period, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )
    return d


def derive_open_stretch_thresholds(
    daily_report: pd.DataFrame, *, qmin: float, qmax: float
) -> tuple[float, float]:
    """
    Uses your daily report table columns (open_minus_ema_pct) to produce thresholds.
    """
    s = daily_report["open_minus_ema_pct"].dropna()
    # We only want "stretched up" opens for a long dip-to-EMA play.
    s = s[s > 0]
    if s.empty:
        return 0.0, float("inf")
    return float(s.quantile(qmin)), float(s.quantile(qmax))


def backtest_dip_to_ema(df: pd.DataFrame, daily_report: pd.DataFrame, *, cfg: DipToEMAConfig) -> pd.DataFrame:
    open_min, open_max = derive_open_stretch_thresholds(
        daily_report, qmin=cfg.open_stretch_quantile_min, qmax=cfg.open_stretch_quantile_max
    )

    results: list[dict] = []
    for day, g in df.groupby("date", sort=True):
        g = g.sort_index()
        first = g.iloc[0]
        open_0930 = float(first["Open"])
        ema_0930 = float(first["EMA20"])
        open_minus_ema_pct = (open_0930 / ema_0930) - 1.0 if ema_0930 else np.nan

        # Open stretch filter (quantified from the daily table)
        open_stretch_ok = bool(
            (not np.isnan(open_minus_ema_pct))
            and (open_minus_ema_pct >= open_min)
            and (open_minus_ema_pct <= open_max)
        )

        trade_taken = False
        entry_time = None
        entry_px = np.nan
        entry_atr = np.nan
        stop_px = np.nan
        exit_time = None
        exit_px = np.nan
        outcome = "no_trade"
        pnl_return = 0.0
        pnl_r = 0.0

        if open_stretch_ok:
            # Find first "dip" close below EMA by threshold.
            # Entry at the CLOSE of that bar (avoids same-bar OHLC path ambiguity).
            dip_level = g["EMA20"] - (cfg.dip_entry_atr_mult * g["ATR14"])
            hit = g["Close"] <= dip_level
            if hit.any():
                trade_taken = True
                entry_time = hit.idxmax()
                row = g.loc[entry_time]
                entry_px = float(row["Close"])
                entry_atr = float(row["ATR14"])
                stop_px = entry_px - (cfg.stop_atr_mult * entry_atr)

                # Walk forward from the NEXT bar for exits.
                after = g.loc[entry_time:]
                if len(after) > 1:
                    after = after.iloc[1:]
                if cfg.max_bars_in_trade is not None:
                    after = after.iloc[: cfg.max_bars_in_trade]

                # Target is EMA20 on each bar; fill at EMA20 when touched.
                hit_target = after["High"] >= after["EMA20"]
                hit_stop = after["Low"] <= stop_px

                # Find first times
                t_target = hit_target.idxmax() if hit_target.any() else None
                t_stop = hit_stop.idxmax() if hit_stop.any() else None

                if t_target is not None and t_stop is not None:
                    if (t_stop <= t_target) and cfg.stop_wins_ties:
                        outcome = "stop"
                        exit_time = t_stop
                        exit_px = stop_px
                    elif t_target <= t_stop:
                        outcome = "target"
                        exit_time = t_target
                        exit_px = float(g.loc[t_target, "EMA20"])
                    else:
                        outcome = "stop"
                        exit_time = t_stop
                        exit_px = stop_px
                elif t_target is not None:
                    outcome = "target"
                    exit_time = t_target
                    exit_px = float(g.loc[t_target, "EMA20"])
                elif t_stop is not None:
                    outcome = "stop"
                    exit_time = t_stop
                    exit_px = stop_px
                else:
                    if cfg.force_eod_exit and len(after) > 0:
                        outcome = "eod"
                        exit_time = after.index[-1]
                        exit_px = float(after.iloc[-1]["Close"])
                    elif len(after) > 0:
                        outcome = "time_exit"
                        exit_time = after.index[-1]
                        exit_px = float(after.iloc[-1]["Close"])
                    else:
                        outcome = "none"

                if trade_taken and not np.isnan(exit_px) and entry_px:
                    pnl_return = (exit_px - entry_px) / entry_px
                    risk = entry_px - stop_px
                    pnl_r = (exit_px - entry_px) / risk if risk > 0 else 0.0

        results.append(
            {
                "date": pd.to_datetime(str(day)),
                "open_minus_ema_pct": open_minus_ema_pct,
                "open_stretch_ok": open_stretch_ok,
                "trade_taken": trade_taken,
                "entry_time": entry_time,
                "entry_px": entry_px,
                "entry_atr14": entry_atr,
                "stop_px": stop_px,
                "exit_time": exit_time,
                "exit_px": exit_px,
                "outcome": outcome,
                "pnl_return": pnl_return,
                "pnl_r": pnl_r,
            }
        )

    res = pd.DataFrame(results).sort_values("date").reset_index(drop=True)
    res.attrs["open_stretch_min"] = open_min
    res.attrs["open_stretch_max"] = open_max
    return res


def print_summary(res: pd.DataFrame) -> None:
    trades = res[res["trade_taken"]].copy()
    print("\n=== DIP → EMA20 SYSTEM ===")
    print("Trades taken:", len(trades))
    if len(trades) == 0:
        return
    print("Outcome counts:")
    print(trades["outcome"].value_counts().to_string())
    wins = trades[trades["pnl_r"] > 0]
    losses = trades[trades["pnl_r"] <= 0]

    win_rate = float((trades["pnl_r"] > 0).mean())
    avg_r = float(trades["pnl_r"].mean())
    avg_win_r = float(wins["pnl_r"].mean()) if len(wins) else 0.0
    avg_loss_r = float(losses["pnl_r"].mean()) if len(losses) else 0.0

    print("Win rate (pnl_r > 0):", round(win_rate * 100, 2), "%")
    print("Avg R (expectancy):", round(avg_r, 3))
    print("Avg win R:", round(avg_win_r, 3))
    print("Avg loss R:", round(avg_loss_r, 3))
    print("Median R:", round(float(trades["pnl_r"].median()), 3))
    print("Avg return:", round(float(trades["pnl_return"].mean()) * 100, 3), "%")

    if "open_stretch_min" in res.attrs:
        print(
            "Open-stretch filter (open_minus_ema_pct) thresholds:",
            round(float(res.attrs["open_stretch_min"]) * 100, 3),
            "% →",
            round(float(res.attrs["open_stretch_max"]) * 100, 3),
            "%",
        )


def print_0dte_translation(cfg: DipToEMAConfig) -> None:
    print("\n=== 0DTE OPTIONS TRANSLATION (clean rules) ===")
    print("- Underlying signal stays the same (SPY 5-min).")
    print(
        "- Entry: when a 5-min bar CLOSES below EMA20 by",
        f"{cfg.dip_entry_atr_mult:.2f}×ATR14, buy a 0DTE call.",
    )
    print("- Profit: exit when SPY re-touches EMA20 (same as underlying target).")
    print(
        "- Stop: exit when SPY hits entry_price -",
        f"{cfg.stop_atr_mult:.2f}×ATR14 (underlying-based stop).",
    )
    print("- Time stop: if neither happens, exit at end-of-day (or after N bars if you set it).")
    print("- Contract choice (practical default): 0DTE call ~0.35–0.55 delta (ATM to 1-ITM).")
    print("- Sizing: pick a fixed $ risk per trade; use the option premium at entry to size contracts.")
    print("  Example: contracts = floor(risk_dollars / (premium * 100)).")
    print("- Note: options PnL won’t equal underlying R due to gamma/theta/IV; use underlying expectancy as the first gate.")


def main():
    p = argparse.ArgumentParser(description="Backtest SPY dip → exit at EMA20 using cached Polygon 5-min CSVs")
    p.add_argument("--cache-dir", default=str(Path(__file__).parent / "data" / "polygon"))
    p.add_argument("--ticker", default="SPY")
    p.add_argument("--out", default="", help="Optional CSV output path")

    # Strategy params
    p.add_argument("--dip-entry-atr", type=float, default=0.20)
    p.add_argument("--stop-atr", type=float, default=0.80)
    p.add_argument("--max-bars", type=int, default=0, help="0 means no time limit; 12 means 60 minutes on 5m bars")
    p.add_argument("--qmin", type=float, default=0.60, help="open_minus_ema_pct quantile min (stretched up opens)")
    p.add_argument("--qmax", type=float, default=0.95, help="open_minus_ema_pct quantile max (stretched up opens)")
    p.add_argument("--grid", action="store_true", help="Run a small grid search over dip-entry-atr and stop-atr")

    args = p.parse_args()

    cfg = DipToEMAConfig(
        ticker=args.ticker,
        dip_entry_atr_mult=float(args.dip_entry_atr),
        stop_atr_mult=float(args.stop_atr),
        max_bars_in_trade=(None if args.max_bars == 0 else int(args.max_bars)),
        open_stretch_quantile_min=float(args.qmin),
        open_stretch_quantile_max=float(args.qmax),
    )

    df_raw = load_cached_5min_ohlcv(args.cache_dir, ticker=cfg.ticker)
    df = add_indicators(df_raw, cfg=cfg)

    # Use the same cache to compute the daily open-stretch table (the one you exported)
    daily_report = build_report_from_cache(args.cache_dir, ticker=cfg.ticker, tz=cfg.tz)

    if args.grid:
        dip_grid = [0.10, 0.20, 0.30, 0.40, 0.50]
        stop_grid = [0.60, 0.80, 1.00, 1.20]

        rows = []
        for dip in dip_grid:
            for st in stop_grid:
                cfg2 = DipToEMAConfig(
                    ticker=cfg.ticker,
                    tz=cfg.tz,
                    rth_start=cfg.rth_start,
                    rth_end=cfg.rth_end,
                    ema_span=cfg.ema_span,
                    atr_period=cfg.atr_period,
                    dip_entry_atr_mult=float(dip),
                    stop_atr_mult=float(st),
                    force_eod_exit=cfg.force_eod_exit,
                    max_bars_in_trade=cfg.max_bars_in_trade,
                    open_stretch_quantile_min=cfg.open_stretch_quantile_min,
                    open_stretch_quantile_max=cfg.open_stretch_quantile_max,
                    stop_wins_ties=cfg.stop_wins_ties,
                )
                r = backtest_dip_to_ema(df, daily_report, cfg=cfg2)
                trades = r[r["trade_taken"]]
                if len(trades) == 0:
                    continue
                rows.append(
                    {
                        "dip_entry_atr": dip,
                        "stop_atr": st,
                        "trades": int(len(trades)),
                        "win_rate": float((trades["pnl_r"] > 0).mean()),
                        "avg_R": float(trades["pnl_r"].mean()),
                        "median_R": float(trades["pnl_r"].median()),
                    }
                )

        grid = pd.DataFrame(rows).sort_values(["avg_R", "trades"], ascending=[False, False])
        print("\n=== GRID SEARCH (dip→EMA20) — top 15 by Avg R ===")
        if not grid.empty:
            g = grid.copy()
            g["win_rate"] = (g["win_rate"] * 100).round(2)
            print(g.head(15).to_string(index=False))
        else:
            print("(no parameter set produced trades)")

        # Still run the requested single config after the grid for convenience.
        print("\n=== SELECTED CONFIG RUN ===")

    res = backtest_dip_to_ema(df, daily_report, cfg=cfg)
    print_summary(res)
    print_0dte_translation(cfg)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        res.to_csv(args.out, index=False)
        print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()

