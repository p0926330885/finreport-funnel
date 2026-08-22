"""
Mock FinMind responses for local demo / bash_tool test.

Synthesizes plausible quarterly + monthly time series that,
at the current quarter, match Scanner SPEC §18.1 aggregates.

For 6789, produces exact data matching Detail SPEC v2.2 §18.
"""
from __future__ import annotations

import math
from typing import Any

# ============================================================
# 6789 exact data from Detail SPEC v2.2 §18 (照抄)
# ============================================================
STOCK_6789_QUARTERLY = [
    # (date, cl, rev, gp, op, noi, np, eps)
    ("2024-09-30", 3531, 7621,  1677, 381,  -11, 370,  1.45),
    ("2024-12-31", 4043, 9497,  2165, 522,  -14, 508,  1.99),
    ("2025-03-31", 4707, 8556,  2011, 513,  -13, 500,  1.96),
    ("2025-06-30", 5391, 10908, 2640, 709,  -19, 690,  2.70),
    ("2025-09-30", 5967, 11042, 2761, 751,  -20, 731,  2.86),
    ("2025-12-31", 5946, 10976, 2799, 790,  -19, 771,  3.02),
    ("2026-03-31", 6934, 11603, 3017, 870,  -20, 850,  3.33),
    ("2026-06-30", 7645, 13381, 3546, 1097, -25, 1072, 4.20),
]

STOCK_6789_MONTHLY = [
    ("2024-07", 2530), ("2024-08", 2540), ("2024-09", 2551),
    ("2024-10", 3050), ("2024-11", 3160), ("2024-12", 3287),
    ("2025-01", 2900), ("2025-02", 2700), ("2025-03", 2956),
    ("2025-04", 3520), ("2025-05", 3650), ("2025-06", 3738),
    ("2025-07", 3650), ("2025-08", 3680), ("2025-09", 3712),
    ("2025-10", 3620), ("2025-11", 3680), ("2025-12", 3676),
    ("2026-01", 3670), ("2026-02", 3800), ("2026-03", 4133),
    ("2026-04", 4088), ("2026-05", 4519), ("2026-06", 4774),
    ("2026-07", 3885), ("2026-08", 4100),
]

# ============================================================
# 20 檔 Scanner §18.1 aggregate → 反推合成 8Q + 26mo
# 每檔僅需要 rev + gm + om + nm + noi + eps 的當季值 + revYoY 用來反推去年同期
# ============================================================
SCANNER_AGGREGATES = {
    # id: (name, industry, market, has_cl, rev, revYoY, gm, om, nm, noiRatio, gmQoQ, omQoQ, nmQoQ, gc, vis, clYoY)
    "6789": ("示範系統",  "資訊服務業",         "twse", True,  13381, 22.7, 26.5, 8.2,  8.0,  -2.3,  0.5,  0.4,  0.3, False, 1.7, 41.8),
    "2451": ("示範半導",  "半導體業",           "twse", True,  45230, 35.1, 42.3, 18.5, 15.2,  5.1,  1.2,  0.8,  0.6, True,  3.2, 55.6),
    "3037": ("示範零件",  "電子零組件業",       "twse", False, 28150, 18.4, 31.8, 12.6, 10.8, -8.5,  0.3,  0.1,  0.2, False, None, None),
    "4919": ("示範精化",  "化學工業",           "twse", True,  15680, 28.9, 35.7, 11.2,  9.5, -3.1,  0.7,  0.5,  0.4, True,  2.5, 33.4),
    "6488": ("示範光電",  "光電業",             "otc",  True,   8940, 42.6, 39.2, 14.8, 12.1, -6.8,  1.5,  1.2,  0.9, True,  4.1, 68.3),
    "2618": ("示範金融",  "金融保險",           "twse", False, 62100,  6.8, 22.5,  6.4,  4.8, 30.2, -0.2, -0.3,  0.1, False, None, None),
    "2882": ("示範傳金",  "金融保險",           "twse", False, 38500,  4.2, 20.1,  5.8,  4.1, 35.6,  0.1, -0.1, -0.2, False, None, None),
    "1102": ("示範水泥",  "水泥工業",           "twse", False, 22400,  9.5, 18.6,  5.2,  3.9, 15.4,  0.4,  0.2,  0.3, False, None, None),
    "4576": ("示範機械",  "電機機械",           "twse", True,   6720, 12.3, 24.8,  7.5,  5.4, -12.3, 0.3,  0.2,  0.4, False, 2.8, 18.5),
    "4108": ("示範生技",  "生技醫療業",         "otc",  False,  3480, 15.7, 55.3,  4.2,  2.8, -25.6, 1.1, -0.5, -0.3, True,  None, None),
    "5522": ("示範營建",  "建材營造",           "twse", True,  18900, -8.5, 12.4,  1.8,  0.5, 78.5, -0.8, -0.6, -0.4, False, 5.2, -12.3),
    "3260": ("示範軟體",  "資訊服務業",         "otc",  True,   2150, -15.2,14.8, -2.5, -3.8, -155.3,-1.2,-1.5, -1.8, False, 0.8, -28.7),
    "2308": ("示範電子",  "電腦及週邊設備業",   "twse", False, 125000,-3.8, 13.5,  2.8,  2.1, 45.6, -0.3, -0.2, -0.1, False, None, None),
    "2603": ("示範航運",  "航運業",             "twse", False, 42800, -22.6, 8.3, -1.2, -2.5, -180.2,-1.5,-1.1, -0.9, False, None, None),
    "4174": ("示範藥廠",  "生技醫療業",         "twse", False,  1820,  3.2, 42.6, -8.5, -12.3,-85.4, 0.5, -1.8, -2.1, False, None, None),
    "2412": ("示範電信",  "通信網路業",         "twse", True,  55600,  3.5, 28.7, 12.5,  9.2, -15.6, 0.2,  0.3,  0.1, False, 2.1, 5.8),
    "3711": ("示範封測",  "半導體業",           "twse", True,  32400, 15.8, 24.5,  9.8,  7.2,  8.3,  0.6,  0.4,  0.3, True,  2.3, 22.5),
    "5871": ("示範租賃",  "金融保險",           "twse", False, 12800, 25.3, 32.1, 15.2, 11.5, 12.8,  0.8,  0.5,  0.4, True,  None, None),
    "6415": ("示範矽力",  "半導體業",           "twse", False,  7850, 55.8, 48.6, 22.5, 18.2, -4.2,  2.1,  1.5,  1.2, True,  None, None),
    "8046": ("示範南電",  "電子零組件業",       "twse", True,  25600, -5.2, 22.8,  7.2,  5.5, -18.5,-0.5, -0.3, -0.2, False, 3.5, -8.5),
}

