# 財報轉化漏斗 · v2.2 前端規格書(LOCKED SPEC)

**版本**:v2.2 · 2026-08-22
**取代**:v2.1
**類型**:前端規格(僅設計,不生產)
**Baseline HTML**:GPT 交付的 v2.2 版 `stock.html`(通過 4 條核心 grep gate 定版)

---

## §0. 給接手 AI(生產 AI)的執行指令

1. 本規格書為單一真相來源。照做即可,不得自行變更架構、色系、命名、單位、術語、公式。
2. 不要提出方案 A/B/C。所有決策已鎖。
3. 產出格式:單一 HTML 檔案(inline CSS + inline JS)。純 HTML/CSS/Vanilla JS + Chart.js v4(CDN)。**禁**任何 framework、build 工具、其他 npm 套件。
4. 樣本資料沿用 §18,不得改動。
5. 金額單位依 §6 §23。**禁** M/K/B/T(除 chart 軸標籤例外)。UI 元件內部值不帶單位,單位由該表/該卡標示一次。
6. 英文縮寫(CL、GP、OP、NP、COGS、Opex 等)**只能在 JS 變數與 CSS class**。UI 文字必須全中文。允許保留:EPS、YoY、QoQ、TWSE、股票代號。
7. 每個指標與區塊都必須有 hover tooltip,內容照抄 §17。
8. 遇到未涵蓋細節,採「保守 + 極簡」原則。**不得自行發明**。
9. §22 列出前次交付失誤,禁重複。
10. 交付前逐項對照 §21 檢查清單。
11. 若規格內部矛盾,依 §24 仲裁優先序處理。
12. **精修 patch 任務時,不得重新生成整個 HTML**。只執行指定 patch,其餘一字不改。

---

## §1. 產品定位

台股財報分析工具的 Detail 頁。單一任務:讓使用者一眼看出這家公司這一季價值在漏斗哪一段漏掉、訂單池水位是否累積、成長動能是否延續。

---

## §2. 命名慣例

### 2.1 內部代號 → 使用者可見文字對照

| 內部代號 | UI 顯示 |
|---|---|
| CL | 合約負債 |
| Revenue / REV | 營業收入 / 營收 |
| GP | 營業毛利 / 毛利 |
| OP | 營業利益 / 本業獲利 |
| NP | 稅後淨利 / 淨利 |
| COGS | 營業成本 |
| Opex / SG&A / R&D | 營業費用 |
| Non-operating | 業外收支 / 業外 |
| Tax | 所得稅 |
| Backlog | 訂單池 / 訂單能見度 |
| MA | 月均線(3MA→3 月均線) |
| Landing rate | 落地率 |

### 2.2 允許保留的英文

EPS、YoY、QoQ、TWSE、股票代號

### 2.3 核心概念定義

- **合約負債**:公司已收預付款、未交付的部分。屬未來營收的燃料,通常 1–2 年內分批認列,不是本季一定全消化
- **訂單能見度**:CL 期末餘額相當於未來幾個月的營收
- **營收轉換漏斗**:營收 → 毛利 → 營業利益 → 淨利 的四段轉化。CL 不在鏈內

---

## §3. 絕對禁止事項

- ❌ CL 放漏斗鏈第一段。禁「消化率 = 本季營收 / 上季 CL」
- ❌ 彩虹配色
- ❌ emoji 作為 UI 元件
- ❌ M/K/B/T 顯示金額(除 chart 軸標籤空間有限的例外)
- ❌ 英文變數名在 UI(GP、OP、NP、CL、COGS、REV)
- ❌ 每格金額都加「百萬」後綴(v2 普遍錯,見 §22)
- ❌ 自行新增區塊或功能
- ❌ React、Vue、Angular、Svelte、Alpine.js
- ❌ Sass、Tailwind CLI 等需編譯工具
- ❌ 「消化率」「Backlog 消化」等誤導詞
- ❌ 改動 §18 樣本資料
- ❌ **【v2.2 新增】保留死代碼函數**:命名含 `V0` / `V1` / `V2` / `Reference` / `Legacy` / `Deprecated` / `Old` / `Backup` / `Test` / `Draft` 等 prefix/suffix 的未被呼叫函數。發現時**必須刪除**,不得保留為「參考」
- ❌ **【v2.2 新增】單位標示用半形冒號**:`單位:` 一律用全形 `:`(U+FF1A),不得用半形 `:`(U+003A)
- ❌ **【v2.2 新增】註解中留 AI 內部標記**:如 `[cite: N]`、`<citation>`、`[ref: N]`、`// AI-generated:`、`// TODO for AI:` 等由 AI 產出流程造成的標記污染

