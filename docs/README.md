# 財報轉化漏斗 · 專案文件索引

**專案**:財報轉化漏斗(Financial Conversion Funnel)
**類型**:台股基本面分析工具 · 純靜態網頁 + Python pipeline
**上線 URL**:https://p0926330885.github.io/finreport-funnel/
**Repo**:https://github.com/p0926330885/finreport-funnel

---

## 給未來 AI(或未來自己)的第一段話

若你是被丟進這個專案的 AI,或是隔了半年後回頭看不懂自己當初設計的自己,**請照以下順序讀這些 SPEC**,10 分鐘進入狀況:

1. **先讀本 README.md**(你正在看)
2. 依「SPEC 演化史」章節順序讀 SPEC 檔案
3. 遇到矛盾時,**版本號較高者為準**(v3 > v2 > v1)
4. 每份 SPEC 都有「§0 給接手 AI 的執行指令」和「§X 仲裁優先序」章節,那裡是真相
5. Code 實作以 GitHub main branch 為準,SPEC 是設計意圖說明,不是實作證明

---

## SPEC 演化史(依時間順序)

| # | 檔案 | 日期 | 章節數 | 用途 |
|:-:|---|---|---|---|
| 1 | [SPEC-v2.2-detail-page.md](./SPEC-v2.2-detail-page.md) | 2026-08-22 | 20 章 | 個股詳細頁前端規格(定版) |
| 2 | [SPEC-v2.1-to-v2.2-patch.md](./SPEC-v2.1-to-v2.2-patch.md) | 2026-08-22 | · | v2.1 → v2.2 差分 patch |
| 3 | [SPEC-scanner-v1.md](./SPEC-scanner-v1.md) | 2026-08-22 | 25 章 | 選股掃描頁前端規格 |
| 4 | [SPEC-scanner-v1.1-delta.md](./SPEC-scanner-v1.1-delta.md) | 2026-08-22 | · | Scanner 微調 delta |
| 5 | [SPEC-pipeline-v1.md](./SPEC-pipeline-v1.md) | 2026-08-22 | 20 章 | 資料管線 v1(20 檔 demo) |
| 6 | [SPEC-integration-v1.md](./SPEC-integration-v1.md) | 2026-08-22 | 15 章 | Layer 3.5 前端與資料整合 |
| 7 | [SPEC-pipeline-v2.md](./SPEC-pipeline-v2.md) | 2026-08-23 | 12 章 | 全市場擴充 + 自動排程接力 |
| 8 | [SPEC-insights-v3.md](./SPEC-insights-v3.md) | 2026-08-23 | 12 章 | 自動判讀引擎重構(11 商業模式)|
| 9 | [DEPLOYMENT-SOP.md](./DEPLOYMENT-SOP.md) | 2026-08-22 | · | GitHub 部署上線手把手 SOP |

**目前最新真相**:每個模組看下面對照表。

---

## 目前生效的 SPEC(2026-08-23 起)

| 模組 | 現行 SPEC 版本 | 說明 |
|---|---|---|
| 個股詳細頁 前端 | **v2.2** + insights v3 章節替換 | 「落地率」已改「毛利轉化率」,自動判讀走 v3 |
| 選股掃描頁 前端 | v1.1 delta 併入 v1 | 中文模糊搜尋加入(v2 前端交付時) |
| 資料管線 | **v2**(全市場自動接力)| v1 已 supersede |
| 前後端整合(Layer 3.5)| v1 | fetch 動態載入 + 錯誤防呆 |
| 自動判讀引擎 | **v3.1**(LOCKED) | 11 商業模式 + fallback |
| CL 隱藏規則 | v3 §6 | hasCL === false 時完整隱藏 |

---

## 專案技術棧

- **前端**:純 HTML + CSS + JS(Chart.js)· 無框架、無 build step
- **資料管線**:Python 3.12 + pandas + pyarrow
- **資料來源**:FinMind API(免費方案 500 req/hr)
- **儲存**:JSON 檔案(每檔股票一個,約 2-3 KB)
- **部署**:GitHub Actions + GitHub Pages(完全免費)
- **排程**:
  - Daily build:每天台北 22:00 增量更新
  - Backfill scheduled:每天台北 03:30 分批補歷史(僅在 backfill 進行中)

---

## 關鍵設計決策(給接手 AI 的心智地圖)

### 為什麼用純靜態 + JSON,不用資料庫?

- 免費運行(GitHub Pages + Actions)
- 極致效能(CDN 直接吐 JSON,毫秒響應)
- 零維運成本(不用管 server / DB)
- 版本控制天然備份(每次 daily build 都是一個 git commit)

### 為什麼判讀邏輯放 pipeline 而不是前端?

- 一致性:所有股票判讀邏輯集中在一個地方
- 效能:前端只 render,不算
- 演化性:改判讀規則不需要動 HTML

### 為什麼用「毛利轉化率」不用「營益率」?

- 兩者定義不同:
  - 營益率 = OP / Rev(每 100 元營收多少營益)
  - 毛利轉化率 = OP / GP(每 100 元毛利留下多少營益)
- 「毛利轉化率」精準反映**費用結構效率**,和「毛利率」+「營益率」形成三角互證
- 名稱有故事性,契合「財報轉化漏斗」產品主題

### 為什麼是「做 100 元生意」語意基座?

- 損益表的自然順序(營收 → 毛利 → 營益)
- 一元化基準,不同規模公司都用同一把尺
- 直覺:「留下越多越好」正向一致,大腦不用轉換

---

## 部署資訊

- **repo**:`p0926330885/finreport-funnel`(Public)
- **Pages**:main branch / (root)
- **Secrets**:`FINMIND_TOKEN`(FinMind API 金鑰)
- **Workflows**:
  - `daily-build.yml`(cron `0 14 * * *` UTC = 22:00 TPE)
  - `backfill-scheduled.yml`(cron `30 19 * * *` UTC = 03:30 TPE,僅在 backfill in_progress 時執行)
  - `backfill.yml`(手動觸發,20 檔 demo mode)

---

## 未來 v4 議題(記錄,不動)

- 分頁載入(1700 檔全載入若太慢)
- 加 alert 通知(某模式命中時推送)
- 商業模式趨勢圖(連續幾季命中同一模式 = 結構性線索)
- 產業橫比
- 加入其他市場(美股 / 港股)

---

## 給接手 AI 的仲裁優先序(全域)

若不同 SPEC 之間有衝突:
