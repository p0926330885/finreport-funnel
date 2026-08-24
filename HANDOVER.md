# HANDOVER · 系統交接手冊

> **給接手者(人類或 AI)的 1 分鐘導讀**
>
> 這是一個台股基本面分析網站,涵蓋約 1,700 檔上市/上櫃/KY 普通股,每天自動更新。
> 純靜態網站(GitHub Pages)+ Python pipeline(GitHub Actions 每日排程)。
> 讀完本文你會知道:資料流、股票池規則、JSON 契約、特殊產業處理、排程機制、除錯路徑。

**版本**:v3.4 · Phase 2 全市場擴充  
**最後更新**:2026-08

---

## 1. 專案定位與線上網址

### 1-1 · 一句話定義

台股基本面分析工具,從財報 + 月營收兩個資料源,產出「訂單能見度、獲利品質、動能訊號」三大分析視角,供選股與追蹤使用。

### 1-2 · Repo

- **GitHub Repo**:https://github.com/p0926330885/finreport-funnel
- **主分支**:`main`
- **上傳方式**:GitHub Web Editor (使用者不用 CLI/git)

### 1-3 · 線上網址(GitHub Pages)

| URL | 用途 |
|---|---|
| https://p0926330885.github.io/finreport-funnel/ | 首頁 → 導向 scanner.html |
| https://p0926330885.github.io/finreport-funnel/scanner.html | 選股掃描 |
| https://p0926330885.github.io/finreport-funnel/stock.html?id=2330 | 個股詳細(以台積電為例)|
| https://p0926330885.github.io/finreport-funnel/help.html | 使用說明頁 |

### 1-4 · 資料儲存

Pipeline 產出的 JSON 檔案存放在 repo 的 `data/` 目錄下,由 GitHub Pages 直接靜態服務(無 backend server)。

---

## 2. 架構全景與資料流

### 2-1 · 資料流圖

```
                    ┌─────────────────────────┐
                    │   FinMind Taiwan API    │
                    │  (公開財報 + 月營收)     │
                    └──────────┬──────────────┘
                               │
                               │ Rate limit: 600 req/hr (免費層)
                               │ Retry 3 次 · Backoff 5s
                               ↓
                    ┌─────────────────────────┐
                    │ pipeline/ingest.py      │
                    │ - fetch_universe        │  ← 全市場清單 (30天 cache)
                    │ - fetch_fs / bs / rev   │  ← 每檔股票 (45/7 天 cache)
                    │ 快取: cache/raw/*.parquet│
                    └──────────┬──────────────┘
                               │
                               ↓
                    ┌─────────────────────────┐
                    │ pipeline/universe.py    │  ← v3.4 Phase 2
                    │ - is_common_stock       │
                    │ - build_universe        │
                    │ - assign_batches (7 批) │
                    └──────────┬──────────────┘
                               │
                               ↓
                    ┌─────────────────────────┐
                    │ pipeline/transform.py   │  ← 核心邏輯
                    │ - _fs_pivot / _bs_pivot │
                    │ - build_detail          │  → data/stocks/{id}.json
                    │ - build_scanner_row     │  → data/scanner_index.json
                    │ - insights_v3 (s01-s05) │
                    └──────────┬──────────────┘
                               │
                               ↓
                    ┌─────────────────────────┐
                    │ data/ (JSON files)      │
                    │ - stocks/{id}.json      │  x 1,700 檔
                    │ - scanner_index.json    │  彙整表
                    │ - meta.json             │  更新狀態
                    └──────────┬──────────────┘
                               │
                               │ Static serve (GitHub Pages)
                               ↓
                    ┌─────────────────────────┐
                    │ Frontend (Vanilla JS)   │
                    │ - scanner.html          │  ← 全市場篩選 UI
                    │ - stock.html            │  ← 個股詳細 UI
                    │ - help.html             │  ← 使用說明
                    └─────────────────────────┘
```

### 2-2 · 檔案結構

