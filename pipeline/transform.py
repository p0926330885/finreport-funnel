"""
Transform layer: compute all business indicators from cached raw data.

Public API:
- build_detail(stock_id, info_df) -> dict matching Detail JSON schema (v2.2 §18)
- build_scanner_row(detail: dict) -> dict matching Scanner row schema (SPEC §18.2)

All formulas: v2.2 §7 + Scanner §7.5.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from . import config, ingest
from .finmind_client import FinMindClient

log = logging.getLogger(__name__)


# ============================================================
# Helpers
# ============================================================
def _q_label(date_str: str) -> str:
    """'2026-06-30' -> '2026/2Q'"""
    y, m, _ = date_str.split("-")
    q = (int(m) - 1) // 3 + 1
    return f"{y}/{q}Q"


def _fs_pivot(fs_df: pd.DataFrame) -> pd.DataFrame:
    """
    FinMind FS returns long format: (date, stock_id, type, value).
    Pivot to wide format with columns rev/gp/op/np/eps/noi.

    All monetary values divided by 1000 (千元 -> 百萬).
    """
    if fs_df.empty:
        return pd.DataFrame()
    wide = fs_df.pivot_table(
        index="date", columns="type", values="value", aggfunc="first"
    ).reset_index()
    for src, dst in config.FS_FIELD_MAP.items():
        if src in wide.columns:
            if dst == "eps":
                wide[dst] = wide[src]  # EPS 已是元
            else:
                wide[dst] = wide[src] / 1000  # 千元 -> 百萬
        else:
            wide[dst] = None
    keep = ["date"] + list(config.FS_FIELD_MAP.values())
    return wide[keep].sort_values("date").reset_index(drop=True)


def _bs_pivot(bs_df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot BS long -> wide, extract contract liabilities.
    """
    if bs_df.empty:
        return pd.DataFrame(columns=["date", "cl"])
    wide = bs_df.pivot_table(
        index="date", columns="type", values="value", aggfunc="first"
    ).reset_index()
    for src, dst in config.BS_FIELD_MAP.items():
        if src in wide.columns:
            wide[dst] = wide[src] / 1000
        else:
            wide[dst] = 0
    return wide[["date", "cl"]].sort_values("date").reset_index(drop=True)


# ============================================================
# Detail JSON builder
# ============================================================
def build_detail(client: FinMindClient, stock_id: str, universe_df: pd.DataFrame) -> Optional[dict]:
    """
    Build one stock's Detail JSON (matches v2.2 §18 schema).
    Returns None on missing data.
    """
    # Basic info
    info_rows = universe_df[universe_df["stock_id"] == stock_id]
    if info_rows.empty:
        log.warning("Stock %s not in universe", stock_id)
        return None
    info = info_rows.iloc[0]
    name = info.get("stock_name", "")
    raw_industry = info.get("industry_category", "")
    industry = config.INDUSTRY_MAP.get(raw_industry, config.INDUSTRY_DEFAULT)
    market_raw = str(info.get("type", "")).lower()
    market = "twse" if "twse" in market_raw or market_raw == "twse" else "otc"

    # Fetch raw
    fs_df = ingest.fetch_financial_statements(client, stock_id)
    bs_df = ingest.fetch_balance_sheet(client, stock_id)
    rev_df = ingest.fetch_month_revenue(client, stock_id)

    fs_wide = _fs_pivot(fs_df)
    bs_wide = _bs_pivot(bs_df)

    if fs_wide.empty:
        log.warning("No FS data for %s", stock_id)
        return None

    # Merge FS + BS by date, take latest 8 quarters
    merged = fs_wide.merge(bs_wide, on="date", how="left").fillna({"cl": 0})
    merged = merged.tail(config.QUARTERLY_HISTORY_QUARTERS).reset_index(drop=True)

    # hasCL determination (v2.2 §4.2)
    max_cl = merged["cl"].max()
    max_rev = merged["rev"].max()
    has_cl = bool(max_rev and (max_cl / max_rev) > config.HAS_CL_THRESHOLD)

    quarterly = []
    for _, row in merged.iterrows():
        quarterly.append({
            "q":   _q_label(str(row["date"])[:10]),
            "cl":  round(float(row["cl"])),
            "rev": round(float(row["rev"])),
            "gp":  round(float(row["gp"])) if row["gp"] is not None else None,
            "op":  round(float(row["op"])) if row["op"] is not None else None,
            "noi": round(float(row["noi"]), 1) if row["noi"] is not None else 0,
            "np":  round(float(row["np"])) if row["np"] is not None else None,
            "eps": round(float(row["eps"]), 2) if row["eps"] is not None else None,
        })

    # Monthly: 26 months, [year-month, revenue]
    monthly = []
    if not rev_df.empty:
        rev_sorted = rev_df.sort_values("date")
        for _, row in rev_sorted.iterrows():
            date_str = str(row["date"])[:7]  # YYYY-MM
            monthly.append([date_str, round(float(row["revenue"]) / 1000)])
        monthly = monthly[-config.MONTHLY_HISTORY_MONTHS:]

    return {
        "id": stock_id,
        "name": name,
        "industry": industry,
        "market": market,
        "hasCL": has_cl,
        "quarterly": quarterly,
        "monthly": monthly,
    }


