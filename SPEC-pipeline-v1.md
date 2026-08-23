# 財報轉化漏斗 · Layer 2 資料管線規格書(LOCKED SPEC)

**版本**:SPEC-pipeline-v1 · 2026-08-22
**類型**:資料管線規格(可執行,附完整參考實作)
**前置依賴**:
- SPEC v2.2(Detail 頁,決定 JSON 輸出 schema)
- SPEC-scanner-v1 + v1.1(Scanner 頁,決定 index JSON schema)
**產出對象**:Python 3.12 專案,部署於 GitHub Actions + GitHub Pages(單一 Public repo)
**Reference 實作**:本專案 `pipeline/*.py` 全部檔案

---

## §0. 給接手 AI(生產 AI)的執行指令

1. 本規格書為 Layer 2 資料管線的單一真相來源。所有 schema、公式、檔案結構、rate limit 策略、workflow 設定皆已鎖。
2. 不要提出方案 A/B/C。所有決策已鎖。
3. 若要修改本 SPEC,只有兩種正當理由:(a)FinMind API schema 變動、(b)Layer 3 前端 JSON 需求變動。其餘 hunch-based 改動一律拒絕。
4. **JSON 輸出 schema 為 Layer 3 前端契約,不得任意變欄位名或型別**。任何 schema 變動必須同步反映在 Detail SPEC v2.2 §18 與 Scanner SPEC v1 §18.2。
5. Python 版本鎖 3.12。允許套件見 §20。禁 pandas / requests / pyarrow 以外的重量級依賴。
6. 遇到未涵蓋細節,採「保守 + 冪等」原則,不得憑感覺補功能。
7. 交付前執行 §21 檢查清單全部項目。

---

## §1. 產品定位

Layer 2 是**資料管線層**。每日從 FinMind 拉台股財報 / 月營收,計算所有漏斗指標,產出前端可直接 `fetch()` 讀取的 JSON 檔。

**單一任務**:讓 Layer 3 前端無需任何後端、無需任何運行時計算,只要 `fetch(json)` 就能渲染所有畫面。

**不做的事**:
- 不做 backend API(全部靜態 JSON)
- 不做即時串流(每日一次更新即可,收盤後才有意義)
- 不做用戶帳號、收藏、alert(那是 Layer 4 議題)
- 不做技術面指標(K線、量能、五檔),只做基本面

---

## §2. 命名慣例

### 2.1 FinMind Dataset → 內部別名對照

| 別名 | FinMind Dataset | 用途 |
|---|---|---|
| `info` | `TaiwanStockInfo` | 股票基本資料(名稱、產業、市場) |
| `fs` | `TaiwanStockFinancialStatements` | 季綜合損益表 |
| `bs` | `TaiwanStockBalanceSheet` | 季資產負債表(取合約負債) |
| `revenue` | `TaiwanStockMonthRevenue` | 月營收 |
| `price` | `TaiwanStockPrice` | 日 K(v1 未使用,預留) |

**參考實作**:`pipeline/config.py` `DATASETS`

### 2.2 FinMind FS `type` 欄位 → 內部欄位對照

FinMind FS API 回傳 long format `(date, stock_id, type, value)`,`type` 欄位為 XBRL 標籤。管線 pivot 為 wide format 時對照如下:

| FinMind `type` | 內部欄位 | 單位轉換 |
|---|---|---|
| `Revenue` | `rev` | 千元 → 百萬(÷1000) |
| `GrossProfit` | `gp` | 千元 → 百萬 |
| `OperatingIncome` | `op` | 千元 → 百萬 |
| `IncomeAfterTaxes` | `np` | 千元 → 百萬 |
| `EPS` | `eps` | 已是元,不轉換 |
| `TotalNonoperatingIncomeAndExpenses` | `noi` | 千元 → 百萬 |

**參考實作**:`pipeline/config.py` `FS_FIELD_MAP` + `pipeline/transform.py::_fs_pivot`

**若 FinMind 未來變更 XBRL 標籤名稱**,只需改 `FS_FIELD_MAP` 一處。

### 2.3 產業對照:FinMind `industry_category` → Scanner 產業代碼

|FinMind 中文 | Scanner 代碼 |
|---|---|
| 半導體業 | `semi` |
| 電子零組件業 / 光電業 / 電腦及週邊 | `component` |
| 資訊服務業 / 電子通路業 | `integration` |
| 化學工業 / 塑膠工業 / 化學生技醫療 | `chemical` |
| 生技醫療業 | `biotech` |
| 電機機械 | `machinery` |
| 水泥 / 食品 / 紡織 / 鋼鐵 / 汽車 / 航運 / 觀光 / 貿易百貨 / 建材營造 / 通信網路 / 其他電子 | `traditional` |
| 金融保險 | `finance` |

**參考實作**:`pipeline/config.py` `INDUSTRY_MAP`,預設 fallback `traditional`。

### 2.4 檔案命名

- `data/stocks/{id}.json`:每檔一份 Detail 資料,`id` 為 4 位股號
- `data/scanner_index.json`:全市場 Scanner index,單一檔案
- `data/meta.json`:build metadata
- `cache/raw/{dataset}/{stock_id}.parquet`:FinMind 原始 raw data 快取

---

## §3. 絕對禁止事項

- ❌ 前端直接呼叫 FinMind API(Token 保密, ToS 也不允許)
- ❌ FINMIND_TOKEN 進 git(必須走 GitHub Secrets)
- ❌ JSON 輸出的金額不同單位(**全部固定為「百萬」**,前端負責顯示切換;禁在 pipeline 端做「億」)
- ❌ JSON 輸出帶單位字串(cell 值純數字,不加「百萬」後綴)
- ❌ 業務公式散落於多個檔案(**只在 `transform.py` 一處**)
- ❌ Rate limit 只在 client-side try/except(必須主動間隔,不靠 429 recovery)
- ❌ 使用 pandas / pyarrow / requests 以外的重量級套件(見 §20)
- ❌ 資料處理走多執行緒 / 多進程(FinMind rate limit 是全域, 反而拖慢)
- ❌ 使用 SQLite / DuckDB 存 raw data(parquet 已夠,升級留給未來)
- ❌ 使用 `time.sleep(random)` 或其他 non-deterministic 間隔(必用固定值,便於估算)
- ❌ 硬碼股票代號在 build 流程(全部走 `config.DEMO_UNIVERSE` 或未來全市場清單)
- ❌ 業務邏輯出現於 workflow yml 內(yml 只做 pipeline 呼叫)
- ❌ pipeline 依賴前端 hardcoded 資料(pipeline 是 source of truth, 反過來)

---

## §4. 系統架構

```
┌──────────────────────────────────────────────────────────────┐
│                     FinMind API (外部)                        │
│                    api.finmindtrade.com                       │
└─────────────────────────┬────────────────────────────────────┘
                          │ HTTPS + token
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  pipeline.finmind_client                                      │
│  - RateLimiter (7.2 秒/req 硬間隔)                             │
│  - retry × 3 with backoff                                     │
│  - Mock mode (env FINMIND_MOCK=1) 供本機測試                   │
└─────────────────────────┬────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  pipeline.ingest                                              │
│  - fetch_universe / fetch_fs / fetch_bs / fetch_revenue       │
│  - 增量策略:cache TTL 判斷 (季報 45 天, 月營收 7 天)           │
│  - 存 cache/raw/{dataset}/{stock_id}.parquet                  │
└─────────────────────────┬────────────────────────────────────┘
                          │ parquet
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  pipeline.transform                                            │
│  - _fs_pivot / _bs_pivot: long -> wide                        │
│  - build_detail(stock_id): 生成 Detail JSON dict              │
│  - build_scanner_row(detail): 生成 Scanner row dict           │
│  - 所有業務公式集中在此 (v2.2 §7, Scanner §7.5)                │
└─────────────────────────┬────────────────────────────────────┘
                          │ dict
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  pipeline.output                                              │
│  - write_stock_detail -> data/stocks/{id}.json                │
│  - write_scanner_index -> data/scanner_index.json             │
│  - write_meta -> data/meta.json                               │
│  - 原子寫入 (先 .tmp 後 rename)                                │
└─────────────────────────┬────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  git commit + push (workflow 內 GITHUB_TOKEN 執行)             │
└─────────────────────────┬────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  GitHub Pages 服務靜態檔                                       │
│  https://<user>.github.io/<repo>/data/scanner_index.json      │
│  https://<user>.github.io/<repo>/data/stocks/{id}.json        │
└─────────────────────────┬────────────────────────────────────┘
                          │ fetch()
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  Layer 3 HTML (stock.html / scanner.html)                     │
└──────────────────────────────────────────────────────────────┘
```

