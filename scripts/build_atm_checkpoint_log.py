#!/usr/bin/env python3
"""Build atm_checkpoint_log.html from CSVs under atm_logs/.

Usage:
  python3 scripts/build_atm_checkpoint_log.py
"""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "atm_logs"
OUT_HTML = ROOT / "atm_checkpoint_log.html"

SESSIONS = [
    {
        "id": "aug19-calls",
        "title": "Aug 19, 2026 — ATM Calls",
        "file": "aug19_2026_atm_call_checkpoints.csv",
        "right": "call",
        "blurb": "First list. Expiry weekly Aug 21 (EL/WPM → Sep 18 monthly). Columns: 9:35, 12:30, 3:30.",
        "cols": [
            ("ticker", "Ticker"),
            ("expiry", "Expiry"),
            ("spot_935", "9:35 stock"),
            ("strike", "Strike"),
            ("opt_935", "Call 9:35"),
            ("opt_1230", "Call 12:30"),
            ("opt_1530", "Call 3:30"),
            ("pct_935_to_1230", "9:35→12:30"),
        ],
    },
    {
        "id": "aug19-puts",
        "title": "Aug 19, 2026 — ATM Puts",
        "file": "aug19_2026_atm_put_checkpoints.csv",
        "right": "put",
        "blurb": "VICR–DOCN put list. Weekly Aug 21 or Sep 18 monthly. Columns: 9:35, 12:30, 3:30.",
        "cols": [
            ("ticker", "Ticker"),
            ("expiry", "Expiry"),
            ("spot_935", "9:35 stock"),
            ("strike", "Put strike"),
            ("opt_935", "Put 9:35"),
            ("opt_1230", "Put 12:30"),
            ("opt_1530", "Put 3:30"),
            ("pct_935_to_1230", "9:35→12:30"),
        ],
    },
    {
        "id": "aug27-calls",
        "title": "Aug 27, 2026 — Large Cap Gainer ATM Calls",
        "file": "aug27_2026_atm_call_checkpoints.csv",
        "right": "call",
        "blurb": "Two gainer screens (24 names). Closest expiry. Columns: 9:32, 9:35, 9:45, 12:30.",
        "cols": [
            ("ticker", "Ticker"),
            ("expiry", "Expiry"),
            ("spot_935", "9:35 stock"),
            ("strike", "Strike"),
            ("opt_932", "Call 9:32"),
            ("opt_935", "Call 9:35"),
            ("opt_945", "Call 9:45"),
            ("opt_1230", "Call 12:30"),
            ("pct_935_to_1230", "9:35→12:30"),
        ],
    },
    {
        "id": "sep1-calls",
        "title": "Sep 1, 2026 — ATM Calls",
        "file": "sep1_2026_atm_call_checkpoints.csv",
        "right": "call",
        "blurb": "BEKE, LLY, CRWD, CVS, NVS, OKE, MDT, UMC. Closest expiry.",
        "cols": [
            ("ticker", "Ticker"),
            ("expiry", "Expiry"),
            ("spot_935", "9:35 stock"),
            ("strike", "Strike"),
            ("opt_932", "Call 9:32"),
            ("opt_935", "Call 9:35"),
            ("opt_945", "Call 9:45"),
            ("opt_1230", "Call 12:30"),
            ("pct_935_to_1230", "9:35→12:30"),
        ],
    },
    {
        "id": "sep2-calls",
        "title": "Sep 2, 2026 — ATM Calls (30 names)",
        "file": "sep2_2026_atm_call_checkpoints.csv",
        "right": "call",
        "blurb": "ASTS–PYPL + BBY–PSKY. NVDA 0DTE. softbbd→SOFI+BBD, bf.b→BF.B.",
        "cols": [
            ("ticker", "Ticker"),
            ("expiry", "Expiry"),
            ("spot_935", "9:35 stock"),
            ("strike", "Strike"),
            ("opt_932", "Call 9:32"),
            ("opt_935", "Call 9:35"),
            ("opt_945", "Call 9:45"),
            ("opt_1230", "Call 12:30"),
            ("pct_935_to_1230", "9:35→12:30"),
        ],
    },
    {
        "id": "sep3-calls",
        "title": "Sep 3, 2026 — ATM Calls (17 names)",
        "file": "sep3_2026_atm_call_checkpoints.csv",
        "right": "call",
        "blurb": "SNOW, HOOD, CRCL, SMMT, MSTR, BMNR, COIN, CLS, PLTR, TSLA, RBRK, SPCX, NOW, ORCL, SAIL, HL, CRWD. Closest expiry.",
        "cols": [
            ("ticker", "Ticker"),
            ("expiry", "Expiry"),
            ("spot_935", "9:35 stock"),
            ("strike", "Strike"),
            ("opt_932", "Call 9:32"),
            ("opt_935", "Call 9:35"),
            ("opt_945", "Call 9:45"),
            ("opt_1230", "Call 12:30"),
            ("pct_935_to_1230", "9:35→12:30"),
        ],
    },
    {
        "id": "sep4-calls",
        "title": "Sep 4, 2026 — ATM Calls (15 names)",
        "file": "sep4_2026_atm_call_checkpoints.csv",
        "right": "call",
        "blurb": "ALAB, CBRS, SOXL, SNDK, MU, INTC, SKHY, ORCL, MRVL, BE, KLAC, TSEM, COHR, SMCI, FIVE. Closest expiry.",
        "cols": [
            ("ticker", "Ticker"),
            ("expiry", "Expiry"),
            ("spot_935", "9:35 stock"),
            ("strike", "Strike"),
            ("opt_932", "Call 9:32"),
            ("opt_935", "Call 9:35"),
            ("opt_945", "Call 9:45"),
            ("opt_1230", "Call 12:30"),
            ("pct_935_to_1230", "9:35→12:30"),
        ],
    },
]