---

## §4. 頁面架構(Detail 頁)

```
1. Topbar
2. Stock Hero
3. 訂單能見度(4 KPIs 精簡面板)
4. 營收轉換漏斗(4 段)
5. 品質儀表 + 自動判讀(桌面並排)
6. 成長率分析(6 subtab,含合約負債-流動)
7. 月營收動能(M/Q/Y + 3 條 MA)
8. 季度資料表(4 view tab)
9. Meta 資訊條
```

### 4.1 CL 分兩處呈現

- 訂單能見度:精簡 4 KPI,快速看水位
- 合約負債-流動 subtab:完整聖暉風格 bar+line + 詳細表

### 4.2 CL 顯示條件

`hasCL = max(近8季 CL) / max(近8季營收) > 0.15`

- hasCL true:兩處都顯示
- hasCL false:訂單能見度整區隱藏、合約負債-流動 subtab 從 tab bar 移除

---

## §5. Design Tokens

```css
:root {
  --bg: #0d0f14; --bg-2: #0a0c11;
  --panel: #151922; --panel-2: #1c2130; --panel-3: #232838;
  --line: #262c3d; --line-2: #303648; --line-soft: #1e2331;
  --text: #e8ecf2; --text-dim: #8a94a8; --text-muted: #5a6478;
  --brass: #d4a574; --brass-2: #b88a5b; --brass-hi: #e6bd8f;
  --brass-dim: rgba(212, 165, 116, 0.15); --brass-line: rgba(212, 165, 116, 0.35);
  --mint: #5eead4; --mint-dim: rgba(94, 234, 212, 0.15); --mint-line: rgba(94, 234, 212, 0.4);
  --amber: #fbbf24; --amber-dim: rgba(251, 191, 36, 0.15); --amber-line: rgba(251, 191, 36, 0.4);
  --coral: #f87171; --coral-dim: rgba(248, 113, 113, 0.15); --coral-line: rgba(248, 113, 113, 0.4);
}
```

- **brass**:主鏈價值(漏斗填充、CL bar、3MA、品牌)
- **mint**:健康正向(綠燈、季營收 line、黃金交叉、正成長)
- **amber**:警戒中間(黃燈、6MA)
- **coral**:損失警告(洩漏帶、紅燈、死亡交叉、負成長)
- 禁:藍、紫、粉、綠(mint 外)、橘(amber 外)

字體:`'Inter', 'Noto Sans TC', sans-serif` UI、`'JetBrains Mono', monospace` 數字

---

## §6. 金額單位規範

### 6.1 核心原則

**UI 元件內部金額值,絕對不帶單位後綴**。單位由容器(表 header / 卡片標題)標示一次且僅一次。

### 6.2 場景表