---

## §5. 資料來源(FinMind Datasets 詳細規格)

### 5.1 Rate Limit

- 免費方案:**600 requests / hour**
- 本管線硬限:**500 req/hr**(留 100 req headroom)
- 實作:`RateLimiter` 強制每次呼叫間隔 3600/500 = **7.2 秒**
- 若遇 402 錯誤(rate limit 超額),額外 backoff:`RETRY_BACKOFF_SECONDS × attempt × 4`

### 5.2 TaiwanStockInfo(公司基本資料)

- **呼叫方式**:一次呼叫回傳全市場所有股票,無需 per-stock query
- **回傳欄位**:`stock_id`, `stock_name`, `industry_category`, `type`(twse/otwo)
- **快取 TTL**:30 天(公司名稱與產業變動極少)
- **首次 backfill**:1 req
- **每日 daily**:0 req(TTL 30 天內)

### 5.3 TaiwanStockFinancialStatements(季綜合損益表)

- **呼叫方式**:`?dataset=TaiwanStockFinancialStatements&data_id={stock_id}&start_date=2023-01-01`
- **回傳格式**:long format,`(date, stock_id, type, value)`
- **關鍵 `type` 欄位**:見 §2.2 對照表
- **貨幣單位**:千元(pipeline 轉為百萬 ÷1000)
- **快取 TTL**:45 天(季報公布間隔約 45 天)
- **首次 backfill**:1 req/stock,全市場 ~1800 req
- **每日 daily**:僅超過 TTL 的股票,穩態近乎 0 req/day,每季公布期間 (5/10/8/13/11/14 前後) 集中觸發

### 5.4 TaiwanStockBalanceSheet(季資產負債表)

- **呼叫方式**:同 FS
- **關鍵 `type` 欄位**:`ContractLiabilities-Current`(合約負債-流動)
- **快取 TTL**:45 天
- **成本**:同 FS

### 5.5 TaiwanStockMonthRevenue(月營收)

- **呼叫方式**:`?dataset=TaiwanStockMonthRevenue&data_id={stock_id}&start_date=2023-01-01`
- **回傳欄位**:`date`, `stock_id`, `revenue`, `revenue_year`, `revenue_month`
- **貨幣單位**:千元 → 百萬
- **快取 TTL**:7 天(月營收每月 10 日左右公布)
- **成本**:1 req/stock/month → 全市場每月 1800 req

### 5.6 全市場成本估算

| 場景 | 呼叫次數 | 純呼叫時間 (7.2s/req) | 需要天數 |
|---|---|---|---|
| 首次 backfill(FS + BS + Rev × 1800 檔) | ~5,400 | ~10.8 hr | 分 2-3 天 |
| 加 4Q 歷史深度需求(若 FinMind 未回傳 2 年以上) | +額外呼叫 | | 分 5-7 天 |
| 每日 daily(月營收公布日,~1800 檔) | ~1,800 | ~3.6 hr | 一天內 |
| 每日 daily(平日,無新資料) | <100 | <15 min | 一天內 |

**GitHub Actions 單次執行上限 6 hours**,故 backfill 需分次執行(見 §12)。

---

## §6. 資料處理管線

### 6.1 三段式:Ingest → Transform → Output

各段職責嚴格分離:

- **Ingest**:只做 HTTP call + cache 寫入。不做欄位轉換、不做業務計算。
- **Transform**:只做欄位轉換、pivot、指標計算。不做 IO。純函式優先。
- **Output**:只做 JSON 寫入。不改變資料。

### 6.2 增量策略

管線預設走**增量**(daily mode)。判斷邏輯:

```python
def _is_cache_fresh(path: Path, ttl_days: int) -> bool:
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_days * 86400
```

- TTL 內:直接讀 parquet,不呼叫 FinMind
- TTL 過期或無 cache:呼叫 FinMind,寫入新 parquet
- 若呼叫失敗但 cache 存在:用舊 cache(避免 pipeline 中斷)

**參考實作**:`pipeline/ingest.py::fetch_*`

### 6.3 全量策略

Backfill mode 走 `force=True`,忽略 cache 直接重抓所有股票、所有 dataset:

```bash
python -m pipeline.build --backfill
```

首次上線建議跑 backfill。之後只跑 daily。

### 6.4 冪等性

每次跑管線都應產出**相同的 JSON**(給定相同 cache)。三個保證:
- Ingest 只寫 parquet,不修改
- Transform 是純函式
- Output 用原子寫入(`.tmp` → `rename`)

---

## §7. 業務公式

**全部繼承 v2.2 §7 與 Scanner §7.5**。管線端 `transform.py` 實作對照:

| 指標 | 公式 | `transform.py` 位置 |
|---|---|---|
| 毛利率 gm | gp / rev × 100 | `build_scanner_row` |
| 營益率 om | op / rev × 100 | `build_scanner_row` |
| 淨利率 nm | np / rev × 100 | `build_scanner_row` |
| 業外占淨利 noiRatio | noi / np × 100 | `build_scanner_row` |
| YoY | (current / yr_ago − 1) × 100 | `_pct_change` |
| QoQ (margin delta) | current_margin − prev_margin | `build_scanner_row` |
| 訂單能見度 vis | cl / (avg 近3月營收) | `_visibility_months` |
| 合約負債判定 hasCL | max(近8Q CL) / max(近8Q rev) > 0.15 | `build_detail` |
| 黃金交叉 gc | MA3 由下穿越 MA12,近 1 月內 | `_detect_golden_cross` |

**健康度總分 healthScore** 不由 pipeline 計算,**留給前端**(Scanner §7.5 已在前端 JS 實作)。理由:pipeline 若計算 score,遇到閾值調整時需重建全部歷史;由前端計算則零成本。

---

## §8. JSON 輸出 Schema(前端契約,不可任意變)

### 8.1 `data/stocks/{id}.json`(Detail 頁)

匹配 v2.2 §18 完整結構:

```json
{
  "id": "6789",
  "name": "示範系統",
  "industry": "integration",
  "market": "twse",
  "hasCL": true,
  "quarterly": [
    { "q": "2024/3Q", "cl": 3531, "rev": 7621, "gp": 1677, "op": 381, "noi": -11, "np": 370, "eps": 1.45 }
  ],
  "monthly": [
    ["2024-07", 2530]
  ]
}
```

- 所有金額欄位單位:**百萬**(整數,四捨五入)
- `eps` 單位:元(小數 2 位)
- `noi` 允許小數 1 位
- `quarterly` 長度:**8**(v2.2 §4.1 固定)
- `monthly` 長度:**26**(24 for 12MA + 2 buffer)
- `q` 格式:`YYYY/{1-4}Q`
- `monthly[i]` 為 `[YYYY-MM, revenue]` tuple 格式(JSON 為 array)

### 8.2 `data/scanner_index.json`(Scanner 頁)

匹配 Scanner v1 §18.2 schema:

```json
{
  "meta": {
    "last_updated": "2026-08-22 22:00 +0800",
    "universe_size": 20,
    "current_quarter": "2026/2Q"
  },
  "stocks": [
    {
      "id": "6789",
      "name": "示範系統",
      "industry": "integration",
      "market": "twse",
      "hasCL": true,
      "rev": 13381,
      "revYoY": 22.7,
      "gm": 26.5,
      "om": 8.2,
      "nm": 8.0,
      "noiRatio": -2.3,
      "gmQoQ": 0.5,
      "omQoQ": 0.4,
      "nmQoQ": 0.3,
      "gc": false,
      "vis": 1.7,
      "clYoY": 41.8
    }
  ]
}
```

