# 財報轉化漏斗 · Pipeline v2 規格書(LOCKED SPEC)

**版本**:SPEC-pipeline-v2 · 2026-08-23
**類型**:資料管線 v2 · 全市場擴充 + 自動排程接力 + 中文模糊搜尋
**前置依賴**:SPEC-pipeline-v1(baseline)、SPEC v2.2(前端契約)、SPEC-scanner-v1
**產出對象**:Python 3.12 專案,部署於 GitHub Actions + GitHub Pages
**核心變動**:
1. Universe 從硬編 20 檔擴充至全市場動態篩選 ~1,700 檔
2. Backfill 從一次全跑改為 5 批自動排程接力
3. State 檔追蹤進度,支援 pause/resume/reset
4. 前端加中文模糊搜尋 + 下拉自動完成

---

## §0. 給接手 AI(或未來 v3 SPEC 作者)的執行指令

1. 本 v2 SPEC 是 v1 的**增修**,未提及部分繼承 v1(§2 命名、§4 系統架構等)。
2. Universe filter 邏輯鎖在 `config.build_full_universe()`。若規則要改,需先修 §2 對照表再改 code。
3. 分批策略常數:`BATCH_SIZE = 350`、`BATCHES_TOTAL = 5`。變更需重估 rate limit + timeout。
4. Backfill state 檔進 git(單一真相來源),不放 GitHub Actions cache。理由:cache 不跨 workflow 分享。
5. 中文搜尋契約:兩份 HTML 都要 fetch `scanner_index.json`,不能只讀單股 detail。
6. Daily-build 對 in_progress 狀態的處理**必須**保守(只 refresh 已 processed_ids),否則會撞未 backfill 的股票造成錯誤。

---

## §1. 相對於 v1 的變動摘要

| 項目 | v1 | v2 |
|---|---|---|
| Universe | 硬編 20 檔 DEMO_UNIVERSE | 從 `TaiwanStockInfo` 動態篩選 ~1,700 檔 |
| Backfill | 單次 workflow,7-10 分鐘跑完 20 檔 | 5 批自動接力,每天凌晨跑一批,5 天完成 |
| Backfill 觸發 | 手動 `workflow_dispatch` | cron 排程 + 手動 |
| State 追蹤 | 無 | `cache/backfill_state.json` |
| Daily-build | 全 universe 掃過 | in_progress 時只跑 processed_ids |
| 前端搜尋 | 純數字 Enter 跳 | 純數字直跳 + 中文模糊 + 下拉 + 方向鍵 |
| `data/` 資料量 | ~50 KB(20 檔) | ~5 MB(1,700 檔) |
| pipeline 每日耗時 | ~5 min | ~30-90 min(視當日更新量) |

---

## §2. Universe 篩選規則(A3)

### 2.1 篩選條件(必須全滿足)

```python
def build_full_universe(universe_df) -> list[str]:
    """
    1. stock_id 為 4 位純數字 (排除 5-6 位權證)
    2. type ∈ {twse, otc, tpex} (排除 emerging 興櫃)
    3. industry_category 不含 {"ETF", "受益證券", "特別股", "指數股票型基金"}
    4. stock_name 不含 {"特別股", "DR", "TDR"}
    """
```

### 2.2 保留與排除清單

| 類別 | 是否保留 | 說明 |
|---|:-:|---|
| 上市普通股(twse) | ✅ | 主要目標 |
| 上櫃普通股(otc/tpex) | ✅ | 主要目標 |
| KY 股(如 6415 矽力-KY) | ✅ | 有正常財報 |
| F 股 | ✅ | 有正常財報 |
| 興櫃(emerging) | ❌ | 財報週期不同 |
| ETF(0050 等) | ❌ | 無財報 |
| TDR / DR | ❌ | 排除 |
| 特別股 | ❌ | 排除 |
| 權證 / 5-6 位股號 | ❌ | 排除 |

### 2.3 實測數量(依 FinMind 當日資料)

預估 **~1,700 檔**。若實測偏差 >10%,需檢查:
- FinMind 是否新增 `type` 值
- `industry_category` 是否有新分類
- 篩選規則是否需要更新

檢查方法:
```bash
FINMIND_TOKEN=xxx python -m pipeline.build --scheduled-batch
# 看 log: "首次觸發, 篩選 universe: XXXX 檔"
```

---

## §3. Backfill 自動排程接力設計(B 自訂調整)

### 3.1 常數

```python
BATCH_SIZE = 350        # 每批股票數
BATCHES_TOTAL = 5       # 總批次 (5 × 350 = 1,750 檔容量, 涵蓋 ~1,700)
```

### 3.2 排程時間

