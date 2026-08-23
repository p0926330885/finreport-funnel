# 財報轉化漏斗 · stock.html 精修 patch v2.1 → v2.2 定版

**任務類型**:對現有 `stock.html` 執行精修 patch,**不重新生成、不換架構、不重寫**  
**基底檔案**:v2.1 版 `stock.html`(由使用者附上)  
**產出**:精修後的單一 `stock.html`  
**同步更新**:SPEC 主體升級至 v2.2(§3、§6.3、§21、§22 新增條目)

---

## §0. 給接手 AI(GPT)的執行指令

1. 這是**精修 patch 任務**,對使用者附上的 `stock.html` 執行 §Patch A 與 §Patch B。**不得**重新生成整個 HTML 檔案,不得換架構、不得美化、不得補功能、不得加除錯註解。
2. 不要提出方案 A/B/C。所有決策已鎖。
3. 只執行以下 §Patch A 與 §Patch B,其餘所有原有內容(包含 CSS tokens、DOM 結構、事件綁定、Chart.js 設定、樣本資料)**一字不改**。
4. Patch 完成後,對照 §21 v2.2 檢查清單(含 v2.1 全部要項 + v2.2 新增 3 條)逐項確認,再交付。
5. 交付格式:**單一 HTML 檔**,inline CSS + inline JS,不變。
6. 若你發現本 patch 之外的其他問題,**不得自作主張修改**,寫進交付時的補充註記給使用者(不寫在 HTML 檔內)。

---

## §Patch A:刪除 6 個死代碼函數

### A.1 定位

在 `stock.html` 內找到以下 6 個函數宣告(v2.1 版本行號僅供參考,以實際內容為準):

| # | 函數名 | v2.1 起始行號(參考) |
|---|---|---|
| 1 | `renderClStatsV1Reference` | ~1534 |
| 2 | `renderFunnelV1Reference` | ~1669 |
| 3 | `renderGaugesV1Reference` | ~1782 |
| 4 | `renderInsightsV1Reference` | ~1816 |
| 5 | `renderMaChartV1Reference` | ~1963 |
| 6 | `renderTableV1Reference` | ~2118 |

### A.2 刪除範圍

每個函數:**從 `function xxxV1Reference() {` 那行開始,一路刪除到函數的閉合大括號 `}` 為止**,包含該函數上方緊鄰的區塊註解(如 `/* ===== Funnel — 4-stage only ===== */` 這類 header)。

### A.3 禁止事項

- ❌ 不留 stub(不要保留空函數殼)
- ❌ 不留註解說明「此處已刪除」
- ❌ 不留 fallback(不要把邏輯合併到活函數)
- ❌ 不要動任何其他函數

### A.4 已驗證安全

這 6 個函數目前**都無 caller**,刪除不影響執行。若 GPT 發現有 caller,**停手詢問使用者**,不得自己合併邏輯。

註:`renderTableV1Reference` 內部 line 2152 呼叫 `renderTable()`,由於 JS 函數提升會綁到後定義的活函數(line 2707),看似有內部呼叫,實則是碰巧無害的死代碼陷阱。刪除後陷阱一併消失。

### A.5 驗證

Patch 完成後執行:

```bash
grep -c "V1Reference" stock.html
```

**必須回傳 `0`**。若非 0,patch 未完成。

---

## §Patch B:全形冒號統一

### B.1 定位所有「單位:」半形冒號

搜尋整份檔案,找出以下模式:

- `單位:百萬`(HTML 內容)
- `單位:${...}`(JS template literal 動態設值)
- 任何 `單位` 字後緊接半形冒號 `:`(U+003A)

### B.2 替換規則

所有 `單位:`(半形冒號 U+003A)→ `單位:`(全形冒號 U+FF1A)

**必須改的位置(v2.1 版本行號僅供參考)**:
- ~Line 1373:`<span class="unit" id="clCardUnit">單位:百萬</span>` → `單位:百萬`
- ~Line 1382:`<span class="unit" id="funnelUnit">單位:百萬</span>` → `單位:百萬`
- JS 內 `textContent = \`單位:${unitInfo.unit}\`` → `textContent = \`單位:${unitInfo.unit}\``(全部發生位置)

### B.3 禁止事項

- ❌ 不動除 `單位:` 之外的其他冒號(判讀敘述句、tooltip 內容、chart tooltip 等的中英冒號維持原狀)
- ❌ 不動 CSS `property: value` 的冒號
- ❌ 不動 JS object literal `key: value` 的冒號

### B.4 驗證

```bash
grep -c "單位:" stock.html   # 半形,應該 0
grep -c "單位:" stock.html   # 全形,應該 ≥ 5
```

---

## §SPEC v2.2 增量修訂(帶回 SPEC 主體,永久生效)

以下規則寫進 SPEC v2.2,適用於**所有未來輪次**,GPT 下次無論做 patch 或全新生成都必須遵守。