- 比率欄位單位:%(小數 1 位)
- `rev`:百萬(整數)
- `vis`:個月(小數 1 位),hasCL=false 時為 `null`
- `clYoY`:%,hasCL=false 時為 `null`
- `gc`:boolean

### 8.3 `data/meta.json`(build metadata)

```json
{
  "last_full_build": "2026-08-22T22:00:00+08:00",
  "universe_size": 20,
  "targets": 20,
  "built_ok": 20,
  "built_fail": 0,
  "backfill_status": "complete",
  "data_freshness": {
    "quarterly": "2026/2Q",
    "monthly": "2026-08"
  }
}
```

前端可用 `meta.json` 顯示「資料日期」或監控管線健康度。

### 8.4 JSON 壓縮

輸出用 `json.dumps(data, ensure_ascii=False, separators=(",", ":"))` 最小化。
若未來檔案過大(單檔 >1MB),考慮 gzip encoding(GitHub Pages 支援)。

---

## §9. 排程與觸發

### 9.1 每日排程(daily-build.yml)

- **cron**:`0 14 * * *`(UTC 14:00 = TPE 22:00,收盤後 2 小時,確保當日資料齊全)
- **timeout**:90 分鐘
- **手動觸發**:`workflow_dispatch` 保留給除錯用
- **concurrency**:`cancel-in-progress: false`,不打斷已在跑的 build

### 9.2 手動 backfill(backfill.yml)

- **僅 workflow_dispatch**,不排程
- **timeout**:350 分鐘(接近 GH Actions 6 hour 上限,保留 10 分緩衝)
- **重跑機制**:cache 保留,失敗後重跑會從斷點續(見 §12)

### 9.3 GitHub Pages 部署

Pages 自動追蹤 branch,無需額外 workflow。JSON 檔 commit 後幾分鐘內生效。

---

## §10. 錯誤處理與監控

### 10.1 分層錯誤策略

| 層級 | 錯誤類型 | 處理 |
|---|---|---|
| Client | 網路 timeout | retry × 3 with backoff |
| Client | HTTP 402 (rate limit) | 更長 backoff |
| Client | HTTP 5xx | retry × 3 |
| Client | HTTP 4xx (非 402) | 立即 fail,log |
| Ingest | 空 payload | 若有 cache 用 cache,否則 skip 該 dataset |
| Transform | 缺欄位 | log warning,該欄位設 None |
| Transform | 8Q 資料不足 | 允許,quarterly 長度 <8 |
| Output | 寫檔失敗 | raise,pipeline 中止(避免壞資料 push) |
| Build | 單檔 build 失敗 | 計入 `built_fail`,繼續其他股 |
| Build | 全部失敗 | exit code 2,workflow 標紅 |

### 10.2 監控管道

- **GitHub Actions email 通知**:workflow 失敗時 GH 自動 email
- **meta.json `built_fail`**:每次 build 後可讀取判斷有多少檔失敗
- **未來擴充**(v2 議題):Discord webhook / Slack notify

---

## §11. 前端整合契約

### 11.1 URL 契約

| 前端頁 | 讀取路徑 |
|---|---|
| Scanner (`scanner.html`) | `./data/scanner_index.json` |
| Detail (`stock.html?id=6789`) | `./data/stocks/${id}.json` |
| Meta 資訊條 | `./data/meta.json` |

**相對路徑**,不是絕對 URL。這樣本機開啟、預覽環境、production 都能用同一份 HTML。

### 11.2 Layer 3.5 前端 patch(未來任務,列出以確認契約)

Scanner v1 目前為 hardcode demo,整合本管線需將:

```javascript
const scannerStocks = [ /* 20 檔 hardcode */ ];
```

改為:

```javascript
const { stocks: scannerStocks, meta } = await fetch('./data/scanner_index.json').then(r => r.json());
```

Detail v2.2 目前為 hardcode demo,整合本管線需將:

```javascript
const stock = { /* 6789 hardcode */ };
```

改為:

```javascript
const params = new URLSearchParams(location.search);
const stockId = params.get('id') || '6789';
const stock = await fetch(`./data/stocks/${stockId}.json`).then(r => r.json());
```

