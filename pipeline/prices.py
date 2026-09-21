"""
股價 K 線資料(v3.6 · K 線功能)

兩條資料線:
  1. 歷史日K  data/prices/{id}/{year}.json + data/prices/{id}/index.json
     - 來源:FinMind TaiwanStockPrice(每檔 1 次 API 呼叫)
     - 時機:跟著既有 7 批 backfill 輪流更新(每檔每週 1 次)
     - 首次:從 PRICE_HISTORY_START 抓全部;之後只重抓「最後一筆所在年份」起
     - 過去年份寫一次就不再變動 · 只有當年檔會被覆寫(控制 git 體積)

  2. 近期日K  data/prices_recent.json(全市場單一檔)
     - 來源:證交所 STOCK_DAY_ALL + 櫃買 daily_close_quotes(官方 openapi · 不耗 FinMind 額度)
     - 時機:每個交易日收盤後(.github/workflows/daily-prices.yml)
     - 用途:選股頁「近3日」迷你K棒 + 個股頁 K 線補上歷史檔之後的最新幾天

檔案格式(兩者一致):每根 K 棒 = [日期 "YYYY-MM-DD", 開, 高, 低, 收, 成交量(張)]
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config

log = logging.getLogger(__name__)

TAIPEI_TZ = timezone(timedelta(hours=8))

# ============================================================
# 設定
# ============================================================
# 歷史 K 線最早起點。想要更長可以往前改(例如 "2010-01-01"),
# 下一輪 backfill 會自動把每檔補抓到新起點。越早 → repo 越大(每年全市場約 20MB)。
PRICE_HISTORY_START = "2016-01-01"

PRICE_DATASET = "TaiwanStockPrice"
PRICES_DIR: Path = config.DATA_DIR / "prices"
PRICES_RECENT_PATH: Path = config.DATA_DIR / "prices_recent.json"

# prices_recent.json 每檔保留幾根
RECENT_KEEP_BARS = 10

TWSE_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAY_ALL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
MIN_TWSE_BARS = 800
MIN_TPEX_BARS = 500


# ============================================================
# 共用小工具
# ============================================================
def _num(v: Any) -> Optional[float]:
    """'1,234.50' / 1234.5 / '--' / '' → float 或 None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).strip().replace(",", "")
        if not s or s.strip("-") == "" or s in ("N/A", "除權息", "除息", "除權"):
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    if f != f:  # NaN
        return None
    return f


def _round_px(f: float) -> float:
    r = round(f, 2)
    return int(r) if r == int(r) else r


def make_bar(d: str, o: Any, h: Any, l: Any, c: Any, shares: Any) -> Optional[list]:
    """組一根 K 棒;任何價格缺漏或 0(當天無成交)→ None"""
    o, h, l, c = _num(o), _num(h), _num(l), _num(c)
    if None in (o, h, l, c) or min(o, h, l, c) <= 0:
        return None
    hi, lo = max(o, h, l, c), min(o, h, l, c)
    vol = _num(shares) or 0.0
    return [d, _round_px(o), _round_px(hi), _round_px(lo), _round_px(c), int(round(vol / 1000))]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parse_roc_or_iso(v: Any) -> Optional[str]:
    """'1150918' / '115/09/18' / '20260918' / '2026-09-18' → '2026-09-18'"""
    if v is None:
        return None
    s = str(v).strip().replace("/", "").replace("-", "")
    if not s.isdigit():
        return None
    try:
        if len(s) == 8:
            y, m, d = int(s[:4]), int(s[4:6]), int(s[6:])
        elif len(s) in (6, 7):
            y, m, d = int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])
        else:
            return None
        return date(y, m, d).isoformat()
    except ValueError:
        return None


# ============================================================
# 1. 歷史日K(FinMind · 跟 backfill 批次)
# ============================================================
def finmind_rows_to_bars(rows: Iterable[dict]) -> list[list]:
    bars: dict[str, list] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d = parse_roc_or_iso(r.get("date"))
        if not d:
            continue
        bar = make_bar(d, r.get("open"), r.get("max"), r.get("min"), r.get("close"),
                       r.get("Trading_Volume"))
        if bar:
            bars[d] = bar
    return [bars[k] for k in sorted(bars)]


