import pandas as pd
import numpy as np
from pathlib import Path
import argparse

def daily_ema_position_report(df: pd.DataFrame) -> pd.DataFrame:
    """
    df: 5-min OHLCV with columns: Open, High, Low, Close, EMA20
        index must be tz-aware DatetimeIndex in America/New_York
        and ideally filtered to RTH 09:30-16:00
    Returns per-day metrics + prev day close metrics.
    """
    d = df.copy()
    d["date"] = d.index.date

    rows = []
    for day, g in d.groupby("date", sort=True):
        g = g.sort_index()

        # today's first and last bar in RTH
        first = g.iloc[0]
        last = g.iloc[-1]

        start_open = float(first["Open"])
        start_ema = float(first["EMA20"])
        start_dist = start_open - start_ema
        start_dist_pct = (start_open / start_ema) - 1.0 if start_ema else np.nan

        # intraday extremes vs EMA20
        max_above = float((g["High"] - g["EMA20"]).max())
        max_below = float((g["Low"] - g["EMA20"]).min())  # negative if below
        close_dist = float(last["Close"] - last["EMA20"])
        close_dist_pct = (float(last["Close"]) / float(last["EMA20"])) - 1.0 if float(last["EMA20"]) else np.nan

        rows.append({
            "date": pd.to_datetime(str(day)),
            "open_0930": start_open,
            "ema20_0930": start_ema,
            "open_minus_ema": start_dist,
            "open_minus_ema_pct": start_dist_pct,
            "max_above_ema": max_above,
            "max_below_ema": max_below,
            "close_minus_ema": close_dist,
            "close_minus_ema_pct": close_dist_pct,
        })

    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)

    # prev day close vs EMA
    out["prev_close_minus_ema"] = out["close_minus_ema"].shift(1)
    out["prev_close_minus_ema_pct"] = out["close_minus_ema_pct"].shift(1)

    return out


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


def build_report_from_cache(
    cache_dir: str | Path,
    ticker: str = "SPY",
    tz: str = "America/New_York",
    rth_start: str = "09:30",
    rth_end: str = "16:00",
    ema_span: int = 20,
) -> pd.DataFrame:
    df = load_cached_5min_ohlcv(cache_dir=cache_dir, ticker=ticker)
    df = df.tz_convert(tz).between_time(rth_start, rth_end)
    df["EMA20"] = df["Close"].ewm(span=ema_span, adjust=False).mean()
    return daily_ema_position_report(df)


def main():
    p = argparse.ArgumentParser(description="Daily EMA20 position report from cached Polygon 5-min CSVs")
    p.add_argument(
        "--cache-dir",
        default=str(Path(__file__).parent / "data" / "polygon"),
        help="Directory containing cached CSVs like SPY_5min_2025-01.csv",
    )
    p.add_argument("--ticker", default="SPY")
    p.add_argument("--tz", default="America/New_York")
    p.add_argument("--rth-start", default="09:30")
    p.add_argument("--rth-end", default="16:00")
    p.add_argument("--ema-span", type=int, default=20)
    p.add_argument("--tail", type=int, default=20, help="Print last N rows (0 prints all)")
    p.add_argument("--out", default="", help="Optional CSV output path")
    args = p.parse_args()

    report = build_report_from_cache(
        cache_dir=args.cache_dir,
        ticker=args.ticker,
        tz=args.tz,
        rth_start=args.rth_start,
        rth_end=args.rth_end,
        ema_span=args.ema_span,
    )

    if args.tail and args.tail > 0:
        print(report.tail(args.tail).to_string(index=False))
    else:
        print(report.to_string(index=False))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        report.to_csv(args.out, index=False)
        print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()
