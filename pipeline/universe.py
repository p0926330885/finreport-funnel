"""
pipeline/universe.py

全市場股票池過濾模組 (Phase 2)
從 FinMind TaiwanStockInfo API 回傳的所有股票中,精準過濾出:
    - TWSE 上市普通股 (約 1,000 檔)
    - OTC  上櫃普通股 (約 600 檔)
    - KY / F 股 (第一上市普通股,約 100 檔)

排除:
    - ETF (0050, 006208, 00929 等 · 產業分類 "ETF" 或 stock_id 00 開頭)
    - TDR (91xx / 92xx 存託憑證)
    - 特別股 (2891A 中信金甲特等,stock_id 尾含字母)
    - 認購/售權證 (5-6 位數字或 03-08 開頭)
    - 可轉債 (通常尾綴 CB)
    - 興櫃股票 (FinMind type != 'twse' | 'tpex')
    - 公發未上市

Design:
    - is_common_stock() 是核心過濾規則
    - build_universe() 從 DataFrame 產出乾淨的 stock_id list
    - assign_batches() 將 universe 平均分成 N 批,用於 GitHub Actions 排程接力
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

import pandas as pd

from . import config

log = logging.getLogger(__name__)


# ============================================================
# 核心過濾規則
# ============================================================

# stock_id 必須是 4 位純數字 (排除權證、特別股、可轉債)
_STOCK_ID_PATTERN = re.compile(r"^\d{4}$")

# 產業分類黑名單 (排除)
_INDUSTRY_BLACKLIST = {
    "ETF",
    "ETN",
    "存託憑證",           # TDR
    "受益證券",           # REIT
    "指數股票型基金",
    "商品型 ETF",
}

# stock_id 前綴排除 (ETF/TDR)
_ID_PREFIX_BLACKLIST = (
    "00",   # ETF (0050, 006208, 00929 等)
    "91",   # TDR (泰金寶-DR 等)
    "92",   # TDR
)


def is_common_stock(stock_id: str, industry_category: str = "", stock_name: str = "", stock_type: str = "") -> bool:
    """
    判定是否為「合格普通股 (含 KY)」。

    Args:
        stock_id:          股票代號 (e.g. "2330")
        industry_category: 產業分類 (FinMind: "半導體業", "金融保險業", ...)
        stock_name:        股票名稱 (e.g. "台積電", "矽力-KY")
        stock_type:        市場別 (FinMind: "twse", "tpex" · 興櫃通常為 "emerging" 或空)

    Returns:
        True  → 收錄
        False → 排除
    """
    stock_id = str(stock_id).strip()
    industry = str(industry_category or "").strip()
    name = str(stock_name or "").strip()
    market = str(stock_type or "").strip().lower()

    # 規則 1: stock_id 必須是 4 位純數字
    if not _STOCK_ID_PATTERN.match(stock_id):
        return False

    # 規則 2: 排除 ID 前綴 (ETF/TDR)
    if stock_id.startswith(_ID_PREFIX_BLACKLIST):
        return False

    # 規則 3: 排除產業分類黑名單
    industry_upper = industry.upper()
    if any(bad in industry_upper for bad in ("ETF", "ETN")):
        return False
    if any(bad in industry for bad in ("存託憑證", "受益證券", "指數股票型")):
        return False

    # 規則 4: 產業分類必須有值 (排除異常股)
    if not industry or industry in ("-", "--", "N/A"):
        return False

    # 規則 5: 市場別必須是 twse (上市) 或 tpex (上櫃)
    #        FinMind 對興櫃 = 'emerging'; 未上市 = ''; 排除這兩類
    #        但 FinMind 有時 type 欄不存在, 若空白也可考慮放行 (視 industry 判斷)
    if market and market not in ("twse", "tpex"):
        return False

    # 規則 6: 排除股名含 "DR" 且產業非科技/傳產 (TDR 存託憑證的補充過濾)
    #        大部分 TDR 已在 91xx/92xx 前綴被過濾
    if "DR" in name and "存託" in industry:
        return False

    return True


# ============================================================
# Universe 建立
# ============================================================
def build_universe(universe_df: pd.DataFrame) -> list[str]:
    """
    從 FinMind TaiwanStockInfo 的 DataFrame 過濾出合格股票 ID list。

    Args:
        universe_df: 至少含 stock_id, industry_category, stock_name, type 欄位

    Returns:
        排序後 (str) 的股票 ID list,約 1,700 檔
    """
    if universe_df.empty:
        log.warning("Empty universe DataFrame passed to build_universe")
        return []

    stocks: set[str] = set()
    stats = {"total": 0, "rejected_id": 0, "rejected_prefix": 0, "rejected_industry": 0, "rejected_market": 0, "accepted": 0}

    # FinMind stock_id 可能重複 (同股不同揭露日期),用 set 去重
    for _, row in universe_df.iterrows():
        stats["total"] += 1
        sid = str(row.get("stock_id", "")).strip()
        if not _STOCK_ID_PATTERN.match(sid):
            stats["rejected_id"] += 1
            continue
        if is_common_stock(
            stock_id=sid,
            industry_category=row.get("industry_category", ""),
            stock_name=row.get("stock_name", ""),
            stock_type=row.get("type", ""),
        ):
            stocks.add(sid)
            stats["accepted"] += 1

    result = sorted(stocks)
    log.info(
        "Universe filter: total=%d rejected_id=%d accepted=%d",
        stats["total"], stats["rejected_id"], stats["accepted"],
    )
    return result


# ============================================================
# Batch 分配 (7 批平均分配)
# ============================================================
def assign_batches(stocks: list[str], batch_count: int = None) -> list[list[str]]:
    """
    將 universe 平均分成 N 批,回傳 list of lists。

    分配策略: 排序後依序分批,確保每批股數盡量平均。
    例: 1,700 檔 / 7 批 → 前 6 批各 243 檔,最後一批 242 檔。

    Args:
        stocks:      universe list (already sorted)
        batch_count: 分幾批,預設用 config.BATCH_COUNT (7)

    Returns:
        list of list, 長度 = batch_count
    """
    if batch_count is None:
        batch_count = config.BATCH_COUNT
    if not stocks or batch_count <= 0:
        return [[] for _ in range(batch_count)]

    per_batch = -(-len(stocks) // batch_count)  # ceil division
    batches = [stocks[i * per_batch : (i + 1) * per_batch] for i in range(batch_count)]
    return batches


def get_batch(stocks: list[str], batch_id: int, batch_count: int = None) -> list[str]:
    """取指定批號的股票 list (0-indexed)。"""
    if batch_count is None:
        batch_count = config.BATCH_COUNT
    if batch_id < 0 or batch_id >= batch_count:
        raise ValueError(f"batch_id must be 0..{batch_count-1}, got {batch_id}")
    return assign_batches(stocks, batch_count)[batch_id]


# ============================================================
# 健康度分佈統計 (Phase 2 Feature)
# ============================================================
def log_health_distribution(scanner_rows: list[dict]) -> None:
    """
    Pipeline 跑完後在 log 輸出全市場健康度分佈,方便 GitHub Actions summary review。

    Log 內容:
        - 健康度總分分佈 (0-8 分各佔多少)
        - ≥6 綠燈 / 3-5.9 黃燈 / <3 紅燈檔數
        - 各產業內綠燈檔數
        - hasCL 檔數與比例
    """
    if not scanner_rows:
        log.info("Health distribution: no scanner rows")
        return

    total = len(scanner_rows)
    green = sum(1 for r in scanner_rows if (r.get("score") or 0) >= 6)
    amber = sum(1 for r in scanner_rows if 3 <= (r.get("score") or 0) < 6)
    red   = sum(1 for r in scanner_rows if (r.get("score") or 0) < 3)
    has_cl = sum(1 for r in scanner_rows if r.get("hasCL"))

    log.info("=" * 60)
    log.info("HEALTH DISTRIBUTION (n=%d)", total)
    log.info("=" * 60)
    log.info("  Score >=6  (綠燈): %d 檔 (%.1f%%)", green, green / total * 100)
    log.info("  Score 3-5.9(黃燈): %d 檔 (%.1f%%)", amber, amber / total * 100)
    log.info("  Score <3   (紅燈): %d 檔 (%.1f%%)", red, red / total * 100)
    log.info("  Has CL           : %d 檔 (%.1f%%)", has_cl, has_cl / total * 100)

    # 產業分佈
    by_industry: dict[str, list[dict]] = {}
    for r in scanner_rows:
        ind = r.get("industry", "unknown")
        by_industry.setdefault(ind, []).append(r)

    log.info("-" * 60)
    log.info("BY INDUSTRY:")
    for ind, rows in sorted(by_industry.items(), key=lambda x: -len(x[1])):
        n = len(rows)
        g = sum(1 for r in rows if (r.get("score") or 0) >= 6)
        log.info("  %-15s n=%-4d 綠燈=%d (%.1f%%)", ind, n, g, g / n * 100 if n else 0)
    log.info("=" * 60)