```
finreport-funnel/
├── .github/workflows/
│   ├── backfill.yml               # 手動 workflow_dispatch (dev/test)
│   ├── backfill-scheduled.yml     # v3.4 Phase 2: 每日 03:30 TPE 7 批接力
│   └── daily-build.yml            # 日常增量 22:00 TPE
├── pipeline/
│   ├── build.py                   # 主入口 (--daily / --backfill / --batch N)
│   ├── config.py                  # 常數 + INDUSTRY_MAP + BATCH_COUNT
│   ├── universe.py                # v3.4 Phase 2: 股票池過濾模組
│   ├── ingest.py                  # FinMind API 抓取 + 快取
│   ├── transform.py               # JSON 產出核心 (~1,000 行)
│   ├── finmind_client.py          # API client + rate limit
│   ├── output.py                  # JSON 寫入
│   └── mock_data.py               # 測試用 mock 資料
├── data/
│   ├── stocks/{id}.json           # 個股詳細 (每檔約 30 KB)
│   ├── scanner_index.json         # 選股彙整表 (全市場 ~500 KB)
│   └── meta.json                  # 更新狀態
├── cache/raw/                     # FinMind 原始 parquet 快取 (不 commit)
├── docs/
│   ├── SPEC-*.md                  # 各版本規格文件
│   └── HANDOVER.md                # 本文件
├── stock.html                     # 前端 · 個股詳細 (2,700 行)
├── scanner.html                   # 前端 · 選股掃描 (1,100 行)
├── help.html                      # 前端 · 使用說明
└── requirements.txt               # Python 依賴
```

### 2-3 · 前端資料契約

前端**沒有** backend API,直接 fetch 靜態 JSON:

```javascript
// scanner.html
fetch('./data/scanner_index.json')   // 全市場彙整表
  → 篩選 + 排序 UI

// stock.html?id=2330
fetch('./data/stocks/2330.json')     // 該檔詳細
  → 漏斗 + 自動判讀 + 成長率等 UI

// stock.html 中文搜尋
fetch('./data/scanner_index.json')   // 為了搜尋 dropdown 顯示全市場清單
```

---

## 3. 股票池過濾規範(Universe Filter)

### 3-1 · 收錄範圍(約 1,700-1,800 檔)

| 類型 | 判定規則 |
|---|---|
| **TWSE 上市普通股** | FinMind `type == 'twse'` + `stock_id` 4 位數字 |
| **OTC 上櫃普通股** | FinMind `type == 'tpex'` + `stock_id` 4 位數字 |
| **KY / F 股** | 上述 + `stock_name` 通常含 `-KY` 或 `-KYSTAR` 後綴 |

### 3-2 · 排除規則(核心邏輯在 `pipeline/universe.py::is_common_stock`)

```python
def is_common_stock(stock_id, industry_category, stock_name, stock_type):
    # 規則 1: stock_id 必須是 4 位純數字
    if not re.match(r'^\d{4}$', stock_id): return False
    
    # 規則 2: 排除 ID 前綴
    if stock_id.startswith(('00',)):    return False  # ETF (0050, 006208)
    if stock_id.startswith(('91','92')): return False  # TDR
    
    # 規則 3: 產業分類黑名單
    if 'ETF' in industry.upper():        return False
    if '存託' in industry:               return False
    
    # 規則 4: 產業分類必須有值
    if not industry:                     return False
    
    # 規則 5: 市場別必須 twse 或 tpex (排除興櫃 emerging)
    if market and market not in ('twse','tpex'): return False
    
    return True
```

### 3-3 · 被排除的類型清單

| 類型 | 例子 | 為什麼排除 |
|---|---|---|
| ETF | 0050, 006208, 00929 | 投資組合工具,無單一公司財報 |
| TDR | 91xx / 92xx | 海外公司二次上市,揭露不完整 |
| 特別股 | 2891A(中信金甲特) | stock_id 4 位後有字母,非普通股 |
| 認購/售權證 | 5-6 位數字 | 衍生金融商品 |
| 可轉債 | 尾綴 CB | 非普通股 |
| 興櫃股 | FinMind `type == 'emerging'` | 揭露義務較弱,資料不穩定 |

