#!/usr/bin/env python3
"""Backfill blank ATM checkpoint option prices using Polygon minute aggs.

Reads an existing atm_logs/*_atm_*_checkpoints.csv, fills empty opt_* cells
from Polygon 1-minute bars (exact minute close when available, else nearest
print within ±2 minutes), rebuilds TOTAL, and writes the CSV in place.

Usage:
  python3 scripts/backfill_atm_checkpoints_polygon.py \
    --csv atm_logs/sep10_2026_atm_call_checkpoints.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "polygon"))
from polygon_secrets import get_polygon_api_key  # noqa: E402

ET = ZoneInfo("America/New_York")
CHECKPOINT_COLS = {
    "opt_932": "09:32",
    "opt_935": "09:35",
    "opt_945": "09:45",
    "opt_1030": "10:30",
    "opt_1230": "12:30",
    "opt_1530": "15:30",
}


def yahoo_to_polygon_option(sym: str) -> str:
    s = (sym or "").strip()
    if not s:
        return ""
    return s if s.startswith("O:") else f"O:{s}"


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_option_1m(option_ticker: str, session: date, api_key: str) -> dict[str, float]:
    """Return {HH:MM ET: close} for the session day."""
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{option_ticker}/range/1/minute/"
        f"{session.isoformat()}/{session.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
    last_err: Exception | None = None
    for attempt in range(6):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(min(0.6 * (2**attempt), 12.0))
            last_err = RuntimeError(f"{r.status_code} for {option_ticker}")
            continue
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
        out: dict[str, float] = {}
        for bar in results:
            ts = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone_utc()).astimezone(ET)
            if ts.date() != session:
                continue
            out[ts.strftime("%H:%M")] = float(bar["c"])
        return out
    if last_err:
        raise last_err
    return {}


def timezone_utc():
    from datetime import timezone

    return timezone.utc


def price_at(bars: dict[str, float], hhmm: str, *, window: int = 2) -> tuple[float | None, str]:
    if hhmm in bars:
        return bars[hhmm], f"poly 1m {hhmm}"
    h, m = map(int, hhmm.split(":"))
    base = h * 60 + m
    cands: list[tuple[int, str, float]] = []
    for t, px in bars.items():
        th, tm = map(int, t.split(":"))
        delta = abs(th * 60 + tm - base)
        if delta <= window:
            cands.append((delta, t, px))
    if not cands:
        return None, "no bar"
    cands.sort()
    delta, t, px = cands[0]
    return px, f"poly 1m {t} (Δ{delta}m)"


def list_call_contracts(underlying: str, session: date, api_key: str) -> list[dict]:
    """Listed call contracts with expiration >= session, sorted by expiry then strike."""
    url = "https://api.polygon.io/v3/reference/options/contracts"
    params = {
        "underlying_ticker": underlying,
        "contract_type": "call",
        "expiration_date.gte": session.isoformat(),
        "limit": 1000,
        "apiKey": api_key,
    }
    out: list[dict] = []
    while True:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(1.5)
            continue
        r.raise_for_status()
        data = r.json() or {}
        out.extend(data.get("results") or [])
        next_url = data.get("next_url")
        if not next_url:
            break
        # next_url may omit apiKey
        if "apiKey=" not in next_url:
            next_url += ("&" if "?" in next_url else "?") + f"apiKey={api_key}"
        r = requests.get(next_url, timeout=30)
        r.raise_for_status()
        data = r.json() or {}
        out.extend(data.get("results") or [])
        # stop after first page+next to keep it light; enough for nearest weeklies
        if not data.get("next_url"):
            break
        break
    return out


def stock_close_at(underlying: str, session: date, hhmm: str, api_key: str) -> float | None:
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{underlying}/range/1/minute/"
        f"{session.isoformat()}/{session.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
    r = requests.get(url, params=params, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    bars: dict[str, float] = {}
    for bar in r.json().get("results") or []:
        ts = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone_utc()).astimezone(ET)
        bars[ts.strftime("%H:%M")] = float(bar["c"])
    # prefer 09:34 close of 09:30-09:35 window ≈ use 09:34/09:35
    for cand in (hhmm, "09:34", "09:33", "09:35", "09:32", "09:31", "09:30"):
        if cand in bars:
            return bars[cand]
    return None


def resolve_missing_contract(row: dict, session: date, api_key: str) -> None:
    """Fill strike/option_symbol for rows like UI that Yahoo couldn't resolve."""
    if (row.get("option_symbol") or "").strip():
        return
    ticker = row["ticker"]
    spot = _num(row.get("spot_935")) or stock_close_at(ticker, session, "09:35", api_key)
    if spot is None:
        row["notes"] = (row.get("notes") or "") + "; polygon: no stock bar for ATM"
        return
    row["spot_935"] = round(spot, 4)
    contracts = list_call_contracts(ticker, session, api_key)
    if not contracts:
        row["notes"] = (row.get("notes") or "") + "; polygon: no call contracts"
        return
    # nearest expiry first
    contracts.sort(key=lambda c: (c.get("expiration_date") or "", abs(float(c.get("strike_price") or 0) - spot)))
    exp = contracts[0]["expiration_date"]
    same_exp = [c for c in contracts if c.get("expiration_date") == exp]
    best = min(same_exp, key=lambda c: abs(float(c.get("strike_price") or 0) - spot))
    row["expiry"] = exp
    row["expiry_kind"] = "polygon_closest"
    row["strike"] = float(best["strike_price"])
    row["option_symbol"] = str(best.get("ticker") or "").replace("O:", "")
    note = f"polygon filled contract {row['option_symbol']}"
    row["notes"] = f"{row['notes']}; {note}" if row.get("notes") else note


