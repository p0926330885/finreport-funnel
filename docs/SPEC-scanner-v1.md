# 財報轉化漏斗 · Scanner 頁前端規格書(LOCKED SPEC)

**版本**:SPEC-scanner-v1 · 2026-08-22
**類型**:前端規格(僅設計,不生產)
**前置依賴**:SPEC v2.2(Detail 頁,共用 Topbar / Design tokens / 命名慣例 / 金額單位)
**產出對象**:單一 HTML 檔 `scanner.html`,與 `stock.html` 同目錄

---

## §0. 給接手 AI(生產 AI)的執行指令

1. 本規格書為 Scanner 頁單一真相來源。**與 Detail SPEC v2.2 同時作用**;凡本規格未明說者,一律依 v2.2 執行(命名、色系、字體、單位、tooltip 樣式、Chart.js 版本)。
2. 不要提出方案 A/B/C。所有決策已鎖。
3. 產出格式:單一 HTML 檔 `scanner.html`(inline CSS + inline JS)。純 HTML/CSS/Vanilla JS + Chart.js v4(CDN)。**禁**任何 framework、build 工具、其他 npm 套件。
4. 樣本股清單沿用 §18,不得改動。
5. 金額單位依 v2.2 §6 §23。**禁** M/K/B/T。結果表 cell 純數字,單位在欄位標題。
6. 英文縮寫**只能在 JS 變數與 CSS class**。UI 文字全中文。允許保留:EPS、YoY、QoQ、TWSE、OTC、股票代號、URL 參數 key(gm、om 等)。
7. 每個 slider、toggle、下拉、模板 chip、結果表欄位標題都必須有 hover tooltip,內容照抄 §17。
8. 遇到未涵蓋細節,採「保守 + 極簡」原則。**不得自行發明**。
9. §22 列出前次交付失誤(v2.2 Detail 頁的失誤模式同樣適用於 Scanner),禁重複。
10. 交付前逐項對照 §21 檢查清單。
11. 若規格內部矛盾,依 §24 仲裁優先序處理。
12. **不得引入 localStorage、sessionStorage、IndexedDB 或任何前端持久化機制**。所有狀態透過 URL querystring 承載。
13. **不得生產 Detail 頁**。本任務只出 `scanner.html`。若使用者附上 `stock.html`,僅供參考共用 topbar/token 樣式,不得覆蓋。

---

## §1. 產品定位

台股財報分析工具的 Scanner(選股掃描器)頁。單一任務:讓使用者透過多維度篩選,從全市場快速收斂出符合條件的候選股清單,並可一鍵套用預設策略模板。

**與 Detail 頁的關係**:
- Scanner 是 discovery 入口,結果表每列可點跳 `stock.html?id=xxxx` 進入 Detail 頁
- Detail 頁 topbar 的「選股掃描」nav tab 可回到 `scanner.html`(帶或不帶原篩選狀態)

---

## §2. 命名慣例

沿用 v2.2 §2 全部。以下為 Scanner 專屬補充:

### 2.4 URL 參數 key(允許保留英文小寫縮寫,僅存在於 URL,不出現在 UI 文字)

| Key | 對應 UI 標籤 | 值格式 |
|---|---|---|
| `gm` | 毛利率區間 | `min,max`(百分比整數) |
| `om` | 營益率區間 | `min,max` |
| `nm` | 淨利率區間 | `min,max` |
| `revYoY` | 營收 YoY 區間 | `min,max` |
| `clYoY` | 合約負債 YoY 區間 | `min,max` |
| `vis` | 訂單能見度區間 | `min,max`(月數,小數 1 位) |
| `gc` | 黃金交叉近 1 月內 | `1` = on / 省略 = off |
| `industry` | 產業代碼 | 見 §18.3 產業對照表,`all` = 不限 |
| `market` | 市場代碼 | `twse` / `otc` / `all` |
| `template` | 已套用之模板 chip | `order-pile-up` / `core-stable` / `three-up` / `momentum-turn` |
| `sort` | 排序欄位與方向 | `score,desc` / `revYoY,asc` etc. |
| `page` | 分頁碼 | 整數 ≥ 1 |

**同時使用多個時以 `&` 串接**,例:`scanner.html?gm=25,60&om=8,40&industry=semi&sort=score,desc`

### 2.5 Scanner 頁專屬術語

- **策略模板**(strategy template):一鍵套用的預設篩選組合,見 §11
- **健康度總分**(health score):4 核心指標加總,0–8 分,見 §7.5
- **命中數**(hit count):符合當前所有條件的股票數量
- **命中率**(hit rate):命中數 / 全市場總數 × 100%

---

## §3. 絕對禁止事項

繼承 v2.2 §3 全部。以下為 Scanner 專屬:

- ❌ 產業/市場用 drill-down 樹狀展開(§9 已鎖為單層下拉)
- ❌ 使用 localStorage / sessionStorage / IndexedDB 保存篩選狀態(§0.12)
- ❌ 篩選面板側邊欄用手風琴 accordion 收合各 section(視覺雜訊,直接全展開)
- ❌ 結果表無限捲動(用分頁,見 §12)
- ❌ 篩選觸發用「套用按鈕」(改用 debounce 200ms 自動觸發,見 §14)
- ❌ URL 參數用 base64 或 JSON.stringify 編碼(必須人類可讀的 key=value)
- ❌ 結果表出現 Chart 或 sparkline(Scanner 只列數字,視覺分析留給 Detail 頁)
- ❌ 模板 chip 高亮時 URL 保留 `template=xxx` 之外還額外複製一份對應的 slider 值(URL 用哪個 key 就靠哪個 key,避免雙重寫入衝突)

