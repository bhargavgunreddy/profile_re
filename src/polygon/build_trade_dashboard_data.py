"""
Build analytics JSON for trade dashboard.

Inputs:
- gains/losses CSV (default: gainsandlosses_enriched.csv)
- SPY 5m cache dir (default: src/polygon/data/polygon)

Output:
- trade_dashboard_data.json (default at repo root)
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd


def parse_float(x: str) -> float:
    try:
        return float(str(x).strip())
    except Exception:
        return 0.0


def parse_date(x: str) -> datetime | None:
    try:
        return datetime.strptime(x, "%Y-%m-%d")
    except Exception:
        return None


def parse_trade_type(instr: str) -> str:
    s = (instr or "").lower()
    if " call" in s:
        return "CALL"
    if " put" in s:
        return "PUT"
    return "UNKNOWN"


def load_spy_daily_direction(spy_cache_root: Path) -> dict[str, dict]:
    # Supports both:
    # - <root>/SPY/SPY_5min_YYYY-MM.csv
    # - <root>/SPY_5min_YYYY-MM.csv
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
    # RTH only
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Build trade dashboard analytics JSON")
    ap.add_argument("--trades_csv", default="gainsandlosses_enriched.csv")
    ap.add_argument("--spy_cache_root", default="src/polygon/data/polygon")
    ap.add_argument("--out", default="trade_dashboard_data.json")
    args = ap.parse_args()

    trades_path = Path(args.trades_csv)
    rows = list(csv.DictReader(trades_path.open()))
    if not rows:
        raise RuntimeError("Trades CSV is empty.")

    spy_daily = load_spy_daily_direction(Path(args.spy_cache_root))

    trades = []
    for r in rows:
        close_date = (r.get("close_date") or "").strip()
        if not close_date:
            continue
        d = parse_date(close_date)
        if d is None:
            continue
        gain = parse_float(r.get("gain", "0"))
        cost = parse_float(r.get("total_cost", "0"))
        pnl_pct = ((gain / cost) * 100.0) if cost else 0.0
        ttype = parse_trade_type(r.get("instrument", ""))
        action = (r.get("action") or "").strip()
        market = spy_daily.get(close_date)
        market_dir = market["dir"] if market else "NA"
        market_ret = market["ret_pct"] if market else 0.0

        close_time = (r.get("close_time") or "").strip()
        open_time = (r.get("open_time") or "").strip()
        hold_minutes = parse_float(r.get("hold_minutes", "0"))

        trades.append(
            {
                "date": close_date,
                "instrument": r.get("instrument", ""),
                "action": action,
                "type": ttype,
                "quantity": parse_float(r.get("quantity", "0")),
                "cost": cost,
                "proceeds": parse_float(r.get("proceeds", "0")),
                "gain": gain,
                "pnl_pct": pnl_pct,
                "win": 1 if gain > 0 else 0,
                "market_dir": market_dir,
                "market_ret_pct": market_ret,
                "close_time": close_time,
                "open_time": open_time,
                "hold_minutes": hold_minutes,
            }
        )

    trades.sort(key=lambda x: x["date"])

    # KPIs
    gains = [t["gain"] for t in trades]
    wins = [x for x in gains if x > 0]
    losses = [x for x in gains if x < 0]
    total = sum(gains)
    n = len(gains)
    win_rate = (len(wins) / n * 100.0) if n else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    gross_profit = sum(wins) if wins else 0.0
    gross_loss_abs = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else None
    expectancy = ((win_rate / 100.0) * avg_win) + ((1 - win_rate / 100.0) * avg_loss)

    # Equity curve by close date
    day_pnl = defaultdict(float)
    for t in trades:
        day_pnl[t["date"]] += t["gain"]
    day_dates = sorted(day_pnl.keys())
    cum = 0.0
    equity_curve = []
    for d in day_dates:
        cum += day_pnl[d]
        equity_curve.append({"date": d, "cum_pnl": cum, "day_pnl": day_pnl[d]})

    # Monthly distribution
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

    # Weekday
    wdays = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    weekday = {k: {"pnl": 0.0, "trades": 0, "wins": 0} for k in wdays}
    for t in trades:
        dt = parse_date(t["date"])
        if dt is None:
            continue
        wd = dt.strftime("%a")
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

    # Market direction vs trade type matrix
    # keys: CALL_UP, CALL_DOWN, PUT_UP, PUT_DOWN
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

    # Time-of-day buckets (by close_time)
    time_buckets = [
        ("09:30-10:00", "09:30", "10:00"),
        ("10:00-11:30", "10:00", "11:30"),
        ("11:30-13:00", "11:30", "13:00"),
        ("13:00-14:30", "13:00", "14:30"),
        ("14:30-15:45", "14:30", "15:45"),
        ("15:45-16:00", "15:45", "16:01"),
    ]
    time_of_day = {b[0]: {"pnl": 0.0, "trades": 0, "wins": 0} for b in time_buckets}

    def _bucket_for_time(ct: str) -> str | None:
        if not ct:
            return None
        for label, lo, hi in time_buckets:
            if lo <= ct < hi:
                return label
        return None

    for t in trades:
        b = _bucket_for_time(t.get("close_time", ""))
        if b:
            time_of_day[b]["pnl"] += t["gain"]
            time_of_day[b]["trades"] += 1
            time_of_day[b]["wins"] += t["win"]

    time_of_day_rows = []
    for label, _, _ in time_buckets:
        v = time_of_day[label]
        wr = (v["wins"] / v["trades"] * 100.0) if v["trades"] else 0.0
        time_of_day_rows.append({"label": label, "pnl": v["pnl"], "trades": v["trades"], "win_rate": wr})

    # Hold-time buckets
    hold_buckets = [
        ("0-2 min", 0, 2),
        ("2-5 min", 2, 5),
        ("5-15 min", 5, 15),
        ("15-30 min", 15, 30),
        ("30-60 min", 30, 60),
        ("60+ min", 60, 1e9),
    ]
    hold_time_data = {b[0]: {"pnl": 0.0, "trades": 0, "wins": 0} for b in hold_buckets}

    for t in trades:
        hm = t.get("hold_minutes", 0)
        if not hm:
            continue
        for label, lo, hi in hold_buckets:
            if lo <= hm < hi:
                hold_time_data[label]["pnl"] += t["gain"]
                hold_time_data[label]["trades"] += 1
                hold_time_data[label]["wins"] += t["win"]
                break

    hold_time_rows = []
    for label, _, _ in hold_buckets:
        v = hold_time_data[label]
        wr = (v["wins"] / v["trades"] * 100.0) if v["trades"] else 0.0
        hold_time_rows.append({"label": label, "pnl": v["pnl"], "trades": v["trades"], "win_rate": wr})

    # Contract-count buckets
    qty_bucket_defs = [
        ("1 ct", 1, 1),
        ("2 ct", 2, 2),
        ("3-5 ct", 3, 5),
        ("10 ct", 10, 10),
        ("Other", -1, -1),
    ]
    qty_data: dict[str, dict] = {b[0]: {"pnl": 0.0, "trades": 0, "wins": 0} for b in qty_bucket_defs}

    def _qty_bucket(q: float) -> str:
        qi = int(q)
        for label, lo, hi in qty_bucket_defs:
            if lo <= qi <= hi:
                return label
        return "Other"

    for t in trades:
        b = _qty_bucket(t.get("quantity", 0))
        qty_data[b]["pnl"] += t["gain"]
        qty_data[b]["trades"] += 1
        qty_data[b]["wins"] += t["win"]

    qty_rows = []
    for label, _, _ in qty_bucket_defs:
        v = qty_data[label]
        if v["trades"] == 0:
            continue
        wr = (v["wins"] / v["trades"] * 100.0) if v["trades"] else 0.0
        avg = (v["pnl"] / v["trades"]) if v["trades"] else 0.0
        qty_rows.append({"label": label, "pnl": v["pnl"], "trades": v["trades"], "win_rate": wr, "avg_pnl": avg})

    # Biggest wins/losses
    biggest_wins = sorted(trades, key=lambda x: x["gain"], reverse=True)[:10]
    biggest_losses = sorted(trades, key=lambda x: x["gain"])[:10]

    payload = {
        "generated_at": datetime.now().isoformat(),
        "source_csv": str(trades_path),
        "kpis": {
            "trades": n,
            "total_pnl": total,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
        },
        "equity_curve": equity_curve,
        "monthly": monthly_rows,
        "weekday": weekday_rows,
        "market_trade_matrix": matrix,
        "time_of_day": time_of_day_rows,
        "hold_time": hold_time_rows,
        "qty_buckets": qty_rows,
        "biggest_wins": biggest_wins,
        "biggest_losses": biggest_losses,
        "trades": trades,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote analytics JSON -> {out_path}")
    print(f"trades={n}, total_pnl={total:.2f}, win_rate={win_rate:.2f}%")


if __name__ == "__main__":
    main()