def paired_pct(rows: list[dict], a: str, b: str) -> tuple[str, int]:
    a_sum = b_sum = 0.0
    n = 0
    for r in rows:
        x, y = _num(r.get(a)), _num(r.get(b))
        if x is None or y is None or x == 0:
            continue
        a_sum += x
        b_sum += y
        n += 1
    if n == 0 or a_sum == 0:
        return "", 0
    return str(round(100.0 * (b_sum / a_sum - 1.0), 1)), n


def sum_field(rows: list[dict], key: str) -> str:
    vals = [_num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return str(round(sum(vals), 4)) if vals else ""


def rebuild_total(data: list[dict], session: date, right: str) -> dict:
    n = len(data)
    pct1030, n1030 = paired_pct(data, "opt_935", "opt_1030")
    pct1230, n1230 = paired_pct(data, "opt_935", "opt_1230")
    pct945, n945 = paired_pct(data, "opt_935", "opt_945")
    return {
        "ticker": "TOTAL",
        "session": session.isoformat(),
        "right": right,
        "spot_935": sum_field(data, "spot_935"),
        "expiry": "",
        "expiry_kind": f"{n} names",
        "strike": sum_field(data, "strike"),
        "option_symbol": "",
        "opt_932": sum_field(data, "opt_932"),
        "opt_935": sum_field(data, "opt_935"),
        "opt_945": sum_field(data, "opt_945"),
        "opt_1030": sum_field(data, "opt_1030"),
        "opt_1230": sum_field(data, "opt_1230"),
        "opt_1530": sum_field(data, "opt_1530"),
        "opt_1030_bar": "",
        "opt_1230_bar": "",
        "opt_1530_bar": "",
        "pct_935_to_945": pct945,
        "pct_935_to_1030": pct1030,
        "pct_935_to_1230": pct1230,
        "pct_945_to_1230": paired_pct(data, "opt_945", "opt_1230")[0],
        "pct_935_to_1530": paired_pct(data, "opt_935", "opt_1530")[0],
        "notes": (
            f"Polygon backfill. Sums skip blanks. "
            f"Paired 9:35→9:45 n={n945}; 9:35→10:30 n={n1030}; 9:35→12:30 n={n1230}."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--only-blanks", action="store_true", default=True)
    args = ap.parse_args()
    path = Path(args.csv)
    rows = list(csv.DictReader(path.open()))
    fields = list(rows[0].keys())
    for col in CHECKPOINT_COLS:
        if col not in fields:
            fields.append(col)
    for extra in ("opt_1030_bar", "pct_935_to_945", "pct_935_to_1030", "pct_935_to_1230"):
        if extra not in fields:
            fields.append(extra)

    api_key = get_polygon_api_key(required=True)
    data = [r for r in rows if r.get("ticker", "").upper() != "TOTAL"]
    session = date.fromisoformat(data[0]["session"])
    right = data[0].get("right") or "call"

    filled = 0
    for r in data:
        resolve_missing_contract(r, session, api_key)
        need = [c for c, _ in CHECKPOINT_COLS.items() if not (r.get(c) or "").strip()]
        if not need and args.only_blanks:
            continue
        sym = yahoo_to_polygon_option(r.get("option_symbol") or "")
        if not sym:
            print(f"{r['ticker']:5} still no contract")
            continue
        try:
            bars = fetch_option_1m(sym, session, api_key)
        except Exception as e:
            print(f"{r['ticker']:5} fetch error {type(e).__name__}: {e}")
            continue
        if not bars:
            print(f"{r['ticker']:5} polygon empty bars for {sym}")
            time.sleep(0.15)
            continue
        for col, hhmm in CHECKPOINT_COLS.items():
            if (r.get(col) or "").strip() and args.only_blanks:
                continue
            px, src = price_at(bars, hhmm)
            if px is None:
                continue
            r[col] = round(px, 4)
            if col == "opt_1030":
                r["opt_1030_bar"] = src
            if col == "opt_1230":
                r["opt_1230_bar"] = src
            filled += 1
            print(f"{r['ticker']:5} filled {col}={px:.4f} via {src}")
        # recompute pcts when possible
        a = _num(r.get("opt_935"))
        for bcol, pcol in (
            ("opt_945", "pct_935_to_945"),
            ("opt_1030", "pct_935_to_1030"),
            ("opt_1230", "pct_935_to_1230"),
        ):
            b = _num(r.get(bcol))
            if a and b is not None:
                r[pcol] = round(100.0 * (b / a - 1.0), 1)
        note = "polygon backfilled blanks"
        r["notes"] = f"{r['notes']}; {note}" if r.get("notes") else note
        time.sleep(0.15)

    total = rebuild_total(data, session, right)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)
        w.writerow(total)
    print(f"wrote {path} filled_cells={filled}")
    print(
        "TOTAL",
        f"935={total['opt_935']}",
        f"945={total['opt_945']}",
        f"1030={total['opt_1030']}",
        f"1230={total['opt_1230']}",
        f"pct={total['pct_935_to_1230']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
