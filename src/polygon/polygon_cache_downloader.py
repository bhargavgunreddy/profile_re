"""
Polygon OHLCV cache downloader (monthly files).

Writes data to:
  src/polygon/data/polygon/<TICKER>/<TICKER>_<MULT>min_<YYYY-MM>.csv

Notes on symbols:
- Stocks: "SPY", "NVDA", "TSLA", "META" work with the standard aggs endpoint.
- Futures: Polygon symbol formats vary by subscription/data product. Common formats are NOT universal.
  If ES/NQ fails, update ES_TICKER / NQ_TICKER in the entrypoint scripts to your Polygon-supported ticker.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

from polygon_secrets import get_polygon_api_key

@dataclass(frozen=True)
class DownloadConfig:
    api_key_env: str = "POLYGON_API_KEY"
    multiplier: int = 5
    timespan: str = "minute"
    adjusted: bool = True
    sort: str = "asc"
    limit: int = 50000
    polite_sleep_s: float = 0.5
    retry_429_sleep_s: float = 15.0


def _require_api_key(cfg: DownloadConfig) -> str:
    # Prefer local .env/.env.local (gitignored) or environment variable.
    return get_polygon_api_key(required=True, env_var=cfg.api_key_env)


def default_base_dir() -> Path:
    # .../src/polygon/
    return Path(__file__).resolve().parent / "data" / "polygon"


def cache_path(base_dir: Path, ticker: str, multiplier: int, year: int, month: int) -> Path:
    out_dir = base_dir / ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{ticker}_{multiplier}min_{year}-{month:02d}.csv"


def fetch_polygon_month_cached(
    ticker: str,
    year: int,
    month: int,
    *,
    base_dir: Path,
    cfg: DownloadConfig,
    force_download: bool = False,
) -> pd.DataFrame:
    """
    Fetch one calendar month of Polygon aggregated bars, cached to disk.
    Returns a DataFrame indexed by tz-aware UTC DatetimeIndex.
    """
    api_key = _require_api_key(cfg)
    file_path = cache_path(base_dir, ticker, cfg.multiplier, year, month)

    if file_path.exists() and not force_download:
        df = pd.read_csv(file_path, parse_dates=["Datetime"])
        df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
        return df.set_index("Datetime").sort_index()

    start_date = datetime(year, month, 1).date()
    end_date = (start_date + relativedelta(months=1))

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/"
        f"{cfg.multiplier}/{cfg.timespan}/{start_date}/{end_date}"
    )
    r = requests.get(
        url,
        params={
            "adjusted": "true" if cfg.adjusted else "false",
            "sort": cfg.sort,
            "limit": cfg.limit,
            "apiKey": api_key,
        },
        timeout=60,
    )

    if r.status_code == 429:
        time.sleep(cfg.retry_429_sleep_s)
        return fetch_polygon_month_cached(ticker, year, month, base_dir=base_dir, cfg=cfg)

    # IMPORTANT: don't call raise_for_status() directly, since it includes the full URL (and apiKey) in exceptions.
    if r.status_code == 403:
        raise RuntimeError(
            "Polygon returned 403 Forbidden for aggregates. "
            "Your API key may be missing permissions for this endpoint/data."
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Polygon request failed: HTTP {r.status_code} {r.reason}")

    data = r.json()
    if "results" not in data:
        return pd.DataFrame()

    df = pd.DataFrame(data["results"])
    df["Datetime"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(
        columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
    )
    df = df[["Datetime", "Open", "High", "Low", "Close", "Volume"]]

    df.to_csv(file_path, index=False)
    return df.set_index("Datetime").sort_index()


def download_range_monthly(
    ticker: str,
    *,
    start_date: str,
    end_date: str,
    base_dir: Path | None = None,
    cfg: DownloadConfig | None = None,
    force_download: bool = False,
) -> pd.DataFrame:
    """
    Download monthly cached data for [start_date, end_date] (YYYY-MM-DD strings).
    Returns concatenated DataFrame (UTC index), deduped and sorted.
    """
    if cfg is None:
        cfg = DownloadConfig()
    if base_dir is None:
        base_dir = default_base_dir()

    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()

    dfs: list[pd.DataFrame] = []
    cur = start.replace(day=1)
    while cur <= end:
        dfm = fetch_polygon_month_cached(
            ticker, cur.year, cur.month, base_dir=base_dir, cfg=cfg, force_download=force_download
        )
        if not dfm.empty:
            dfs.append(dfm)
        cur = cur + relativedelta(months=1)
        time.sleep(cfg.polite_sleep_s)

    if not dfs:
        return pd.DataFrame()

    out = pd.concat(dfs).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    return out


def download_last_n_days(
    ticker: str,
    *,
    days: int = 365,
    base_dir: Path | None = None,
    cfg: DownloadConfig | None = None,
    force_download: bool = False,
) -> pd.DataFrame:
    if cfg is None:
        cfg = DownloadConfig()
    end = datetime.now(timezone.utc).date()
    start = (end - relativedelta(days=days))
    return download_range_monthly(
        ticker,
        start_date=str(start),
        end_date=str(end),
        base_dir=base_dir,
        cfg=cfg,
        force_download=force_download,
    )