### §3 絕對禁止事項(新增 3 條)

原有禁止項全部保留,新增:

- ❌ **保留死代碼函數**:命名含 `V0` / `V1` / `V2` / `Reference` / `Legacy` / `Deprecated` / `Old` / `Backup` / `Test` / `Draft` 等 prefix/suffix 的未被呼叫函數。發現時**必須刪除**,不得保留為「參考」
- ❌ **單位標示用半形冒號**:`單位:` 一律用全形 `:`(U+FF1A),不得用半形 `:`(U+003A)
- ❌ **註解中留 AI 內部標記**:如 `[cite: N]`、`<citation>`、`[ref: N]`、`// AI-generated:`、`// TODO for AI:` 等由 AI 產出流程造成的標記污染

### §6.3 單位小字樣式(修訂)

原文:「表格欄位單位後綴:`營收(百萬)`」保留不變。

**新增**:「單位:百萬」形式的冒號**必須用全形 `:`(U+FF1A)**,不得用半形 `:`(U+003A)。理由:繁體中文標點規範,且視覺上全形冒號與中文字寬對齊更整齊。

### §21 交付檢查清單(v2.2 新增 3 條,原 v2.1 全部保留)

- [ ] §4 9 個區塊順序正確(v2.1)
- [ ] hasCL 顯示邏輯正確(§4.2)(v2.1)
- [ ] 色系依 §5,無彩虹(v2.1)
- [ ] 表格 cell 無「百萬」後綴,單位在欄位標題(v2.1)
- [ ] 漏斗 tier 主值無「百萬」後綴,單位在卡片右上(v2.1)
- [ ] 洩漏帶金額無「百萬」後綴(v2.1)
- [ ] 訂單能見度 KPI 主值無「百萬」後綴,單位在卡片右上(v2.1)
- [ ] §23 智能單位切換演算法正確實作(v2.1)
- [ ] EPS 用「元」小數 2 位(v2.1)
- [ ] 能見度用「個月」小數 1 位(v2.1)
- [ ] 比率用 `%`(v2.1)
- [ ] UI 無 CL/GP/OP/NP/COGS/REV 等英文縮寫(v2.1)
- [ ] 漏斗 4 段,CL 不在鏈內(v2.1)
- [ ] §22 前次失誤已避免(v2.1)
- [ ] §17 tooltip 完整(v2.1)
- [ ] hero chips 依 §9.2 可互動(v2.1)
- [ ] 樣本資料照抄 §18(v2.1)
- [ ] 單一 HTML 檔(v2.1)
- [ ] **無 V0/V1/V2/Reference/Legacy/Deprecated/Old/Backup 等死函數**(v2.2 新增)
- [ ] **所有 `單位:` 用全形冒號 `:`(U+FF1A)**(v2.2 新增)
- [ ] **無 `[cite: N]` 或其他 AI 內部標記污染**(v2.2 新增)

### §22 前次交付審計(補入 v2.2 迭代)

**v2.1 stock.html(本次要修完的基底)失誤**:

1. **6 個 `V1Reference` 死函數保留**(1534/1669/1782/1816/1963/2118 起,共 ~540 行技術負債)
   - 根因:AI 在 patch 過程中新舊實作並存,未清掃舊版
   - 修法:§Patch A 完整刪除
2. **`單位:` 使用半形冒號**(5+ 處)
   - 根因:AI 未意識到繁體中文標點規範
   - 修法:§Patch B 全形替換

**v2.1 Gemini 版失誤(此次不採用其為基底,列出以警惕)**:

1. **註解中留 30 處 `[cite: 3]` AI 內部標記**
   - 根因:Gemini 把 SPEC 引用標記當內容抄進 JS 註解
   - 修法:§3 v2.2 新增禁止項

**根因總結**:AI 交付前未做「靜態掃描」自我審計。§21 v2.2 檢查清單新增條目正是為這類低級失誤設立的 gate。

---

## §24 仲裁優先序(v2.2 不變)

```
§3(禁止事項)
 > §7(業務公式)
 > §6 + §23(金額單位)
 > §5(design tokens)
 > §17(tooltip 文字)
 > §4(頁面架構)
 > §22(前次審計)
 > 其他章節
```

---

## 【本次 patch 任務結束】

**GPT 完成 §Patch A + §Patch B 並通過 §21 v2.2 全部檢查後,將修改後的 `stock.html` 回傳給使用者,由使用者交給 Claude 進行最終 audit**。

**Claude 收到 GPT 交付後的 audit 檢查**(此段給 Claude,不給 GPT):
```bash
grep -c "V1Reference" stock.html        # 期望 0
grep -c "單位:" stock.html               # 期望 0
grep -c "單位:" stock.html               # 期望 ≥ 5
grep -c "\[cite" stock.html              # 期望 0
```

若四項全通過,pass 定版;若有任一項未過,列 patch 未執行完的部分回傳 GPT 補做。
