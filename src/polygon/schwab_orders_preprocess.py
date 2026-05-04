"""Helpers for Schwab multi-leg / OCO order CSV exports."""

from __future__ import annotations


def normalize_schwab_bracket_rows(rows: list[dict]) -> list[dict]:
    """
    Child rows often use Symbol \"--\" and Time \"--\". Walk upward (toward newer
    rows in the file) and copy the first filled row's anchor Symbol / Time so
    bracket legs are not dropped.
    """
    n = len(rows)
    out = [dict(r) for r in rows]
    for i in range(n):
        if (out[i].get("Status") or "").strip() != "Filled":
            continue
        tm = (out[i].get("Time") or "").strip()
        if tm in ("", "--"):
            for j in range(i - 1, -1, -1):
                if (out[j].get("Status") or "").strip() != "Filled":
                    continue
                tj = (out[j].get("Time") or "").strip()
                if tj and tj != "--":
                    out[i]["Time"] = tj
                    break
    for i in range(n):
        if (out[i].get("Status") or "").strip() != "Filled":
            continue
        sym = (out[i].get("Symbol") or "").strip()
        if sym in ("", "--"):
            for j in range(i - 1, -1, -1):
                sj = (out[j].get("Symbol") or "").strip()
                if sj and sj != "--":
                    out[i]["Symbol"] = sj
                    break
    return out


def dedupe_identical_option_events(events: list[dict]) -> list[dict]:
    """Drop duplicate parent+child lines (same instant, contract, side, qty, px)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for e in sorted(events, key=lambda x: (x["dt"], x["seq"])):
        key = (
            e["dt"],
            e["symbol"],
            e["exp_date"],
            e["strike"],
            e["cp"],
            e["side"],
            e["oc"],
            e["qty"],
            round(float(e["price"]), 6),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
