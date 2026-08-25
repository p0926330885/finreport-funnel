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
      "clRatio": 78.9,     // v3.4 P1 · 合約負債佔股本比 % · 給 clRatio slider + 動態欄位使用 · 無 CL 時為 null
      "opYoY": 340.7,      // v3.4 P2 · 營業利益 YoY % · 給「營運槓桿釋放」模板 + 動態欄位「營益 YoY」使用
      "gmYoY": 11.7,       // v3.4 P2 · 毛利率 YoY 差值 (pp) · 給「營運槓桿釋放」模板判定
      // score 由前端 healthScore() 動態計算,不寫入 JSON
    },
    ... // 全市場 ~1,700 筆
  ]
}
```

**⚠️ v3.4 三個新欄位覆蓋率(隨 backfill 進度變化)**:
- `clRatio`: 只在 `hasCL=true` 時有值 · 全市場約 25-30% 有值 · 個股 JSON `quarterly[-1].clRatio` 抽取
- `opYoY`: 大部分股票有值(需要 4 季以上歷史)· 全市場約 95-98% 有值 · 個股 JSON `quarterly[-1].opYoY` 抽取
- `gmYoY`: 同上 · 需 `build_scanner_row` 內即時計算(當季 gm - 去年同期 gm 的 pp 差值)· 全市場約 95-98% 有值

**新上市 < 1 年的股票**(如 7835 永悅健康-創、8497 格威傳媒):`opYoY` 和 `gmYoY` 會是 null(缺去年同期資料),不算 bug。P2「營運槓桿釋放」模板已加 null 嚴格排除。

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
| `backfill.yml` | 手動 workflow_dispatch | dev / 指定股票 rebuild · 支援 `stocks` 逗號分隔輸入(cache hit) | 5-10 分鐘 (20 檔 cache hit) |
| `backfill-scheduled.yml` | Cron `30 19 * * *` (UTC) = 03:30 TPE | Phase 2: 7 批接力,每天跑一批 | 90-120 分鐘/批 |
| `daily-build.yml` | Cron `0 14 * * *` (UTC) = 22:00 TPE | 日常增量更新(cache 生效) | 30-60 分鐘 |

**`backfill.yml` v3.4 升級用法**(2026-08-25):

```
# stocks input 有值 → 走 --daily --stock (cache hit,秒完成,不吃 API)
   輸入範例:2330,2454,3037   → 只跑這 3 檔

# stocks input 留空 → 走 --backfill (全市場強制刷新,90 分可能 timeout)