---

## §4. 頁面架構(Scanner 頁)

```
1. Topbar(與 Detail 共用,§8)
2. Scanner Hero(標題 + 命中統計 + 清空條件按鈕)
3. 兩欄主體布局:
   ┌──────────────────┬────────────────────────────────┐
   │  篩選面板         │  策略模板 chip 列               │
   │  (280px)         │  ──────────────────────────    │
   │  §5 Filters      │  結果表(§12)                   │
   │                  │                                │
   │  ▸ 分類          │  ┌──┬──┬──┬──┬──┬──┬──┬──┐  │
   │  ▸ 財務健康度     │  │ 股│名│產│營│Yo│營│健│能│  │
   │  ▸ 成長動能       │  │ 號│稱│業│收│Y │率│分│度│  │
   │  ▸ 訂單能見度     │  ├──┼──┼──┼──┼──┼──┼──┼──┤  │
   │  ▸ 交叉指標       │  │  │  │  │  │  │  │  │  │  │
   │                  │  └──┴──┴──┴──┴──┴──┴──┴──┘  │
   │  [清除全部]       │  ──────────────────────────    │
   └──────────────────┴  分頁 + 每頁筆數                │
                       └────────────────────────────────┘
4. Meta 資訊條(與 Detail 共用格式)
```

### 4.1 兩欄布局規則

- 桌面(≥ 960px):左側 filter panel 固定寬度 280px、右側主內容 flex 1
- 平板(640–959px):左側收合為抽屜(頂部「篩選 (N)」按鈕開啟,N = 已啟用條件數)
- 手機(< 640px):同平板,結果表切換為卡片式呈現(見 §12.5)

### 4.2 頁面切換

Topbar 的 nav tab 兩個都是 anchor:
- 「選股掃描」→ `scanner.html`(無 params 時)
- 「個股詳細」→ `stock.html?id=6789`(預設帶樣本股)

結果表每列點擊 → `stock.html?id={股號}`

---

## §5. Design Tokens

沿用 v2.2 §5 全部。Scanner 專屬補充:

```css
:root {
  /* Scanner 篩選面板 */
  --filter-panel-width: 280px;
  --filter-section-gap: 20px;
  --filter-label-color: var(--text-dim);

  /* Slider 專用 */
  --slider-track-bg: var(--panel-2);
  --slider-track-active: var(--brass);
  --slider-thumb: var(--brass-hi);
  --slider-thumb-hover: var(--mint);

  /* 結果表命中列高亮(hover) */
  --row-hover-bg: var(--panel-2);

  /* 空狀態 */
  --empty-state-color: var(--text-muted);
}
```

**Scanner 頁色系不引入新色相**。所有健康度顯示沿用 brass / mint / amber / coral,不新增藍紫粉。

---

## §6. 金額單位規範(Scanner 頁補充)

沿用 v2.2 §6 全部。以下為 Scanner 專屬場景表:

| 元件類型 | 格式 | 單位標示位置 |
|---|---|---|
| 結果表金額欄(當季營收) | `13,381`(純數字) | 欄位標題 `當季營收(百萬)` |
| 結果表比率欄(YoY、營益率) | `+28.1%`(帶正負號) | 不需外部標示 |
| 結果表健康度總分欄 | `6.5`(小數 1 位) | 欄位標題 `健康度總分` |
| 結果表能見度欄 | `1.7` | 欄位標題 `能見度(月)` |
| Slider tooltip 顯示當前值 | `毛利率:25% – 40%` | inline |
| 命中統計 | `命中 12 檔 / 全市場 20 檔 (60.0%)` | inline |

**結果表所有金額 cell 純數字,單位在欄位標題,不重複**。

---

## §7. 業務公式

沿用 v2.2 §7 全部。Scanner 專屬新增:

### 7.5 健康度總分公式(0–8 分)

依 v2.2 §7.3 綠/黃/紅閾值,取 4 個核心指標各 0/1/2 分加總:

| 核心指標 | 綠 = 2 分 | 黃 = 1 分 | 紅 = 0 分 |
|---|---|---|---|
| 毛利率 | ≥25% | 15–25% | <15% |
| 營益率 | ≥8% | 3–8% | <3% |
| 淨利率 | ≥5% | 0–5% | <0% |
| 業外占淨利(絕對值) | <25% | 25–70% | >70% |

**總分範圍 0–8**,結果表用 1 位小數顯示(允許加權平均導致小數)。

**總分健康度色碼**:
- ≥6.0:綠(mint)
- 3.0–5.9:黃(amber)
- <3.0:紅(coral)

### 7.6 黃金交叉近 1 月內判定

`gc = 1` 篩選條件:近 1 個月內(過去 30 天)3MA 曾由下穿越 12MA。判定演算法同 v2.2 §7.4,回傳 boolean。