**注意**:上述 patch 屬於 Layer 3.5,本 SPEC 不涵蓋。列出僅為確認契約。

### 11.3 CORS / MIME 注意

GitHub Pages 對 `.json` 檔預設 `Content-Type: application/json`,無 CORS 問題(同域)。無需額外設定。

### 11.4 前端錯誤處理契約

- 404(股票 JSON 不存在)→ 前端顯示「本股票資料尚未建置,請稍後再試」
- Malformed JSON → 前端顯示「資料格式異常」+ console error
- Meta 顯示「資料日期」讀 `meta.last_full_build`,轉台北時區

---

## §12. 歷史回填策略

FinMind 首次抓全市場需 ~10-18 hours 純呼叫時間,遠超 GH Actions 單次 6 hr 上限。

### 12.1 分次執行策略

- 每次 backfill workflow 手動觸發跑 ~5 hr,做完就 commit + push
- 下次觸發時,cache 保留 → 已 fresh 的資料跳過 → 只補剩下的
- 5-7 天內全部完成

### 12.2 進度追蹤

`data/meta.json` 內 `backfill_status`:
- `"incremental"`:daily 模式跑的
- `"complete"`:最後一次 backfill 已跑完所有 targets

前端可讀此值提示使用者「資料建置中,約 X 檔已完成」(v2 議題)。

### 12.3 首次上線建議 SOP

1. Day 0:上傳 code、設 Secret、Pages 啟用
2. Day 1:手動觸發 backfill workflow
3. Day 2-6:每天檢查 workflow 是否失敗,失敗則重跑
4. Day 7:確認 `meta.json` `backfill_status == "complete"`,關 backfill,daily 排程接手

**v1 版本以 `DEMO_UNIVERSE`(20 檔)為限**,無需分次。全市場擴充留給 v2。

---

## §13. 檔案結構

見專案根目錄與 README.md。核心:

```
pipeline/
├── config.py           # 常數 + 對照表 (§2, §5, §7)
├── finmind_client.py   # API wrapper (§5.1, §10.1)
├── ingest.py           # 拉 raw + cache (§6.1, §6.2)
├── transform.py        # 業務公式 (§7)
├── output.py           # 寫 JSON (§8)
├── build.py            # 主 orchestrator (§9)
└── mock_data.py        # 本機測試用 (§16)
```

**約束**:
- 每個檔案 <300 行(不含 mock_data)
- 全部使用 `from __future__ import annotations`
- 無 mutable module-level state(除 config 常數)

---

## §14. 環境變數與 Secrets

| 變數 | 用途 | 設定位置 |
|---|---|---|
| `FINMIND_TOKEN` | FinMind API 認證 | GitHub Secrets(禁進 git) |
| `FINMIND_MOCK` | 本機測試模式(不呼叫真實 API) | 本機 env(GH Actions 不設) |

**檢查方法**:
```bash
# 本機
export FINMIND_TOKEN=xxx        # 或 FINMIND_MOCK=1
python -m pipeline.build --daily

# CI
env | grep FINMIND_TOKEN        # 在 workflow step 內執行, 應該有值
```

---

## §15. 效能與資源估算

### 15.1 儲存需求

| 資料 | 每檔大小 | 20 檔 | 全市場 1800 檔 |
|---|---|---|---|
| Detail JSON | ~1.3 KB | 26 KB | ~2.3 MB |
| Scanner index | — | 4.7 KB | ~420 KB |
| Meta | ~0.2 KB | — | — |
| Raw parquet cache | ~15 KB/檔 | 300 KB | ~27 MB |
| **總計** | | **<400 KB** | **~30 MB** |

**遠低於 GitHub Actions cache 10 GB 上限**、GitHub Pages 1 GB repo 上限、100 GB/月頻寬上限。

### 15.2 Actions 分鐘配額

Public repo:**Actions 分鐘無限**。無配額壓力。

### 15.3 FinMind 呼叫次數

見 §5.6 表。核心結論:
- 20 檔示範清單:daily <10 req/day, backfill 一次跑完 (~60 req)
- 全市場:daily <100 req 平日 / ~1800 req 月報公布日;backfill 需分次

---