- **cron**: `30 19 * * *`(UTC 19:30 = 台北 03:30)
- **理由**:
  - 避開 daily-build 排程(22:00 TPE)
  - 避開泡泡圖專案白天使用 FinMind token
  - 台灣夜間 rate limit 一定閒著

### 3.3 每批執行時間估算

| 項目 | 時間 |
|---|---|
| 每檔 API 呼叫(fs + bs + rev) | 3 × 7.2s = 21.6s |
| 350 檔純呼叫 | 350 × 21.6s = **2.1 hr** |
| Setup + git 操作 | ~5 min |
| Buffer | ~15 min |
| **每批總時間** | **~2.3 hr** |

遠低於 workflow timeout 350 min(5.8 hr),有大量緩衝。

### 3.4 5 天完成節奏

| Day | 累計檔數 | Backfill 狀態 | Daily-build 覆蓋 |
|---|---|---|---|
| Day 0 | 0 | idle | 若前有 v1 data 則 refresh v1 20 檔 |
| Day 1 | 350 | in_progress (1/5) | 350 檔 |
| Day 2 | 700 | in_progress (2/5) | 700 檔 |
| Day 3 | 1,050 | in_progress (3/5) | 1,050 檔 |
| Day 4 | 1,400 | in_progress (4/5) | 1,400 檔 |
| Day 5 | 1,700 | **complete** | 全 universe (增量) |

**Day 5 之後每天 daily-build 自動涵蓋全市場**,不需要人工干預。

---

## §4. State 檔 Schema

**路徑**:`cache/backfill_state.json`
**進 git**:是(單一真相跨 workflow 分享,不能靠 GH Actions cache)

```json
{
  "status": "in_progress",
  "started_at": "2026-08-24T03:30:00+08:00",
  "completed_at": null,
  "batches_completed": 2,
  "batches_total": 5,
  "batch_size": 350,
  "universe_snapshot": ["1101", "1102", "..."],
  "universe_size": 1700,
  "processed_ids": ["1101", "1102", "..."],
  "failed_ids": ["9999"],
  "next_batch_start_idx": 700
}
```

### 4.1 status 狀態機

```
idle ── (首次觸發) ──→ in_progress ── (all batches done) ──→ complete
  ↑                                                              │
  └───────────── (--reset-backfill) ─────────────────────────────┘
```

### 4.2 universe_snapshot 凍結原因

首次觸發時凍結 universe 到 snapshot,後續批次都從 snapshot 取。**理由**:
- FinMind 可能中途新增/移除股票(新掛牌、下市)
- 若不凍結,`next_batch_start_idx` 對應的股票會漂移
- 造成部分股票被跳過或重複處理

Backfill complete 後,daily-build 恢復動態讀 universe,自動涵蓋新掛牌股票。

### 4.3 首次觸發自動初始化

`scheduled-batch` workflow 執行時:
- state 檔不存在或 `status: idle` → 自動初始化(不需要人工先跑 `--reset-backfill`)
- state 檔 `status: in_progress` → 讀進度,執行下一批
- state 檔 `status: complete` → 略過本次執行,不消耗 rate limit

### 4.4 手動重置

- **Actions 頁 → Backfill scheduled → Run workflow → inputs.reset: "yes"**
- 或本機:`python -m pipeline.build --reset-backfill`

**使用時機**:
- 篩選規則變更(config 改動),要重跑全 universe
- state 檔損壞
- 首次上線但已有 v1 data,想全部重跑

---

## §5. Daily-build 對 in_progress 狀態的處理

### 5.1 邏輯

```python
def run_daily():
    state = _load_state()
    if state.get("status") == "in_progress":
        targets = state.get("processed_ids", [])  # 只 refresh 已 backfill 的
    else:
        targets = build_full_universe(universe_df)  # 全掃
    ...
```

### 5.2 理由

- **避免撞未 backfill 的股票**:某些股票 pipeline 沒 fetch 過,daily 若嘗試會產生額外 API 呼叫
- **控制執行時間**:in_progress 期間 processed_ids 從 350 → 1,700 逐步增加,daily 時間可預期
- **不干擾 backfill 進度**:daily 和 backfill 用同一批 rate limit,daily 保守可讓 backfill 順利

### 5.3 Scanner index 完整性

**關鍵設計**:每次 daily-build / scheduled-batch 完成後,scanner_index.json **完整重建**(掃 `data/stocks/*.json` 每檔重算 row),不是增量更新。

理由:
- 保證 scanner_index 涵蓋所有已存在的 detail JSON
- 避免因為分批執行導致 scanner_index 只有最新批次
- 效能:1,700 檔重建約 3 秒,可接受

實作:`_rebuild_scanner_index()` in `build.py`

---

## §6. 前端中文模糊搜尋契約(D2)