### 7.7 命中率

`hitRate = filteredList.length / totalList.length × 100%`

顯示於 Scanner Hero 區,小數 1 位。

---

## §8. Topbar

沿用 v2.2 §8。差異:「選股掃描」nav tab 在此頁為 `active` 狀態。

---

## §9. Scanner Hero

```
┌──────────────────────────────────────────────────────────┐
│  選股掃描              命中 12 檔 / 全市場 20 檔 (60.0%)   │
│  多維度篩選 · 策略模板                        [清除全部]  │
└──────────────────────────────────────────────────────────┘
```

- 左側:標題 `選股掃描` + 副標 `多維度篩選 · 策略模板`
- 中間:命中統計(即時反映當前篩選結果,mono 字體)
- 右側:`清除全部` 按鈕(重置所有 filter 到預設,URL 去除 params)

**清除全部**行為:重置到零 filter 狀態,URL 變為 `scanner.html`(無 querystring)。命中回到全市場總數(20 檔)。

---

## §10. 篩選面板(§5 §Filters)

側邊欄由上而下 6 個 section,每 section 有 `<h4>` 小標,無收合按鈕。

### 10.1 Section「分類」(2 個下拉)

**產業**下拉:
- 選項:見 §18.3 產業對照表(9 個產業 + `全部`)
- URL key:`industry`,預設 `all`
- Tooltip:「以產業維度過濾。單層下拉,不做 drill-down。」

**市場**下拉:
- 選項:`全部` / `TWSE` / `OTC`
- URL key:`market`,預設 `all`
- Tooltip:「TWSE 為上市、OTC 為上櫃。」

### 10.2 Section「財務健康度」(3 個 slider)

每個 slider 為 dual-thumb 區間 slider,顯示 `min – max`。

| 標籤 | Slider 範圍 | 預設值 | 步進 | 單位 |
|---|---|---|---|---|
| 毛利率 | 0 – 100 | 0 – 100(不限) | 1 | % |
| 營益率 | -20 – 100 | -20 – 100(不限) | 1 | % |
| 淨利率 | -30 – 100 | -30 – 100(不限) | 1 | % |

**每個 slider 下方顯示當前值**:例 `毛利率:25% – 60%`

**Tooltip**:同 v2.2 §17 的三率 tooltip 全文。

### 10.3 Section「成長動能」(1 個 slider)

| 標籤 | Slider 範圍 | 預設值 | 步進 | 單位 |
|---|---|---|---|---|
| 營收 YoY | -100 – 500 | -100 – 500(不限) | 5 | % |

Tooltip:「相較去年同期營收的變化百分比。>15% 為強勁成長。」

### 10.4 Section「訂單能見度」(2 個 slider)

| 標籤 | Slider 範圍 | 預設值 | 步進 | 單位 |
|---|---|---|---|---|
| 合約負債 YoY | -100 – 500 | -100 – 500(不限) | 5 | % |
| 能見度月數 | 0 – 24 | 0 – 24(不限) | 0.5 | 個月 |

**特殊規則**:若使用者啟用「合約負債 YoY」或「能見度月數」任一 slider(即移動任一 thumb 使其偏離預設 min/max),**自動排除 hasCL=false 的股票**。此排除不會顯示於 URL(不需新增 key),但需在命中統計 tooltip 說明。

Tooltip 對應 v2.2 §17 合約負債 / 能見度全文。

### 10.5 Section「交叉指標」(1 個 toggle)

**3MA vs 12MA 近 1 月黃金交叉**:布林開關
- URL key:`gc`,值 `1` 或省略
- Tooltip:「篩選近 30 天內 3MA 由下穿越 12MA 的股票。動能轉正訊號。」

### 10.6 篩選面板底部

- `清除全部`(次要按鈕,和 Hero 的按鈕同功能)
- 已啟用條件數 badge:例 `已啟用 3 個條件`

---

## §11. 策略模板 chip 列

位於結果表上方,一橫排 4 個 chip:

| Chip 標籤 | URL key | 對應條件 |
|---|---|---|
| **訂單池累積、認列跟上** | `template=order-pile-up` | `clYoY=30,500` AND `revYoY=15,500` |
| **本業獲利穩健** | `template=core-stable` | `om=8,100` AND(業外占淨利絕對值 <25% 隱含條件) |
| **三率同升** | `template=three-up` | 毛利率/營益率/淨利率 QoQ 全部 ≥ 上一季(需計算欄位,見 §11.2) |
| **動能轉正** | `template=momentum-turn` | `gc=1` |

### 11.1 Chip 互動

- 點 chip → 套用對應條件、URL 更新為 `?template=xxx&<對應 filter keys>`、對應的 slider/toggle **同時反映到 UI**(這樣使用者能看到當前實際條件、也能再手動微調)
- 點已啟用的 chip 一次 → 取消該模板(URL 移除 template 及相關 filter)
- **同時只能啟用 1 個模板**,點另一個會自動取消前一個
- 使用者手動改動 slider 後 chip 高亮消失(URL 移除 `template` key,但保留 filter keys)

### 11.2 「三率同升」實作說明