## §16. 測試策略

### 16.1 本機 smoke test

```bash
pip install -r requirements.txt
FINMIND_MOCK=1 python -m pipeline.build --backfill
```

預期:
- `data/stocks/` 產出 20 個 JSON
- `data/scanner_index.json` 內 20 檔
- `data/meta.json` `built_ok == 20`, `built_fail == 0`
- 6789.json 的 quarterly 完全對應 v2.2 §18(bit-exact)

### 16.2 Mock 資料保證

`pipeline/mock_data.py` 對 **6789 使用 bit-exact 資料**(直接照抄 v2.2 §18),其餘 19 檔用合成資料(基於 Scanner §18.1 aggregates 反推)。

**注意**:合成資料的 gmQoQ/omQoQ/nmQoQ/vis 與 Scanner §18.1 硬編值有微小差異,原因是 Scanner §18.1 為手工估計,合成資料為數學反推。**Pipeline 產出即為 source of truth**,Scanner §18.1 未來應以 pipeline 產出為準。

### 16.3 Production smoke test(接上 FinMind 之後)

1. 在 workflow 執行前,先手動 `python -m pipeline.build --stock 2330`(台積電)驗證真實 FinMind schema 沒變
2. 檢查 `data/stocks/2330.json` 欄位齊全、金額量級合理
3. 若通過,才啟用 daily cron

---

## §17. Tooltip 對照(繼承前端 SPEC,pipeline 無新增)

Pipeline 不負責 UI tooltip,全部繼承 v2.2 §17 與 Scanner §17。

---

## §18. Demo Universe(照抄)

見 `pipeline/config.py::DEMO_UNIVERSE`:

```python
DEMO_UNIVERSE = [
    "6789", "2451", "3037", "4919", "6488",
    "2618", "2882", "1102", "4576", "4108",
    "5522", "3260", "2308", "2603", "4174",
    "2412", "3711", "5871", "6415", "8046",
]
```

**v1 限縮於此 20 檔**。全市場升級為 v2 議題,屆時 `DEMO_UNIVERSE` 改為讀 `TaiwanStockInfo` 全部,加篩選(排除 F 股、TDR、權證等)。

---

## §19. Manual test procedure

見 §16。

---

## §20. 技術限制

### 20.1 允許的依賴

- Python 3.12
- `pandas>=2.0`(DataFrame 操作)
- `pyarrow>=14.0`(parquet 讀寫)
- `requests>=2.31`(HTTP client)

### 20.2 禁用的依賴

- 任何 ORM(SQLAlchemy 等)
- 任何 async framework(asyncio、httpx async、aiohttp)
- 任何 task queue(Celery、RQ)
- 任何 3rd-party API framework wrapper(finmind 官方 pip 套件也禁,自行 wrap HTTP)
- pytest、black、ruff 等 dev 依賴不打包進 pipeline;若專案根需要可額外裝

### 20.3 GitHub Actions 版本鎖

以下 action 版本固定:
- `actions/checkout@v4`
- `actions/setup-python@v5`
- `actions/cache@v4` + `actions/cache/save@v4`

不使用第三方 auto-commit action,直接 `git commit + push`。

### 20.4 Python style

- 全部 `from __future__ import annotations`
- Type hints 必寫(允許 `Any`, `Optional`,不強制 `TypedDict`)
- 業務常數 UPPER_SNAKE_CASE,函數 lower_snake_case,類 PascalCase
- 不用 `print()` 除錯,一律 `log.info/warning/error`

---

## §21. 交付檢查清單

### 21.1 檔案齊全

- [ ] `pipeline/__init__.py`
- [ ] `pipeline/config.py`
- [ ] `pipeline/finmind_client.py`
- [ ] `pipeline/ingest.py`
- [ ] `pipeline/transform.py`
- [ ] `pipeline/output.py`
- [ ] `pipeline/build.py`
- [ ] `pipeline/mock_data.py`
- [ ] `.github/workflows/daily-build.yml`
- [ ] `.github/workflows/backfill.yml`
- [ ] `requirements.txt`
- [ ] `README.md`
- [ ] `.gitignore`(排除 cache/、__pycache__、venv 等)