def _stock_dir(stock_id: str) -> Path:
    return PRICES_DIR / stock_id


def load_index(stock_id: str) -> Optional[dict]:
    idx = _read_json(_stock_dir(stock_id) / "index.json")
    return idx if isinstance(idx, dict) and idx.get("years") else None


def _fetch_start(idx: Optional[dict]) -> str:
    """決定這次要從哪天開始抓"""
    if not idx or idx.get("start") != PRICE_HISTORY_START or not idx.get("last"):
        return PRICE_HISTORY_START
    # 從「最後一筆所在年份」的 1/1 重抓 → 跨年時舊年份也會被補完整
    return f"{str(idx['last'])[:4]}-01-01"


def write_history(stock_id: str, bars: list[list], fetch_start: str,
                  prev_idx: Optional[dict]) -> Optional[dict]:
    """把抓到的 bars 依年份寫檔 · 回傳新 index(沒資料 → None)"""
    if not bars:
        return None
    sdir = _stock_dir(stock_id)
    by_year: dict[int, list] = {}
    for b in bars:
        by_year.setdefault(int(b[0][:4]), []).append(b)
    for y, rows in by_year.items():
        _write_json(sdir / f"{y}.json", rows)

    start_year = int(fetch_start[:4])
    years = set(by_year)
    if prev_idx and fetch_start != PRICE_HISTORY_START:
        # 增量:保留未重抓的舊年份
        years |= {int(y) for y in prev_idx.get("years", []) if int(y) < start_year}
        first = prev_idx.get("first") or bars[0][0]
    else:
        first = bars[0][0]
        # 全量重抓:清掉起點以前的舊年份檔(起點往後調時)
        for p in sdir.glob("*.json"):
            if p.stem.isdigit() and int(p.stem) not in years:
                p.unlink()
    idx = {
        "id": stock_id,
        "start": PRICE_HISTORY_START,
        "first": first,
        "last": bars[-1][0],
        "years": sorted(years),
        "updated": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(sdir / "index.json", idx)
    return idx


def update_price_history(client, stock_id: str) -> bool:
    """給 build.py 在每檔處理時呼叫 · 永不 raise(K 線失敗不影響財報主流程)"""
    try:
        idx = load_index(stock_id)
        start = _fetch_start(idx)
        rows = client.fetch(PRICE_DATASET, data_id=stock_id, start_date=start)
        bars = finmind_rows_to_bars(rows)
        if not bars:
            log.info("prices: %s 無股價資料(start=%s)", stock_id, start)
            return False
        write_history(stock_id, bars, start, idx)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("prices: %s 更新失敗: %s", stock_id, exc)
        return False


def read_history_tail(stock_id: str, n: int) -> list[list]:
    """讀歷史檔最後 n 根(給 prices_recent 冷啟動補齊用)"""
    idx = load_index(stock_id)
    if not idx:
        return []
    out: list[list] = []
    for y in sorted(idx["years"], reverse=True):
        rows = _read_json(_stock_dir(stock_id) / f"{y}.json") or []
        out = rows + out
        if len(out) >= n:
            break
    return out[-n:]


# ============================================================
# 2. 近期日K(官方 openapi · 每日)
# ============================================================
def _pick(r: dict, *keys: str) -> Any:
    for k in keys:
        if k in r:
            return r[k]
    return None


def parse_twse_day_all(data: Any, fallback_date: Optional[str] = None) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        sid = str(_pick(r, "Code", "證券代號") or "").strip()
        if not (len(sid) == 4 and sid.isdigit()):
            continue
        d = parse_roc_or_iso(_pick(r, "Date", "日期")) or fallback_date
        if not d:
            continue
        bar = make_bar(d,
                       _pick(r, "OpeningPrice", "開盤價"),
                       _pick(r, "HighestPrice", "最高價"),
                       _pick(r, "LowestPrice", "最低價"),
                       _pick(r, "ClosingPrice", "收盤價"),
                       _pick(r, "TradeVolume", "成交股數"))
        if bar:
            out[sid] = bar
    return out


def parse_tpex_day_all(data: Any, fallback_date: Optional[str] = None) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        sid = str(_pick(r, "SecuritiesCompanyCode", "代號") or "").strip()
        if not (len(sid) == 4 and sid.isdigit()):
            continue
        d = parse_roc_or_iso(_pick(r, "Date", "資料日期")) or fallback_date
        if not d:
            continue
        bar = make_bar(d,
                       _pick(r, "Open", "開盤"),
                       _pick(r, "High", "最高"),
                       _pick(r, "Low", "最低"),
                       _pick(r, "Close", "收盤"),
                       _pick(r, "TradingShares", "成交股數"))
        if bar:
            out[sid] = bar
    return out


def _http_json(url: str, retries: int = 3) -> Any:
    import requests  # 延遲 import · 測試不需要網路
    last = None
    for i in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=(10, 60), headers={"User-Agent": "finreport-funnel/1.0"})
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("GET %s 失敗(%d/%d): %s", url, i, retries, exc)
            time.sleep(3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


def merge_recent(existing: Optional[dict], new_bars: dict[str, list],
                 tail_loader=None, keep: int = RECENT_KEEP_BARS) -> dict:
    """
    existing: 舊的 prices_recent.json 內容
    new_bars: {stock_id: bar} 今天官方資料
    tail_loader(stock_id, n): 冷啟動時從歷史檔補的函式(可為 None)
    """
    old = (existing or {}).get("bars", {}) if isinstance(existing, dict) else {}
    ids = set(old) | set(new_bars)
    merged: dict[str, list] = {}
    for sid in sorted(ids):
        by_date = {b[0]: b for b in old.get(sid, []) if isinstance(b, list) and len(b) == 6}
        if tail_loader and len(by_date) < keep:
            for b in tail_loader(sid, keep):
                by_date.setdefault(b[0], b)
        if sid in new_bars:
            by_date[new_bars[sid][0]] = new_bars[sid]
        rows = [by_date[d] for d in sorted(by_date)][-keep:]
        if rows:
            merged[sid] = rows
    last_dates = [rows[-1][0] for rows in merged.values()]
    as_of = max(last_dates) if last_dates else None
    if as_of:
        # 下市/長期停牌:最後一根早於 asOf 45 天以上就移除
        cutoff = (date.fromisoformat(as_of) - timedelta(days=45)).isoformat()
        merged = {k: v for k, v in merged.items() if v[-1][0] >= cutoff}
    return {
        "updated": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"),
        "asOf": as_of,
        "fields": ["date", "open", "high", "low", "close", "volume_lots"],
        "bars": merged,
    }


def run_daily() -> int:
    """抓官方全市場當日行情 → 更新 data/prices_recent.json"""
    today = datetime.now(TAIPEI_TZ).date().isoformat()
    new_bars: dict[str, list] = {}
    ok_sources = 0
    for name, url, parser, min_n in (
        ("TWSE", TWSE_DAY_ALL_URL, parse_twse_day_all, MIN_TWSE_BARS),
        ("TPEx", TPEX_DAY_ALL_URL, parse_tpex_day_all, MIN_TPEX_BARS),
    ):
        try:
            bars = parser(_http_json(url), fallback_date=today)
        except Exception as exc:  # noqa: BLE001
            log.error("%s 抓取失敗: %s", name, exc)
            continue
        dates = {b[0] for b in bars.values()}
        if len(bars) < min_n:
            log.error("%s 只解析到 %d 檔(< %d)· 可能欄位格式改變,這次不採用", name, len(bars), min_n)
            continue
        log.info("%s: %d 檔 · 日期 %s", name, len(bars), sorted(dates))
        new_bars.update(bars)
        ok_sources += 1

    if ok_sources == 0:
        log.error("兩個來源都失敗 · 不寫檔")
        return 1

    merged = merge_recent(_read_json(PRICES_RECENT_PATH), new_bars, tail_loader=read_history_tail)
    _write_json(PRICES_RECENT_PATH, merged)
    log.info("prices_recent.json 已更新:%d 檔 · asOf=%s", len(merged["bars"]), merged["asOf"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(run_daily())