| 元件類型 | 格式 | 單位標示位置 |
|---|---|---|
| 表格 cell(金額欄) | `7,645`(純數字) | 欄位標題 `營收(百萬)` |
| 表格 cell(比率欄) | `28.1%` | 不需外部標示 |
| 表格 cell(EPS 欄) | `4.20` | 欄位標題 `EPS(元)` |
| 表格 cell(能見度欄) | `1.7` | 欄位標題 `能見度(月)` |
| 卡片內 KPI 主值 | `7,645`(純數字) | 卡片右上 `單位:百萬` |
| 漏斗 tier 主值 | `13,381`(純數字) | 漏斗卡右上 `單位:百萬` |
| 洩漏帶金額 | `−9,835`(帶正負純數字) | 漏斗卡統一 |
| Chart tooltip | `13,381 百萬`(含單位) | 例外允許(建議走 `moneyWithUnit()` helper) |
| 自動判讀敘述句 | `13,381 百萬`(含單位) | 例外允許(中文語感,建議走 `moneyWithUnit()` helper) |
| Chart 軸標籤 | `13K`(允許簡寫) | 空間有限例外 |

### 6.3 單位小字樣式

- 表格欄位單位後綴:`營收(百萬)`,單位字級 11px、text-muted、mono
- 卡片右上單位標示:`單位:百萬`,11-12px、mono、text-muted
- **禁**每個 cell 後面加「百萬」灰字後綴
- **【v2.2】「單位:」的冒號必須用全形 `:`(U+FF1A),不得用半形 `:`(U+003A)**。理由:繁體中文標點規範,且視覺上全形冒號與中文字寬對齊更整齊

### 6.4-6.6 例外

- EPS 用「元」,小數 2 位,欄位標題 `EPS(元)`
- 比率用 `%`,標題不加單位:`毛利率`(不是「毛利率(%)」)
- 帶正負號用長減號 U+2212:`+28.1%` / `−28.1%`
- 能見度用「個月」,小數 1 位。這是唯一 cell 內保留單位的例外(`1.7 個月`)

---

## §7. 業務公式

### 7.1 比率

| 名稱 | 公式 |
|---|---|
| 毛利率 | 毛利 ÷ 營收 × 100% |
| 營益率 | 營業利益 ÷ 營收 × 100% |
| 淨利率 | 淨利 ÷ 營收 × 100% |
| 落地率 | 營業利益 ÷ 毛利 × 100% |
| 業外占淨利 | 業外收支 ÷ 淨利 × 100% |
| 合約負債占季營收比 | CL ÷ 當季營收 × 100% |
| 訂單能見度(月) | CL ÷ 該季 3 個月平均月營收 |

### 7.2 成長率

- YoY = (本期 / 去年同期 − 1) × 100%
- QoQ = (本期 / 上一期 − 1) × 100%

### 7.3 健康度閾值(不可改)

| 指標 | 綠 | 黃 | 紅 |
|---|---|---|---|
| 毛利率 | ≥25% | 15–25% | <15% |
| 營益率 | ≥8% | 3–8% | <3% |
| 淨利率 | ≥5% | 0–5% | <0% |
| 落地率 | ≥40% | 20–40% | <20% |
| 業外占淨利(絕對值) | <25% | 25–70% | >70% |
| 業外+稅占營業利益 | <20% | 20–40% | >40% |
| 合約負債 YoY | >30% | 0–30% | <0% |

### 7.4 均線與交叉

- N 期均線:近 N 期算術平均
- 黃金交叉:短線由下穿過長線
- 死亡交叉:短線由上穿過長線
- 只用 3MA vs 12MA 判定交叉,6MA 不參與

---

## §8-§16 各區塊規格

**§8 Topbar**:60px 高、sticky top、blur;左到右:品牌 + Nav tab + spacer + 搜尋 + 資料狀態

**§9 Stock Hero**:左股號+名+chips、右季度切換。**4 個 chip 都要 tooltip 或點擊行為**(產業→跳 Scanner、財報→顯示公布日、月營收→同)

**§10 訂單能見度**:hasCL 判定顯示。**卡片右上標「單位:百萬/億」**(全形冒號)。4 KPI 橫排:期末餘額(純數字)、YoY、QoQ、能見度(1.7 個月 + 占季營收 X%)。下方判讀敘述句

