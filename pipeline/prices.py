"""
股價 K 線資料(v3.6 · K 線功能)

兩條資料線:
  1. 歷史日K  data/prices/{id}/{year}.json + data/prices/{id}/index.json
     - 來源:FinMind TaiwanStockPrice(每檔 1 次 API 呼叫)
     - 時機:跟著既有 7 批 backfill 輪流更新(每檔每週輪到 1 次)
     - 首次:從 PRICE_HISTORY_START 抓全部(每檔 1 次 FinMind)
     - v3.6.1 省額度:之後輪到時,若 prices_recent.json(官方每日資料)能無縫接上歷史檔,
       就直接用官方資料延長歷史 → 0 次 FinMind;每 FINMIND_VERIFY_DAYS 天才向 FinMind 對帳一次
     - 過去年份寫一次就不再變動 · 只有當年檔會被覆寫(控制 git 體積)

  2. 近期日K  data/prices_recent.json(全市場單一檔)
     - 來源:證交所 STOCK_DAY_ALL(官網 rwd + openapi 兩個都抓)+ 櫃買 daily_close_quotes
       (官方資料 · 不耗 FinMind 額度)· v3.6.1:證交所 openapi 會晚一天,官網 rwd 版收盤後就更新
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
TRADING_STATUS_PATH: Path = config.DATA_DIR / "trading_status.json"

# v3.6.6 交易狀態判定(日曆天 · 以全市場最新交易日 asOf 為基準)
#   · 暫停交易(減資換股、處置、停止買賣…)只加標籤,不移除
#   · 真正下市(終止上市櫃)由 active_universe(證交所/櫃買官方公司清單)判定,批次會自動移出
HALT_DAYS = 7          # 最後成交落後 > 7 天(約 5 個交易日)→ 「暫停交易」標籤
FINMIND_LOOKBACK_DAYS = 120   # 查不到最後成交日時,向 FinMind 查近 120 天
FINMIND_RECHECK_DAYS = 7      # 同一檔最多每 7 天向 FinMind 查一次
FINMIND_STATUS_MAX_CALLS = 40

# prices_recent.json 每檔保留幾根
RECENT_KEEP_BARS = 10

# 用官方每日資料延長歷史時,多久一定要向 FinMind 重新對帳一次(天)
FINMIND_VERIFY_DAYS = 28

TWSE_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
# 證交所官網版(收盤後約 14:30 起就是當天資料;openapi 版要到隔天才更新)
TWSE_RWD_DAY_ALL_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json"
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
        "verified": datetime.now(TAIPEI_TZ).date().isoformat(),   # 最近一次 FinMind 對帳
    }
    _write_json(sdir / "index.json", idx)
    return idx


_recent_cache: Optional[dict] = None


def _recent_bars(stock_id: str) -> list[list]:
    global _recent_cache
    if _recent_cache is None:
        data = _read_json(PRICES_RECENT_PATH)
        _recent_cache = data.get("bars", {}) if isinstance(data, dict) else {}
    rows = _recent_cache.get(stock_id) or []
    return [b for b in rows if isinstance(b, list) and len(b) == 6]


def extend_from_recent(stock_id: str, idx: Optional[dict]) -> bool:
    """
    v3.6.1 省 FinMind:用官方每日資料(prices_recent)延長歷史檔。
    條件:歷史檔存在 · 最近一次 FinMind 對帳在 FINMIND_VERIFY_DAYS 天內 ·
          官方資料最早一根 <= 歷史最後一天(有重疊 = 中間沒有缺口)
    """
    if not idx or idx.get("start") != PRICE_HISTORY_START or not idx.get("last"):
        return False
    verified = str(idx.get("verified") or "")[:10]
    today = datetime.now(TAIPEI_TZ).date()
    try:
        if not verified or (today - date.fromisoformat(verified)).days >= FINMIND_VERIFY_DAYS:
            return False
    except ValueError:
        return False
    recent = _recent_bars(stock_id)
    last = str(idx["last"])
    if not recent or recent[0][0] > last:
        return False                       # 沒資料或有缺口 → 交給 FinMind
    newer = [b for b in recent if b[0] > last]
    if not newer:
        return True                        # 已是最新 · 不用動
    years = sorted({int(b[0][:4]) for b in newer})
    sdir = _stock_dir(stock_id)
    for y in years:
        rows = _read_json(sdir / f"{y}.json") or []
        by_date = {b[0]: b for b in rows if isinstance(b, list) and len(b) == 6}
        for b in newer:
            if int(b[0][:4]) == y:
                by_date[b[0]] = b
        _write_json(sdir / f"{y}.json", [by_date[d] for d in sorted(by_date)])
    idx = dict(idx)
    idx["last"] = newer[-1][0]
    idx["years"] = sorted(set(int(y) for y in idx.get("years", [])) | set(years))
    idx["updated"] = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    _write_json(sdir / "index.json", idx)
    return True


def update_price_history(client, stock_id: str) -> bool:
    """給 build.py 在每檔處理時呼叫 · 永不 raise(K 線失敗不影響財報主流程)"""
    try:
        idx = load_index(stock_id)
        if extend_from_recent(stock_id, idx):
            return True                    # 0 次 FinMind
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


def parse_twse_rwd(payload: Any) -> dict[str, list]:
    """證交所官網版:{"stat":"OK","date":"20260921","fields":[...],"data":[[...],...]}"""
    out: dict[str, list] = {}
    if not isinstance(payload, dict) or str(payload.get("stat", "")).upper() != "OK":
        return out
    d = parse_roc_or_iso(payload.get("date"))
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    if not d or not isinstance(fields, list):
        return out
    pos = {str(f).strip(): i for i, f in enumerate(fields)}
    need = ["證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數"]
    if any(k not in pos for k in need):
        log.warning("TWSE rwd 欄位不符:%s", fields)
        return out
    for r in rows:
        if not isinstance(r, list) or len(r) < len(fields):
            continue
        sid = str(r[pos["證券代號"]]).strip()
        if not (len(sid) == 4 and sid.isdigit()):
            continue
        bar = make_bar(d, r[pos["開盤價"]], r[pos["最高價"]], r[pos["最低價"]],
                       r[pos["收盤價"]], r[pos["成交股數"]])
        if bar:
            out[sid] = bar
    return out


def parse_twse_rwd_csv(text: Any) -> dict[str, list]:
    """
    v3.6.3:證交所官網實際回傳的是 CSV(即使網址寫 response=json):
      日期,證券代號,證券名稱,成交股數,成交金額,開盤價,最高價,最低價,收盤價,漲跌價差,成交筆數
      "1150921","2330","台積電","40,893,000",...
    """
    import csv
    import io
    out: dict[str, list] = {}
    if not isinstance(text, str) or "證券代號" not in text:
        return out
    clean = lambda v: str(v).strip().lstrip("=").strip('"').strip()
    pos = None
    for row in csv.reader(io.StringIO(text)):
        cells = [clean(c) for c in row]
        if pos is None:
            if "證券代號" in cells:
                pos = {c: i for i, c in enumerate(cells)}
                need = ["證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數"]
                if any(k not in pos for k in need):
                    log.warning("TWSE rwd CSV 欄位不符:%s", cells)
                    return {}
            continue
        if len(cells) < len(pos):
            continue
        sid = cells[pos["證券代號"]]
        if not (len(sid) == 4 and sid.isdigit()):
            continue
        d = parse_roc_or_iso(cells[pos["日期"]]) if "日期" in pos else None
        if not d:
            continue
        bar = make_bar(d, cells[pos["開盤價"]], cells[pos["最高價"]], cells[pos["最低價"]],
                       cells[pos["收盤價"]], cells[pos["成交股數"]])
        if bar:
            out[sid] = bar
    return out


def _parse_twse_rwd_any(payload: Any, fallback_date: Optional[str] = None) -> dict[str, list]:
    """官網版:可能是 JSON(dict)或 CSV(文字)· 兩種都吃"""
    if isinstance(payload, dict):
        return parse_twse_rwd(payload)
    return parse_twse_rwd_csv(payload)


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


def _http_json(url: str, retries: int = 3, allow_text: bool = False) -> Any:
    import requests  # 延遲 import · 測試不需要網路
    last = None
    for i in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=(10, 60), headers={
                # v3.6.2: 證交所官網會擋非瀏覽器的 User-Agent → 模擬一般瀏覽器
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-TW,zh;q=0.9",
                "Referer": "https://www.twse.com.tw/zh/trading/historical/stock-day-all.html",
            })
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                resp.encoding = resp.encoding or "utf-8"
                text = resp.text
                if allow_text and "證券代號" in text:
                    return text                     # 證交所官網回 CSV → 交給 CSV 解析器
                # 回的不是 JSON(多半是擋爬蟲的網頁)→ 記下開頭方便診斷
                snippet = " ".join(text[:160].split())
                raise ValueError(f"非 JSON 回應(HTTP {resp.status_code}):{snippet}")
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("GET %s 失敗(%d/%d): %s", url, i, retries, exc)
            time.sleep(3 * i)
    raise RuntimeError(f"GET {url} failed: {last}")


def merge_recent(existing: Optional[dict], new_bars: dict[str, list],
                 tail_loader=None, keep: int = RECENT_KEEP_BARS,
                 extra_ids: Iterable[str] = ()) -> dict:
    """
    existing: 舊的 prices_recent.json 內容
    new_bars: {stock_id: bar 或 [bar, bar...]} 官方資料
    tail_loader(stock_id, n): 從歷史檔補缺的函式(可為 None · 官方資料優先)
    """
    old = (existing or {}).get("bars", {}) if isinstance(existing, dict) else {}
    # v3.6.6: extra_ids = 有歷史檔的股票 → 最近沒成交的也能從歷史補出 K 棒(前端會變淡顯示)
    ids = set(old) | set(new_bars) | set(extra_ids)
    merged: dict[str, list] = {}
    for sid in sorted(ids):
        by_date = {b[0]: b for b in old.get(sid, []) if isinstance(b, list) and len(b) == 6}
        if tail_loader:
            # v3.6.1:每次都拿歷史檔補洞(某天排程漏跑 → 批次更新歷史後自動補回)
            for b in tail_loader(sid, keep):
                by_date.setdefault(b[0], b)
        nb = new_bars.get(sid)
        if nb:
            for b in (nb if isinstance(nb[0], list) else [nb]):
                by_date[b[0]] = b
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


# ============================================================
# 3. 交易狀態(暫停交易 / 停牌)· v3.6.6
# ============================================================
def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def build_trading_status(ids: Iterable[str], recent_bars: dict, as_of: str,
                         prev: Optional[dict] = None, client=None,
                         today: Optional[str] = None) -> dict:
    """
    ids: 選股清單上的所有股票
    recent_bars: prices_recent.json 的 bars
    as_of: 全市場最新交易日
    client: FinMind client(可為 None · 只在「查不到最後成交日」時用 · 每天最多 40 次)
    """
    today = today or datetime.now(TAIPEI_TZ).date().isoformat()
    prev_stocks = (prev or {}).get("stocks", {}) if isinstance(prev, dict) else {}
    stocks: dict[str, dict] = {}
    calls = 0
    for sid in sorted(set(ids)):
        p = prev_stocks.get(sid, {}) if isinstance(prev_stocks.get(sid), dict) else {}
        cands = []
        rows = recent_bars.get(sid) or []
        if rows:
            cands.append(rows[-1][0])
        idx = load_index(sid)
        if idx and idx.get("last"):
            cands.append(str(idx["last"]))
        if p.get("last"):
            cands.append(p["last"])
        last = max(cands) if cands else None
        rec = {"last": last, "checked": p.get("checked"), "none_since": p.get("none_since")}

        # 最近沒成交、又不知道最後成交日 → 向 FinMind 查(有節流)
        need_check = (last is None or _days_between(last, as_of) > HALT_DAYS)
        recently_checked = bool(rec["checked"]) and _days_between(rec["checked"], today) < FINMIND_RECHECK_DAYS
        if need_check and client is not None and not recently_checked and calls < FINMIND_STATUS_MAX_CALLS:
            start = (date.fromisoformat(as_of) - timedelta(days=FINMIND_LOOKBACK_DAYS)).isoformat()
            try:
                fm = finmind_rows_to_bars(client.fetch(PRICE_DATASET, data_id=sid, start_date=start))
                calls += 1
                rec["checked"] = today
                if fm:
                    rec["last"] = max(last or "", fm[-1][0]) or fm[-1][0]
                    rec["none_since"] = None
                else:
                    rec["none_since"] = start      # 近 120 天完全沒成交
            except Exception as exc:  # noqa: BLE001
                log.warning("trading_status: %s FinMind 查詢失敗: %s", sid, exc)

        last = rec["last"]
        if last:
            gap = _days_between(last, as_of)
            rec["gap_days"] = gap
            rec["state"] = "halted" if gap > HALT_DAYS else "trading"
        elif rec.get("none_since"):
            rec["state"] = "halted"            # 近 120 天完全沒成交(仍在官方掛牌清單 → 只標示不移除)
        else:
            rec["state"] = "unknown"          # 還沒有任何資料可判斷 → 不處理
        stocks[sid] = {k: v for k, v in rec.items() if v is not None}

    halted = sorted(k for k, v in stocks.items() if v.get("state") == "halted")
    log.info("trading_status: 暫停交易 %d 檔 %s · FinMind 查詢 %d 次", len(halted), halted[:30], calls)
    return {
        "updated": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"),
        "asOf": as_of,
        "rules": {"halt_days": HALT_DAYS},
        "halted": halted,
        "stocks": stocks,
    }


def _scanner_ids() -> list[str]:
    data = _read_json(config.SCANNER_INDEX_PATH)
    rows = data.get("stocks", []) if isinstance(data, dict) else []
    return [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]


def _optional_finmind_client():
    import os
    if not os.environ.get("FINMIND_TOKEN"):
        log.info("未設定 FINMIND_TOKEN · 交易狀態只用官方資料判斷")
        return None
    try:
        from .finmind_client import load_client
        return load_client()
    except Exception as exc:  # noqa: BLE001
        log.warning("FinMind client 建立失敗: %s", exc)
        return None


def run_daily() -> int:
    """抓官方全市場當日行情 → 更新 data/prices_recent.json"""
    today = datetime.now(TAIPEI_TZ).date().isoformat()
    new_bars: dict[str, list] = {}
    ok_sources = 0
    for name, url, parser, min_n in (
        # 證交所兩個版本都抓:官網 rwd 當天就更新 · openapi 晚一天(兩者日期不同時兩天都收)
        ("TWSE-rwd", TWSE_RWD_DAY_ALL_URL, _parse_twse_rwd_any, MIN_TWSE_BARS),
        ("TWSE-openapi", TWSE_DAY_ALL_URL, parse_twse_day_all, MIN_TWSE_BARS),
        ("TPEx", TPEX_DAY_ALL_URL, parse_tpex_day_all, MIN_TPEX_BARS),
    ):
        try:
            bars = parser(_http_json(url, allow_text=(name == "TWSE-rwd")), fallback_date=today)
        except Exception as exc:  # noqa: BLE001
            log.error("%s 抓取失敗: %s", name, exc)
            continue
        dates = {b[0] for b in bars.values()}
        if len(bars) < min_n:
            log.error("%s 只解析到 %d 檔(< %d)· 可能欄位格式改變,這次不採用", name, len(bars), min_n)
            continue
        log.info("%s: %d 檔 · 日期 %s", name, len(bars), sorted(dates))
        for sid, b in bars.items():
            new_bars.setdefault(sid, []).append(b)
        ok_sources += 1

    if ok_sources == 0:
        log.error("所有來源都失敗 · 不寫檔")
        return 1

    hist_ids = [p.name for p in PRICES_DIR.iterdir() if p.is_dir()] if PRICES_DIR.exists() else []
    merged = merge_recent(_read_json(PRICES_RECENT_PATH), new_bars, tail_loader=read_history_tail,
                          extra_ids=hist_ids)
    _write_json(PRICES_RECENT_PATH, merged)
    log.info("prices_recent.json 已更新:%d 檔 · asOf=%s", len(merged["bars"]), merged["asOf"])

    # v3.6.6: 交易狀態(暫停交易 / 停牌)
    try:
        ids = _scanner_ids()
        if ids and merged["asOf"]:
            status = build_trading_status(ids, merged["bars"], merged["asOf"],
                                          prev=_read_json(TRADING_STATUS_PATH),
                                          client=_optional_finmind_client())
            _write_json(TRADING_STATUS_PATH, status)
    except Exception as exc:  # noqa: BLE001
        log.warning("trading_status 更新失敗(不影響 K 棒): %s", exc)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(run_daily())
