#!/usr/bin/env python3
"""ATM call checkpoints for a session date using Yahoo Finance 5-minute bars.

For each underlying:
  - close of the 09:30-09:35 ET 5m candle
  - nearest listed call, preferring 0DTE then this week then this month
  - option close on the 5m candle ending at 12:30 and 15:30 ET
    (falls back to 15m candle ending at those times, then the 5m bar starting then)
"""

from __future__ import annotations

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


def pick_expiration(exp_dates: list[date], session: date) -> tuple[date | None, str]:
    if not exp_dates:
        return None, "none"
    weekly = friday_of_week(session)
    monthly = third_friday(session.year, session.month)
    if monthly < session:
        month = session.month + 1
        year = session.year
        if month == 13:
            month, year = 1, year + 1
        monthly = third_friday(year, month)
    if session in exp_dates:
        return session, "0DTE"
    if weekly in exp_dates:
        return weekly, "this_week"
    if monthly in exp_dates:
        return monthly, "this_month"
    later = [e for e in exp_dates if e >= session]
    if later:
        chosen = min(later)
        label = "this_week" if chosen <= weekly else ("this_month" if chosen <= monthly else "next_listed")
        return chosen, label
    return None, "none"


def nearest_call(calls: list[dict], spot: float) -> dict | None:
    priced = [c for c in calls if c.get("strike") is not None]
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


def main() -> int:
    session = date(2026, 8, 19)
    opener, crumb = yahoo_session()
    rows: list[dict] = []

    for i, ticker in enumerate(TICKERS):
        row = {
            "ticker": ticker,
            "session": session.isoformat(),
            "spot_935": "",
            "expiry": "",
            "expiry_kind": "",
            "strike": "",
            "option_symbol": "",
            "opt_935": "",
            "opt_1230": "",
            "opt_1530": "",
            "opt_1230_bar": "",
            "opt_1530_bar": "",
            "pct_935_to_1230": "",
            "pct_935_to_1530": "",
            "notes": "",
        }
        try:
            stock5 = parse_chart(fetch_bars(opener, ticker, "5m"), session)
            spot = stock5.get("09:30")
            if spot is None:
                row["notes"] = "missing 09:30-09:35 5m stock bar"
                rows.append(row)
                continue
            row["spot_935"] = round(spot, 4)

            chain = option_chain(opener, crumb, ticker)
            result = (chain.get("optionChain") or {}).get("result") or []
            if not result:
                row["notes"] = "no option chain"
                rows.append(row)
                continue
            dated = unix_dates(result[0].get("expirationDates") or [])
            exp_dates = [d for _, d in dated]
            chosen, kind = pick_expiration(exp_dates, session)
            if chosen is None:
                row["notes"] = "no usable expiration"
                rows.append(row)
                continue
            row["expiry"] = chosen.isoformat()
            row["expiry_kind"] = kind
            exp_unix = next(u for u, d in dated if d == chosen)

            chain_exp = option_chain(opener, crumb, ticker, exp_unix)
            res2 = (chain_exp.get("optionChain") or {}).get("result") or []
            calls = (((res2[0].get("options") or [{}])[0]).get("calls")) or []
            call = nearest_call(calls, spot)
            if call is None:
                row["notes"] = "no calls on chosen expiry"
                rows.append(row)
                continue
            strike = float(call["strike"])
            opt_sym = str(call.get("contractSymbol") or "")
            row["strike"] = strike
            row["option_symbol"] = opt_sym

            opt5 = parse_chart(fetch_bars(opener, opt_sym, "5m"), session)
            opt15 = parse_chart(fetch_bars(opener, opt_sym, "15m"), session)
            p935 = opt5.get("09:30")
            if p935 is None:
                p935 = opt15.get("09:30")
            if p935 is not None:
                row["opt_935"] = round(p935, 4)

            p1230, src1230 = checkpoint_close(opt5, opt15, "12:30")
            p1530, src1530 = checkpoint_close(opt5, opt15, "15:30")
            row["opt_1230_bar"] = src1230
            row["opt_1530_bar"] = src1530
            if p1230 is not None:
                row["opt_1230"] = round(p1230, 4)
            if p1530 is not None:
                row["opt_1530"] = round(p1530, 4)
            if p935 and p1230 is not None:
                row["pct_935_to_1230"] = round(100.0 * (p1230 / p935 - 1.0), 1)
            if p935 and p1530 is not None:
                row["pct_935_to_1530"] = round(100.0 * (p1530 / p935 - 1.0), 1)
            if p1230 is None and p1530 is None:
                last = call.get("lastPrice")
                row["notes"] = (
                    f"no option bars; chain lastPrice={last} (not a 12:30/3:30 print)"
                )
        except Exception as e:
            row["notes"] = f"error: {type(e).__name__}: {e}"
        rows.append(row)
        time.sleep(0.15 if i < 3 else 0.08)

    out = Path("aug19_2026_atm_call_checkpoints.csv")
    fields = list(rows[0].keys()) if rows else []
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} rows={len(rows)}")
    for r in rows:
        print(
            f"{r['ticker']:5} spot={r['spot_935']!s:>8} {r['expiry_kind']:11} "
            f"{r['expiry']} K={r['strike']!s:>7}  "
            f"12:30={r['opt_1230']!s:>7}  3:30={r['opt_1530']!s:>7}  {r['notes']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