### 6.1 資料來源

- **兩份 HTML 都 fetch `./data/scanner_index.json`**
- Stock.html 除了 fetch 單股 detail 外,也 fetch scanner_index 供搜尋

### 6.2 搜尋邏輯

```javascript
function fuzzyMatch(query) {
  const q = query.trim().toLowerCase();
  return searchIndex.filter(s =>
    String(s.id).includes(q) ||           // 打 "23" 出 2330/2379/...
    String(s.name).toLowerCase().includes(q)  // 打 "聯" 出 聯發科/聯電/...
  ).slice(0, 8);  // 最多顯示 8 筆
}
```

### 6.3 輸入行為

| 輸入 | 行為 |
|---|---|
| 純數字 4-6 位(如 `2330`)+ Enter | 直接 `location.href = 'stock.html?id=2330'` |
| 中文(如 `聯`)| 即時篩 name/id 顯示下拉(最多 8 筆) |
| 中文 + Enter | 跳選中項,或跳第一個候選 |
| 中文 + ArrowDown/ArrowUp | 移動選中 |
| Escape | 關閉下拉 |
| Blur(點空白處) | 關閉下拉 |

### 6.4 UI 元件

```html
<div class="search-box">
  <input type="text" placeholder="搜尋股號 / 名稱" value="">
  <div class="search-dropdown" id="searchDropdown" hidden>
    <button class="search-item" data-id="2454">
      <span class="search-id">2454</span>
      <span class="search-name">聯發科</span>
      <span class="search-industry">半導體</span>
    </button>
    ...
  </div>
</div>
```

**CSS token**:繼承 SPEC v2.2 §5,`--panel`、`--brass`、`--line`、`--text`、`--text-dim`。

### 6.5 空狀態

若 `fuzzyMatch(query).length === 0`,下拉顯示「找不到符合的股票」。

### 6.6 資料量

Scanner index 1,700 檔 × 每檔 ~200 bytes = **~340 KB**。前端一次載入,無效能問題。

---

## §7. 遷移路徑(v1 → v2)

### 7.1 使用者升級步驟

1. **下載 v2 完整 zip**
2. **覆蓋本機檔案**(保留 GitHub 上原有的 `data/` 資料夾)
3. **推送到 GitHub main**(以下 5 個檔要覆蓋):
   - `pipeline/config.py`
   - `pipeline/build.py`
   - `pipeline/mock_data.py`
   - `stock.html`
   - `scanner.html`
   - 兩份 workflow yml
4. **手動觸發**:Actions → Backfill scheduled → Run workflow(留 reset: no)
   - 首次執行會自動初始化 state
   - 跑第 1 批(350 檔)
   - 完成後 commit 資料 + state 檔
5. **接下來 4 天 cron 自動跑**,你什麼都不用做
6. **Day 5 全部完成**

### 7.2 避免衝突

- v1 `data/stocks/*.json` 內的 20 檔會被 v2 batch 中的相同股票覆蓋
- v1 scanner_index.json 會被 v2 重建覆蓋
- v1 `DEMO_UNIVERSE` 保留在 config,`--demo` 模式仍可用

### 7.3 Rollback 路徑

若 v2 出問題想回 v1:
1. `git revert` 對應 commit
2. 手動觸發 `Backfill (legacy / demo)` workflow 恢復 20 檔資料
3. State 檔刪除即可

---

## §8. 監控與問題排除

### 8.1 觀察 backfill 進度

打開 `data/meta.json`(每次 workflow 執行後更新):

```json
{
  "mode": "scheduled_batch",
  "batch_num": 2,
  "batches_total": 5,
  "backfill_status": "in_progress",
  "backfill_progress_pct": 40.0,
  "total_stocks_available": 700,
  "built_ok_this_run": 348,
  "built_fail_this_run": 2
}
```

### 8.2 常見失敗

| 症狀 | 原因 | 對策 |
|---|---|---|
| built_fail_this_run 很多(>50) | FinMind schema 變動或 rate limit | 檢查 workflow log |
| Universe 篩選出 <1000 檔 | FinMind `type` 欄位變動 | 檢查 §2.1 篩選規則 |
| Universe 篩選出 >2500 檔 | 篩選規則失效 | 檢查 §2.1 排除關鍵字 |
| Daily-build 每次跑很久(>2 hr) | processed_ids 太大 | 正常,backfill 完成後穩定 |
| state 檔顯示 in_progress 但很久沒進展 | cron 沒跑 / workflow 失敗 | 手動觸發一次 |

### 8.3 觀察 workflow 執行歷史

- Actions 頁 → 篩選 `Backfill scheduled`
- 每天應該有一筆(除非 status=complete)
- 綠勾勾 + 執行時間 ~2 hr = 正常
- 執行時間 <1 min = state=complete 略過(正常)

