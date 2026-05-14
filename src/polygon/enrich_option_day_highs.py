"""
Enrich gains/loss CSV with actual option day-high data from Polygon.

Adds optional columns:
- option_ticker
- close_day_option_high
- close_day_max_return_pct
- close_day_option_high_time
- close_day_high_timing

Usage:
  python3 src/polygon/enrich_option_day_highs.py \
    --input gainsandlosses.csv \
    --output gainsandlosses_enriched.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from polygon_secrets import get_polygon_api_key


MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def parse_instrument(instr: str) -> tuple[str, str, float, str] | None:
    """
    Parse strings like:
      SPY Feb 20 '26 $684 Call
      SPY Feb 06 '26 $687 Put w
    Returns (underlying, yymmdd, strike, cp) where cp is "C" or "P".
    """
    s = instr.strip()
    m = re.search(
        r"^([A-Z]+)\s+([A-Za-z]{3})\s+(\d{2})\s+'(\d{2})\s+\$([0-9]+(?:\.[0-9]+)?)\s+(Call|Put)\b",
        s,
    )
    if not m:
        return None
    under = m.group(1)
    mon = m.group(2).title()
    day = int(m.group(3))
    yy = int(m.group(4))
    strike = float(m.group(5))
    cp = "C" if m.group(6).lower() == "call" else "P"

    mm = MONTHS.get(mon)
    if not mm:
        return None
    year = 2000 + yy
    exp = datetime(year, mm, day).strftime("%y%m%d")
    return under, exp, strike, cp


def to_option_ticker(instr: str) -> str | None:
    parsed = parse_instrument(instr)
    if not parsed:
        return None
    under, exp, strike, cp = parsed
    strike_int = int(round(strike * 1000))
    return f"O:{under}{exp}{cp}{strike_int:08d}"


def fetch_close_day_high(
    option_ticker: str,
    close_date: str,
    api_key: str,
    *,
    timeout: int = 30,
    max_retries: int = 6,
) -> tuple[float | None, str]:
    url = f"https://api.polygon.io/v2/aggs/ticker/{option_ticker}/range/1/minute/{close_date}/{close_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
    last_err: Exception | None = None
    for attempt in range(max_retries):
        r = requests.get(url, params=params, timeout=timeout)
        # Polygon can throttle bursts; retry with backoff on transient responses.
        if r.status_code == 429 or 500 <= r.status_code < 600:
            retry_after = r.headers.get("Retry-After", "").strip()
            if retry_after:
                try:
                    sleep_s = float(retry_after)
                except Exception:
                    sleep_s = 0.0
            else:
                sleep_s = 0.6 * (2**attempt)
            time.sleep(min(max(sleep_s, 0.25), 12.0))
            last_err = RuntimeError(f"{r.status_code} from Polygon for {option_ticker} on {close_date}")
            continue
        r.raise_for_status()
        data = r.json()
        break
    else:
        if last_err:
            raise last_err
        raise RuntimeError(f"Failed to fetch Polygon data for {option_ticker} on {close_date}")

    results = data.get("results") or []
    if not results:
        return None, ""
    # Polygon aggregate fields:
    # - h: bar high
    # - t: bar start time in ms epoch
    best_h = None
    best_t = None
    for x in results:
        h = x.get("h")
        if h is None:
            continue
        hf = float(h)
        if best_h is None or hf > best_h:
            best_h = hf
            best_t = x.get("t")
    if best_h is None:
        return None, ""
    iso = ""
    if best_t is not None:
        try:
            dt = datetime.fromtimestamp(float(best_t) / 1000.0, tz=timezone.utc)
            iso = dt.isoformat()
        except Exception:
            iso = ""
    return best_h, iso


def _parse_ts(ts: str) -> datetime | None:
    s = (ts or "").strip()
    if not s:
        return None
    # Support "Z" suffix and timezone offsets
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _timing_label(high_ts: str, close_ts: str) -> str:
    h = _parse_ts(high_ts)
    c = _parse_ts(close_ts)
    if h is None or c is None:
        return ""
    if h > c:
        return "After close"
    if h < c:
        return "Before close"
    return "At close"


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich gains CSV with option day-high data from Polygon")
    ap.add_argument("--input", required=True, help="Input CSV path (e.g., gainsandlosses.csv)")
    ap.add_argument("--output", required=True, help="Output CSV path")
    ap.add_argument("--api_key_env", default="POLYGON_API_KEY", help="Env var for Polygon key")
    args = ap.parse_args()

    api_key = get_polygon_api_key(required=True, env_var=args.api_key_env)
    in_path = Path(args.input)
    out_path = Path(args.output)

    rows = list(csv.DictReader(in_path.open()))
    if not rows:
        raise RuntimeError("Input CSV is empty")

    cache: dict[tuple[str, str], tuple[float | None, str]] = {}
    enriched: list[dict] = []

    for r in rows:
        instr = str(r.get("instrument", "")).strip()
        close_date = str(r.get("close_date", "")).strip()
        cost_per = float(str(r.get("cost_per_share", "")).strip() or 0)
        opt_tkr = to_option_ticker(instr)

        day_high = None
        day_high_time = ""
        if opt_tkr and close_date:
            key = (opt_tkr, close_date)
            if key not in cache:
                try:
                    cache[key] = fetch_close_day_high(opt_tkr, close_date, api_key)
                except Exception as e:
                    print(f"warn: {opt_tkr} {close_date} -> {e}")
                    cache[key] = (None, "")
            day_high, day_high_time = cache[key]

        max_ret = None
        if day_high is not None and cost_per > 0:
            max_ret = ((day_high / cost_per) - 1.0) * 100.0

        close_ts = (
            str(r.get("close_time", "")).strip()
            or str(r.get("exit_time", "")).strip()
            or str(r.get("close_timestamp", "")).strip()
        )
        timing = _timing_label(day_high_time, close_ts)

        rr = dict(r)
        rr["option_ticker"] = opt_tkr or ""
        rr["close_day_option_high"] = "" if day_high is None else f"{day_high:.4f}"
        rr["close_day_max_return_pct"] = "" if max_ret is None else f"{max_ret:.2f}"
        rr["close_day_option_high_time"] = day_high_time
        rr["close_day_high_timing"] = timing
        enriched.append(rr)

    # preserve original order, append new cols if missing
    out_fields = list(rows[0].keys())
    for c in [
        "option_ticker",
        "close_day_option_high",
        "close_day_max_return_pct",
        "close_day_option_high_time",
        "close_day_high_timing",
    ]:
        if c not in out_fields:
            out_fields.append(c)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(enriched)

    print(f"wrote {len(enriched)} rows -> {out_path}")


if __name__ == "__main__":
    main()