需計算的隱藏欄位:
- `gmQoQ = 當季毛利率 − 上季毛利率`
- `omQoQ = 當季營益率 − 上季營益率`
- `nmQoQ = 當季淨利率 − 上季淨利率`

**符合條件**:三者皆 ≥ 0。

由於 URL 難以承載此複合布林,此模板僅透過 `template=three-up` 表達,不對外提供獨立 slider(§14.4 例外)。

### 11.3 「本業獲利穩健」實作說明

主要條件:`om=8,100`(進 URL)
隱含條件:業外占淨利絕對值 <25%(不進 URL,由 template 判定時附加)

同上,此隱含條件透過 `template=core-stable` 表達,不獨立 slider。

---

## §12. 結果表

### 12.1 8 個欄位(固定,不可改序)

| # | 欄位標題 | Cell 內容 | 型別 | 排序 key |
|---|---|---|---|---|
| 1 | 股號 | `6789` | 純數字 | `id` |
| 2 | 名稱 | `示範系統` | 純文字 | `name` |
| 3 | 產業 | `系統整合` | 純文字 | `industry` |
| 4 | 當季營收(百萬) | `13,381` | 純數字 | `rev` |
| 5 | 營收 YoY | `+22.7%` | 帶正負百分比 | `revYoY` |
| 6 | 營益率 | `8.2%` | 百分比 | `om` |
| 7 | 健康度總分 | `6.5` | 數字帶健康色 | `score` |
| 8 | 能見度(月) | `1.7` 或 `—` | 數字或 dash(無 CL) | `vis` |

**每欄標題必須含單位小字**(對應 v2.2 §6.3):`當季營收<span class="col-unit">(百萬)</span>`

### 12.2 排序

- 預設:按 `score` 降冪(健康度總分高 → 低)
- 點欄位標題:切換 asc / desc 或指定新排序欄位
- URL 同步:`sort=score,desc`

**排序無效欄位**:名稱(無數值)、產業(無數值)—— 點擊時無反應(或改為文字字典序,不進 URL)。

### 12.3 分頁

- 每頁固定 25 筆
- 分頁控制:`< 1 2 3 ... N >` 顯示於表格下方
- URL 同步:`page=2`,若無則預設 1
- 樣本 20 檔全部 <=25,實際不會分頁,但 UI 元件需保留

### 12.4 空狀態

命中數 = 0 時,結果表區塊顯示:

```
┌────────────────────────────────────────────────┐
│                                                │
│         🔍 無符合條件的股票                     │
│                                                │
│  試試放寬「毛利率」或「能見度月數」條件         │
│  或點  [清除全部]  重新開始                     │
│                                                │
└────────────────────────────────────────────────┘
```

**注意**:此為唯一允許用 emoji 的場景(§3 emoji 禁令的例外),因為空狀態需要弱化文字份量。若不用 emoji,改用 SVG icon(建議 24px 放大鏡 outline)。

### 12.5 手機版切換為卡片

< 640px 螢幕,結果表每列變成一張卡片:

```
┌──────────────────────────────────┐
│  6789 · 示範系統       [ 6.5 綠 ] │
│  系統整合                          │
│  ────────────────────────────    │
│  當季營收 13,381 百萬  YoY +22.7% │
│  營益率 8.2%          能見度 1.7 月│
└──────────────────────────────────┘
```

卡片整張可點,跳 `stock.html?id=6789`。

---

## §13. 命中列點擊行為(路由)

點結果表任一列 / 卡片:
- 跳轉 `stock.html?id={股號}`
- 使用 `<a>` tag 或 `window.location.href`,支援 Back button
- **不使用**打新分頁(除非按住 Cmd/Ctrl 由瀏覽器決定)

---

## §14. 互動行為與 URL 序列化

### 14.1 篩選觸發時機

| 元件 | 觸發時機 | Debounce |
|---|---|---|
| Slider 拖動 | `input` 事件 | 200ms |
| 下拉選單 | `change` 事件 | 立即 |
| 布林開關 | `change` 事件 | 立即 |
| 模板 chip | `click` 事件 | 立即 |
| 排序欄位 | `click` 事件 | 立即 |
| 分頁按鈕 | `click` 事件 | 立即 |

### 14.2 URL 更新機制

- **使用 `history.replaceState()`**,不是 `pushState()`(避免 slider 拖動產生 100 筆歷史)
- 例外:模板 chip 點擊、清除全部、排序改變、分頁改變 → 用 `pushState()`(這些是使用者明確意圖切換,應可 Back button 回上一狀態)

### 14.3 URL 編碼規則

- 所有值以純文字 key=value 呈現,不做 base64 / JSON.stringify
- 逗號分隔區間值:`gm=15,40`
- 預設值(slider 未偏離兩端)**不寫入 URL**,例:若 gm 停在 0–100 就不加 `gm=0,100` 這個 key,保持 URL 精簡
- Boolean off 值不寫入(`gc=0` 一律省略,只有 `gc=1` 出現)
- `all` 值不寫入(`industry=all` 一律省略)

### 14.4 URL 反向解析(頁面載入時)

- 讀 `location.search`,依 §2.4 對照表反向填入所有 UI 元件
- 若 URL 帶 `template=xxx`,先套用模板對應的 filter,再讓 URL 的其他 key 覆寫(允許使用者分享「基於模板再微調」的連結)
- 遇未知 key 或格式錯誤 → 忽略該 key,不 throw,不 alert