---

## 4. 資料契約(Data Contracts)

### 4-1 · 個股 JSON · `data/stocks/{stock_id}.json`

```typescript
{
  // === 基本資料 ===
  "id": "2330",
  "name": "台積電",
  "industry": "semi" | "component" | "integration" | "chemical" 
            | "biotech" | "machinery" | "construction" | "utility"
            | "traditional" | "finance",
  "market": "twse" | "tpex",
  
  // === 特殊產業 flag ===
  "hasCL": boolean,   // 是否有合約負債 (max_cl/max_rev > 0.15) 
                      // ⚠️ 金融股 (industry=='finance') 被強制設為 false
  
  // === 季度資料 (最近 20 季 · 5 年) ===
  "quarterly": [
    {
      "q": "2026/2Q",              // 季度標籤
      "rev": 12703800,             // 營業收入 (單位: 千元)
      "gp": 8603110,               // 營業毛利
      "op": 7666030,               // 營業利益
      "np": 7067810,               // 稅後淨利
      "noi": -598220,              // 業外損益淨額
      "eps": 27.25,                // EPS
      "cl": 12345,                 // 合約負債期末 (千元, 若無揭露則 0)
      "capitalStock": 15000000,    // v3.4: 普通股股本 (千元)
      "clRatio": 78.9,             // v3.4: cl/capitalStock % (若股本=0 為 null)
      "revYoY": 36.0,              // 營收 YoY %
      "revQoQ": 8.5,               // 營收 QoQ %
      "gmYoY": 2.3,                // 毛利率 YoY 變化 (pp)
      "omYoY": 3.1,                // 營益率 YoY 變化 (pp)
      "nmYoY": 4.5,                // 淨利率 YoY 變化 (pp)
      "clYoY": 32.8,               // 合約負債 YoY %
      "clQoQ": 2.8,                // 合約負債 QoQ %
      "gm": 67.7,                  // 毛利率 %
      "om": 60.3,                  // 營益率 %
      "nm": 55.6,                  // 淨利率 %
      "noiRatio": 7.8,             // 業外占淨利 %
      "revCumYoY": 28.5,           // 累計營收 YoY (至該季)
    },
    ... // 共 20 筆 (2021Q3 ~ 2026Q2)
  ],
  
  // === 月營收 (最近 60 個月 · 5 年) ===
  "monthly": [
    ["2021-09", 156000],           // [YYYY-MM, revenue in 千元]
    ...
    ["2026-08", 289000]
  ],
  
  // === 自動判讀 (4 或 5 條) ===
  "insights": [
    {
      "id": "s01",                 // s01 主判讀 / s02-s04 常規 / s05 訂單能見度 (僅 hasCL)
      "kind": "primary" | "supporting",
      "tone": "mint" | "amber" | "coral" | "text-dim",
      "mode_code": "M01" | ... | null,   // 10 種模式代號
      "mode_name": "本業強勁" | ... | null,
      "text": "本季營收... EPS 27.25 元。"
    },
    ...
  ]
}
```

### 4-2 · Scanner Index · `data/scanner_index.json`

```typescript
{
  "current_quarter": "2026/2Q",
  "generated_at": "2026-08-24T03:45:12Z",
  "stocks": [
    {
      "id": "2330",
      "name": "台積電",
      "industry": "semi",
      "market": "twse",
      "rev": 12703800,
      "revYoY": 36.0,
      "gm": 67.7,
      "om": 60.3,
      "nm": 55.6,
      "noiRatio": 7.8,
      "clYoY": 0,          // 無合約負債時為 0
      "gmQoQ": 2.3,        // 給「三率同升」模板判定
      "omQoQ": 3.1,
      "nmQoQ": 4.5,
      "hasCL": false,      // 前端根據此決定顯示訂單能見度區塊
      "gc": true,          // 3MA vs 12MA 近 1 月黃金交叉
      // score 由前端 healthScore() 動態計算,不寫入 JSON
    },
    ... // 全市場 ~1,700 筆
  ]
}
```