**§11 營收轉換漏斗**:**4 段固定**,CL 絕不進鏈。3 個洩漏帶:營收→毛利(毛利率 pill + 營業成本)、毛利→營業利益(落地率 pill + 營業費用)、營業利益→淨利(業外+稅 pill)。**卡片右上標單位**(全形冒號)。tier 主值純數字,寬度 sqrt 壓縮

**§12 品質儀表**:4 KPI:毛利率、營益率、淨利率、業外占淨利。全比率無需外部單位。每 KPI 有健康度光暈進度條

**§13 自動判讀**:1-4 條 insight,依健康度左邊框上色。**敘述句可帶「百萬」單位**(§6.2 例外,建議走 `moneyWithUnit()` helper)

**§14 成長率分析**:6 subtab(營收/毛利/營益/淨利/EPS/合約負債-流動),右上 YoY/QoQ toggle。每 subtab:左 stat panel + 右 bar+line chart + 底部詳細表。**表頭欄位含單位**(如「營收(百萬)」)

**§15 月營收動能**:右上 M/Q/Y toggle;chart legend 3 條(3/6/12 月均線)可獨立 toggle 隱藏。bar 底 + 3 線疊。黃金/死亡交叉圓點標記(僅 3MA vs 12MA)

**§16 季度資料表**:4 view(標準/完整/合約負債明細/成長率)。**表頭欄位含單位**。列可點切換全域季度

---

## §17 Tooltip 對照表(照抄)

| 位置 | Tooltip |
|---|---|
| 合約負債期末 | 「公司預收但尚未認列為營收的部分。屬未來營收的燃料,通常在 1–2 年內分批認列,不是本季一定會全部消化。」 |
| 訂單能見度(月) | 「合約負債 ÷ 該季 3 個月平均月營收 = 未來能見的月數。>3 個月為長訂單能見度、1–3 個月為一般、<1 個月為短期能見度。」 |
| 毛利率 | 「毛利 ÷ 營收。反映產品定價力與成本控制。≥25% 綠 / 15–25% 黃 / <15% 紅。」 |
| 營益率 | 「營業利益 ÷ 營收。反映本業獲利能力。≥8% 綠 / 3–8% 黃 / <3% 紅。」 |
| 淨利率 | 「淨利 ÷ 營收。≥5% 綠 / 0–5% 黃 / <0% 紅。」 |
| 落地率 | 「營業利益 ÷ 毛利。反映毛利被費用吃掉多少。≥40% 綠 / 20–40% 黃 / <20% 紅。」 |
| 業外占淨利 | 「業外 ÷ 淨利。反映淨利中有多少來自本業以外。絕對值 <25% 為健康。」 |
| 3 月均線 | 「近 3 個月營收的算術平均。反映短期動能。」 |
| 6 月均線 | 「近 6 個月營收的算術平均。反映中期動能。」 |
| 12 月均線 | 「近 12 個月營收的算術平均。反映長期趨勢。」 |
| 黃金交叉 | 「短期均線由下往上穿過長期均線。代表動能轉正。」 |
| 死亡交叉 | 「短期均線由上往下穿過長期均線。代表動能轉負。」 |

(其餘 tooltip 沿用 v2.2 baseline HTML `tooltips` 物件內容)

---

## §18 樣本股資料(照抄)

