"""
Build gainsandlosses_enriched.csv from Orders.csv by reconstructing closed option trades.

This creates a calendar-compatible CSV with columns expected by pnl_calendar.html:
- close_date, instrument, quantity, total_cost, proceeds, gain, cost_per_share, etc.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

_POLYGON_DIR = Path(__file__).resolve().parent
if str(_POLYGON_DIR) not in sys.path:
    sys.path.insert(0, str(_POLYGON_DIR))

from schwab_orders_preprocess import dedupe_identical_option_events, normalize_schwab_bracket_rows


MONTH_ABBR = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


def parse_dt_local(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s or s == "--":
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y, %I:%M:%S %p")
    except Exception:
        return None


def parse_fill(fill: str) -> tuple[int, float] | None:
    m = re.search(r"(\d+)\s*@\s*([0-9.]+)", fill or "")
    if not m:
        return None
    return int(m.group(1)), float(m.group(2))


def parse_desc(desc: str) -> dict | None:
    pat = re.compile(
        r"^(Buy|Sell)\s+(\d+)\s+([A-Za-z]{3})-(\d{2})-(\d{2})\s+(\d+)\s+(Call|Put)s?\s+@\s+([0-9.]+)\s+.*?\bto\s+(Open|Close)\b",
        re.IGNORECASE,
    )
    m = pat.search((desc or "").strip())
    if not m:
        return None
    side, qty, mon, day, yy, strike, cp, px, oc = m.groups()
    exp_year = 2000 + int(yy)
    exp_month = datetime.strptime(mon.title(), "%b").month
    exp_day = int(day)
    exp_date = f"{exp_year:04d}-{exp_month:02d}-{exp_day:02d}"
    return {
        "side": side.title(),
        "qty_desc": int(qty),
        "exp_date": exp_date,
        "strike": float(strike),
        "cp": cp.upper(),
        "oc": oc.title(),
        "px_desc": float(px),
    }


def contract_key(symbol: str, exp_date: str, strike: float, cp: str) -> tuple:
    return (symbol.upper(), exp_date, float(strike), cp.upper())


def instrument_label(symbol: str, exp_date: str, strike: float, cp: str) -> str:
    y, m, d = exp_date.split("-")
    mon = MONTH_ABBR[int(m)]
    yy = y[2:]
    cp_word = "Call" if cp.upper() == "CALL" else "Put"
    strike_txt = f"{int(strike)}" if float(strike).is_integer() else f"{strike:g}"
    return f"{symbol.upper()} {mon} {int(d):02d} '{yy} ${strike_txt} {cp_word}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Build gainsandlosses_enriched.csv from Orders.csv")
    ap.add_argument("--orders_csv", default="Orders.csv")
    ap.add_argument("--out_csv", default="gainsandlosses_enriched.csv")
    ap.add_argument("--commission", type=float, default=0.65, help="Per-contract commission (default $0.65)")
    ap.add_argument("--reg_fee", type=float, default=0.066, help="Per-contract regulatory fees (default $0.066)")
    args = ap.parse_args()
    fee_per_contract = args.commission + args.reg_fee

    lines = Path(args.orders_csv).read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 2:
        raise RuntimeError("Orders.csv appears empty/invalid.")

    raw_rows = list(csv.DictReader(lines[1:]))
    rows = normalize_schwab_bracket_rows(raw_rows)
    events = []
    skipped = 0
    for seq, r in enumerate(rows):
        status = (r.get("Status") or "").strip()
        symbol = (r.get("Symbol") or "").strip()
        if status != "Filled" or symbol in {"", "--"}:
            continue
        desc = parse_desc(r.get("Description") or "")
        fill = parse_fill(r.get("Fill") or "")
        ts = parse_dt_local(r.get("Time") or "")
        if not desc or not fill or not ts:
            skipped += 1
            continue
        qty_fill, px_fill = fill
        if qty_fill <= 0:
            skipped += 1
            continue
        events.append(
            {
                "dt": ts,
                "seq": seq,
                "symbol": symbol.upper(),
                "side": desc["side"],
                "oc": desc["oc"],
                "exp_date": desc["exp_date"],
                "strike": desc["strike"],
                "cp": desc["cp"],
                "qty": qty_fill,
                "price": px_fill,
            }
        )

    events = dedupe_identical_option_events(events)
    inv: dict[tuple, deque] = defaultdict(deque)
    out_rows = []
    unmatched_close_qty = 0

    for e in events:
        key = contract_key(e["symbol"], e["exp_date"], e["strike"], e["cp"])
        if e["side"] == "Buy" and e["oc"] == "Open":
            for _ in range(int(e["qty"])):
                inv[key].append({"dt": e["dt"], "price": e["price"]})
            continue

        if e["side"] == "Sell" and e["oc"] == "Close":
            q = int(e["qty"])
            matched = []
            while q > 0 and inv[key]:
                matched.append(inv[key].popleft())
                q -= 1
            if q > 0:
                unmatched_close_qty += q
            if not matched:
                continue

            n = len(matched)
            cost_total = sum(x["price"] * 100.0 for x in matched)
            proceeds_total = e["price"] * 100.0 * n
            commission = fee_per_contract * n * 2  # buy + sell sides
            cost_total += commission / 2  # half to cost (buy side)
            proceeds_total -= commission / 2  # half from proceeds (sell side)
            gain = proceeds_total - cost_total
            cost_per_share = (cost_total / (100.0 * n)) if n else 0.0
            instrument = instrument_label(e["symbol"], e["exp_date"], e["strike"], e["cp"])
            close_dt = e["dt"]
            open_dt = min(x["dt"] for x in matched)
            strike_scaled = int(round(e["strike"] * 1000))
            cp_char = "C" if e["cp"] == "CALL" else "P"
            exp_yymmdd = datetime.strptime(e["exp_date"], "%Y-%m-%d").strftime("%y%m%d")
            option_ticker = f"O:{e['symbol']}{exp_yymmdd}{cp_char}{strike_scaled:08d}"

            hold_min = (close_dt - open_dt).total_seconds() / 60.0

            out_rows.append(
                {
                    "account": "",
                    "search_start_date": "",
                    "search_end_date": "",
                    "search_symbol": e["symbol"],
                    "generated_at": datetime.now().strftime("%b %d %Y %I:%M %p ET"),
                    "instrument": instrument,
                    "action": "Sell To Close",
                    "quantity": str(n),
                    "open_date": open_dt.date().isoformat(),
                    "open_time": open_dt.strftime("%H:%M:%S"),
                    "cost_per_share": f"{cost_per_share:.2f}",
                    "total_cost": f"{cost_total:.2f}",
                    "close_date": close_dt.date().isoformat(),
                    "close_time": close_dt.strftime("%H:%M:%S"),
                    "hold_minutes": f"{hold_min:.1f}",
                    "price_per_share": f"{e['price']:.2f}",
                    "proceeds": f"{proceeds_total:.2f}",
                    "gain": f"{gain:.2f}",
                    "commission": f"{commission:.2f}",
                    "deferred_loss": ".00",
                    "term": "Short",
                    "lot_selection": "FIFO",
                    "option_ticker": option_ticker,
                    "close_day_option_high": "",
                    "close_day_max_return_pct": "",
                    "close_day_option_high_time": "",
                    "close_day_high_timing": "",
                }
            )

    today = datetime.now().date()
    for key, lots in inv.items():
        if not lots:
            continue
        symbol, exp_date, strike, cp = key
        exp_dt = datetime.strptime(exp_date, "%Y-%m-%d").date()
        if exp_dt > today:
            continue
        lot_list = list(lots)
        lots.clear()
        n = len(lot_list)
        cost_total = sum(x["price"] * 100.0 for x in lot_list)
        commission = fee_per_contract * n  # buy side only, never sold
        cost_total += commission
        gain = -cost_total
        cost_per_share = (cost_total / (100.0 * n)) if n else 0.0
        instrument = instrument_label(symbol, exp_date, strike, cp)
        open_dt = min(x["dt"] for x in lot_list)
        strike_scaled = int(round(strike * 1000))
        cp_char = "C" if cp == "CALL" else "P"
        exp_yymmdd = datetime.strptime(exp_date, "%Y-%m-%d").strftime("%y%m%d")
        option_ticker = f"O:{symbol}{exp_yymmdd}{cp_char}{strike_scaled:08d}"

        out_rows.append(
            {
                "account": "",
                "search_start_date": "",
                "search_end_date": "",
                "search_symbol": symbol,
                "generated_at": datetime.now().strftime("%b %d %Y %I:%M %p ET"),
                "instrument": instrument + " [EXPIRED]",
                "action": "Expired Worthless",
                "quantity": str(n),
                "open_date": open_dt.date().isoformat(),
                "open_time": open_dt.strftime("%H:%M:%S"),
                "cost_per_share": f"{cost_per_share:.2f}",
                "total_cost": f"{cost_total:.2f}",
                "close_date": exp_date,
                "close_time": "16:00:00",
                "hold_minutes": "",
                "price_per_share": "0.00",
                "proceeds": "0.00",
                "gain": f"{gain:.2f}",
                "commission": f"{commission:.2f}",
                "deferred_loss": ".00",
                "term": "Short",
                "lot_selection": "FIFO",
                "option_ticker": option_ticker,
                "close_day_option_high": "",
                "close_day_max_return_pct": "",
                "close_day_option_high_time": "",
                "close_day_high_timing": "",
            }
        )

    out_rows.sort(key=lambda r: (r["close_date"], r["instrument"]))

    fields = [
        "account",
        "search_start_date",
        "search_end_date",
        "search_symbol",
        "generated_at",
        "instrument",
        "action",
        "quantity",
        "open_date",
        "open_time",
        "cost_per_share",
        "total_cost",
        "close_date",
        "close_time",
        "hold_minutes",
        "price_per_share",
        "proceeds",
        "gain",
        "commission",
        "deferred_loss",
        "term",
        "lot_selection",
        "option_ticker",
        "close_day_option_high",
        "close_day_max_return_pct",
        "close_day_option_high_time",
        "close_day_high_timing",
    ]

    out_path = Path(args.out_csv)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)

    print(f"wrote {len(out_rows)} rows -> {out_path}")
    print(f"skipped_rows={skipped}, unmatched_close_qty={unmatched_close_qty}")
    if out_rows:
        print(f"date_range={out_rows[0]['close_date']}..{out_rows[-1]['close_date']}")


if __name__ == "__main__":
    main()

