"""
Download high-beta stocks (NVDA, TSLA, META) 5-minute bars into:
  src/polygon/data/polygon/<TICKER>/...
"""

from __future__ import annotations

import argparse
from pathlib import Path

from polygon_cache_downloader import DownloadConfig, default_base_dir, download_last_n_days


DEFAULT_TICKERS = ["NVDA", "TSLA", "META"]


def main():
    p = argparse.ArgumentParser(description="Download high-beta stock 5-minute Polygon cache")
    p.add_argument("--tickers", default=",".join(DEFAULT_TICKERS), help="Comma-separated tickers")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--mult", type=int, default=5)
    p.add_argument("--out-dir", default=str(default_base_dir()))
    args = p.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    cfg = DownloadConfig(multiplier=int(args.mult))
    out_dir = Path(args.out_dir)

    for t in tickers:
        df = download_last_n_days(t, days=int(args.days), base_dir=out_dir, cfg=cfg)
        print(f"{t}: downloaded rows={len(df)}; cache={out_dir / t}/")


if __name__ == "__main__":
    main()

