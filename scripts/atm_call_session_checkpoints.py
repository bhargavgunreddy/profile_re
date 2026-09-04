#!/usr/bin/env python3
"""ATM call checkpoints for a session date using Yahoo Finance 5-minute bars.

For each underlying:
  - close of the 09:30-09:35 ET 5m candle
  - nearest listed call: 0DTE if listed, else this week's Friday, else
    this month / next month — whichever chain actually prints at 12:30/3:30
  - option close on the 5m candle ending at 12:30 and 15:30 ET
    (falls back to 15m candle ending at those times, then the 5m bar starting then)
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TICKERS = [
    "BNTX",
    "EL",
    "HL",
    "MRK",
    "MSTR",
    "CDE",
    "AEM",
    "WPM",
    "EQX",
    "AU",
    "KGC",
    "BMNR",
    "MRVL",
    "HMY",
    "CRCL",
    "COIN",
    "ILMN",
    "AGI",
]


PUT_TICKERS = [
    "VICR",
    "NBIS",
    "MOG.B",
    "SITM",
    "MTSI",
    "STX",
    "TTMI",
    "SMTC",
    "STLD",
    "VIK",
    "AMKR",
    "AAOI",
    "ZTO",
    "DOCN",
]

# Yahoo equity symbols that differ from the user's ticker spelling.
YAHOO_SYMBOL = {
    "MOG.B": "MOG-B",
    "BF.B": "BF-B",
}


def _opener() -> urllib.request.OpenerDirector:
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(opener: urllib.request.OpenerDirector, url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    last: Exception | None = None
    for attempt in range(6):
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(0.8 * (2**attempt))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(0.8 * (2**attempt))
    raise last or RuntimeError(url)


def yahoo_session() -> tuple[urllib.request.OpenerDirector, str]:
    last: Exception | None = None
    for _ in range(4):
        opener = _opener()
        try:
            _get(opener, "https://finance.yahoo.com/")
        except Exception as e:
            last = e
        try:
            crumb = _get(opener, "https://query1.finance.yahoo.com/v1/test/getcrumb").decode().strip()
            if crumb and crumb != "{}":
                # sanity check
                probe = option_chain(opener, crumb, "BNTX")
                if (probe.get("optionChain") or {}).get("result"):
                    return opener, crumb
        except Exception as e:
            last = e
            time.sleep(1.2)
    raise RuntimeError(f"Could not establish Yahoo crumb session: {last}")


def parse_chart(payload: dict, session: date) -> dict[str, float]:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return {}
    r0 = result[0]
    ts = r0.get("timestamp") or []
    quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    out: dict[str, float] = {}
    for t, c in zip(ts, closes):
        if c is None:
            continue
        dt = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(ET)
        if dt.date() != session:
            continue
        out[dt.strftime("%H:%M")] = float(c)
    return out


def fetch_bars(
    opener: urllib.request.OpenerDirector,
    symbol: str,
    interval: str,
) -> dict:
    q = urllib.parse.urlencode(
        {"interval": interval, "range": "5d", "includePrePost": "false"}
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{q}"
    return json.loads(_get(opener, url))


def option_chain(
    opener: urllib.request.OpenerDirector,
    crumb: str,
    ticker: str,
    exp_unix: int | None = None,
) -> dict:
    q = {"crumb": crumb, "formatted": "false"}
    if exp_unix is not None:
        q["date"] = str(exp_unix)
    url = (
        f"https://query1.finance.yahoo.com/v7/finance/options/{urllib.parse.quote(ticker)}"
        f"?{urllib.parse.urlencode(q)}"
    )
    return json.loads(_get(opener, url))


def friday_of_week(d: date) -> date:
    return d + timedelta(days=(4 - d.weekday()) % 7)


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # first Friday
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def next_third_friday(d: date) -> date:
    m = third_friday(d.year, d.month)
    if m >= d:
        return m
    month = d.month + 1
    year = d.year
    if month == 13:
        month, year = 1, year + 1
    return third_friday(year, month)


def ranked_expirations(
    exp_dates: list[date],
    session: date,
    *,
    mode: str = "weekly_monthly",
) -> list[tuple[date, str]]:
    """Rank listed expiries.

    mode=weekly_monthly: 0DTE, this week, this month, next month.
    mode=closest: 0DTE, then nearest future listed expiry by date.
    """
    if not exp_dates:
        return []
    listed = set(exp_dates)
    ranked: list[tuple[date, str]] = []
    seen: set[date] = set()

    def add(d: date, kind: str) -> None:
        if d in listed and d not in seen:
            seen.add(d)
            ranked.append((d, kind))

    add(session, "0DTE")
    if mode == "closest":
        for d in sorted(e for e in exp_dates if e > session):
            days = (d - session).days
            if days <= 7:
                kind = "this_week"
            elif d <= next_third_friday(session):
                kind = "this_month"
            else:
                kind = "closest"
            add(d, kind)
        return ranked

    weekly = friday_of_week(session)
    monthly = next_third_friday(session)
    nxt_month = next_third_friday(monthly + timedelta(days=1))
    add(weekly, "this_week")
    add(monthly, "this_month")
    add(nxt_month, "next_month")
    return ranked


def nearest_contract(contracts: list[dict], spot: float) -> dict | None:
    priced = [c for c in contracts if c.get("strike") is not None]
    if not priced:
        return None
    return min(priced, key=lambda c: abs(float(c["strike"]) - spot))


def _hhmm_min(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def _from_min(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def checkpoint_close(bars5: dict[str, float], bars15: dict[str, float], hhmm: str) -> tuple[float | None, str]:
    """Price at hhmm from 5m/15m bars.

    Prefer the 5m candle ending at hhmm, then the 15m candle ending at hhmm,
    then any print in the prior 20 minutes (covers thin names).
    """
    end_5 = _from_min(_hhmm_min(hhmm) - 5)
    end_15 = _from_min(_hhmm_min(hhmm) - 15)
    if end_5 in bars5:
        return bars5[end_5], f"5m {end_5}-{hhmm}"
    if end_15 in bars15:
        return bars15[end_15], f"15m {end_15}-{hhmm}"
    if hhmm in bars5:
        return bars5[hhmm], f"5m {hhmm}-{_from_min(_hhmm_min(hhmm) + 5)}"
    if hhmm in bars15:
        return bars15[hhmm], f"15m {hhmm}-{_from_min(_hhmm_min(hhmm) + 15)}"

    target = _hhmm_min(hhmm)
    cands: list[tuple[int, str, float]] = []
    for src, bars in (("5m", bars5), ("15m", bars15)):
        for t, px in bars.items():
            delta = target - _hhmm_min(t)
            if 0 <= delta <= 20:
                cands.append((delta, f"{src} {t}", px))
    if not cands:
        return None, "no bar"
    cands.sort()
    delta, label, px = cands[0]
    return px, f"{label} (t-{delta}m)"


def unix_dates(exps: list[int]) -> list[tuple[int, date]]:
    out = []
    for e in exps:
        # Yahoo expirationDates are midnight UTC on the expiration calendar date
        out.append((e, datetime.fromtimestamp(e, tz=timezone.utc).date()))
    return out


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sum_field(rows: list[dict], key: str) -> float | None:
    vals = [_num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(vals), 4)


def _paired_pct(rows: list[dict], a_key: str, b_key: str) -> tuple[float | None, int]:
    """Pct change of summed premiums for rows that have both a and b."""
    a_sum = 0.0
    b_sum = 0.0
    n = 0
    for r in rows:
        a = _num(r.get(a_key))
        b = _num(r.get(b_key))
        if a is None or b is None or a == 0:
            continue
        a_sum += a
        b_sum += b
        n += 1
    if n == 0 or a_sum == 0:
        return None, 0
    return round(100.0 * (b_sum / a_sum - 1.0), 1), n


def build_total_row(rows: list[dict], *, session, right: str) -> dict:
    """Aggregate TOTAL row: sums skip blanks; pcts are paired-premium change."""
    data = [r for r in rows if str(r.get("ticker", "")).upper() != "TOTAL"]
    n = len(data)
    pct_935_1230, n_paired = _paired_pct(data, "opt_935", "opt_1230")
    pct_945_1230, _ = _paired_pct(data, "opt_945", "opt_1230")
    pct_935_1530, _ = _paired_pct(data, "opt_935", "opt_1530")
    note = f"Sums skip blanks. Pct totals are paired-premium change (n={n_paired})."
    return {
        "ticker": "TOTAL",
        "session": session.isoformat() if hasattr(session, "isoformat") else str(session),
        "right": right,
        "spot_935": _sum_field(data, "spot_935") or "",
        "expiry": "",
        "expiry_kind": f"{n} names",
        "strike": _sum_field(data, "strike") or "",
        "option_symbol": "",
        "opt_932": _sum_field(data, "opt_932") or "",
        "opt_935": _sum_field(data, "opt_935") or "",
        "opt_945": _sum_field(data, "opt_945") or "",
        "opt_1230": _sum_field(data, "opt_1230") or "",
        "opt_1530": _sum_field(data, "opt_1530") or "",
        "opt_1230_bar": "",
        "opt_1530_bar": "",
        "pct_935_to_1230": pct_935_1230 if pct_935_1230 is not None else "",
        "pct_945_to_1230": pct_945_1230 if pct_945_1230 is not None else "",
        "pct_935_to_1530": pct_935_1530 if pct_935_1530 is not None else "",
        "notes": note,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ATM option 12:30/3:30 checkpoints")
    ap.add_argument("--right", choices=("call", "put"), default="call")
    ap.add_argument("--tickers", default="", help="Comma-separated tickers")
    ap.add_argument("--session", default="2026-08-19")
    ap.add_argument(
        "--expiry-mode",
        choices=("weekly_monthly", "closest"),
        default="weekly_monthly",
        help="weekly_monthly=0DTE/week/month; closest=today then nearest listed expiry",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    session = date.fromisoformat(args.session)
    right = args.right
    expiry_mode = args.expiry_mode
    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers.strip()
        else (PUT_TICKERS if right == "put" else TICKERS)
    )
    # Keep user's mixed-case / class-share spelling for MOG.B
    if args.tickers.strip():
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        preserve = {"MOG.B", "BF.B"}
        tickers = [
            t.upper() if t.upper() not in preserve and t not in preserve else t.upper().replace("BF.B", "BF.B")
            for t in tickers
        ]
        tickers = [("BF.B" if t.upper() == "BF.B" else ("MOG.B" if t.upper() == "MOG.B" else t.upper())) for t in tickers]
    out = Path(
        args.out
        or (
            f"aug19_2026_atm_{right}_checkpoints.csv"
            if session == date(2026, 8, 19)
            else f"{session.isoformat()}_atm_{right}_checkpoints.csv"
        )
    )
    opener, crumb = yahoo_session()
    rows: list[dict] = []
    side_key = "puts" if right == "put" else "calls"
    noun = "put" if right == "put" else "call"

    for i, ticker in enumerate(tickers):
        yahoo = YAHOO_SYMBOL.get(ticker, ticker)
        row = {
            "ticker": ticker,
            "session": session.isoformat(),
            "right": right,
            "spot_935": "",
            "expiry": "",
            "expiry_kind": "",
            "strike": "",
            "option_symbol": "",
            "opt_932": "",
            "opt_935": "",
            "opt_945": "",
            "opt_1230": "",
            "opt_1530": "",
            "opt_1230_bar": "",
            "opt_1530_bar": "",
            "pct_935_to_1230": "",
            "pct_945_to_1230": "",
            "pct_935_to_1530": "",
            "notes": "",
        }
        try:
            stock5 = parse_chart(fetch_bars(opener, yahoo, "5m"), session)
            spot = stock5.get("09:30")
            if spot is None:
                row["notes"] = "missing 09:30-09:35 5m stock bar"
                rows.append(row)
                continue
            row["spot_935"] = round(spot, 4)

            chain = option_chain(opener, crumb, yahoo)
            result = (chain.get("optionChain") or {}).get("result") or []
            if not result:
                row["notes"] = "no option chain"
                rows.append(row)
                continue
            dated = unix_dates(result[0].get("expirationDates") or [])
            exp_dates = [d for _, d in dated]
            candidates = ranked_expirations(exp_dates, session, mode=expiry_mode)
            if not candidates:
                row["notes"] = "no usable expiration"
                rows.append(row)
                continue

            unix_by_date = {d: u for u, d in dated}
            tried: list[str] = []
            filled = False
            for chosen, kind in candidates:
                exp_unix = unix_by_date[chosen]
                chain_exp = option_chain(opener, crumb, yahoo, exp_unix)
                res2 = (chain_exp.get("optionChain") or {}).get("result") or []
                if not res2:
                    tried.append(f"{kind}:{chosen.isoformat()}(no chain)")
                    continue
                contracts = (((res2[0].get("options") or [{}])[0]).get(side_key)) or []
                contract = nearest_contract(contracts, spot)
                if contract is None:
                    tried.append(f"{kind}:{chosen.isoformat()}(no {noun}s)")
                    continue
                opt_sym = str(contract.get("contractSymbol") or "")
                opt5 = parse_chart(fetch_bars(opener, opt_sym, "5m"), session)
                opt15 = parse_chart(fetch_bars(opener, opt_sym, "15m"), session)
                opt1 = parse_chart(fetch_bars(opener, opt_sym, "1m"), session)
                p932 = opt1.get("09:32")
                if p932 is None:
                    # nearest 1m print within ±2 minutes of 09:32
                    for alt in ("09:31", "09:33", "09:30", "09:34"):
                        if alt in opt1:
                            p932 = opt1[alt]
                            break
                p935 = opt5.get("09:30")
                if p935 is None:
                    p935 = opt15.get("09:30")
                if p935 is None:
                    p935 = opt1.get("09:35")
                # 5m candle ending 09:45 starts at 09:40
                p945 = opt5.get("09:40")
                if p945 is None:
                    p945, _ = checkpoint_close(opt5, opt15, "09:45")
                if p945 is None:
                    p945 = opt1.get("09:45")
                p1230, src1230 = checkpoint_close(opt5, opt15, "12:30")
                p1530, src1530 = checkpoint_close(opt5, opt15, "15:30")
                last_cand = (chosen, kind) == candidates[-1]
                # In closest mode, lock the nearest listed expiry even if thin.
                # Otherwise keep falling through until a 12:30/3:30 print appears.
                if (
                    expiry_mode != "closest"
                    and kind != "0DTE"
                    and p1230 is None
                    and p1530 is None
                    and not last_cand
                ):
                    tried.append(f"{kind}:{chosen.isoformat()}(no 12:30/3:30)")
                    continue

                row["expiry"] = chosen.isoformat()
                row["expiry_kind"] = kind
                row["strike"] = float(contract["strike"])
                row["option_symbol"] = opt_sym
                if p935 is None:
                    p935_alt, src935 = checkpoint_close(opt5, opt15, "09:35")
                    if p935_alt is not None:
                        p935 = p935_alt
                        row["notes"] = f"9:35 used {src935}"
                if p932 is not None:
                    row["opt_932"] = round(p932, 4)
                if p935 is not None:
                    row["opt_935"] = round(p935, 4)
                if p945 is not None:
                    row["opt_945"] = round(p945, 4)
                row["opt_1230_bar"] = src1230
                row["opt_1530_bar"] = src1530
                if p1230 is not None:
                    row["opt_1230"] = round(p1230, 4)
                if p1530 is not None:
                    row["opt_1530"] = round(p1530, 4)
                if p935 and p1230 is not None:
                    row["pct_935_to_1230"] = round(100.0 * (p1230 / p935 - 1.0), 1)
                if p945 and p1230 is not None:
                    row["pct_945_to_1230"] = round(100.0 * (p1230 / p945 - 1.0), 1)
                if p935 and p1530 is not None:
                    row["pct_935_to_1530"] = round(100.0 * (p1530 / p935 - 1.0), 1)
                last = contract.get("lastPrice")
                if p1230 is None and p1530 is None:
                    extra = f"; tried {', '.join(tried)}" if tried else ""
                    note = f"no 12:30/3:30 bars yet; chain lastPrice={last}{extra}"
                    row["notes"] = f"{row['notes']}; {note}" if row["notes"] else note
                elif tried:
                    note = f"fell back after {', '.join(tried)}"
                    row["notes"] = f"{row['notes']}; {note}" if row["notes"] else note
                filled = True
                break
            if not filled:
                row["notes"] = f"no listed {noun} with contracts; tried " + ", ".join(tried)
        except Exception as e:
            row["notes"] = f"error: {type(e).__name__}: {e}"
        rows.append(row)
        time.sleep(0.15 if i < 3 else 0.08)

    if rows:
        rows.append(build_total_row(rows, session=session, right=right))

    fields = list(rows[0].keys()) if rows else []
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    data_n = sum(1 for r in rows if str(r.get("ticker", "")).upper() != "TOTAL")
    print(f"wrote {out} rows={data_n}")
    for r in rows:
        print(
            f"{r['ticker']:6} spot={r['spot_935']!s:>8} K={r['strike']!s:>7}  "
            f"9:32={r['opt_932']!s:>6} 9:35={r['opt_935']!s:>6} "
            f"9:45={r['opt_945']!s:>6} 12:30={r['opt_1230']!s:>6}  {r['notes']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