```javascript
const stock = {
  id: '6789',
  name: '示範系統',
  industry: '系統整合',
  hasCL: true,
  quarterly: [
    { q: '2024/3Q', cl: 3531, rev: 7621,  gp: 1677, op: 381,  noi: -11, np: 370,  eps: 1.45 },
    { q: '2024/4Q', cl: 4043, rev: 9497,  gp: 2165, op: 522,  noi: -14, np: 508,  eps: 1.99 },
    { q: '2025/1Q', cl: 4707, rev: 8556,  gp: 2011, op: 513,  noi: -13, np: 500,  eps: 1.96 },
    { q: '2025/2Q', cl: 5391, rev: 10908, gp: 2640, op: 709,  noi: -19, np: 690,  eps: 2.70 },
    { q: '2025/3Q', cl: 5967, rev: 11042, gp: 2761, op: 751,  noi: -20, np: 731,  eps: 2.86 },
    { q: '2025/4Q', cl: 5946, rev: 10976, gp: 2799, op: 790,  noi: -19, np: 771,  eps: 3.02 },
    { q: '2026/1Q', cl: 6934, rev: 11603, gp: 3017, op: 870,  noi: -20, np: 850,  eps: 3.33 },
    { q: '2026/2Q', cl: 7645, rev: 13381, gp: 3546, op: 1097, noi: -25, np: 1072, eps: 4.20 },
  ],
  monthly: [
    ['2024-07', 2530], ['2024-08', 2540], ['2024-09', 2551],
    ['2024-10', 3050], ['2024-11', 3160], ['2024-12', 3287],
    ['2025-01', 2900], ['2025-02', 2700], ['2025-03', 2956],
    ['2025-04', 3520], ['2025-05', 3650], ['2025-06', 3738],
    ['2025-07', 3650], ['2025-08', 3680], ['2025-09', 3712],
    ['2025-10', 3620], ['2025-11', 3680], ['2025-12', 3676],
    ['2026-01', 3670], ['2026-02', 3800], ['2026-03', 4133],
    ['2026-04', 4088], ['2026-05', 4519], ['2026-06', 4774],
    ['2026-07', 3885], ['2026-08', 4100],
  ]
};
```

金額單位:百萬。欄位:cl=合約負債、rev=營收、gp=毛利、op=營業利益、noi=業外、np=淨利、eps=每股盈餘

---

## §19 互動行為

### 全域同步(切換季度)

- 點季度按鈕 / 點資料表列 / 點 CL 圖 bar → 全動態區塊同步刷新
- 點成長率 subtab / YoY-QoQ toggle → 只影響該卡
- 點月營收 M/Q/Y / MA legend → 只影響月營收 chart
- 點表頭 → 只重排該表

---

## §20 技術限制

- 允許:HTML5 + CSS3 + Vanilla JS + Chart.js v4.4.x(CDN)+ Google Fonts CDN
- 單一 HTML 檔,inline CSS/JS
- 禁:framework、npm、Sass、Tailwind CLI、其他 JS 套件、backend、localStorage

---

## §21 交付檢查清單(v2.2)

### 核心 grep gate(4 條,交付前用命令列驗證)

```bash
grep -c "V1Reference" stock.html                    # 期望 0
python3 -c "import re;print(len(re.findall(r'單位:', open('stock.html').read())))"  # 半形 期望 0
python3 -c "import re;print(len(re.findall(r'單位:', open('stock.html').read())))"  # 全形 期望 ≥5
grep -c "\[cite" stock.html                         # 期望 0
```

### v2.1 保留條目

- [ ] §4 9 個區塊順序正確
- [ ] hasCL 顯示邏輯正確(§4.2)
- [ ] 色系依 §5,無彩虹
- [ ] 表格 cell 無「百萬」後綴,單位在欄位標題
- [ ] 漏斗 tier 主值無「百萬」後綴,單位在卡片右上
- [ ] 洩漏帶金額無「百萬」後綴
- [ ] 訂單能見度 KPI 主值無「百萬」後綴,單位在卡片右上
- [ ] §23 智能單位切換演算法正確實作
- [ ] EPS 用「元」小數 2 位
- [ ] 能見度用「個月」小數 1 位
- [ ] 比率用 `%`
- [ ] UI 無 CL/GP/OP/NP/COGS/REV 等英文縮寫
- [ ] 漏斗 4 段,CL 不在鏈內
- [ ] §22 前次失誤已避免
- [ ] §17 tooltip 完整
- [ ] hero chips 依 §9.2 可互動
- [ ] 樣本資料照抄 §18
- [ ] 單一 HTML 檔

### v2.2 新增

