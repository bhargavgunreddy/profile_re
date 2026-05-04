"""
Build dashboard analytics JSON directly from Orders.csv.

Parses filled option orders and reconstructs closed trades by matching:
- Buy ... to Open
- Sell ... to Close

Outputs the same JSON schema used by tradezilla_dashboard.html.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import pandas as pd

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
    # Example:
    # Sell 2 Feb-20-26 687 Calls @ 1.28 Limit to Close
    # Buy 1 Mar-04-26 685 Put @ 0.58 Limit to Open
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
    strike_f = float(strike)
    cp_u = cp.upper()
    return {
        "side": side.title(),
        "qty_desc": int(qty),
        "exp_date": exp_date,
        "strike": strike_f,
        "cp": cp_u,
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


def load_spy_daily_direction(spy_cache_root: Path) -> dict[str, dict]:
    files = sorted((spy_cache_root / "SPY").glob("SPY_5min_*.csv"))
    if not files:
        files = sorted(spy_cache_root.glob("SPY_5min_*.csv"))
    if not files:
        return {}

    parts = []
    for fp in files:
        d = pd.read_csv(fp, parse_dates=["Datetime"])
        d["Datetime"] = pd.to_datetime(d["Datetime"], utc=True)
        d = d.set_index("Datetime").sort_index()
        d = d.rename(columns=str.capitalize)
        parts.append(d[["Open", "Close"]])

    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.tz_convert("America/New_York")
    rth = df.between_time("09:30", "16:00").copy()
    rth["date"] = rth.index.date

    out: dict[str, dict] = {}
    for d, g in rth.groupby("date"):
        g = g.sort_index()
        o = float(g.iloc[0]["Open"])
        c = float(g.iloc[-1]["Close"])
        ret = ((c / o) - 1.0) * 100.0 if o else 0.0
        out[d.isoformat()] = {
            "open": o,
            "close": c,
            "ret_pct": ret,
            "dir": "UP" if c >= o else "DOWN",
        }
    return out


def build_trades_from_orders(orders_csv: Path, fee_per_contract: float = 0.716) -> tuple[list[dict], dict]:
    lines = orders_csv.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 2:
        return [], {"skipped_rows": 0, "unmatched_close_qty": 0}

    raw_rows = list(csv.DictReader(lines[1:]))
    rows = normalize_schwab_bracket_rows(raw_rows)
    events = []
    skipped = 0
    for seq, r in enumerate(rows):
        status = (r.get("Status") or "").strip()
        symbol = (r.get("Symbol") or "").strip()
        if status != "Filled" or symbol in {"--", ""}:
            continue

        desc = parse_desc(r.get("Description") or "")
        fill = parse_fill(r.get("Fill") or "")
        dt = parse_dt_local(r.get("Time") or "")
        if not desc or not fill or not dt:
            skipped += 1
            continue

        qty_fill, px_fill = fill
        if qty_fill <= 0:
            skipped += 1
            continue

        events.append(
            {
                "dt": dt,
                "seq": seq,
                "symbol": symbol.upper(),
                "side": desc["side"],   # Buy / Sell
                "oc": desc["oc"],       # Open / Close
                "exp_date": desc["exp_date"],
                "strike": desc["strike"],
                "cp": desc["cp"],       # CALL / PUT
                "qty": qty_fill,
                "price": px_fill,
            }
        )

    events = dedupe_identical_option_events(events)

    inventory: dict[tuple, deque] = defaultdict(deque)
    closed = []
    unmatched_close_qty = 0

    for e in events:
        k = contract_key(e["symbol"], e["exp_date"], e["strike"], e["cp"])

        if e["side"] == "Buy" and e["oc"] == "Open":
            # store lots as qty=1 for easy FIFO matching
            for _ in range(int(e["qty"])):
                inventory[k].append({"dt": e["dt"], "price": e["price"]})
            continue

        if e["side"] == "Sell" and e["oc"] == "Close":
            q = int(e["qty"])
            matched = []
            while q > 0 and inventory[k]:
                matched.append(inventory[k].popleft())
                q -= 1
            if q > 0:
                unmatched_close_qty += q

            if not matched:
                continue

            qty_matched = len(matched)
            buy_cost_total = sum(x["price"] * 100.0 for x in matched)
            sell_proceeds_total = e["price"] * 100.0 * qty_matched
            commission = fee_per_contract * qty_matched * 2  # buy + sell sides
            buy_cost_total += commission / 2
            sell_proceeds_total -= commission / 2
            gain = sell_proceeds_total - buy_cost_total
            avg_buy = (buy_cost_total / (100.0 * qty_matched)) if qty_matched else 0.0
            open_time = min(x["dt"] for x in matched)
            close_time = e["dt"]
            hold_min = (close_time - open_time).total_seconds() / 60.0
            pnl_pct = ((gain / buy_cost_total) * 100.0) if buy_cost_total else 0.0

            cp_word = "CALL" if e["cp"] == "CALL" else "PUT"
            closed.append(
                {
                    "date": close_time.date().isoformat(),
                    "instrument": instrument_label(e["symbol"], e["exp_date"], e["strike"], e["cp"]),
                    "action": "Sell To Close",
                    "type": cp_word,
                    "quantity": qty_matched,
                    "cost": buy_cost_total,
                    "proceeds": sell_proceeds_total,
                    "gain": gain,
                    "pnl_pct": pnl_pct,
                    "win": 1 if gain > 0 else 0,
                    "open_time": open_time.isoformat(),
                    "close_time": close_time.isoformat(),
                    "hold_minutes": hold_min,
                }
            )

    today = datetime.now().date()
    for key, lots in inventory.items():
        if not lots:
            continue
        symbol, exp_date, strike, cp = key
        exp_dt = datetime.strptime(exp_date, "%Y-%m-%d").date()
        if exp_dt > today:
            continue
        lot_list = list(lots)
        lots.clear()
        qty_matched = len(lot_list)
        buy_cost_total = sum(x["price"] * 100.0 for x in lot_list)
        commission = fee_per_contract * qty_matched  # buy side only
        buy_cost_total += commission
        gain = -buy_cost_total
        open_time = min(x["dt"] for x in lot_list)
        exp_eod = datetime.strptime(exp_date, "%Y-%m-%d").replace(hour=16, minute=0)
        hold_min = (exp_eod - open_time).total_seconds() / 60.0
        pnl_pct = -100.0

        cp_word = "CALL" if cp == "CALL" else "PUT"
        closed.append(
            {
                "date": exp_date,
                "instrument": instrument_label(symbol, exp_date, strike, cp) + " [EXPIRED]",
                "action": "Expired Worthless",
                "type": cp_word,
                "quantity": qty_matched,
                "cost": buy_cost_total,
                "proceeds": 0.0,
                "gain": gain,
                "pnl_pct": pnl_pct,
                "win": 0,
                "open_time": open_time.isoformat(),
                "close_time": exp_eod.isoformat(),
                "hold_minutes": hold_min,
            }
        )

    return closed, {"skipped_rows": skipped, "unmatched_close_qty": unmatched_close_qty}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build trade dashboard JSON from Orders.csv")
    ap.add_argument("--orders_csv", default="Orders.csv")
    ap.add_argument("--spy_cache_root", default="src/polygon/data/polygon")
    ap.add_argument("--out", default="trade_dashboard_data.json")
    ap.add_argument("--commission", type=float, default=0.65, help="Per-contract commission (default $0.65)")
    ap.add_argument("--reg_fee", type=float, default=0.066, help="Per-contract regulatory fees (default $0.066)")
    args = ap.parse_args()
    fee_per_contract = args.commission + args.reg_fee

    trades, diag = build_trades_from_orders(Path(args.orders_csv), fee_per_contract)
    spy_daily = load_spy_daily_direction(Path(args.spy_cache_root))

    for t in trades:
        md = spy_daily.get(t["date"])
        t["market_dir"] = md["dir"] if md else "NA"
        t["market_ret_pct"] = md["ret_pct"] if md else 0.0

    gains = [t["gain"] for t in trades]
    wins = [x for x in gains if x > 0]
    losses = [x for x in gains if x < 0]
    n = len(gains)
    total = sum(gains)
    win_rate = (len(wins) / n * 100.0) if n else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    gross_profit = sum(wins) if wins else 0.0
    gross_loss_abs = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else None
    expectancy = ((win_rate / 100.0) * avg_win) + ((1 - win_rate / 100.0) * avg_loss)

    day_pnl = defaultdict(float)
    for t in trades:
        day_pnl[t["date"]] += t["gain"]
    eq = []
    cum = 0.0
    for d in sorted(day_pnl.keys()):
        cum += day_pnl[d]
        eq.append({"date": d, "cum_pnl": cum, "day_pnl": day_pnl[d]})

    monthly = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    for t in trades:
        m = t["date"][:7]
        monthly[m]["pnl"] += t["gain"]
        monthly[m]["trades"] += 1
        monthly[m]["wins"] += t["win"]
    monthly_rows = []
    for m in sorted(monthly.keys()):
        v = monthly[m]
        wr = (v["wins"] / v["trades"] * 100.0) if v["trades"] else 0.0
        monthly_rows.append({"month": m, "pnl": v["pnl"], "trades": v["trades"], "win_rate": wr})

    wdays = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    weekday = {k: {"pnl": 0.0, "trades": 0, "wins": 0} for k in wdays}
    for t in trades:
        wd = datetime.strptime(t["date"], "%Y-%m-%d").strftime("%a")
        if wd not in weekday:
            continue
        weekday[wd]["pnl"] += t["gain"]
        weekday[wd]["trades"] += 1
        weekday[wd]["wins"] += t["win"]
    weekday_rows = []
    for wd in wdays:
        v = weekday[wd]
        wr = (v["wins"] / v["trades"] * 100.0) if v["trades"] else 0.0
        weekday_rows.append({"weekday": wd, "pnl": v["pnl"], "trades": v["trades"], "win_rate": wr})

    matrix = {
        "CALL_UP": {"trades": 0, "wins": 0, "pnl": 0.0},
        "CALL_DOWN": {"trades": 0, "wins": 0, "pnl": 0.0},
        "PUT_UP": {"trades": 0, "wins": 0, "pnl": 0.0},
        "PUT_DOWN": {"trades": 0, "wins": 0, "pnl": 0.0},
    }
    for t in trades:
        if t["type"] not in {"CALL", "PUT"} or t["market_dir"] not in {"UP", "DOWN"}:
            continue
        k = f"{t['type']}_{t['market_dir']}"
        matrix[k]["trades"] += 1
        matrix[k]["wins"] += t["win"]
        matrix[k]["pnl"] += t["gain"]
    for k in matrix:
        tr = matrix[k]["trades"]
        matrix[k]["win_rate"] = (matrix[k]["wins"] / tr * 100.0) if tr else 0.0

    biggest_wins = sorted(trades, key=lambda x: x["gain"], reverse=True)[:10]
    biggest_losses = sorted(trades, key=lambda x: x["gain"])[:10]

    payload = {
        "generated_at": datetime.now().isoformat(),
        "source_csv": args.orders_csv,
        "source_type": "orders",
        "diagnostics": diag,
        "kpis": {
            "trades": n,
            "total_pnl": total,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
        },
        "equity_curve": eq,
        "monthly": monthly_rows,
        "weekday": weekday_rows,
        "market_trade_matrix": matrix,
        "biggest_wins": biggest_wins,
        "biggest_losses": biggest_losses,
        "trades": trades,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote analytics JSON -> {out_path}")
    print(
        f"trades={n}, total_pnl={total:.2f}, win_rate={win_rate:.2f}%, "
        f"unmatched_close_qty={diag['unmatched_close_qty']}, skipped_rows={diag['skipped_rows']}"
    )


if __name__ == "__main__":
    main()