# 已修:build.py --stock 也走 _merge_scanner_index upsert
#       (之前 --stock 20 檔會把 index 從 312 縮成 20 檔,現在會 upsert 保留其他股票)
```

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
| v3.4 | 2026-08 | **Phase 2 + P0+P1+P2 + 月營收動能 truth table**:全市場 1,700 檔擴充 · P0 表格固定 8 欄 + 動態 4 欄機制 · P1 clRatio slider (0-200%) · P2 「營運槓桿釋放」策略模板(第 5 個 pill,含 opYoY 動態欄自動排序)· 月營收動能圖交叉點 truth table 邏輯 · 手機端 header + 2x2 grid + footer chip 升級 · Backfill workflow 工具化(stocks 參數)|

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

**為什麼 scanner 表格改成「固定 8 欄 + 動態 4 欄」機制?(v3.4 P0)**
- 舊版所有欄位固定顯示,「調了 slider 卻看不到對應數據」的痛點
- 固定 8 欄(股號/名稱/產業/當季營收/營收 YoY/毛利率/營益率/健康度總分)= 體質輪廓
- 動態候選 5 欄(淨利率 / CL YoY / CL/股本比 / 動能狀態 / 營益 YoY)依 slider 或模板觸發右側自動展開
- 欄位去重:模板 + slider 同時觸發同一欄只渲染 1 欄
- 手機版同步升級為 header + 2x2 grid + footer dynamic chip 結構

**為什麼 clRatio slider 上限 200% 代表「≥200%」而不是絕對上限?(v3.4 P1)**
- 建材/設備/工程業合約負債佔股本比常見 300-500%(如某些建設股)
- 若寫死 500% 為上限,slider 拖曳時 UI 難用(拖 100% 只走 20% 距離)
- 折衷:0-200% 涵蓋 95% 標的 · 上限 = 200 特殊解讀為「≥200 無上限」
- 下限 > 0% 時隱含排除 hasCL=false(避免無 CL 標的當作 0% 命中)

**為什麼「營運槓桿釋放」模板無定值門檻?(v3.4 P2)**
- 純粹狀態判定:revYoY > 0 · 且 opYoY > revYoY · 且 gmYoY ≥ 0(pp)· 且當季 op > 0
- 強度差別交給 sidebar slider 主動加強(想找爆發最猛的自己拉「營收 YoY > 30」)
- 點模板時自動用 opYoY 降序排序,強者浮到最前(P2 補強)
- 動態展開「營益 YoY (%)」欄讓使用者一眼比較強度

**為什麼月營收動能圖交叉點用 truth table 動態選對?(v3.4)**
- 舊邏輯:交叉點寫死在 3MA vs 12MA
- 使用者關掉 12MA 只留 3MA,3MA 上還有孤立圓點在跳 → 誤導「3MA 自己金叉自己」
- 新邏輯 truth table:
  - 3=off → 不畫(3MA 是基準,缺 3 就沒交叉可談)
  - 3=on, 6=off, 12=off → 不畫(單條無對照)
  - 3=on, 6=off, 12=on → 3 vs 12(標準長期動能對照)
  - 3=on, 6=on, 12=off → 3 vs 6(短期節奏)
  - 3=on, 6=on, 12=on → 只畫 3 vs 12(12 優先,避免視覺重疊)
- 預設 `{ 3: true, 6: false, 12: true }`(短長對照最標準)
- 圓點視覺:半徑 3.5px + 深色邊框(#0d0f14) 1.5px + hover 放大到 8px

### 7-3 · 部署歷程

實際部署到 production 的時間點記錄,和 §7-1(特性完成日)分開追蹤,方便 debug 時精準定位「什麼時候切換到 XX 行為」。

| 日期 (TPE) | 事件 | 對應 commit / run |
|---|---|---|
| 2026-08 早期 | v3.4 Phase 2 靜態上料:`pipeline/universe.py` 新增、`pipeline/config.py` 加入 Phase 2 常數(`USE_FULL_UNIVERSE=True`、`BATCH_COUNT=7`、`SCANNER_INDEX_PATH`、`DEMO_UNIVERSE`、`INDUSTRY_FORCE_NO_CL={"finance"}`、`INDUSTRY_NOTED={"finance","construction"}`)。但 `build.py` 還是舊版、不認新常數,實際 pipeline 仍走 DEMO 20 檔 | — |
| 2026-08-24 | **Phase 2 排程啟用**:一次到位 3 個變更 —— `pipeline/build.py` 覆蓋為 Phase 2 版本(支援 `--batch N` + `_merge_scanner_index` + `USE_FULL_UNIVERSE` 分支)、`daily-build.yml` cron 改為 22:00 TPE(避開 03:30)、新增 `backfill-scheduled.yml` 觸發 7 批接力排程 | 本 commit + 前一筆 `Add files via upload`(2 個 YAML 一次上傳) |
| 2026-08-25 22:00 | 首次 Phase 2 版 daily-build 執行(見下方過渡狀態) | 見 GitHub Actions |
| 2026-08-26 03:30 | 首次 scheduled-backfill batch 執行 · batch = 238 % 7 = **0**(stocks[0:243]) | 見 GitHub Actions |
| 2026-09-01 03:30 | 最後一批(batch 6)執行 · `scanner_index.json` 累積為完整全市場 ~1,700 檔 | 見 GitHub Actions |

### 7-4 · 已知邊界情境

未來 debug 時常遇到的「非 bug 但看似奇怪」的狀況,先整理避免重複挖坑:

**新上市未滿 1 年的股票缺 opYoY / gmYoY**
- 症狀:某些股票(如 7835 永悅健康-創、8497 格威傳媒)在 scanner_index 內 `opYoY: null` `gmYoY: null`
- 原因:需要「去年同期」對照,新上市未滿 1 年沒歷史資料
- 影響:P2 營運槓桿釋放模板永遠排除這些股(rules 有 null 嚴格排除,正確行為)
- 不用修

**scheduled-backfill fail=7 是預期**
- Batch 6 內約有 7 檔股票(集中在興櫃或剛下市股)API 抓不到完整資料
- Build.py 已修 return 0,fail=7 不會導致整批 skip commit
- 若某天 fail 突然變 50+,才需要追(可能是 FinMind API 大範圍故障)

**scanner_index 有時包含舊格式股票**
- 當 backfill 只跑部分 batch,現有 scanner_index 內未被覆蓋的股票會保留舊格式欄位
- 例如 DEMO 20 檔可能沒 `clRatio / opYoY / gmYoY`(如果只跑 batch 6 沒跑 batch 0)
- P1 P2 filter 會排除這些「缺欄位」的股票,顯示上表格欄位是「—」
- 隨著 7 批全跑完,所有股票逐漸統一新格式

**backdrop-filter 在 iOS Safari 特殊 bug**
- `.topbar` 有 `backdrop-filter: blur()` + `position: sticky` 時,iOS Safari 會把 backdrop rendering surface 擴展到整個 viewport,截斷下方 fixed 元素(即使 z-index:10000)
- 症狀:手機端個股頁搜尋 dropdown 被下方卡片遮住
- 修法:mobile @media 內 `.topbar` 加 `backdrop-filter: none` + 純不透明背景
- 桌面版保留 backdrop-filter 效果不變

**手機端 daily-build.yml 首週 timeout 是預期**
- 首週 5-7 天 cache 沒滿,daily-build 22:00 必然 timeout
- Timeout 時不 commit,不影響資料完整性
- 明細見 §7-3 過渡狀態說明

**新股 monthly 資料不足以算 12MA(如三商餐飲 7705)**
- 症狀:某股票 monthly 資料只有 23 個月(如三商 2024-10 才有 monthly),12MA 無法從最左側開始畫
- 原因:興櫃時期不強制公布月營收,轉上市前 1 個月才開始有 monthly · 這是資料源本質,非 bug
- 財務嚴謹處理原則(v3.4 定案):
  - `movingAverage` 函式前 N-1 位置嚴格 return null,不 padding 假值
  - `crosses` 函式雙線都需有值才判定,不誤產交叉點
  - `ma-toggle` 按鈕當 `series.length < length` 時 disable + tooltip 顯示「資料不足 · 需 N 個月」
  - `chart-annot` 內加 `data-range-chip`,當 `series.length < 60` 時顯示「📅 資料範圍:YYYY-MM ~ YYYY-MM(N 個月)」讓使用者立刻理解
- 相關檔案:`stock.html` 內的 `renderMaChart()`, `movingAverage()`, `crosses()`
- 案例:7705 三商餐飲(2023-12 登錄興櫃,2024-11 轉上市,monthly 23 筆 vs quarterly 13 筆)

**均線暖機期機制(v3.4 · Backward Extension)**
- 動機:老公司(如 2308 台達電)明明有更早的月營收,但 stock JSON 只存 60 個月,導致 3MA 前 2 個月 null、12MA 前 11 個月 null,均線視覺上「從 1/6 處才開始」
- 解法:pipeline 抓 71 個月(60 顯示 + 11 暖機期)· `config.MONTHLY_HISTORY_MONTHS = 71`
- 前端 `stock.html renderMaChart` 邏輯:
  - `useWarmup = state.period === 'M' && fullSeries.length >= 71`(M 頻率 + 資料足夠時啟用)
  - `displayStart = fullSeries.length - 60`(取後 60 個月顯示)
  - 均線用 `fullValues` (含暖機期) 算,再 `slice(displayStart)` 對齊圖表 x 軸
  - 效果:台達電 3MA 和 12MA 都從第 1 個顯示月份(2021-09)就有值
- Fallback 邏輯:
  - 新股 monthly < 71(如三商 23 筆)→ `useWarmup=false`,全部顯示 · 保持嚴格 null 邏輯
  - Q/Y 頻率 → 不啟用暖機(不需要,月營收季/年聚合後才 20 筆左右)
- 相關 commit:2026-08-25 深夜 · pipeline/config.py 60→71 · stock.html 加 displayStart 邏輯
- 生效方式:改 config 後**不需要手動 backfill**,自然隨明晚 03:30 batch 0 排程用新 config 產出 stock JSON · 一週後全市場 stock JSON 都會有 71 筆 monthly

**⚠️ 已知過渡狀態(2026-08-25 ~ 08-31 · 約 6-7 天)**:

`daily-build.yml` 每晚 22:00 會嘗試處理全部 ~1,700 檔(因為 `USE_FULL_UNIVERSE=True` 且未帶 `--batch`)。FinMind 免費層 500 req/hr × 每檔 3 request → 90 分鐘上限 = ~245 檔 API 呼叫 → **cache 覆蓋率必須 > 85% (~1,455 檔)daily-build 才可能在 90 分內完成**。快取由 `backfill-scheduled.yml` 每天 03:30 分批填充(每批 ~243 檔 · 7 天填滿)+ daily-build 每晚失敗前累積的 partial cache 共同貢獻。

**預估時程**:

- 2026-08-25 ~ 08-29(前 5 天):daily-build **幾乎必然 timeout**,不會 commit 任何資料
- 2026-08-30 ~ 09-01(第 6-8 天):cache 覆蓋接近門檻,daily-build **有機會首次成功**
- 2026-09-02 之後:cache 全滿,daily-build 應穩定成功(< 30 分鐘完成)

首週 daily-build failure **是預期行為,不用手動介入**。若 2026-09-03 之後 daily-build 仍持續 timeout,才需要 debug。

**加速選項(選用,不做也沒差)**:若不想看 6-7 天紅色 X,可在 `pipeline/config.py` 臨時把 `USE_FULL_UNIVERSE = False`,daily-build 會 fallback 到 `DEMO_UNIVERSE`(20 檔),立刻可用。等 backfill 跑完 7 批後(2026-09-02)再切回 `True`。

---

#### 2026-08-25(週二 · 全日大整修 · 13 個 commit)

早上 8:36 診斷 batch 6 fail=7 開始,一路修到深夜手機端搜尋 dropdown bug,是 v3.4 的**實質內容大定案日**。以下依時序整理:

| 時段 | 類別 | commit 摘要 |
|---|---|---|
| 09:00-09:30 | 🔴 Bug fix | `pipeline/build.py return 0`:原本 fail>0 就 return 2 導致 scheduled-backfill fail=7/301 整批 skip commit(294 檔資料被丟)· 改為 return 0 讓 workflow 繼續 commit |
| 09:00-09:30 | 🔴 Bug fix | `stock.html` 季度資料成長率 view 依 `hasCL` 過濾 clYoY 欄位(修 renderTable 加 filter)|
| 10:00-11:00 | ✨ Feature | `scanner.html v3.4 P0+P1+P2`:表格固定 8 欄 + 動態 4 欄候選 · sidebar 加 clRatio dual slider (0-200%) · 策略模板加第 5 個 pill「營運槓桿釋放」· 手機版 header + 2x2 grid + footer chip |
| 12:00-13:00 | 🔴 Bug fix | `pipeline/transform.py build_scanner_row`:補齊 `clRatio` + `opYoY` + `gmYoY` 三欄(之前 scanner_index 缺這 3 欄,P1/P2 filter 全 null 被排除 → 0 檔)|
| 13:00-13:30 | ✨ Tool | `.github/workflows/backfill.yml v3.4`:加 `stocks` 逗號分隔輸入 · 有值走 `--daily --stock`(cache hit 秒完成)· 無值保留 `--backfill` 全市場舊行為 · 加 concurrency group + job summary |
| 13:30-14:00 | 🔴 Bug fix | `pipeline/build.py --stock upsert`:upsert 條件從 `batch is not None` 擴展為 `batch is not None or stock_ids`,修「--stock 20 檔會把整份 scanner_index 從 312 縮成 20 檔」的邏輯 bug |
| 14:30-15:00 | ✨ Feature | `scanner.html P2 補強`:dynamicColumnCandidates 加第 5 個 opYoY 動態欄 · 點「營運槓桿釋放」pill 時自動設 sortKey=opYoY, sortDir=desc(強者浮到最前)|
| 15:00-15:30 | ✨ Feature | `stock.html` 月營收動能圖:交叉點 truth table 動態選對(3+12 標準 / 3+6 短期 / 三線全開仍 3 vs 12 / 缺 3MA 不畫)· 圓點視覺 3 段優化(3.5px + 深色邊框 + hover 8px)· 預設 6MA 關閉 |
| 15:30-16:00 | 📚 Docs | `help.html v3.4 大同步`:§4 加 clRatio slider 說明 · §4 策略模板 4→5 · §4 結果表大改為固定+動態機制 · §8 開頭 4→5 · §8 隱含規則表 4→5 · §10 Q9 手機版新卡片結構具體化 · §9 新增實例 4「營運槓桿釋放找爆發成長股」· 板塊 6 加 truth table 說明 · 名詞速查更新黃金交叉定義 |
| 16:30-17:00 | 🎨 UX | `stock.html` 漏斗圖對比度升級:.tier-sub / .leak-note / .tier-val .u 從 --text-dim (5.4:1) 升到 rgba(232,236,242,0.72) (~10:1)· 字級 10-10.5px 加大到 11-11.5px |
| 17:00-17:30 | 🐛 Bug fix | `stock.html` 手機端搜尋 dropdown 被遮 bug:iOS Safari sticky+backdrop-filter 造成 fixed 子元素被截斷 · 三層防禦:mobile @media disable topbar backdrop-filter + dropdown 加 isolation:isolate + 保留 fixed z-index:10000 |

**當日累積成果**:
- ✅ 3 個新前端功能(P0 動態欄 · P1 clRatio · P2 opLev + 自動排序)
- ✅ 4 個 pipeline/workflow bug 修復(return code · --stock upsert · scanner_index 補 3 欄 · dropdown 遮蔽)
- ✅ 2 個 UX 優化(月營收圓點 · 漏斗對比度)
- ✅ 1 個 workflow 工具化(backfill.yml stocks 參數)
- ✅ 說明書 7 段同步 + 1 段新增(實例 4)
- ✅ 月營收動能圖交叉點 truth table 邏輯確立

**scanner_index 演變**:8/25 早上 20 檔 → 09:30 搶救 batch 6 = 312 檔 → 22:XX --stock bug 縮成 20 檔 → 修 build.py + 再跑 batch 6 = 312 檔恢復。

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