### 4-3 · Meta · `data/meta.json`

```typescript
{
  "universe_size": 1724,       // FinMind 回傳總股數 (含 ETF/TDR 等)
  "targets": 246,              // 本次執行處理的檔數
  "target_desc": "batch 3/7 (246/1724 stocks)",
  "built_ok": 245,
  "built_fail": 1,
  "scanner_size": 1720,        // scanner_index.json 中的總股數 (累積結果)
  "backfill_status": "complete",
  "data_freshness": {
    "quarterly": "2026/2Q",
    "monthly": "2026-08"
  },
  "last_batch": 3,             // Phase 2: 最後跑過的 batch
  "batch_count": 7             // Phase 2: 總批數
}
```

---

## 5. 特殊產業處理機制

### 5-1 · 金融業(強制 `hasCL = false`)

**代表**:2882 國泰金、2891 中信金、2884 玉山金

**核心邏輯**(`pipeline/transform.py` build_detail 內):
```python
# 一般判定 hasCL 邏輯
has_cl = (max_cl / max_rev) > HAS_CL_THRESHOLD  # 0.15

# 金融股防呆 (config.INDUSTRY_FORCE_NO_CL)
if industry == "finance":
    has_cl = False
```

**為什麼**:
- 金融資產不適用 IFRS 15 收入認列準則
- 銀行/保險/證券的「合約負債」是保險合約準備金,和訂單池概念完全不同
- 若不強制排除,國泰金的 CL/rev 會達 100x 以上,產生荒謬的能見度數字

**前端連鎖反應**(`stock.html`):
- 訂單能見度區塊 hidden
- 「合約負債明細」tab hidden
- 自動判讀 s05 不出現
- 漏斗頂部顯示金融業備註提示

### 5-2 · 建材營造業(顯示備註但不強制)

**代表**:2597 潤弘、5522 遠雄、2542 興富發

**設計選擇**:
- **不強制** hasCL=false(因為合約負債是他們主要營收指標)
- **但顯示備註**告訴使用者「佔股本比可能達 500%+ 屬正常」

**核心邏輯**(前端 `stock.html`):
```javascript
const industryNotes = {
  finance:      { title: '...', text: '金融業損益結構為...' },
  construction: { title: '...', text: '採完工比例法認列營收...' },
};
if (industryNotes[stock.industry]) {
  // 顯示黃色 amber 提示框
}
```

### 5-3 · 未來擴充新產業

若要加新特殊產業(如公用事業、生技新藥):

1. `pipeline/config.py` INDUSTRY_MAP 加映射:  
   `"油電燃氣業": "utility"`

2. 若要**強制 hasCL=false**,加到 `INDUSTRY_FORCE_NO_CL` set

3. 前端 `stock.html` industryNotes 加對應項:
   ```javascript
   utility: {
     title: '公用事業備註',
     text: '公用事業毛利率通常受法定電價/水價限制...'
   }
   ```

不需要改 pipeline 主邏輯,不需要 backfill(如果只是加 note)。

---

## 6. 自動化排程與維護手冊

### 6-1 · 三個 GitHub Actions Workflow

| Workflow | 觸發 | 用途 | 耗時 |
|---|---|---|---|
| `backfill.yml` | 手動 workflow_dispatch | dev / 緊急修復 | 8 分鐘 (20 檔) |
| `backfill-scheduled.yml` | Cron `30 19 * * *` (UTC) = 03:30 TPE | Phase 2: 7 批接力,每天跑一批 | 90-120 分鐘/批 |
| `daily-build.yml` | Cron `0 14 * * *` (UTC) = 22:00 TPE | 日常增量更新(cache 生效) | 30-60 分鐘 |

### 6-2 · 7 批接力機制