### 14.5 網頁初始渲染順序

```
1. 解析 URL → 建立 filterState 物件
2. 渲染 topbar / hero(先出視覺骨架)
3. 渲染篩選面板 UI(依 filterState 反填 slider/toggle/dropdown 值)
4. 渲染模板 chip 列(依 filterState.template 高亮)
5. 執行 applyFilters() → 生成 filteredList
6. 渲染結果表 + 分頁
7. 更新命中統計
```

---

## §15. 響應式行為

### 15.1 桌面 (≥ 960px)

- 兩欄並列布局,filter panel 固定左側
- 結果表所有欄位並排顯示

### 15.2 平板 (640–959px)

- Filter panel 收合為抽屜,頂部按鈕 `[≡ 篩選 (3)]`(數字為已啟用條件數)
- 點按鈕從左側滑入 filter panel,占 320px,右側背景半透明遮罩
- 結果表所有欄位並排顯示

### 15.3 手機 (< 640px)

- Filter 抽屜同上,寬度 100%
- 結果表切換為 §12.5 卡片模式
- 分頁按鈕縮小為 `< 1 / N >`

---

## §16. 效能與資料量

- 樣本 20 檔全數 client-side 篩選 + 排序,無需分頁/虛擬捲動
- 未來全市場 ~1800 檔時,應在 build 階段預先生成 `scanner_index.json`(每檔僅含 §12.1 的 8 欄位 + `hasCL` + `gmQoQ` / `omQoQ` / `nmQoQ` / `gc`,不含完整 8Q 歷史),控制單檔 <1MB
- 現階段 mock 資料直接 hardcode 於 HTML `<script>` 內,無 fetch

---

## §17. Tooltip 對照表(照抄)

繼承 v2.2 §17 全部。以下為 Scanner 專屬新增:

| 位置 | Tooltip |
|---|---|
| 篩選面板「產業」 | 「以產業維度過濾。單層下拉,不做 drill-down。」 |
| 篩選面板「市場」 | 「TWSE 為上市、OTC 為上櫃。」 |
| 篩選面板「毛利率」 | 「毛利 ÷ 營收。反映產品定價力與成本控制。≥25% 綠 / 15–25% 黃 / <15% 紅。」 |
| 篩選面板「營益率」 | 「營業利益 ÷ 營收。反映本業獲利能力。≥8% 綠 / 3–8% 黃 / <3% 紅。」 |
| 篩選面板「淨利率」 | 「淨利 ÷ 營收。≥5% 綠 / 0–5% 黃 / <0% 紅。」 |
| 篩選面板「營收 YoY」 | 「相較去年同期營收的變化百分比。>15% 為強勁成長。」 |
| 篩選面板「合約負債 YoY」 | 「合約負債期末相較去年同期的變化。>30% 為訂單池強勁累積,<0% 為訂單轉弱。啟用此條件會自動排除無合約負債的股票。」 |
| 篩選面板「能見度月數」 | 「合約負債 ÷ 該季 3 個月平均月營收。啟用此條件會自動排除無合約負債的股票。」 |
| 篩選面板「3MA vs 12MA 近 1 月黃金交叉」 | 「近 30 天內 3MA 由下穿越 12MA。動能轉正訊號。」 |
| 命中統計 | 「符合當前所有條件的股票數量。若啟用合約負債相關條件,分母排除無合約負債的股票。」 |
| 結果表「健康度總分」欄 | 「4 核心指標(毛利率、營益率、淨利率、業外占淨利)綠 2 分 / 黃 1 分 / 紅 0 分加總。0–8 分。≥6 綠 / 3–5.9 黃 / <3 紅。」 |
| 結果表「能見度(月)」欄 | 「合約負債 ÷ 該季 3 個月平均月營收。無合約負債的股票顯示 —。」 |
| 模板 chip「訂單池累積、認列跟上」 | 「合約負債 YoY >30% 且營收 YoY >15%。訂單池累積且認列速度跟得上。」 |
| 模板 chip「本業獲利穩健」 | 「營益率 ≥8% 且業外占淨利絕對值 <25%。本業獲利健康、業外雜訊小。」 |
| 模板 chip「三率同升」 | 「毛利率、營益率、淨利率 QoQ 全部持平或上升。獲利品質全面改善。」 |
| 模板 chip「動能轉正」 | 「近 30 天內 3MA 由下穿越 12MA。月營收動能剛剛翻正。」 |
| 清除全部按鈕 | 「重置所有篩選條件與模板到初始狀態。」 |

---

## §18. 樣本資料(照抄)

### 18.1 20 檔樣本股清單