### 8.4 rate limit 監控

無直接工具,靠 workflow log 判斷:
- log 出現 `FinMind rate limit hit, backing off` = 快滿了
- log 出現 `HTTP 402` = 已超額,workflow 會 retry
- 過度出現 = 需要調低 `BATCH_SIZE` 或延長 `RATE_LIMIT_INTERVAL_SECONDS`

---

## §9. 效能與資源估算(更新)

### 9.1 儲存

| 資料 | 大小 |
|---|---|
| Detail JSON × 1,700 | ~5 MB |
| Scanner index | ~340 KB |
| Meta | ~1 KB |
| State 檔 | ~50 KB |
| Cache parquet(不進 git)| ~30-50 MB |
| **Git repo 大小** | **~6 MB** |

遠低於 GitHub 100 MB 上限。

### 9.2 GH Actions 分鐘

Public repo 無限制。Backfill 5 天 × 2.3 hr/day = 11.5 hr,daily-build 每天 30-90 min,遠低於任何配額。

### 9.3 FinMind 呼叫預算

| 情境 | 每日呼叫 |
|---|---|
| Backfill 期間(1-5 天) | 每天 ~1,050 req(2.1 hr @ 500/hr) |
| Backfill 完成後 daily | <500 req(增量, cache 命中) |
| 月營收公布日(每月 10 日) | ~1,700 req(全 universe 過期) |

保留給泡泡圖專案的 quota:白天 8:00-24:00 × 500/hr = 8,000 req/day,絕對足夠。

---

## §10. 檢查清單(交付前)

### 10.1 檔案齊全

- [ ] `pipeline/config.py` v2(含 `build_full_universe`, `BATCH_SIZE`, `BATCHES_TOTAL`)
- [ ] `pipeline/build.py` v2(含 `run_scheduled_batch`, `_load_state`, `_save_state`, `_rebuild_scanner_index`)
- [ ] `pipeline/mock_data.py`(加真台股名稱,加 fallback 合成)
- [ ] `.github/workflows/backfill-scheduled.yml`(新)
- [ ] `.github/workflows/daily-build.yml`(更新)
- [ ] `.github/workflows/backfill.yml`(legacy,保留)
- [ ] `stock.html`(加中文搜尋)
- [ ] `scanner.html`(加中文搜尋)
- [ ] `.gitignore`(排除 `cache/raw/` 但**保留** `cache/backfill_state.json`)

### 10.2 本機 smoke test

```bash
pip install -r requirements.txt
rm -rf cache/ data/
FINMIND_MOCK=1 python -m pipeline.build --demo             # v1 兼容 ✓
FINMIND_MOCK=1 python -m pipeline.build --scheduled-batch  # 首次觸發, init state ✓
cat cache/backfill_state.json                              # status: in_progress or complete ✓
FINMIND_MOCK=1 python -m pipeline.build --daily            # in_progress 時只跑 processed ✓
FINMIND_MOCK=1 python -m pipeline.build --reset-backfill   # 清空 state ✓
```

### 10.3 前端契約

- [ ] scanner_index.json 被 stock.html 也 fetch(除 stock.json 之外)
- [ ] 搜尋框有 `searchDropdown` 元素
- [ ] 打中文出下拉(最多 8 筆)
- [ ] 方向鍵 + Enter + Escape 全部運作
- [ ] 純數字 Enter 直接跳
- [ ] 點下拉項跳頁

### 10.4 workflow 正確性

- [ ] `backfill-scheduled.yml` cron 為 `30 19 * * *`
- [ ] `backfill-scheduled.yml` timeout-minutes >= 300
- [ ] `daily-build.yml` 讀 backfill_state 決定 targets
- [ ] 三個 workflow 都有 `permissions: contents: write`
- [ ] git add 包含 `cache/backfill_state.json`

---

## §11. 仲裁優先序

```
§0(執行指令)
 > §2(Universe filter, A3 契約)
 > §3(Backfill 排程契約)
 > §4(State schema)
 > §5(daily-build 對 state 的處理)
 > §6(前端搜尋契約)
 > §7-9(遷移 / 監控 / 效能)
 > 其他章節
```

**若 v2 SPEC 與 v1 SPEC 衝突**,以 v2 為準(v1 已 supersede)。
**若 v2 SPEC 與 SPEC-integration-v1 衝突**,以本 v2 §6 為前端搜尋契約真相。

---

**規格書結束**
版本 SPEC-pipeline-v2 · 2026-08-23
下次迭代:全市場 backfill 穩定 3 個月後,考慮 v3(可能議題:分頁載入、CDN 選擇、alert 通知)