QUARTER_DATES = [
    "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
    "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30",
]
QUARTER_LABELS = ["2024/3Q", "2024/4Q", "2025/1Q", "2025/2Q",
                  "2025/3Q", "2025/4Q", "2026/1Q", "2026/2Q"]

MONTHLY_MONTHS = [
    "2024-07", "2024-08", "2024-09", "2024-10", "2024-11", "2024-12",
    "2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
    "2026-07", "2026-08",
]


def _synthesize_quarterly(agg: tuple) -> list[tuple]:
    """
    Reverse-engineer 8Q from (rev, revYoY, gm, om, nm, ..., clYoY).

    Strategy: latest quarter matches agg exactly. Prior 7Q backfilled with
    linear taper from (rev / (1 + revYoY/100)) 4 quarters ago.
    """
    (_name, _industry, _market, has_cl, rev, revYoY, gm, om, nm, noiRatio,
     gmQoQ, omQoQ, nmQoQ, _gc, _vis, clYoY) = agg

    # Reverse 4Q ago rev from YoY
    rev_yr_ago = rev / (1 + revYoY / 100) if revYoY is not None else rev * 0.85
    # Simple growth curve: linear interpolate quarterly revenue
    revs = []
    for i in range(8):
        # i=0 (oldest, 2024/3Q) ~ 4Q before yr_ago
        # i=4 = yr_ago (2025/3Q for latest 2026/2Q)
        # i=7 = latest
        if i <= 4:
            revs.append(rev_yr_ago * (0.85 + 0.04 * i))
        else:
            revs.append(rev_yr_ago + (rev - rev_yr_ago) * (i - 4) / 3)

    # Reverse QoQ for margins: latest margin - QoQ * 1, ..., minus QoQ * 7
    gms = [gm - gmQoQ * (7 - i) for i in range(8)]
    oms = [om - omQoQ * (7 - i) for i in range(8)]
    nms = [nm - nmQoQ * (7 - i) for i in range(8)]

    # Reverse CL from clYoY (if hasCL)
    if has_cl and clYoY is not None:
        cls_current_yr = [None] * 4  # will fill
        cl_yr_ago = None
        # Assume current-Q CL / last-yr-CL = 1 + clYoY/100
        # Solve: latest CL = agg's implied "vis * rev / 3" -> approximate reasonably
        vis = agg[14]
        cl_latest = vis * (rev / 3) if vis else rev * 0.5
        cl_yr_ago = cl_latest / (1 + clYoY / 100)
        cls = []
        for i in range(8):
            if i <= 4:
                cls.append(cl_yr_ago * (0.7 + 0.075 * i))
            else:
                cls.append(cl_yr_ago + (cl_latest - cl_yr_ago) * (i - 4) / 3)
    else:
        cls = [0] * 8

    # Compute derived: gp = rev * gm/100, op = rev * om/100, np = rev * nm/100
    # noi = np - op + tax; simplification: noi = np * noiRatio/100
    rows = []
    for i in range(8):
        r = revs[i]
        g = r * gms[i] / 100
        o = r * oms[i] / 100
        n = r * nms[i] / 100
        noi = n * noiRatio / 100
        eps = n * 4 / 254  # rough placeholder (dividing by shares outstanding proxy)
        rows.append((
            QUARTER_DATES[i],
            round(cls[i]),
            round(r),
            round(g),
            round(o),
            round(noi, 1),
            round(n),
            round(eps, 2),
        ))
    return rows