def load_rows(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open()))


def fmt_num(v: str, *, pct: bool = False) -> str:
    s = (v or "").strip()
    if not s:
        return "—"
    try:
        x = float(s)
    except ValueError:
        return escape(s)
    if pct:
        sign = "+" if x > 0 else ""
        return f"{sign}{x:.0f}%"
    return f"{x:.2f}"


def is_total(row: dict) -> bool:
    return (row.get("ticker") or "").strip().upper() == "TOTAL"


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sum_field(rows: list[dict], key: str) -> str:
    vals = [_num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    if not vals:
        return ""
    return str(round(sum(vals), 4))


def _paired_pct(rows: list[dict], a_key: str, b_key: str) -> str:
    a_sum = b_sum = 0.0
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
        return ""
    return str(round(100.0 * (b_sum / a_sum - 1.0), 1))


def ensure_total_row(rows: list[dict], *, right: str) -> list[dict]:
    """Guarantee a TOTAL row is present and is last."""
    data = [r for r in rows if not is_total(r)]
    existing = [r for r in rows if is_total(r)]
    if existing:
        return data + existing[-1:]
    session = (data[0].get("session") if data else "") or ""
    n = len(data)
    total = {
        "ticker": "TOTAL",
        "session": session,
        "right": right,
        "spot_935": _sum_field(data, "spot_935"),
        "expiry": "",
        "expiry_kind": f"{n} names",
        "strike": _sum_field(data, "strike"),
        "option_symbol": "",
        "opt_932": _sum_field(data, "opt_932"),
        "opt_935": _sum_field(data, "opt_935"),
        "opt_945": _sum_field(data, "opt_945"),
        "opt_1230": _sum_field(data, "opt_1230"),
        "opt_1530": _sum_field(data, "opt_1530"),
        "opt_1230_bar": "",
        "opt_1530_bar": "",
        "pct_935_to_1230": _paired_pct(data, "opt_935", "opt_1230"),
        "pct_945_to_1230": _paired_pct(data, "opt_945", "opt_1230"),
        "pct_935_to_1530": _paired_pct(data, "opt_935", "opt_1530"),
        "notes": "Sums skip blanks. Pct totals are paired-premium change.",
    }
    return data + [total]


def pct_class(v: str) -> str:
    try:
        x = float((v or "").strip())
    except Exception:
        return ""
    if x > 0:
        return "pos"
    if x < 0:
        return "neg"
    return ""


def main() -> int:
    sections: list[str] = []
    toc: list[str] = []
    for sess in SESSIONS:
        path = LOG_DIR / sess["file"]
        if not path.exists():
            raise SystemExit(f"Missing {path}")
        rows = load_rows(path)
        rows = ensure_total_row(rows, right=sess["right"])
        data_rows = [r for r in rows if not is_total(r)]
        total_rows = [r for r in rows if is_total(r)]
        n = len(data_rows)
        toc.append(
            f'<li><a href="#{sess["id"]}">{escape(sess["title"])}</a> '
            f'<span class="muted">({n} names)</span></li>'
        )
        thead = "".join(f"<th>{escape(label)}</th>" for _, label in sess["cols"])

        def render_row(r: dict, *, total: bool = False) -> str:
            cells: list[str] = []
            for key, _label in sess["cols"]:
                raw = r.get(key, "")
                # TOTAL stores "N names" in expiry_kind; show it under Expiry.
                if total and key == "expiry" and not (raw or "").strip():
                    raw = r.get("expiry_kind", "") or "TOTAL"
                is_pct = key.startswith("pct_")
                cls = pct_class(raw) if is_pct else ""
                if key in {
                    "spot_935",
                    "strike",
                    "opt_932",
                    "opt_935",
                    "opt_945",
                    "opt_1230",
                    "opt_1530",
                } or is_pct:
                    txt = fmt_num(raw, pct=is_pct)
                    align = "num"
                else:
                    txt = escape((raw or "—").strip() or "—")
                    align = ""
                cells.append(f'<td class="{align} {cls}">{txt}</td>')
            tr_cls = "total" if total else ""
            return f'<tr class="{tr_cls}">{"".join(cells)}</tr>'

        body_rows = [render_row(r) for r in data_rows]
        # If CSV somehow omitted TOTAL, synthesize nothing here — totals come from CSV.
        # Always render TOTAL rows last via <tfoot> so they stay the final table row.
        foot_rows = [render_row(r, total=True) for r in total_rows]
        tfoot = (
            f"<tfoot>{''.join(foot_rows)}</tfoot>" if foot_rows else ""
        )
        sections.append(
            f"""
    <section class="day" id="{sess['id']}">
      <header class="day-head">
        <div>
          <h2>{escape(sess['title'])}</h2>
          <p class="blurb">{escape(sess['blurb'])}</p>
        </div>
        <div class="meta">
          <span class="pill">{escape(sess['right'].upper())}</span>
          <span class="pill muted-pill">{n} names</span>
          <a class="pill link" href="atm_logs/{escape(sess['file'])}">CSV</a>
        </div>
      </header>
      <div class="table-wrap">
        <table>
          <thead><tr>{thead}</tr></thead>
          <tbody>
            {''.join(body_rows)}
          </tbody>
          {tfoot}
        </table>
      </div>
    </section>
"""
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ATM Checkpoint Log</title>
  <style>
    :root {{
      --bg: #0b0f14;
      --panel: #121821;
      --stroke: #243041;
      --text: #e8eef7;
      --muted: #93a1b5;
      --green: #3ddc97;
      --red: #ff6b6b;
      --accent: #6cb6ff;
      --total: #1a2433;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #1a2740 0%, var(--bg) 55%);
      color: var(--text);
      line-height: 1.4;
    }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 28px 18px 60px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; letter-spacing: -0.02em; }}
    h2 {{ font-size: 20px; margin: 0 0 6px; }}
    .sub {{ color: var(--muted); margin: 0 0 22px; max-width: 70ch; }}
    .toc {{
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: 12px;
      padding: 14px 18px;
      margin-bottom: 28px;
    }}
    .toc h3 {{ margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }}
    .toc ul {{ margin: 0; padding-left: 18px; }}
    .toc li {{ margin: 4px 0; }}
    .toc a {{ color: var(--accent); text-decoration: none; }}
    .toc a:hover {{ text-decoration: underline; }}
    .day {{
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: 14px;
      padding: 16px 16px 10px;
      margin-bottom: 22px;
    }}
    .day-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }}
    .blurb {{ margin: 0; color: var(--muted); font-size: 14px; max-width: 80ch; }}
    .meta {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--stroke);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      background: #0e141d;
    }}
    .muted-pill {{ color: var(--muted); }}
    .link {{ color: var(--accent); text-decoration: none; }}
    .table-wrap {{ overflow-x: auto; border-radius: 10px; border: 1px solid var(--stroke); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
      font-size: 13.5px;
      min-width: 860px;
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid var(--stroke);
      text-align: left;
      white-space: nowrap;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #0e141d;
      color: var(--muted);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    tr:nth-child(even) td {{ background: rgba(255,255,255,0.02); }}
    tr.total td {{
      background: #243447;
      font-weight: 700;
      border-top: 2px solid #6cb6ff;
    }}
    tfoot tr.total td {{
      position: sticky;
      bottom: 0;
      z-index: 1;
      box-shadow: 0 -6px 12px rgba(0,0,0,0.25);
    }}
    td.num {{ text-align: right; font-family: "IBM Plex Mono", ui-monospace, monospace; }}
    td.pos {{ color: var(--green); }}
    td.neg {{ color: var(--red); }}
    .foot {{ margin-top: 18px; color: var(--muted); font-size: 13px; }}
    code {{
      background: #0e141d;
      border: 1px solid var(--stroke);
      border-radius: 6px;
      padding: 1px 6px;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>ATM Checkpoint Log</h1>
    <p class="sub">
      Saved tables for each session date. Open this HTML file directly in a browser —
      tables are embedded (no server needed). Raw CSVs are under <code>atm_logs/</code>.
    </p>
    <nav class="toc">
      <h3>Dates</h3>
      <ul>
        {''.join(toc)}
      </ul>
    </nav>
    {''.join(sections)}
    <p class="foot">
      Rebuild with <code>python3 scripts/build_atm_checkpoint_log.py</code>.
    </p>
  </div>
</body>
</html>
"""
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
