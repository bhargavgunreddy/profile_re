"""
Download ES (S&P 500 futures) 5-minute bars into:
  src/polygon/data/polygon/ES/...

IMPORTANT: The ES ticker format depends on your Polygon subscription/data product.
Update ES_TICKER below to the exact symbol Polygon supports for your account.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from polygon_cache_downloader import DownloadConfig, default_base_dir, download_last_n_days


# TODO: replace with your Polygon-supported ES symbol if needed.
ES_TICKER = "ES"


def main():
    p = argparse.ArgumentParser(description="Download ES 5-minute Polygon cache")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--mult", type=int, default=5)
    p.add_argument("--out-dir", default=str(default_base_dir()))
    args = p.parse_args()

    cfg = DownloadConfig(multiplier=int(args.mult))
    df = download_last_n_days(
        ES_TICKER,
        days=int(args.days),
        base_dir=Path(args.out_dir),
        cfg=cfg,
    )
    print(f"Downloaded rows: {len(df)}")
    print(f"Saved monthly cache under: {Path(args.out_dir) / ES_TICKER}/")


if __name__ == "__main__":
    main()