def _synthesize_monthly(quarterly_rows: list[tuple], vis: float | None) -> list[tuple]:
    """Break each quarter's revenue into 3 months, add mild noise."""
    monthly = []
    for i, row in enumerate(quarterly_rows):
        q_rev = row[2]
        base = q_rev / 3
        # slight variation month-to-month
        m1 = base * 0.95
        m2 = base * 1.00
        m3 = base * 1.05
        base_idx = i * 3
        for j, val in enumerate((m1, m2, m3)):
            monthly.append((MONTHLY_MONTHS[base_idx + j], round(val)))
    # add 2 more months for the "current in-progress quarter"
    latest_avg = quarterly_rows[-1][2] / 3
    monthly.append((MONTHLY_MONTHS[24], round(latest_avg * 0.94)))
    monthly.append((MONTHLY_MONTHS[25], round(latest_avg * 0.99)))
    return monthly[:26]


def _quarterly_to_finmind_fs(stock_id: str, quarterly: list[tuple]) -> list[dict]:
    """Convert (date, cl, rev, gp, op, noi, np, eps) into FinMind FS shape."""
    rows = []
    for date, _cl, rev, gp, op, noi, np, eps in quarterly:
        rows.extend([
            {"date": date, "stock_id": stock_id, "type": "Revenue",                            "value": rev * 1000},  # FinMind returns 千元
            {"date": date, "stock_id": stock_id, "type": "GrossProfit",                        "value": gp * 1000},
            {"date": date, "stock_id": stock_id, "type": "OperatingIncome",                    "value": op * 1000},
            {"date": date, "stock_id": stock_id, "type": "IncomeAfterTaxes",                   "value": np * 1000},
            {"date": date, "stock_id": stock_id, "type": "EPS",                                "value": eps},
            {"date": date, "stock_id": stock_id, "type": "TotalNonoperatingIncomeAndExpenses", "value": noi * 1000},
        ])
    return rows


def _quarterly_to_finmind_bs(stock_id: str, quarterly: list[tuple]) -> list[dict]:
    rows = []
    for date, cl, *_rest in quarterly:
        rows.append({"date": date, "stock_id": stock_id, "type": "ContractLiabilities-Current", "value": cl * 1000})
    return rows


def _monthly_to_finmind_revenue(stock_id: str, monthly: list[tuple]) -> list[dict]:
    rows = []
    for month_str, val in monthly:
        year, month = month_str.split("-")
        rows.append({
            "date": f"{year}-{month}-01",
            "stock_id": stock_id,
            "revenue": val * 1000,  # 千元
            "revenue_year": int(year),
            "revenue_month": int(month),
        })
    return rows


def generate(dataset: str, **params: Any) -> list[dict]:
    """Entry point: return mock FinMind response for given dataset."""
    stock_id = params.get("data_id") or params.get("stock_id")

    if dataset == "TaiwanStockInfo":
        # returns full universe in one call
        return [
            {
                "stock_id": sid,
                "stock_name": agg[0],
                "industry_category": agg[1],
                "type": "twse" if agg[2] == "twse" else "otc",
            }
            for sid, agg in SCANNER_AGGREGATES.items()
        ]

    if not stock_id or stock_id not in SCANNER_AGGREGATES:
        return []

    agg = SCANNER_AGGREGATES[stock_id]
    if stock_id == "6789":
        quarterly = [(d, cl, rev, gp, op, noi, np, eps)
                     for d, cl, rev, gp, op, noi, np, eps in STOCK_6789_QUARTERLY]
        monthly = STOCK_6789_MONTHLY
    else:
        quarterly = _synthesize_quarterly(agg)
        monthly = _synthesize_monthly(quarterly, agg[14])

    if dataset == "TaiwanStockFinancialStatements":
        return _quarterly_to_finmind_fs(stock_id, quarterly)
    if dataset == "TaiwanStockBalanceSheet":
        return _quarterly_to_finmind_bs(stock_id, quarterly)
    if dataset == "TaiwanStockMonthRevenue":
        return _monthly_to_finmind_revenue(stock_id, monthly)
    return []