### 21.2 本機 smoke test

```bash
pip install -r requirements.txt
FINMIND_MOCK=1 python -m pipeline.build --backfill
```

- [ ] Exit code = 0
- [ ] `data/stocks/*.json` 有 20 個檔
- [ ] `data/scanner_index.json` 存在且 `stocks` 陣列長度 = 20
- [ ] `data/meta.json` 存在且 `built_ok == 20`
- [ ] `data/stocks/6789.json` 的 quarterly 與 v2.2 §18 bit-exact
- [ ] `data/stocks/2308.json` 的 latest quarter `rev == 125000`(觸發前端「億」單位驗證)

### 21.3 Schema 一致性

- [ ] Detail JSON 所有金額欄為純整數(無單位後綴)
- [ ] Scanner index 所有比率欄為小數 1 位 float
- [ ] `hasCL == false` 的股票在 scanner_index 中 `vis == null` 且 `clYoY == null`
- [ ] `monthly` 陣列格式為 `[["YYYY-MM", int], ...]`
- [ ] `q` 格式為 `YYYY/{1-4}Q`

### 21.4 業務邏輯

- [ ] `hasCL` 判定用 `max(cl) / max(rev) > 0.15`(v2.2 §4.2)
- [ ] 健康度**未**在 pipeline 端計算(留給前端)
- [ ] 業務公式全部在 `transform.py`,不散佈於其他檔
- [ ] 產業對照有 fallback `traditional`

### 21.5 效能與安全

- [ ] RateLimiter 硬間隔 7.2 秒
- [ ] retry × 3 with backoff
- [ ] 402 rate limit 特殊處理
- [ ] Token 只讀 env,絕不 hardcode
- [ ] cache/ 加入 .gitignore
- [ ] JSON 輸出為原子寫入(.tmp → rename)

### 21.6 Workflow 正確性

- [ ] daily-build.yml cron 為 `0 14 * * *`
- [ ] daily-build.yml 有 `restore cache` + `save cache`
- [ ] 兩個 workflow 皆 `permissions: contents: write`
- [ ] Secret 名稱為 `FINMIND_TOKEN`

---

## §22. 前次交付審計

**Pipeline v1 為首輪產出,無前次審計紀錄**。列出前端 Layer 3 v2/v2.1/v2.2 的失誤模式作警惕(pipeline 不會直接踩相同雷,但公式與 schema 若不一致將透過前端表現出來):

1. 金額單位在前端每個 cell 重複附加 → pipeline 輸出**純數字**,由前端集中標示
2. 使用 M/K/B/T 顯示金額 → pipeline **絕不做單位轉換**,一律「百萬」
3. UI 出現英文縮寫 → pipeline schema 欄位可英文,但值不含中文標籤污染

Pipeline v1 交付後,若前端遇到欄位不符或值錯誤,將於 v2 迭代寫入本節。

---

## §23. 智能單位切換

**Pipeline 不參與**。單位切換由前端 v2.2 §23 `pickUnit()` + `formatMoney()` 處理。Pipeline 一律輸出「百萬」,由前端根據 max value 決定顯示為「百萬」或「億」。

---

## §24. 仲裁優先序

```
§3(禁止事項)
 > §8(JSON schema 前端契約)
 > §7(業務公式,對齊 v2.2 §7)
 > §2(命名慣例)
 > §5(FinMind datasets)
 > §6(管線流程)
 > §9-12(排程/監控/回填)
 > §13-16(結構/env/資源/測試)
 > §20(技術限制)
 > 其他章節
```

**若 SPEC 內部矛盾**,依上表由高至低仲裁。
**若 SPEC 與 Layer 3 前端 SPEC 矛盾**,以 §8 JSON schema 為契約層,pipeline 讓步、前端不動;但若前端 SPEC 有明確定義的公式或閾值,以前端 SPEC 為準,pipeline 更新對應計算。

---

**規格書結束**
版本 SPEC-pipeline-v1 · 2026-08-22
Reference 實作:本專案 `pipeline/*.py` 全部檔案
下次迭代開新版 SPEC-pipeline-v2 時,以本文為 baseline。