Batch ID 計算(`backfill-scheduled.yml` 內):
```bash
BATCH=$(TZ=Asia/Taipei date +%j)   # day-of-year (1-366)
BATCH=$((10#$BATCH % 7))            # 0-6
```

- Day 1  → batch 1
- Day 2  → batch 2
- Day 8  → batch 1(下週同一批)
- 一週跑完全部 1,700 檔,週而復始

**手動觸發指定 batch**(緊急修某批):  
GitHub Actions → Backfill (Scheduled 7-Batch) → Run workflow → 輸入 batch_id (0-6)

### 6-3 · Scanner Index 累積機制

7 批獨立跑,但共用同一份 `scanner_index.json`。

**Upsert 邏輯**(`build.py` `_merge_scanner_index`):
```python
# 讀取現有 scanner_index.json → dict by id
# 本批新產出的 rows → 覆蓋同 id 的舊 row
# 未處理的 id 保留不動
# 寫回 merged 結果
```

這樣 7 天後,`scanner_index.json` 累積為完整全市場 1,700 檔資料。

### 6-4 · API Rate Limit 對策

**FinMind 免費層**:600 req/hr = 每 6 秒 1 req  
**config.py 設定**:`RATE_LIMIT_PER_HOUR = 500`(保守值,實際 7.2s/req)

**每檔股票需要 3 個 API request**(fs + bs + revenue,universe 是全局一次):
- 每檔耗時 = 3 × 7.2s = ~22s
- 一批 250 檔耗時 = 250 × 22s = **5,500s = 92 分鐘**

**GitHub Actions 免費層限制**:每次執行最多 6 小時 → 92 分鐘遠低於限制。

**Rate Limit 觸發時的處理**(`finmind_client.py`):
- 收到 402/429 → sleep RETRY_BACKOFF_SECONDS × attempt(exponential backoff)
- 重試最多 MAX_RETRIES = 3 次
- 全失敗 → log error 跳過該檔,不 abort

### 6-5 · 除錯指南

#### 問題:GitHub Actions 執行失敗

1. 打開 https://github.com/p0926330885/finreport-funnel/actions
2. 點失敗的 run → 展開失敗 step 看 log
3. 常見錯誤:

| 錯誤訊息 | 原因 | 解法 |
|---|---|---|
| `FinMind API 402` | Free tier 用完 | 隔天再跑,或升級 sponsor tier |
| `Empty universe` | FinMind API 異常 | 手動重試 workflow |
| `Failed to build XXXX` | 某檔資料異常 | 看 transform.py log 內指定 stock_id |
| `git push failed` | 權限或 conflict | 檢查 workflow permissions: contents: write |

#### 問題:某檔股票資料不對

1. 檢查 `data/stocks/{id}.json` 是否存在
2. 若不存在 → 該檔可能被 universe filter 排除(檢查 stock_id 格式)
3. 若存在但數字異常 → 手動觸發 `backfill.yml` --stock 該檔重跑
4. 若持續異常 → 看 FinMind API 原始資料
   `cache/raw/TaiwanStockFinancialStatements/{id}.parquet`

#### 問題:金融股沒有正確隱藏 CL 區塊

1. 檢查 `data/stocks/{id}.json` 內 `industry` 是否為 `"finance"`
2. 檢查 `hasCL` 是否為 `false`
3. 若 industry 錯誤 → 修 `config.py` INDUSTRY_MAP 映射
4. 若 hasCL 為 true → 檢查 `transform.py` build_detail 內 `INDUSTRY_FORCE_NO_CL` 邏輯

#### 問題:前端不顯示新資料

1. 檢查 `data/meta.json` 的 `data_freshness` 是否為最新
2. Ctrl+Shift+R 強制清 cache 重載
3. 開發者工具 → Network → 看 `stocks/xxxx.json` 是否 304 (cache hit) 或 200 (fresh)
4. 若持續舊資料 → GitHub Pages 部署延遲(通常 <5 分鐘)

### 6-6 · Cache 管理

**兩層 cache**:

1. **本地 parquet cache**(`cache/raw/`):
   - Ingest 抓完後存 parquet
   - Cache 檢查 (`ingest.py::_is_cache_fresh`):
     - Universe: 30 天
     - FS/BS: 45 天(季報間距)
     - Revenue: 7 天(月營收 10 號公布)

2. **GitHub Actions cache**(`actions/cache@v4`):
   - Key: `pipeline-batch-{N}-{run_id}`
   - Restore fallback: `pipeline-batch-{N}-` → `pipeline-backfill-` → `pipeline-raw-`
   - 允許跨 run 共用,加速接連的 batch 執行

**清除 cache**:
- GitHub Repo → Actions → Caches → 手動刪除
- 或改 `USE_FULL_UNIVERSE = False` 走 DEMO 20 檔測試

### 6-7 · 更新 Python 依賴

若 FinMind 升級或 pandas 有新版本:
1. 修 `requirements.txt`
2. Push → 下次 workflow 執行時自動 pip install
3. 若有 breaking change → 先手動觸發 `backfill.yml` 測試

---

## 7. 版本歷史與關鍵決策

### 7-1 · 版本歷史

| 版本 | 日期 | 主要改動 |
|---|---|---|
| v3.0 | 2026-08 | 20 季 + 60 月資料擴充,選股掃描介面 |
| v3.1 | 2026-08 | 手機 UI + 雙 range slider |
| v3.2 | 2026-08 | 自動判讀 v3(10 模式)|
| v3.3 | 2026-08 | CurrentContractLiabilities 抓取修正 · CL tab 隱藏 bug 修 |
| v3.4 | 2026-08 | **Phase 2**: CL 佔股本比 · 通用產業備註 · 策略模板複選 · 說明頁 · **全市場 1,700 檔擴充** |

### 7-2 · 關鍵設計決策

**為什麼用 GitHub Pages + Static JSON 而非後端 API?**
- 不用 server = 不用維護費
- 資料量 ~50 MB 靜態,GitHub Pages 完全能承載
- 前端無 auth 需求,適合公開工具

**為什麼是 7 批而不是每天全跑?**
- FinMind 免費層 600 req/hr,一批 250 檔約 90 分鐘
- 若全 1,700 檔一次跑 = 10+ 小時,超出 GitHub Actions 6 小時限制
- 7 批 × 每批 90 分 = 一週跑完,合理節奏

**為什麼「訂單能見度」指標拿掉?**
- 統計驗證失真:分佈受營收規模扭曲
- 3037 欣興 CL 佔股本 79% 但能見度只有 0.8 月,和使用者體感衝突
- 改用「佔股本比」+「佔季營收比」兩個相對指標更公平

**為什麼金融股要強制 `hasCL=false`?**
- 金融資產不適用 IFRS 15
- 未強制時前端會顯示 CL 71,359 億 / rev 725 億 = 98 倍的荒謬數字
- 詳見 §5-1

### 7-3 · 部署歷程

實際部署到 production 的時間點記錄,和 §7-1(特性完成日)分開追蹤,方便 debug 時精準定位「什麼時候切換到 XX 行為」。

| 日期 (TPE) | 事件 | 對應 commit / run |
|---|---|---|
| 2026-08 早期 | v3.4 Phase 2 靜態上料:`pipeline/universe.py` 新增、`pipeline/config.py` 加入 Phase 2 常數(`USE_FULL_UNIVERSE=True`、`BATCH_COUNT=7`、`SCANNER_INDEX_PATH`、`DEMO_UNIVERSE`、`INDUSTRY_FORCE_NO_CL={"finance"}`、`INDUSTRY_NOTED={"finance","construction"}`)。但 `build.py` 還是舊版、不認新常數,實際 pipeline 仍走 DEMO 20 檔 | — |
| 2026-08-24 | **Phase 2 排程啟用**:一次到位 3 個變更 —— `pipeline/build.py` 覆蓋為 Phase 2 版本(支援 `--batch N` + `_merge_scanner_index` + `USE_FULL_UNIVERSE` 分支)、`daily-build.yml` cron 改為 22:00 TPE(避開 03:30)、新增 `backfill-scheduled.yml` 觸發 7 批接力排程 | 本 commit + 前一筆 `Add files via upload`(2 個 YAML 一次上傳) |
| 2026-08-25 22:00 | 首次 Phase 2 版 daily-build 執行(見下方過渡狀態) | 見 GitHub Actions |
| 2026-08-26 03:30 | 首次 scheduled-backfill batch 執行 · batch = 238 % 7 = **0**(stocks[0:243]) | 見 GitHub Actions |
| 2026-09-01 03:30 | 最後一批(batch 6)執行 · `scanner_index.json` 累積為完整全市場 ~1,700 檔 | 見 GitHub Actions |

