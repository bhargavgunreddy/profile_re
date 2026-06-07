#!/usr/bin/env python3
"""Generate PDF report mirroring spx-strangle-last-20d canvas from analysis CSVs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
ANALYSIS = REPO / "data" / "spx_0dte_straddle" / "analysis"
OUT_HTML = ANALYSIS / "spx_strangle_last_20d_report.html"
OUT_PDF = ANALYSIS / "spx_strangle_last_20d_report.pdf"


def fmt(v: float) -> str:
    if pd.isna(v):
        return "—"
    sign = "+" if v >= 0 else "-"
    return f"${sign}{abs(round(v)):,}"


def pnl_class(v: float) -> str:
    if pd.isna(v):
        return ""
    return "pos" if v > 0 else "neg" if v < 0 else ""


def load_table(path: Path, label: str) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(path)
    data = df[df["Date"].astype(str) != "GRAND TOTAL"].copy()
    totals = df[df["Date"].astype(str) == "GRAND TOTAL"].iloc[0]
    wins_12 = int((data["Combined_PnL_12pm"] > 0).sum())
    wins_330 = int((data["Combined_PnL_330"] > 0).sum())
    meta = {
        "label": label,
        "total_12": totals["Combined_PnL_12pm"],
        "total_330": totals["Combined_PnL_330"],
        "avg_cost": data["Strangle_Cost"].mean(),
        "wins_330": wins_330,
        "n": len(data),
    }
    return data, meta


def table_html(data: pd.DataFrame, totals_row: pd.Series) -> str:
    rows = []
    for _, r in data.iterrows():
        rows.append(
            f"<tr>"
            f"<td>{str(r['Date'])[5:]}</td><td>{r['Day']}</td>"
            f"<td class='num'>{int(r['ATM_Strike']):,}</td>"
            f"<td class='center'>{int(r['Call_Strike'])}/{int(r['Put_Strike'])}</td>"
            f"<td class='num'>${r['Strangle_Cost']:.2f}</td>"
            f"<td class='num {pnl_class(r['Combined_PnL_12pm'])}'>{fmt(r['Combined_PnL_12pm'])}</td>"
            f"<td class='num {pnl_class(r['Combined_PnL_330'])}'>{fmt(r['Combined_PnL_330'])}</td>"
            f"</tr>"
        )
    rows.append(
        f"<tr class='total'>"
        f"<td colspan='5'><strong>GRAND TOTAL</strong></td>"
        f"<td class='num {pnl_class(totals_row['Combined_PnL_12pm'])}'><strong>{fmt(totals_row['Combined_PnL_12pm'])}</strong></td>"
        f"<td class='num {pnl_class(totals_row['Combined_PnL_330'])}'><strong>{fmt(totals_row['Combined_PnL_330'])}</strong></td>"
        f"</tr>"
    )
    return "\n".join(rows)


def build_html(pm10_meta: dict, pm20_meta: dict, pm10_rows: str, pm20_rows: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>SPX 0DTE Strangles — Last 20 Days</title>
<style>
  @page {{ margin: 0.55in; size: letter; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    color: #1a1a1a; font-size: 11px; line-height: 1.4; margin: 0; padding: 24px;
    background: #fff;
  }}
  h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 6px; }}
  .sub {{ color: #555; margin-bottom: 20px; max-width: 720px; }}
  .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }}
  .stat {{
    border: 1px solid #ddd; border-radius: 6px; padding: 10px 12px; background: #fafafa;
  }}
  .stat .val {{ font-size: 16px; font-weight: 600; margin-bottom: 2px; }}
  .stat .lbl {{ color: #666; font-size: 10px; }}
  .callout {{
    border-left: 3px solid #2563eb; background: #f0f6ff; padding: 10px 12px;
    margin: 16px 0 20px; color: #333;
  }}
  h2 {{ font-size: 14px; font-weight: 600; margin: 22px 0 6px; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
  .cap {{ color: #777; font-size: 10px; margin-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; }}
  th, td {{ border-bottom: 1px solid #eee; padding: 5px 6px; text-align: left; }}
  th {{ font-size: 10px; color: #666; font-weight: 600; background: #f5f5f5; }}
  tr:nth-child(even) td {{ background: #fcfcfc; }}
  tr.total td {{ border-top: 2px solid #ccc; background: #f5f5f5; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .center {{ text-align: center; }}
  .pos {{ color: #15803d; }}
  .neg {{ color: #b91c1c; }}
  .page-break {{ page-break-before: always; }}
  footer {{ margin-top: 24px; color: #888; font-size: 9px; }}
</style>
</head>
<body>
  <h1>SPX 0DTE Strangles — Last 20 Days</h1>
  <p class="sub">Entry 9:31 AM ET · SPX = SPY × 10 · Buy 1 call + 1 put · $100 multiplier · May 8 – Jun 4, 2026 (19 sessions) · Source: Polygon SPXW 1m bars</p>

  <div class="stats">
    <div class="stat"><div class="val">±$10</div><div class="lbl">Call ATM+10 / Put ATM−10</div></div>
    <div class="stat"><div class="val">{fmt(pm10_meta['total_12'])}</div><div class="lbl">±10 · 12:00 PM total</div></div>
    <div class="stat"><div class="val">{fmt(pm10_meta['total_330'])}</div><div class="lbl">±10 · 3:30 PM total</div></div>
    <div class="stat"><div class="val">{pm10_meta['wins_330']}/{pm10_meta['n']}</div><div class="lbl">±10 · 3:30 PM win days</div></div>
  </div>
  <div class="stats">
    <div class="stat"><div class="val">±$20</div><div class="lbl">Call ATM+20 / Put ATM−20</div></div>
    <div class="stat"><div class="val">{fmt(pm20_meta['total_12'])}</div><div class="lbl">±20 · 12:00 PM total</div></div>
    <div class="stat"><div class="val">{fmt(pm20_meta['total_330'])}</div><div class="lbl">±20 · 3:30 PM total</div></div>
    <div class="stat"><div class="val">{pm20_meta['wins_330']}/{pm20_meta['n']}</div><div class="lbl">±20 · 3:30 PM win days</div></div>
  </div>

  <div class="callout">Wider wings (±$20) cost ~26% less to enter (${pm20_meta['avg_cost']:.2f} vs ${pm10_meta['avg_cost']:.2f} avg) but capture less on big trend days. ±$10 outperformed at both checkpoints over this window.</div>

  <h2>±$10 Strangle — 12:00 PM &amp; 3:30 PM P/L</h2>
  <p class="cap">Avg entry cost ${pm10_meta['avg_cost']:.2f}/contract</p>
  <table>
    <thead><tr><th>Date</th><th>Day</th><th>ATM</th><th>C/P Strikes</th><th>Cost</th><th>12:00 PM</th><th>3:30 PM</th></tr></thead>
    <tbody>{pm10_rows}</tbody>
  </table>

  <h2>±$20 Strangle — 12:00 PM &amp; 3:30 PM P/L</h2>
  <p class="cap">Avg entry cost ${pm20_meta['avg_cost']:.2f}/contract</p>
  <table>
    <thead><tr><th>Date</th><th>Day</th><th>ATM</th><th>C/P Strikes</th><th>Cost</th><th>12:00 PM</th><th>3:30 PM</th></tr></thead>
    <tbody>{pm20_rows}</tbody>
  </table>

  <footer>Generated from strangle_pm10_last_20d.csv and strangle_pm20_last_20d.csv · profile_re SPX straddle analysis branch</footer>
</body>
</html>"""


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        f"--print-to-pdf={pdf_path}",
        "--no-pdf-header-footer",
        f"file://{html_path.resolve()}",
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    pm10_path = ANALYSIS / "strangle_pm10_last_20d.csv"
    pm20_path = ANALYSIS / "strangle_pm20_last_20d.csv"
    if not pm10_path.exists() or not pm20_path.exists():
        print("Missing strangle CSV files", file=sys.stderr)
        return 1

    pm10_df, pm10_meta = load_table(pm10_path, "±$10")
    pm20_df, pm20_meta = load_table(pm20_path, "±$20")
    pm10_totals = pd.read_csv(pm10_path)
    pm10_totals = pm10_totals[pm10_totals["Date"].astype(str) == "GRAND TOTAL"].iloc[0]
    pm20_totals = pd.read_csv(pm20_path)
    pm20_totals = pm20_totals[pm20_totals["Date"].astype(str) == "GRAND TOTAL"].iloc[0]

    html = build_html(
        pm10_meta,
        pm20_meta,
        table_html(pm10_df, pm10_totals),
        table_html(pm20_df, pm20_totals),
    )
    OUT_HTML.write_text(html, encoding="utf-8")
    html_to_pdf(OUT_HTML, OUT_PDF)
    print(f"Wrote {OUT_HTML.relative_to(REPO)}")
    print(f"Wrote {OUT_PDF.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
