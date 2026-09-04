"""
v3.5.4-u1 · Active Universe module

對應 P2 §四~§十一 + P3 blocker fixes(3/6/7/8/9)+ P3-r2 fixes(A/B/C)。

主入口: load_active_universe(finmind_common_ids=B) -> ActiveUniverseState

核心不變式(FI · P2 + P3 + P3-r2):
  · FI-U1: TWSE + TPEx 必須【同時成功】才可組成 candidate;半套市場禁止發布
  · FI-U2: candidate 通過 drift guard(<5% removed & <5% added)才可覆寫 LKG
  · FI-U3: 任何情況下 · LKG 損毀 or 官方源失敗 · 若 LKG 有效 → 完整使用 LKG
                                              · 若 LKG 無效 → 回傳 None (build.py 必須 abort)
  · FI-U4: LKG snapshot 寫入必須 atomic(tmp file + os.replace)
  · FI-U5: LKG checksum = sha256("\n".join(sorted(official_active_ids))) · 不包含 checksum 欄位本身
  · FI-U6: 四位純數字過濾 · 不接受空白/null/非數字/長度非 4
  · FI-U7: 無 LKG + 兩來源 valid → abort(不 unattended bootstrap)
           initial LKG 必須由 deployment 人工附上(見 data/active_universe.json)
  · FI-U8: source label 明確分六類:
             · official_attachment_bootstrap             (initial LKG 由 attachment 產出)
             · official_live                              (live fetch + drift pass + LKG write ok · 覆寫 LKG)
             · last_known_good_after_source_failure       (至少一端點 fetch 失敗)
             · last_known_good_after_drift_reject         (candidate 通過 schema 但 drift >= 5%)
             · last_known_good_after_freshness_reject     (P3-r2 · candidate 日期倒退)
             · last_known_good_after_write_failure        (P3-r2 · LKG atomic write 失敗)
  · FI-U9: 【P3 Blocker 6】loader 嚴格 schema · 任一 record 違反即 reject
  · FI-U10: 【P3 Blocker 9】所有輸出必須 deterministic sorted
  · FI-U11: 【P3-r2 Blocker A】LKG payload 讀取必須通過 validate_lkg_payload 17 項檢查
  · FI-U12: 【P3-r2 Blocker B】source 每筆 date 都要 valid + 所有 record date 必須一致
           freshness guard:candidate 任一 as_of < LKG as_of → reject
  · FI-U13: 【P3-r2 Blocker C】candidate 通過 drift 後 · 必須先 write LKG 成功 · 才使用 candidate
           寫失敗時 · 用 LKG · 不使用未持久化的 candidate

不動:pipeline/universe.py 的 build_universe / is_common_stock 語意
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

import requests

from . import config

log = logging.getLogger(__name__)

TAIPEI_TZ = timezone(timedelta(hours=8))

# 四位純數字 ID pattern(對齊 pipeline/universe.py 既有規則)
_FOUR_DIGIT = re.compile(r"^\d{4}$")


# ============================================================
# Data classes
# ============================================================

@dataclass
class SourceLoadResult:
    """單一來源(TWSE 或 TPEx)載入結果"""
    ok: bool
    source_name: str                   # 'twse' | 'tpex'
    ids: set                           # 四位純數字過濾後的 IDs
    raw_count: int = 0                 # 官方 payload 原始筆數
    as_of: Optional[str] = None        # ISO 日期(YYYY-MM-DD)· 由民國 YYYMMDD 轉換
    error: Optional[str] = None        # 失敗原因
    dup_warning: Optional[str] = None  # duplicate IDs warning · 進 ActiveUniverseState.warnings


@dataclass
class ActiveUniverseState:
    """
    load_active_universe() 回傳的完整狀態物件。

    active_ids 可能為 None(表示 build 必須 abort · 詳見 abort_reason)。
    """
    active_ids: Optional[set]                             # None = must abort
    source: str                                           # 'official_live' | 'last_known_good' | 'last_known_good_after_source_failure' | 'last_known_good_after_drift_reject'
    as_of: Optional[str] = None                           # official / lkg 的 as-of
    twse_size: Optional[int] = None                       # C 大小
    tpex_size: Optional[int] = None                       # D 大小
    official_active_size: Optional[int] = None            # |C ∪ D|
    schema_status: str = "unknown"                        # 'ok' | 'partial_source_failure' | 'drift_rejected' | 'lkg_only' | 'abort'
    warnings: list = field(default_factory=list)          # 給 log 與 meta 用
    abort_reason: Optional[str] = None                    # active_ids is None 時填


# ============================================================
# ROC date parsing
# ============================================================

def _parse_roc_date(roc: Any) -> Optional[str]:
    """
    民國 YYYMMDD → ISO 'YYYY-MM-DD'
      · 民國 115 → 西元 2026
      · roc='1150831' → '2026-08-31'
      · 對 3-4 位數年份都相容
      · 失敗 → None(不 raise)

    邊界:
      · None / '' / 非數字 → None
      · 長度不對(需 7 位:3 年 + 2 月 + 2 日)→ None
      · 月 out-of-range(1-12) / 日 out-of-range(1-31) → None
    """
    if roc is None:
        return None
    s = str(roc).strip()
    if not s.isdigit() or len(s) != 7:
        return None
    try:
        roc_year = int(s[:3])
        month = int(s[3:5])
        day = int(s[5:7])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        year = roc_year + 1911
        # datetime 會驗 day 對 month 是否合法(如 2/30 → error)
        d = datetime(year, month, day)
        return d.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ============================================================
# ID filter
# ============================================================

def _filter_four_digit_ids(raw_ids: Iterable) -> set:
    """
    對齊 P2 §四 + §十:
      · 去 null / blank / 非四位純數字
      · deterministic(回 set · 呼叫端負責 sort)
    """
    out = set()
    for x in raw_ids or []:
        if x is None:
            continue
        s = str(x).strip()
        if _FOUR_DIGIT.match(s):
            out.add(s)
    return out


# ============================================================
# HTTP fetch(bounded retry)
# ============================================================

def _fetch_json_with_retry(url: str,
                            connect_timeout: float,
                            read_timeout: float,
                            max_retries: int,
                            backoff_seconds: float,
                            _requests_module=None) -> Tuple[Any, Optional[str]]:
    """
    Fetch URL 並解析 JSON · bounded retry。

    Returns:
        (data, error) — error 為 None 表示成功;data 型別由 endpoint 決定

    對齊 P2 §四:
      · connect + read timeout
      · HTTP status 非 200 → 記錯
      · body 非 JSON → 記錯
      · retry 至多 max_retries 次
      · _requests_module 允許 test 注入 mock(否則用 module-level requests)
    """
    r_mod = _requests_module if _requests_module is not None else requests
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = r_mod.get(url, timeout=(connect_timeout, read_timeout))
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                log.warning("%s attempt %d/%d: %s", url, attempt + 1, max_retries, last_error)
            else:
                try:
                    return resp.json(), None
                except (ValueError, json.JSONDecodeError) as je:
                    last_error = f"JSON decode failed: {je}"
                    log.warning("%s attempt %d/%d: %s", url, attempt + 1, max_retries, last_error)
        except Exception as e:  # noqa: BLE001 — 涵蓋 requests.Timeout / ConnectionError / 其他
            last_error = f"{type(e).__name__}: {e}"
            log.warning("%s attempt %d/%d: %s", url, attempt + 1, max_retries, last_error)
        if attempt < max_retries - 1:
            time.sleep(backoff_seconds * (2 ** attempt))  # 指數退避
    return None, last_error or "unknown error"


# ============================================================
# Source loaders
# ============================================================

def load_official_snapshot(source_name: str,
                            url: str,
                            id_key: str,
                            date_key: str,
                            min_count: int,
                            _requests_module=None) -> SourceLoadResult:
    """
    通用 loader:抓 openapi endpoint · 解 JSON · 提取 IDs 與 as-of。

    Args:
        source_name: 'twse' | 'tpex' — 只用於錯誤訊息
        url: endpoint
        id_key: '公司代號' | 'SecuritiesCompanyCode'
        date_key: '出表日期' | 'Date'
        min_count: 過濾後最低筆數(低於視為 schema 失敗)
        _requests_module: test 注入用

    對齊 P2 §四:
      · body 必須是非空 list
      · id_key 完整性 · null/blank/非四位過濾
      · duplicate 自動去除(set)
      · 過濾後 count 低於 min_count 判失敗(防禦性 · 避免對方回幾筆假資料就發布)
      · timeouts / bounded retry / HTTP 非 200 判失敗
    """
    data, err = _fetch_json_with_retry(
        url,
        connect_timeout=config.ACTIVE_UNIVERSE_CONNECT_TIMEOUT,
        read_timeout=config.ACTIVE_UNIVERSE_READ_TIMEOUT,
        max_retries=config.ACTIVE_UNIVERSE_MAX_RETRIES,
        backoff_seconds=config.ACTIVE_UNIVERSE_RETRY_BACKOFF,
        _requests_module=_requests_module,
    )
    if err:
        return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                error=f"fetch failed: {err}")

    if not isinstance(data, list):
        return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                error=f"body not a list (got {type(data).__name__})")
    if len(data) == 0:
        return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                error="body is empty list")

    # P3 Blocker 6:嚴格 per-record 驗證 · 任一違反即 reject
    #   · 每筆必須是 dict
    #   · 每筆必須含 id_key
    #   · id value 不得 null 或 blank
    raw_count = len(data)
    raw_ids = []
    for i, r in enumerate(data):
        if not isinstance(r, dict):
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] not a dict (got {type(r).__name__})")
        if id_key not in r:
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] missing key '{id_key}'")
        v = r[id_key]
        if v is None:
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] '{id_key}' is null")
        if isinstance(v, str) and not v.strip():
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] '{id_key}' is blank")
        raw_ids.append(v)

    # 檢查 duplicates(記 warning 但不 reject · 對齊 P3 Blocker 6)
    raw_ids_str = [str(x).strip() for x in raw_ids]
    seen: set = set()
    dup_ids: list = []
    for s in raw_ids_str:
        if s in seen:
            dup_ids.append(s)
        else:
            seen.add(s)
    dup_warning: Optional[str] = None
    if dup_ids:
        dup_warning = f"{source_name} source has {len(dup_ids)} duplicate IDs: {sorted(set(dup_ids))[:10]}"
        log.warning(dup_warning)

    # 四位純數字過濾(合法可行 · 不算 schema 缺失)
    filtered = _filter_four_digit_ids(raw_ids)
    if len(filtered) < min_count:
        return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                raw_count=raw_count,
                                error=f"filtered count {len(filtered)} < min {min_count}")

    # P3-r2 Blocker B:date 嚴格驗證
    #   · 每一筆必須有 date_key
    #   · date 不得 null / blank
    #   · 每一筆 date 必須可解析
    #   · 所有 record 的日期必須完全一致(不得不同日期混雜)
    dates_seen: set = set()
    for i, r in enumerate(data):
        if date_key not in r:
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] missing date key '{date_key}'")
        raw_date = r[date_key]
        if raw_date is None:
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] '{date_key}' is null")
        if isinstance(raw_date, str) and not raw_date.strip():
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] '{date_key}' is blank")
        iso = _parse_roc_date(raw_date)
        if iso is None:
            return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                    raw_count=raw_count,
                                    error=f"record[{i}] '{date_key}'='{raw_date}' cannot be parsed as ROC date")
        dates_seen.add(iso)
    if len(dates_seen) != 1:
        return SourceLoadResult(ok=False, source_name=source_name, ids=set(),
                                raw_count=raw_count,
                                error=f"records contain {len(dates_seen)} distinct dates: {sorted(dates_seen)}")
    as_of = dates_seen.pop()

    return SourceLoadResult(
        ok=True, source_name=source_name, ids=filtered,
        raw_count=raw_count, as_of=as_of, error=None,
        dup_warning=dup_warning,
    )


def load_twse_official(_requests_module=None) -> SourceLoadResult:
    return load_official_snapshot(
        source_name="twse",
        url=config.ACTIVE_UNIVERSE_TWSE_URL,
        id_key=config.ACTIVE_UNIVERSE_TWSE_ID_KEY,
        date_key=config.ACTIVE_UNIVERSE_TWSE_DATE_KEY,
        min_count=config.ACTIVE_UNIVERSE_MIN_TWSE_COUNT,
        _requests_module=_requests_module,
    )


def load_tpex_official(_requests_module=None) -> SourceLoadResult:
    return load_official_snapshot(
        source_name="tpex",
        url=config.ACTIVE_UNIVERSE_TPEX_URL,
        id_key=config.ACTIVE_UNIVERSE_TPEX_ID_KEY,
        date_key=config.ACTIVE_UNIVERSE_TPEX_DATE_KEY,
        min_count=config.ACTIVE_UNIVERSE_MIN_TPEX_COUNT,
        _requests_module=_requests_module,
    )


# ============================================================
# Checksum
# ============================================================

def compute_checksum(ids: Iterable) -> str:
    """
    對齊 P2 §五 · FI-U5:
      · 只對 sorted ids 計算
      · serialization = '\\n'.join(sorted(ids)) · UTF-8
      · 不含 checksum 欄位本身 · 只含 ids
    """
    canon = "\n".join(sorted(str(x) for x in ids))
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ============================================================
# LKG snapshot I/O
# ============================================================

def _now_taipei_iso() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


def build_snapshot_payload(twse: SourceLoadResult,
                            tpex: SourceLoadResult,
                            source: str) -> dict:
    """
    對齊 P2 §五 · 產出 LKG snapshot dict(準備寫檔或供 test 檢驗)。

    · official_as_of = 兩來源較早者(對齊「不得把兩個來源錯寫成同一天」)
    · checksum 只計算 official_active_ids
    """
    assert twse.ok and tpex.ok, "build_snapshot_payload requires both sources ok"
    twse_ids = sorted(twse.ids)
    tpex_ids = sorted(tpex.ids)
    official_ids = sorted(set(twse_ids) | set(tpex_ids))
    # 較早日期
    twse_as = twse.as_of or "1970-01-01"
    tpex_as = tpex.as_of or "1970-01-01"
    official_as_of = min(twse_as, tpex_as) if (twse.as_of and tpex.as_of) else (twse.as_of or tpex.as_of)
    payload = {
        "schema_version": config.ACTIVE_UNIVERSE_SCHEMA_VERSION,
        "generated_at": _now_taipei_iso(),
        "source": source,
        "twse_url": config.ACTIVE_UNIVERSE_TWSE_URL,
        "tpex_url": config.ACTIVE_UNIVERSE_TPEX_URL,
        "twse_as_of": twse.as_of,
        "tpex_as_of": tpex.as_of,
        "official_as_of": official_as_of,
        "twse_count": len(twse_ids),
        "tpex_count": len(tpex_ids),
        "official_active_size": len(official_ids),
        "twse_ids": twse_ids,
        "tpex_ids": tpex_ids,
        "official_active_ids": official_ids,
        "ids_checksum": compute_checksum(official_ids),
    }
    return payload


# module-level indirection · 讓 test monkey-patch atomic write 不會誤影響其他 module 的 os.replace
_atomic_replace = os.replace


def write_lkg(payload: dict, path: Optional[Path] = None) -> Path:
    """
    Atomic write:tmp file → _atomic_replace(=os.replace)。對齊 P2 §五 · FI-U4。

    test 可 monkey-patch pipeline.active_universe._atomic_replace 模擬失敗 ·
    此 patch 不影響其他 module 的 os.replace 呼叫。
    """
    p = path if path is not None else config.ACTIVE_UNIVERSE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
                   encoding="utf-8")
    _atomic_replace(tmp, p)
    return p


def _is_valid_iso_date(s) -> bool:
    """驗證是 'YYYY-MM-DD' 格式且合法日期"""
    if not isinstance(s, str) or len(s) != 10:
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def validate_lkg_payload(payload) -> Tuple[bool, Optional[str]]:
    """
    P3-r2 Blocker A · 完整 LKG payload validator。

    LKG 一定要是這個模組本身或 attachment bootstrap 產出的 · 所以我們可以要求
    完整 schema · 而不是「盡量接受」。

    Returns:
        (True, None) or (False, reason)
    """
    if not isinstance(payload, dict):
        return False, f"payload not a dict (got {type(payload).__name__})"

    # 1. schema_version
    sv = payload.get("schema_version")
    if sv != config.ACTIVE_UNIVERSE_SCHEMA_VERSION:
        return False, f"schema_version {sv} != expected {config.ACTIVE_UNIVERSE_SCHEMA_VERSION}"

    # 2. source · 只能是新 LKG 產出的兩種
    src = payload.get("source")
    if src not in ("official_attachment_bootstrap", "official_live"):
        return False, f"source '{src}' not in allowed set (bootstrap|live)"

    # 3. 必要欄位齊全
    required = ["generated_at", "source", "twse_url", "tpex_url",
                "twse_as_of", "tpex_as_of", "official_as_of",
                "twse_count", "tpex_count", "official_active_size",
                "twse_ids", "tpex_ids", "official_active_ids", "ids_checksum"]
    for k in required:
        if k not in payload:
            return False, f"missing required field '{k}'"

    # 4-8. IDs arrays
    for arr_key in ("twse_ids", "tpex_ids", "official_active_ids"):
        arr = payload[arr_key]
        if not isinstance(arr, list):
            return False, f"{arr_key} not a list (got {type(arr).__name__})"
        # 每個 ID 都是 4-digit numeric string
        for i, x in enumerate(arr):
            if not isinstance(x, str):
                return False, f"{arr_key}[{i}] not a string"
            if not _FOUR_DIGIT.match(x):
                return False, f"{arr_key}[{i}]='{x}' not a 4-digit numeric string"
        # 無 duplicate
        if len(arr) != len(set(arr)):
            return False, f"{arr_key} contains duplicates"
        # deterministic sorted
        if arr != sorted(arr):
            return False, f"{arr_key} not deterministic-sorted"

    # 9-11. counts vs arrays
    if payload["twse_count"] != len(payload["twse_ids"]):
        return False, f"twse_count {payload['twse_count']} != len(twse_ids) {len(payload['twse_ids'])}"
    if payload["tpex_count"] != len(payload["tpex_ids"]):
        return False, f"tpex_count {payload['tpex_count']} != len(tpex_ids) {len(payload['tpex_ids'])}"
    if payload["official_active_size"] != len(payload["official_active_ids"]):
        return False, f"official_active_size mismatch"

    # 12. union
    twse_set = set(payload["twse_ids"])
    tpex_set = set(payload["tpex_ids"])
    official_set = set(payload["official_active_ids"])
    if twse_set | tpex_set != official_set:
        return False, "twse_ids ∪ tpex_ids != official_active_ids"

    # 13. checksum
    recomputed = compute_checksum(payload["official_active_ids"])
    if recomputed != payload["ids_checksum"]:
        return False, f"checksum mismatch: stored={payload['ids_checksum']} recomputed={recomputed}"

    # 14. 3 個 as_of 是合法 ISO date
    for date_key in ("twse_as_of", "tpex_as_of", "official_as_of"):
        v = payload[date_key]
        if not _is_valid_iso_date(v):
            return False, f"{date_key}='{v}' not a valid ISO date (YYYY-MM-DD)"

    # 15. official_as_of == min(twse_as_of, tpex_as_of)
    expected_official = min(payload["twse_as_of"], payload["tpex_as_of"])
    if payload["official_as_of"] != expected_official:
        return False, (f"official_as_of='{payload['official_as_of']}' != "
                        f"min(twse={payload['twse_as_of']}, tpex={payload['tpex_as_of']})={expected_official}")

    # 16. counts 過 min(production LKG · test 用 monkeypatch 降門檻)
    if payload["twse_count"] < config.ACTIVE_UNIVERSE_MIN_TWSE_COUNT:
        return False, f"twse_count {payload['twse_count']} < min {config.ACTIVE_UNIVERSE_MIN_TWSE_COUNT}"
    if payload["tpex_count"] < config.ACTIVE_UNIVERSE_MIN_TPEX_COUNT:
        return False, f"tpex_count {payload['tpex_count']} < min {config.ACTIVE_UNIVERSE_MIN_TPEX_COUNT}"

    # 17. URL 與 config 一致(避免舊 LKG 綁到廢棄 endpoint)
    if payload["twse_url"] != config.ACTIVE_UNIVERSE_TWSE_URL:
        return False, f"twse_url mismatch with config"
    if payload["tpex_url"] != config.ACTIVE_UNIVERSE_TPEX_URL:
        return False, f"tpex_url mismatch with config"

    return True, None


def load_lkg(path: Optional[Path] = None) -> Optional[dict]:
    """
    讀 LKG snapshot · 全面驗證(P3-r2 Blocker A)。

    Returns:
        · 有效 dict → 原 payload
        · 檔案不存在 / 損毀 / validation 任一項失敗 → None
    """
    p = path if path is not None else config.ACTIVE_UNIVERSE_PATH
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("LKG file exists but failed to parse (%s): %s", p, e)
        return None
    ok, reason = validate_lkg_payload(payload)
    if not ok:
        log.warning("LKG validation failed: %s (path=%s)", reason, p)
        return None
    return payload


# ============================================================
# Drift guard
# ============================================================

@dataclass
class DriftResult:
    accepted: bool
    removed_count: int
    added_count: int
    lkg_size: int
    removed_ratio: float
    added_ratio: float
    reason: Optional[str] = None


def passes_drift_guard(candidate_ids: set,
                        lkg_ids: set,
                        max_removed_ratio: Optional[float] = None,
                        max_added_ratio: Optional[float] = None) -> DriftResult:
    """
    對齊 P2 §三 + §九 · FI-U2:
      · removed = LKG - candidate
      · added   = candidate - LKG
      · removed_ratio = |removed| / |LKG|
      · added_ratio   = |added|   / |LKG|
      · ratio < 5% pass · ratio >= 5% reject(邊界 >= 0.05)
      · candidate empty → reject(不當作「全部 removed」正常 diff)
      · LKG empty → 判 invalid(caller 應該視為「無 LKG」)

    Returns:
        DriftResult · accepted 為 True 才可覆寫 LKG。
    """
    if max_removed_ratio is None:
        max_removed_ratio = config.ACTIVE_UNIVERSE_DRIFT_MAX_REMOVED_RATIO
    if max_added_ratio is None:
        max_added_ratio = config.ACTIVE_UNIVERSE_DRIFT_MAX_ADDED_RATIO

    if not candidate_ids:
        return DriftResult(accepted=False, removed_count=0, added_count=0,
                           lkg_size=len(lkg_ids), removed_ratio=0.0, added_ratio=0.0,
                           reason="candidate empty")
    if not lkg_ids:
        return DriftResult(accepted=False, removed_count=0, added_count=0,
                           lkg_size=0, removed_ratio=0.0, added_ratio=0.0,
                           reason="lkg empty (bootstrap situation)")

    removed = lkg_ids - candidate_ids
    added = candidate_ids - lkg_ids
    r_ratio = len(removed) / len(lkg_ids)
    a_ratio = len(added) / len(lkg_ids)

    reasons = []
    if r_ratio >= max_removed_ratio:
        reasons.append(f"removed {len(removed)}/{len(lkg_ids)} = {r_ratio*100:.2f}% >= {max_removed_ratio*100:.2f}%")
    if a_ratio >= max_added_ratio:
        reasons.append(f"added {len(added)}/{len(lkg_ids)} = {a_ratio*100:.2f}% >= {max_added_ratio*100:.2f}%")

    if reasons:
        return DriftResult(accepted=False, removed_count=len(removed), added_count=len(added),
                           lkg_size=len(lkg_ids), removed_ratio=r_ratio, added_ratio=a_ratio,
                           reason="; ".join(reasons))

    return DriftResult(accepted=True, removed_count=len(removed), added_count=len(added),
                       lkg_size=len(lkg_ids), removed_ratio=r_ratio, added_ratio=a_ratio)


# ============================================================
# Main entry:load_active_universe
# ============================================================

def load_active_universe(finmind_common_ids: Optional[set] = None,
                          _requests_module=None,
                          _lkg_path: Optional[Path] = None) -> ActiveUniverseState:
    """
    build.py 主流程呼叫的統一入口。對齊 P2 §六 + P3 Blocker 7 + P3-r2 Blocker A/B/C。

    Args:
        finmind_common_ids:
            B 集合(FinMind common stocks)· 由 universe.build_universe() 產出
            None → 回傳 Official(供 test 用)
            非 None → 回傳 B ∩ Official 的 active_ids

    邏輯(FI-U1 ~ FI-U13):
      1. try load TWSE + TPEx(loader:每筆嚴格 schema + 每筆 date valid + 同來源日期一致)
      2. 若兩者 ok:
           candidate_ids = twse.ids | tpex.ids
           lkg_payload = load_lkg()   # 17 項 validate_lkg_payload 檢查
           if lkg_payload is None:
               abort('no valid LKG; unattended bootstrap disallowed')      # FI-U7
           elif candidate 日期 < LKG 日期:                                  # FI-U12 · freshness
               use lkg_ids; source='last_known_good_after_freshness_reject'
           elif drift.accepted:
               先 write_lkg 成功 才用 candidate                              # FI-U13 · write-then-publish
                 · 成功 → use candidate; source='official_live'
                 · 失敗 → use lkg_ids; source='last_known_good_after_write_failure'
           else:  # drift reject
               use lkg_ids; source='last_known_good_after_drift_reject'
      3. 若任一 source failed:
           if lkg valid → use lkg_ids; source='last_known_good_after_source_failure'
           else abort()
      4. active_ids = (B ∩ official_ids) if B given else official_ids

    Returns:
        ActiveUniverseState · active_ids=None 表示呼叫端必須 abort
    """
    warnings: list = []
    twse = load_twse_official(_requests_module=_requests_module)
    tpex = load_tpex_official(_requests_module=_requests_module)

    official_ids: set
    source: str
    schema_status: str
    as_of: Optional[str]
    twse_size: Optional[int]
    tpex_size: Optional[int]

    if twse.ok and tpex.ok:
        # 把 source loader 產出的 dup_warning 進 state.warnings(FI-U8 · duplicate 不能只 log 消失)
        if twse.dup_warning:
            warnings.append(twse.dup_warning)
        if tpex.dup_warning:
            warnings.append(tpex.dup_warning)

        candidate_ids = twse.ids | tpex.ids
        lkg_payload = load_lkg(_lkg_path)
        if lkg_payload is None:
            # P3 Blocker 7:無 LKG 時【不允許 unattended bootstrap】
            return ActiveUniverseState(
                active_ids=None,
                source="abort",
                schema_status="abort",
                abort_reason=(
                    "no valid LKG present; deployment must include initial "
                    "data/active_universe.json (unattended bootstrap disallowed "
                    "to prevent both-endpoints-consistent-but-wrong scenario)"
                ),
            )

        lkg_ids = set(lkg_payload["official_active_ids"])

        # P3-r2 Blocker B:freshness guard(candidate 日期不得早於 LKG)
        lkg_twse_as = lkg_payload.get("twse_as_of")
        lkg_tpex_as = lkg_payload.get("tpex_as_of")
        freshness_ok = True
        freshness_reason = None
        if lkg_twse_as and twse.as_of and twse.as_of < lkg_twse_as:
            freshness_ok = False
            freshness_reason = (f"twse date regression: candidate={twse.as_of} < LKG={lkg_twse_as}")
        if lkg_tpex_as and tpex.as_of and tpex.as_of < lkg_tpex_as:
            reason2 = f"tpex date regression: candidate={tpex.as_of} < LKG={lkg_tpex_as}"
            freshness_reason = f"{freshness_reason}; {reason2}" if freshness_reason else reason2
            freshness_ok = False

        if not freshness_ok:
            official_ids = lkg_ids
            source = "last_known_good_after_freshness_reject"
            schema_status = "freshness_rejected"
            twse_size = lkg_payload.get("twse_count")
            tpex_size = lkg_payload.get("tpex_count")
            as_of = lkg_payload.get("official_as_of")
            warnings.append(f"freshness guard rejected candidate: {freshness_reason}")
        else:
            # freshness OK · 再 drift guard
            drift = passes_drift_guard(candidate_ids, lkg_ids)
            if drift.accepted:
                # P3-r2 Blocker C:必須先 write LKG 成功 · 才使用 candidate
                payload = build_snapshot_payload(twse, tpex, source="official_live")
                write_ok = True
                write_err = None
                try:
                    write_lkg(payload, _lkg_path)
                except Exception as e:  # noqa: BLE001
                    write_ok = False
                    write_err = f"{type(e).__name__}: {e}"

                if write_ok:
                    official_ids = candidate_ids
                    source = "official_live"
                    schema_status = "ok"
                    twse_size = len(twse.ids)
                    tpex_size = len(tpex.ids)
                    as_of = min(twse.as_of, tpex.as_of)
                else:
                    # 寫失敗 · 不使用 candidate · 用 LKG
                    official_ids = lkg_ids
                    source = "last_known_good_after_write_failure"
                    schema_status = "lkg_write_failed"
                    twse_size = lkg_payload.get("twse_count")
                    tpex_size = lkg_payload.get("tpex_count")
                    as_of = lkg_payload.get("official_as_of")
                    warnings.append(f"LKG write failed, using LKG: {write_err}")
            else:
                # drift reject · 保留 LKG
                official_ids = lkg_ids
                source = "last_known_good_after_drift_reject"
                schema_status = "drift_rejected"
                twse_size = lkg_payload.get("twse_count")
                tpex_size = lkg_payload.get("tpex_count")
                as_of = lkg_payload.get("official_as_of")
                warnings.append(f"drift guard rejected candidate: {drift.reason}")
    else:
        # 至少一來源失敗 · 不允許發布半套(FI-U1)
        lkg_payload = load_lkg(_lkg_path)
        if lkg_payload is None:
            # 無 LKG · 必須 abort
            err_msgs = []
            if not twse.ok:
                err_msgs.append(f"twse: {twse.error}")
            if not tpex.ok:
                err_msgs.append(f"tpex: {tpex.error}")
            return ActiveUniverseState(
                active_ids=None,
                source="abort",
                schema_status="abort",
                abort_reason=f"official source failed and no valid LKG: {'; '.join(err_msgs)}",
            )
        official_ids = set(lkg_payload["official_active_ids"])
        source = "last_known_good_after_source_failure"
        schema_status = "lkg_only"
        twse_size = lkg_payload.get("twse_count")
        tpex_size = lkg_payload.get("tpex_count")
        as_of = lkg_payload.get("official_as_of")
        err_msgs = []
        if not twse.ok:
            err_msgs.append(f"twse: {twse.error}")
        if not tpex.ok:
            err_msgs.append(f"tpex: {tpex.error}")
        warnings.append(f"official source failure, using LKG: {'; '.join(err_msgs)}")

    # 計算 active_ids = B ∩ Official(若 B 已知)
    if finmind_common_ids is None:
        active_ids = official_ids
        warnings.append("finmind_common_ids not provided; returning official_ids as active_ids (bootstrap/test)")
    else:
        active_ids = set(finmind_common_ids) & official_ids

    return ActiveUniverseState(
        active_ids=active_ids,
        source=source,
        as_of=as_of,
        twse_size=twse_size,
        tpex_size=tpex_size,
        official_active_size=len(official_ids),
        schema_status=schema_status,
        warnings=warnings,
    )


# ============================================================
# Prune helper(供 build.py 呼叫)
# ============================================================

def prune_inactive_rows(merged_rows: list,
                         active_ids: set) -> Tuple[list, list, list]:
    """
    對齊 P2 §七:merge helper 回傳 (merged_rows, inactive_rows_pruned, pruned_ids)。

    · 只移除 inactive(id not in active_ids)
    · 保留 active row 的所有 v3.5.4-r3 schema 欄位(不動)
    · deterministic ID sort

    Returns:
        (kept_rows, inactive_rows, pruned_ids)
    """
    kept, inactive = [], []
    for r in merged_rows:
        if r.get("id") in active_ids:
            kept.append(r)
        else:
            inactive.append(r)
    kept.sort(key=lambda r: r.get("id", ""))
    pruned_ids = sorted(r.get("id", "") for r in inactive)
    return kept, inactive, pruned_ids
