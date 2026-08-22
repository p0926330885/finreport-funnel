"""
Ingest layer: pull raw data from FinMind, cache locally as parquet.

Design:
- Each dataset per stock cached at cache/raw/{dataset}/{stock_id}.parquet
- Incremental mode: skip if cached file is fresher than lookback threshold
- Backfill mode: forces refresh regardless of cache age
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from . import config
from .finmind_client import FinMindClient

log = logging.getLogger(__name__)

# ============================================================
# Cache freshness policy
# ============================================================
CACHE_TTL_QUARTERLY_DAYS = 45   # 季報只在季末 45 日內公布,每日檢查
CACHE_TTL_MONTHLY_DAYS = 7      # 月營收每月 10 日左右公布
CACHE_TTL_INFO_DAYS = 30        # 公司基本資料很少變


def _cache_path(dataset: str, stock_id: str = "") -> Path:
    d = config.RAW_DIR / dataset
    d.mkdir(parents=True, exist_ok=True)
    return d / (f"{stock_id}.parquet" if stock_id else "_all.parquet")


def _is_cache_fresh(path: Path, ttl_days: int) -> bool:
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_days * 86400


# ============================================================
# Individual dataset fetchers
# ============================================================
def fetch_universe(client: FinMindClient, force: bool = False) -> pd.DataFrame:
    """Fetch TaiwanStockInfo (whole universe in one call)."""
    dataset = config.DATASETS["info"]
    path = _cache_path(dataset)
    if not force and _is_cache_fresh(path, CACHE_TTL_INFO_DAYS):
        log.info("Universe cache hit")
        return pd.read_parquet(path)

    log.info("Fetching universe from FinMind")
    rows = client.fetch(dataset)
    if not rows:
        if path.exists():
            log.warning("Universe fetch returned empty, using stale cache")
            return pd.read_parquet(path)
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)
    return df


def fetch_financial_statements(client: FinMindClient, stock_id: str, force: bool = False) -> pd.DataFrame:
    """Fetch quarterly income statement for one stock."""
    dataset = config.DATASETS["fs"]
    path = _cache_path(dataset, stock_id)
    if not force and _is_cache_fresh(path, CACHE_TTL_QUARTERLY_DAYS):
        return pd.read_parquet(path)

    rows = client.fetch(dataset, data_id=stock_id, start_date="2023-01-01")
    if not rows:
        if path.exists():
            return pd.read_parquet(path)
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)
    return df


def fetch_balance_sheet(client: FinMindClient, stock_id: str, force: bool = False) -> pd.DataFrame:
    """Fetch quarterly balance sheet (for contract liabilities) for one stock."""
    dataset = config.DATASETS["bs"]
    path = _cache_path(dataset, stock_id)
    if not force and _is_cache_fresh(path, CACHE_TTL_QUARTERLY_DAYS):
        return pd.read_parquet(path)

    rows = client.fetch(dataset, data_id=stock_id, start_date="2023-01-01")
    if not rows:
        if path.exists():
            return pd.read_parquet(path)
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)
    return df


def fetch_month_revenue(client: FinMindClient, stock_id: str, force: bool = False) -> pd.DataFrame:
    """Fetch monthly revenue for one stock."""
    dataset = config.DATASETS["revenue"]
    path = _cache_path(dataset, stock_id)
    if not force and _is_cache_fresh(path, CACHE_TTL_MONTHLY_DAYS):
        return pd.read_parquet(path)

    rows = client.fetch(dataset, data_id=stock_id, start_date="2023-01-01")
    if not rows:
        if path.exists():
            return pd.read_parquet(path)
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)
    return df


# ============================================================
# Batch orchestration
# ============================================================
def ingest_all(client: FinMindClient, stock_ids: list[str], force: bool = False) -> None:
    """Fetch all 4 datasets for a list of stock ids, sequentially."""
    log.info("Ingest starting: %d stocks, force=%s", len(stock_ids), force)
    for i, sid in enumerate(stock_ids, 1):
        fetch_financial_statements(client, sid, force=force)
        fetch_balance_sheet(client, sid, force=force)
        fetch_month_revenue(client, sid, force=force)
        if i % 10 == 0:
            log.info("Ingest progress: %d / %d", i, len(stock_ids))
    log.info("Ingest complete: %d stocks", len(stock_ids))