```javascript
const scannerStocks = [
  // 優等生群(綠燈為主,score ≥ 6)
  { id: '6789', name: '示範系統', industry: 'integration', market: 'twse', hasCL: true,  rev: 13381, revYoY: 22.7, gm: 26.5, om: 8.2, nm: 8.0, noiRatio: -2.3, gmQoQ:  0.5, omQoQ:  0.4, nmQoQ:  0.3, gc: false, vis: 1.7, clYoY: 41.8 },
  { id: '2451', name: '示範半導', industry: 'semi',        market: 'twse', hasCL: true,  rev: 45230, revYoY: 35.1, gm: 42.3, om: 18.5, nm: 15.2, noiRatio: 5.1, gmQoQ:  1.2, omQoQ:  0.8, nmQoQ:  0.6, gc: true,  vis: 3.2, clYoY: 55.6 },
  { id: '3037', name: '示範零件', industry: 'component',   market: 'twse', hasCL: false, rev: 28150, revYoY: 18.4, gm: 31.8, om: 12.6, nm: 10.8, noiRatio: -8.5, gmQoQ:  0.3, omQoQ:  0.1, nmQoQ:  0.2, gc: false, vis: null, clYoY: null },
  { id: '4919', name: '示範精化', industry: 'chemical',    market: 'twse', hasCL: true,  rev: 15680, revYoY: 28.9, gm: 35.7, om: 11.2, nm: 9.5, noiRatio: -3.1, gmQoQ:  0.7, omQoQ:  0.5, nmQoQ:  0.4, gc: true,  vis: 2.5, clYoY: 33.4 },
  { id: '6488', name: '示範光電', industry: 'component',   market: 'otc',  hasCL: true,  rev: 8940, revYoY: 42.6, gm: 39.2, om: 14.8, nm: 12.1, noiRatio: -6.8, gmQoQ:  1.5, omQoQ:  1.2, nmQoQ:  0.9, gc: true,  vis: 4.1, clYoY: 68.3 },

  // 一般群(黃燈為主,score 3–5)
  { id: '2618', name: '示範金融', industry: 'finance',     market: 'twse', hasCL: false, rev: 62100, revYoY: 6.8, gm: 22.5, om: 6.4, nm: 4.8, noiRatio: 30.2, gmQoQ: -0.2, omQoQ: -0.3, nmQoQ:  0.1, gc: false, vis: null, clYoY: null },
  { id: '2882', name: '示範傳金', industry: 'finance',     market: 'twse', hasCL: false, rev: 38500, revYoY: 4.2, gm: 20.1, om: 5.8, nm: 4.1, noiRatio: 35.6, gmQoQ:  0.1, omQoQ: -0.1, nmQoQ: -0.2, gc: false, vis: null, clYoY: null },
  { id: '1102', name: '示範水泥', industry: 'traditional', market: 'twse', hasCL: false, rev: 22400, revYoY: 9.5, gm: 18.6, om: 5.2, nm: 3.9, noiRatio: 15.4, gmQoQ:  0.4, omQoQ:  0.2, nmQoQ:  0.3, gc: false, vis: null, clYoY: null },
  { id: '4576', name: '示範機械', industry: 'machinery',   market: 'twse', hasCL: true,  rev: 6720, revYoY: 12.3, gm: 24.8, om: 7.5, nm: 5.4, noiRatio: -12.3, gmQoQ:  0.3, omQoQ:  0.2, nmQoQ:  0.4, gc: false, vis: 2.8, clYoY: 18.5 },
  { id: '4108', name: '示範生技', industry: 'biotech',     market: 'otc',  hasCL: false, rev: 3480, revYoY: 15.7, gm: 55.3, om: 4.2, nm: 2.8, noiRatio: -25.6, gmQoQ:  1.1, omQoQ: -0.5, nmQoQ: -0.3, gc: true,  vis: null, clYoY: null },

  // 警訊群(紅燈為主,score < 3)
  { id: '5522', name: '示範營建', industry: 'traditional', market: 'twse', hasCL: true,  rev: 18900, revYoY: -8.5, gm: 12.4, om: 1.8, nm: 0.5, noiRatio: 78.5, gmQoQ: -0.8, omQoQ: -0.6, nmQoQ: -0.4, gc: false, vis: 5.2, clYoY: -12.3 },
  { id: '3260', name: '示範軟體', industry: 'integration', market: 'otc',  hasCL: true,  rev: 2150, revYoY: -15.2, gm: 14.8, om: -2.5, nm: -3.8, noiRatio: -155.3, gmQoQ: -1.2, omQoQ: -1.5, nmQoQ: -1.8, gc: false, vis: 0.8, clYoY: -28.7 },
  { id: '2308', name: '示範電子', industry: 'component',   market: 'twse', hasCL: false, rev: 125000, revYoY: -3.8, gm: 13.5, om: 2.8, nm: 2.1, noiRatio: 45.6, gmQoQ: -0.3, omQoQ: -0.2, nmQoQ: -0.1, gc: false, vis: null, clYoY: null },
  { id: '2603', name: '示範航運', industry: 'traditional', market: 'twse', hasCL: false, rev: 42800, revYoY: -22.6, gm: 8.3, om: -1.2, nm: -2.5, noiRatio: -180.2, gmQoQ: -1.5, omQoQ: -1.1, nmQoQ: -0.9, gc: false, vis: null, clYoY: null },
  { id: '4174', name: '示範藥廠', industry: 'biotech',     market: 'twse', hasCL: false, rev: 1820, revYoY: 3.2, gm: 42.6, om: -8.5, nm: -12.3, noiRatio: -85.4, gmQoQ:  0.5, omQoQ: -1.8, nmQoQ: -2.1, gc: false, vis: null, clYoY: null },

  // 轉型中(混合,score 3–6)
  { id: '2412', name: '示範電信', industry: 'traditional', market: 'twse', hasCL: true,  rev: 55600, revYoY: 3.5, gm: 28.7, om: 12.5, nm: 9.2, noiRatio: -15.6, gmQoQ:  0.2, omQoQ:  0.3, nmQoQ:  0.1, gc: false, vis: 2.1, clYoY: 5.8 },
  { id: '3711', name: '示範封測', industry: 'semi',        market: 'twse', hasCL: true,  rev: 32400, revYoY: 15.8, gm: 24.5, om: 9.8, nm: 7.2, noiRatio: 8.3, gmQoQ:  0.6, omQoQ:  0.4, nmQoQ:  0.3, gc: true,  vis: 2.3, clYoY: 22.5 },
  { id: '5871', name: '示範租賃', industry: 'finance',     market: 'twse', hasCL: false, rev: 12800, revYoY: 25.3, gm: 32.1, om: 15.2, nm: 11.5, noiRatio: 12.8, gmQoQ:  0.8, omQoQ:  0.5, nmQoQ:  0.4, gc: true,  vis: null, clYoY: null },
  { id: '6415', name: '示範矽力', industry: 'semi',        market: 'twse', hasCL: false, rev: 7850, revYoY: 55.8, gm: 48.6, om: 22.5, nm: 18.2, noiRatio: -4.2, gmQoQ:  2.1, omQoQ:  1.5, nmQoQ:  1.2, gc: true,  vis: null, clYoY: null },
  { id: '8046', name: '示範南電', industry: 'component',   market: 'twse', hasCL: true,  rev: 25600, revYoY: -5.2, gm: 22.8, om: 7.2, nm: 5.5, noiRatio: -18.5, gmQoQ: -0.5, omQoQ: -0.3, nmQoQ: -0.2, gc: false, vis: 3.5, clYoY: -8.5 },
];
```