# ============================================================
# Scanner row builder
# ============================================================
def _pct_change(current: float, base: float) -> Optional[float]:
    if base is None or base == 0:
        return None
    return (current / base - 1) * 100


def _visibility_months(cl: float, monthly: list) -> Optional[float]:
    """CL / (avg of last 3 months revenue). Returns None if cl==0."""
    if not cl or not monthly or len(monthly) < 3:
        return None
    last_3 = monthly[-3:]
    avg = sum(m[1] for m in last_3) / 3
    if avg == 0:
        return None
    return cl / avg


def _detect_golden_cross(monthly: list, lookback_days: int = 30) -> bool:
    """
    Golden cross (3MA vs 12MA) within lookback days.
    We interpret 'days' as 'months' here since monthly granularity.
    """
    if not monthly or len(monthly) < 13:
        return False
    values = [m[1] for m in monthly]
    n = len(values)
    lookback_months = 1  # scanner spec: within 1 month
    for i in range(n - lookback_months, n):
        if i < 12:
            continue
        ma3_now = sum(values[i-2:i+1]) / 3
        ma12_now = sum(values[i-11:i+1]) / 12
        ma3_prev = sum(values[i-3:i]) / 3
        ma12_prev = sum(values[i-12:i]) / 12
        if ma3_prev <= ma12_prev and ma3_now > ma12_now:
            return True
    return False


def build_scanner_row(detail: dict) -> Optional[dict]:
    """
    Compute Scanner row from Detail JSON.
    Returns row matching Scanner SPEC §18.2 schema.
    """
    q = detail["quarterly"]
    if len(q) < 2:
        return None
    cur = q[-1]
    prev = q[-2]
    yr_ago = q[-5] if len(q) >= 5 else None

    rev = cur["rev"]
    revYoY = _pct_change(cur["rev"], yr_ago["rev"]) if yr_ago else None

    gm = (cur["gp"] / cur["rev"] * 100) if cur["gp"] and cur["rev"] else None
    om = (cur["op"] / cur["rev"] * 100) if cur["op"] and cur["rev"] else None
    nm = (cur["np"] / cur["rev"] * 100) if cur["np"] and cur["rev"] else None
    noiRatio = (cur["noi"] / cur["np"] * 100) if cur["noi"] is not None and cur["np"] else None

    gmPrev = (prev["gp"] / prev["rev"] * 100) if prev["gp"] and prev["rev"] else None
    omPrev = (prev["op"] / prev["rev"] * 100) if prev["op"] and prev["rev"] else None
    nmPrev = (prev["np"] / prev["rev"] * 100) if prev["np"] and prev["rev"] else None

    gmQoQ = (gm - gmPrev) if gm is not None and gmPrev is not None else None
    omQoQ = (om - omPrev) if om is not None and omPrev is not None else None
    nmQoQ = (nm - nmPrev) if nm is not None and nmPrev is not None else None

    vis = _visibility_months(cur["cl"], detail["monthly"]) if detail["hasCL"] else None
    clYoY = _pct_change(cur["cl"], yr_ago["cl"]) if detail["hasCL"] and yr_ago else None
    gc = _detect_golden_cross(detail["monthly"])

    return {
        "id":       detail["id"],
        "name":     detail["name"],
        "industry": detail["industry"],
        "market":   detail["market"],
        "hasCL":    detail["hasCL"],
        "rev":      rev,
        "revYoY":   round(revYoY, 1) if revYoY is not None else None,
        "gm":       round(gm, 1)     if gm     is not None else None,
        "om":       round(om, 1)     if om     is not None else None,
        "nm":       round(nm, 1)     if nm     is not None else None,
        "noiRatio": round(noiRatio, 1) if noiRatio is not None else None,
        "gmQoQ":    round(gmQoQ, 1)  if gmQoQ  is not None else None,
        "omQoQ":    round(omQoQ, 1)  if omQoQ  is not None else None,
        "nmQoQ":    round(nmQoQ, 1)  if nmQoQ  is not None else None,
        "gc":       gc,
        "vis":      round(vis, 1)    if vis    is not None else None,
        "clYoY":    round(clYoY, 1)  if clYoY  is not None else None,
    }