- [ ] **無 V0/V1/V2/Reference/Legacy/Deprecated/Old/Backup 等死函數**
- [ ] **所有 `單位:` 用全形冒號 `:`(U+FF1A)**
- [ ] **無 `[cite: N]` 或其他 AI 內部標記污染**

---

## §22 前次交付審計(禁重複)

### v2.1 stock.html(已修完,列出以警惕)

1. **6 個 `V1Reference` 死函數保留**(1534/1669/1782/1816/1963/2118 起,~540 行)
   - 根因:AI 在 patch 過程中新舊實作並存,未清掃舊版
   - 修法:§3 v2.2 新增禁止項、§Patch A 完整刪除
2. **`單位:` 使用半形冒號**(5+ 處)
   - 根因:AI 未意識到繁體中文標點規範
   - 修法:§6.3 v2.2 修訂

### v2.1 Gemini 版(未採用為基底,列出以警惕)

1. **註解中留 30 處 `[cite: 3]` AI 內部標記**
   - 根因:Gemini 把 SPEC 引用標記當內容抄進 JS 註解
   - 修法:§3 v2.2 新增禁止項

### v2 GPT/Gemini 版失誤(v2.1 已修完)

1. 表格每格金額後綴「百萬」全部重複
2. 判讀敘述句出現 `${fmt.int(q.cl)}M` — 中文上下文冒 M
3. 每個金額嵌入 `<span class="u">百萬</span>` 22+ 處
4. `fmt.money()` 全域附加單位

### 共通根因與修法

```javascript
// ❌ 錯誤
const money = value => `${fmt.int(value)} 百萬`;

// ✅ 正確
const money = (value, unit = 'M') => {
  if (value == null) return '—';
  if (unit === '億') return (value / 100).toFixed(2);
  return Math.round(value).toLocaleString('en-US');
};

// ✅ v2.2 baseline 進一步用 helper 集中判讀/tooltip 場景
function moneyWithUnit(value, unitInfo) {
  return value == null ? '—' : `${formatMoney(value, unitInfo)} ${unitInfo.unit}`;
}
```

---

## §23 智能單位切換演算法

```javascript
const THRESHOLD_TO_YI = 50000; // 百萬 = 500 億

function pickUnit(maxValue) {
  if (maxValue == null) return { unit: '百萬', divisor: 1, decimals: 0 };
  if (Math.abs(maxValue) >= THRESHOLD_TO_YI) {
    return { unit: '億', divisor: 100, decimals: 2 };
  }
  return { unit: '百萬', divisor: 1, decimals: 0 };
}

function formatMoney(value, unitInfo) {
  if (value == null) return '—';
  const scaled = value / unitInfo.divisor;
  if (unitInfo.decimals === 0) {
    return Math.round(scaled).toLocaleString('en-US');
  }
  return scaled.toLocaleString('en-US', {
    minimumFractionDigits: unitInfo.decimals,
    maximumFractionDigits: unitInfo.decimals,
  });
}

function formatMoneySigned(value, unitInfo) {
  if (value == null) return '—';
  const sign = value >= 0 ? '+' : '−';
  return sign + formatMoney(Math.abs(value), unitInfo);
}

// v2.2 baseline 新增:判讀句與 chart tooltip 走此 helper
function moneyWithUnit(value, unitInfo) {
  return value == null ? '—' : `${formatMoney(value, unitInfo)} ${unitInfo.unit}`;
}
```

判定值來源:
- 訂單能見度卡 → 當季 CL 值
- 漏斗 → 當季營收
- 成長率各 subtab 表 → 該欄近 8 季 max
- 資料表 → 每欄獨立判定
- MA chart → series max

樣本股所有欄位 max 均 < 50,000,全數用「百萬」單位。

---

## §24 仲裁優先序

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

**不得基於「經驗上這樣比較好」或「一般設計慣例」為由偏離本規格。**

---

**規格書結束**
版本 v2.2 · 2026-08-22 · 取代 v2.1
Baseline HTML:GPT 交付 `stock.html`(通過 4 條核心 grep gate)