### 18.2 欄位定義

- `id`:股票代號(4 位數字字串)
- `name`:公司簡稱
- `industry`:產業代碼(對應 §18.3)
- `market`:`twse` / `otc`
- `hasCL`:是否有顯著合約負債(依 v2.2 §4.2 邏輯,此處直接標記)
- `rev`:當季營收(百萬)
- `revYoY`:營收 YoY(%)
- `gm`:毛利率(%)
- `om`:營益率(%)
- `nm`:淨利率(%)
- `noiRatio`:業外占淨利(%)
- `gmQoQ` / `omQoQ` / `nmQoQ`:三率 QoQ 變化(百分點)
- `gc`:近 30 天內是否 3MA 黃金交叉 12MA(boolean)
- `vis`:訂單能見度月數(hasCL=false 為 `null`)
- `clYoY`:合約負債 YoY(%,hasCL=false 為 `null`)

**注意:此樣本股 6789 與 v2.2 Detail 頁樣本一致,以便點列跳轉 Detail 頁看到完整資料**。

### 18.3 產業對照表

| 代碼 | 中文顯示 |
|---|---|
| `all` | 全部 |
| `semi` | 半導體 |
| `component` | 電子零組件 |
| `integration` | 系統整合 |
| `chemical` | 化工 |
| `biotech` | 生技 |
| `machinery` | 機械 |
| `traditional` | 傳產 |
| `finance` | 金融 |

**Scanner 產業下拉選單順序**:全部 → 半導體 → 電子零組件 → 系統整合 → 化工 → 生技 → 機械 → 傳產 → 金融

---

## §19 互動行為總覽

沿用 §14 的觸發與 URL 序列化規則。額外補充:

### 19.1 篩選面板與 URL 雙向繫結

- URL 是單一真相來源(single source of truth)
- 所有 UI 元件變動 → 更新 URL → 觸發 applyFilters() → 更新結果表
- URL 手動變動(使用者貼書籤、按 Back)→ popstate 事件 → 重新反填 UI + 應用篩選

### 19.2 Slider dual-thumb 實作

- **必須用原生 `<input type="range">` × 2 疊加**,禁 3rd-party slider 套件
- 兩個 thumb 分別代表 min / max,位置以 CSS overlay 呈現
- 拖動時即時更新旁邊的數值顯示
- Debounce 200ms 後才更新 URL

### 19.3 「清除全部」的預設狀態

= URL 完全無 querystring 的狀態:
- 所有 slider 兩端 thumb
- 所有下拉為「全部」
- 所有 toggle off
- 排序 `score,desc`
- 分頁 1
- 命中 = 全市場總數(20)

---

## §20 技術限制

繼承 v2.2 §20 全部。額外:

- **不引入 debounce 套件**,自己實作 6 行的 debounce 函式
- **不引入 slider 套件**,原生 `<input type="range">` 疊 2 個實作 dual-thumb
- **URL 操作只用 `URLSearchParams` + `history.replaceState/pushState`**,禁其他 router 套件

---

## §21 交付檢查清單

### 核心 grep gate(交付前用命令列驗證)