**⚠️ 已知過渡狀態(2026-08-25 ~ 08-31 · 約 6-7 天)**:

`daily-build.yml` 每晚 22:00 會嘗試處理全部 ~1,700 檔(因為 `USE_FULL_UNIVERSE=True` 且未帶 `--batch`)。FinMind 免費層 500 req/hr × 每檔 3 request → 90 分鐘上限 = ~245 檔 API 呼叫 → **cache 覆蓋率必須 > 85% (~1,455 檔)daily-build 才可能在 90 分內完成**。快取由 `backfill-scheduled.yml` 每天 03:30 分批填充(每批 ~243 檔 · 7 天填滿)+ daily-build 每晚失敗前累積的 partial cache 共同貢獻。

**預估時程**:

- 2026-08-25 ~ 08-29(前 5 天):daily-build **幾乎必然 timeout**,不會 commit 任何資料
- 2026-08-30 ~ 09-01(第 6-8 天):cache 覆蓋接近門檻,daily-build **有機會首次成功**
- 2026-09-02 之後:cache 全滿,daily-build 應穩定成功(< 30 分鐘完成)

首週 daily-build failure **是預期行為,不用手動介入**。若 2026-09-03 之後 daily-build 仍持續 timeout,才需要 debug。

**加速選項(選用,不做也沒差)**:若不想看 6-7 天紅色 X,可在 `pipeline/config.py` 臨時把 `USE_FULL_UNIVERSE = False`,daily-build 會 fallback 到 `DEMO_UNIVERSE`(20 檔),立刻可用。等 backfill 跑完 7 批後(2026-09-02)再切回 `True`。

---

---

## 8. AI 接手 checklist(新 AI 開始工作前先確認)

- [ ] 讀完本 HANDOVER.md 前 3 節(架構、URL、Universe 規則)
- [ ] 打開一次 https://p0926330885.github.io/finreport-funnel/scanner.html 確認網站正常
- [ ] 打開一次 https://p0926330885.github.io/finreport-funnel/stock.html?id=2330 看 UI
- [ ] `curl -sL https://raw.githubusercontent.com/p0926330885/finreport-funnel/main/data/meta.json` 看資料是否最新
- [ ] 確認 GitHub Actions 最後幾次執行是否成功
- [ ] 若使用者要求改前端 → 只動 `stock.html` / `scanner.html` / `help.html`,不用 backfill
- [ ] 若使用者要求改 pipeline → 動 `pipeline/*.py`,需要觸發 backfill 才能看到新資料
- [ ] 若使用者要求改資料契約 → 需要同步改 pipeline **和** 前端 render 邏輯

---

## 9. 聯絡與貢獻

**專案擁有者**:@p0926330885(GitHub)

**貢獻方式**:
- 使用者為非開發者,只用 GitHub Web Editor 上傳檔案
- 討論 / 規劃透過 AI 對話進行
- 所有改動都需要保持向後兼容(現有 JSON 契約不能破壞)

**授權**:此專案為個人工具,無公開授權。

---

**End of HANDOVER.md** · 讀完你已經是這個專案的 90% 專家 · 剩下 10% 邊做邊學