```bash
grep -c "V1Reference\|V2Reference\|Legacy\|Deprecated" scanner.html    # 期望 0
python3 -c "import re;print(len(re.findall(r'單位:', open('scanner.html').read())))"  # 半形 期望 0
grep -c "\[cite" scanner.html                                          # 期望 0
grep -c "localStorage\|sessionStorage\|indexedDB" scanner.html         # 期望 0
```

### 結構檢查

- [ ] Topbar 沿用 v2.2 §8,「選股掃描」nav tab active
- [ ] Scanner Hero 含命中統計 + 清除全部按鈕
- [ ] 篩選面板 6 個 section 順序:分類 / 財務健康度 / 成長動能 / 訂單能見度 / 交叉指標 / 清除全部
- [ ] 策略模板 chip 列 4 個 chip,順序:訂單池累積 / 本業穩健 / 三率同升 / 動能轉正
- [ ] 結果表 8 欄順序固定,見 §12.1
- [ ] Meta 資訊條沿用 v2.2 樣式

### 樣本資料

- [ ] §18.1 20 檔樣本股完整照抄,不改動任何數值
- [ ] §18.3 9 個產業代碼與中文對照完整照抄

### 篩選邏輯

- [ ] Slider 為 dual-thumb 區間選擇
- [ ] Slider 未偏離兩端時不寫入 URL
- [ ] 下拉為「全部」時不寫入 URL
- [ ] Toggle off 時不寫入 URL
- [ ] 啟用 CL 相關 slider 時自動排除 hasCL=false 股票
- [ ] 模板 chip 4 組對應條件正確(§11)
- [ ] 同時只能啟用 1 個模板
- [ ] 手動改動 slider 後模板 chip 高亮消失

### 結果表

- [ ] 每欄標題含單位小字(§12.1)
- [ ] 健康度總分依 §7.5 公式計算,顯示 1 位小數
- [ ] 健康度總分依 §7.5 色碼上色
- [ ] 能見度欄位,hasCL=false 顯示 `—` 不是 `0`
- [ ] 預設排序 `score,desc`
- [ ] 點列跳 `stock.html?id={股號}`
- [ ] 分頁每頁 25 筆,樣本 20 檔全顯示於單頁
- [ ] 空狀態顯示 §12.4 訊息

### URL 與路由

- [ ] URL key 依 §2.4 對照表
- [ ] Slider debounce 200ms 才更新 URL
- [ ] 頁面載入時反向解析 URL 填入 UI
- [ ] popstate 事件正確處理(Back/Forward button)
- [ ] URL 中的 `template` 與其他 filter key 可共存(§11.1 允許再微調)

### 響應式

- [ ] 桌面兩欄並列
- [ ] 平板/手機篩選面板收合為抽屜
- [ ] 手機結果表切換為卡片(§12.5)

### 色系 / 單位 / 命名

- [ ] 色系依 v2.2 §5,無彩虹
- [ ] 結果表 cell 純數字,單位在欄位標題(v2.2 §6)
- [ ] 命中統計、slider 值顯示可帶單位(inline 描述)
- [ ] UI 無 CL / GP / OP / NP / COGS / REV 等英文縮寫
- [ ] URL key 可為英文縮寫(此為技術例外,非 UI)

### v2.2 繼承檢查

- [ ] 全形冒號 `:`(v2.2 §6.3)
- [ ] 無 V*Reference / Legacy / Deprecated 死函數
- [ ] 無 `[cite: N]` AI 標記污染

---

## §22 前次交付審計

**Scanner v1 為首輪產出,無前次審計紀錄**。以下列出 v2.2 Detail 頁的失誤模式作警惕(這些禁反覆重犯):

1. 保留 V1Reference 死函數 → §3 v2.2 已鎖
2. 半形冒號 `單位:` → §3 v2.2 已鎖
3. `[cite: N]` 註解污染 → §3 v2.2 已鎖
4. 表格每格加「百萬」後綴 → §6 v2.2 已鎖
5. `fmt.money()` 全域附加單位 → §22 v2.2 已鎖

Scanner v1 交付後,若出現失誤,將於 v2 迭代寫入本節。

---

## §23 智能單位切換

沿用 v2.2 §23 全部。Scanner 結果表的「當季營收」欄應呼叫:

```javascript
const revUnit = pickUnit(Math.max(...filteredList.map(s => s.rev)));
// 表頭:當季營收 (${revUnit.unit})
// Cell:formatMoney(stock.rev, revUnit)
```

樣本股中最大 rev = 125,000(2308 示範電子),超過 THRESHOLD_TO_YI = 50,000,**應觸發「億」單位切換**。

**這是驗證 §23 是否正確實作的關鍵測試場景**:若表頭仍顯示「當季營收(百萬)」而非「當季營收(億)」,§23 未正確接入。

---

## §24 仲裁優先序

```
§3(禁止事項)
 > §0(執行指令)
 > §7.5(健康度總分公式)
 > §14(URL 序列化)
 > v2.2 §6 + §23(金額單位)
 > v2.2 §5(design tokens)
 > §17(tooltip 文字)
 > §4(頁面架構)
 > 其他章節
```

**不得基於「經驗上這樣比較好」或「一般設計慣例」為由偏離本規格。**

---

**規格書結束**
版本 SPEC-scanner-v1 · 2026-08-22
產出對象:`scanner.html`
前置依賴:SPEC v2.2 Detail 頁
