# 財報轉化漏斗 · Layer 3.5 前端整合 Patch(LOCKED SPEC)

**版本**:SPEC-integration-v1 · 2026-08-22
**類型**:前端整合 patch(將 hardcoded mock 資料替換為 fetch 動態載入 + 錯誤防呆)
**基底檔案**:
- `stock.html`(SPEC v2.2 定版,GPT 交付)
- `scanner.html`(SPEC-scanner-v1 定版,GPT 交付)
**輸出**:精修後的兩份 HTML,行為完全等價於原版 + 加上網路資料層 + 錯誤 UI
**任務性質**:精修 patch,**不重寫、不換架構、不加功能**

---

## §0. 給接手 AI(GPT)的執行指令

1. 本規格為精修 patch。對使用者附上的 `stock.html` 與 `scanner.html` **分別**執行 §Patch A 與 §Patch B。
2. 不得重新生成整份 HTML,不得改動任何既有 render 函數的內部邏輯。
3. 只執行以下 §Patch A / §Patch B 條目,其餘不動。
4. 保留所有 v2.2 / v1 已通過的檢查:全形冒號、無死代碼、無 `[cite` 污染、無 localStorage、CL 4 段漏斗、健康度公式等等。
5. 產出:兩份精修後 HTML,單一檔各自 inline CSS / JS,不引入外部 JS 依賴。
6. §Patch A 與 §Patch B 使用**完全一致的狀態機設計**(見 §4)、**完全一致的錯誤面板 UI**(見 §2)。不得只做其中一份。
7. 交付前對照 §21 檢查清單逐項確認。若無把握,依 §24 仲裁優先序處理。
8. 遇到未涵蓋細節,採「保守 + 極簡」原則。**不得自行發明新狀態、新按鈕、新色彩**。

---

## §1. 資料契約(繼承 Pipeline SPEC §11)

| 前端頁 | 讀取路徑 | 觸發時機 |
|---|---|---|
| `stock.html?id={id}` | `./data/stocks/${id}.json` | 頁面載入,`id` 缺席時預設 `6789` |
| `scanner.html` | `./data/scanner_index.json` | 頁面載入,無條件 |
| 共通(topbar 資料日期) | `./data/meta.json` | 頁面載入,靜默失敗 |

**相對路徑必用 `./data/...`**,不得改成絕對 URL,不得加 base URL 前綴。GitHub Pages / 本機 `python -m http.server` / 預覽環境全部走同一份 HTML。

---

## §2. 錯誤狀態 UI 設計

### 2.1 四種狀態

| 狀態 | `body[data-status]` 值 | 觸發條件 | 顯示 |
|---|---|---|---|
| 載入中 | `loading` | 初始 + fetch 進行中 | Loading 面板 |
| 成功 | `ok` | 所有必要 fetch 完成 | 主內容(.wrap) |
| 找不到股票 | `notfound` | fetch 回應 404 | NotFound 面板(僅 stock.html) |
| 載入失敗 | `error` | 網路錯誤 / 非 404 HTTP 錯誤 / JSON 解析失敗 | Error 面板 |

### 2.2 統一 CSS(兩份 HTML 都要加,放於 `<style>` 內)

```css
/* ============================================================
   Layer 3.5 · Status panels (統一於 stock.html + scanner.html)
   ============================================================ */
.status-panel {
  min-height: 46vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 60px 20px;
  text-align: center;
  color: var(--text-dim);
}
.status-panel h2 {
  font-size: 18px;
  color: var(--text);
  font-weight: 600;
  letter-spacing: -0.01em;
}
.status-panel p {
  max-width: 400px;
  line-height: 1.6;
}
.status-panel .btn {
  margin-top: 6px;
}
.status-icon {
  width: 44px;
  height: 44px;
  color: var(--brass);
  stroke-width: 1.8;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.status-icon-error { color: var(--coral); }
.status-icon-notfound { color: var(--brass); }
.loading-spinner {
  width: 34px;
  height: 34px;
  border: 3px solid var(--line);
  border-top-color: var(--brass);
  border-radius: 50%;
  animation: status-spin 850ms linear infinite;
}
@keyframes status-spin {
  to { transform: rotate(360deg); }
}
/* 狀態切換 (body attribute driven, 不動任何既有 class) */
body[data-status="loading"] .wrap,
body[data-status="notfound"] .wrap,
body[data-status="error"] .wrap { display: none; }
body[data-status="ok"] .status-panel { display: none; }
/* 手機版:狀態面板適度縮小 */
@media (max-width: 640px) {
  .status-panel { min-height: 36vh; padding: 40px 16px; }
  .status-panel h2 { font-size: 16px; }
  .status-icon { width: 36px; height: 36px; }
}
```

### 2.3 統一 HTML 面板(兩份 HTML 都要加,放於 `<header class="topbar">` 之後、`.wrap` 之前)

```html
<!-- Layer 3.5 · Status panels -->
<div class="status-panel" id="loadingPanel">
  <div class="loading-spinner" aria-label="載入中"></div>
  <p>資料載入中…</p>
</div>

<div class="status-panel" id="notFoundPanel" hidden>
  <svg viewBox="0 0 24 24" class="status-icon status-icon-notfound" aria-hidden="true">
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M15 15 L20 20" />
    <path d="M7 10.5 L14 10.5" />
  </svg>
  <h2>找不到股票 <span id="missingStockId">—</span></h2>
  <p>此股票資料尚未建置,可能是新掛牌、暫停交易,或不在追蹤清單。</p>
  <a href="./scanner.html" class="btn primary" data-tooltip="回到選股掃描器">回到選股頁</a>
</div>

<div class="status-panel" id="errorPanel" hidden>
  <svg viewBox="0 0 24 24" class="status-icon status-icon-error" aria-hidden="true">
    <path d="M12 3 L22 20 L2 20 Z" />
    <path d="M12 10 L12 14" />
    <circle cx="12" cy="17" r="0.5" fill="currentColor" />
  </svg>
  <h2>資料載入失敗</h2>
  <p id="errorDetail">請檢查網路連線後重試。若問題持續,可能是後端資料尚未部署。</p>
  <button type="button" class="btn primary" id="retryBtn" data-tooltip="重新載入頁面">重新載入</button>
</div>
```

**注意**:兩份 HTML 都要加 `notFoundPanel`,但 scanner.html 實際不會觸發 404(index.json 缺失屬於 error 狀態,不是 notfound)。保留 HTML 一致性即可,scanner.html 的 notfound 面板永遠不會顯示。

### 2.4 SVG icon 選擇理由

- **放大鏡**(404):中性、無焦慮感,配 brass 主色與品牌一致
- **警告三角**(error):標準通用符號,配 coral 傳達「有問題但可重試」
- **CSS 旋轉圈**(loading):無需 SVG,純 CSS,重量最小

**禁**用 emoji(依 v2.2 §3),scanner.html §12.4 空狀態的 emoji 例外**不適用**於本 patch 的狀態面板。

---

## §3. 絕對禁止事項

繼承 v2.2 §3 + Scanner v1 §3 全部。以下為 Layer 3.5 專屬:

- ❌ **保留 hardcoded mock 為 fallback**:發現 fetch 失敗時,不得回退到原本的 `const stock = {...}` 或 `const scannerStocks = [...]`。fetch 是唯一資料源,失敗就顯示 error 面板。
- ❌ **改用 `<script type="module">` + top-level await**:維持既有 `<script>` 標籤形式,用 `async function boot()` + `boot()` 呼叫,避免動到既有 script 型別可能牽動的 CORS / MIME 行為。
- ❌ **移動或改動既有 render 函數的定義**:所有 `function renderX()` 定義保持在原位置、原內容。只動「初始執行呼叫」的位置。
- ❌ **新增 localStorage / sessionStorage / cookie**:繼承 Scanner v1 §3。
- ❌ **在 fetch URL 加 querystring 破快取**(如 `?t=${Date.now()}`):瀏覽器自己會處理快取,GitHub Pages 每次 push 也會更新 ETag。手動破快取只是製造重複下載。
- ❌ **loading 面板加進度條或百分比**:單一 JSON 檔載入速度極快,progress 反而閃爍。
- ❌ **加 timeout(如 5 秒未回就 error)**:讓瀏覽器自己決定,不加人為限制。GitHub Pages 慢的機率極低,加 timeout 反而在慢網路下錯殺。

---

## §4. 全域狀態機

### 4.1 body attribute driven

用 `document.body.dataset.status` 承載狀態,CSS 依 `body[data-status="..."]` 選擇器決定顯示什麼。

**理由**:
- 純 CSS driven 切換,無需操作各元件的 hidden 屬性
- 從瀏覽器 devtools 看 `body` 即可知道當前狀態
- 未來新增狀態(如 `partial`)只需加 CSS rule,不動 JS

### 4.2 狀態轉移圖

```
       ┌─────────┐   fetch 開始
初始 → │ loading │─────────────────┐
       └─────────┘                 │
            │                       │
     ┌──────┼─────────────┐         │
     │      │             │         │
   成功    404          其他錯誤   │
     │      │             │         │
     ▼      ▼             ▼         │
  ┌────┐ ┌──────────┐ ┌───────┐    │
  │ ok │ │ notfound │ │ error │◀───┘
  └────┘ └──────────┘ └───────┘
                                     
   狀態一經進入不再變動(除非 user 按重新載入)
```

### 4.3 通用實作函式(JS)

```javascript
function setStatus(mode) { document.body.dataset.status = mode; }
```

**只有一個 setter**,呼叫地點:
1. `boot()` 開頭 → `setStatus('loading')`
2. `boot()` try 尾端 → `setStatus('ok')`
3. `catch NotFoundError` → `setStatus('notfound')`
4. `catch 其他` → `setStatus('error')`

---

## §Patch A:stock.html 五步驟

### A1. 加入 §2.2 CSS 到 `<style>` 尾端

在 stock.html 的 `<style>` 區塊尾端(閉合 `</style>` 之前)貼入 §2.2 完整 CSS。

### A2. 加入 §2.3 HTML 面板

找到 `<header class="topbar">`,在其 `</header>` 之後、原有 `<div class="wrap">` 或 `<main class="wrap">` 之前,貼入 §2.3 完整 HTML(三個 status panel)。

### A3. 刪除 hardcoded `const stock = { ... };`

定位 `<script>` 內宣告樣本股資料的區塊。典型形式為:

```javascript
const stock = {
  id: '6789',
  name: '示範系統',
  industry: '系統整合',
  hasCL: true,
  quarterly: [ ... 8 筆 ... ],
  monthly: [ ... 26 筆 ... ]
};
```

**完整刪除**這整個 `const stock = {...};` 宣告。

### A4. 在 `<script>` 內加入資料載入邏輯

於 `<script>` 開頭(緊接在其他 helper 之前 / 之後皆可,但**必須在所有 render 函數呼叫之前**)貼入:

```javascript
/* =========================================================
   Layer 3.5 · Data loader
   ========================================================= */
let stock = null;
let meta = null;

class NotFoundError extends Error {
  constructor(id) { super(id); this.name = 'NotFoundError'; }
}

async function loadStock() {
  const params = new URLSearchParams(location.search);
  const stockId = params.get('id') || '6789';
  document.getElementById('missingStockId').textContent = stockId;
  const resp = await fetch(`./data/stocks/${stockId}.json`);
  if (resp.status === 404) throw new NotFoundError(stockId);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  stock = await resp.json();
}

async function loadMeta() {
  try {
    const resp = await fetch('./data/meta.json');
    if (resp.ok) meta = await resp.json();
  } catch { /* meta 非必需, 靜默失敗 */ }
}

function setStatus(mode) { document.body.dataset.status = mode; }

function applyMetaToTopbar() {
  if (!meta || !meta.last_full_build) return;
  const date = String(meta.last_full_build).slice(0, 10);
  const label = document.querySelector('.status-btn span:last-child');
  if (label) label.textContent = `資料 ${date}`;
}

document.getElementById('retryBtn')?.addEventListener('click', () => location.reload());
```

### A5. 包裝原本的初始 render 呼叫

在 stock.html 的 `<script>` **最尾端**,通常有一段直接執行的初始化呼叫(不在任何 function 內、通常是最後幾行),例如:

```javascript
// 原本可能長這樣(實際行號依 GPT v2.2 交付版本略有差異)
bindEvents();
renderAll();
```

**或分散為多個呼叫**,如 `renderClStats(); renderFunnel(); renderGauges(); renderInsights(); renderGrowthAnalysis(); renderMaChart(); renderTable();` 之類。

將**所有這些初始執行呼叫**完整搬進以下 `boot()` 函式的標示位置,再於檔案最尾呼叫 `boot()`:

```javascript
async function boot() {
  setStatus('loading');
  try {
    await loadStock();
    await loadMeta();
    applyMetaToTopbar();

    // === ↓↓↓ 把原本檔案尾端的初始 render 呼叫全部搬進這裡 ↓↓↓ ===
    // 例如:
    //   bindEvents();
    //   renderAll();
    // === ↑↑↑ 搬完後上面的原位置刪除 ↑↑↑ ===

    setStatus('ok');
  } catch (err) {
    if (err instanceof NotFoundError) {
      setStatus('notfound');
    } else {
      document.getElementById('errorDetail').textContent =
        `${err.message || '未知錯誤'}。可能是網路異常或後端資料尚未部署。`;
      setStatus('error');
      console.error('[stock.html] load failed:', err);
    }
  }
}
boot();
```

**規則**:
- 只搬「直接執行的呼叫」,**不搬** function 定義本身
- 搬完後原位置**完全刪除**該行,不留註解
- 若初始執行包含 `state` 物件的 `let state = { qIdx: stock.quarterly.length - 1, ... }` 之類**引用 `stock` 的 module-level 宣告**,一併搬入 `boot()` 的位置,並改為 `state = { ... }`(將 `let` 拿掉,`let state;` 保留在原 module scope)。
- 若有多個 `let X = ...` 引用 `stock` / `scannerStocks`,依相同模式處理

---

## §Patch B:scanner.html 五步驟

### B1. 加入 §2.2 CSS 到 `<style>` 尾端

同 A1。

### B2. 加入 §2.3 HTML 面板

同 A2。位置為 `<header class="topbar">` 之後、`<main class="wrap">` 之前。

### B3. 刪除 hardcoded `const scannerStocks = [ ... ];`

定位 `<script>` 內樣本股清單宣告,典型形式:

```javascript
const scannerStocks = [
  { id: '6789', name: '示範系統', industry: 'integration', ... },
  { id: '2451', ... },
  ...  // 共 20 筆
];
```

**完整刪除**這整個 `const scannerStocks = [...];` 宣告。

### B4. 在 `<script>` 內加入資料載入邏輯

於 `<script>` 開頭貼入:

```javascript
/* =========================================================
   Layer 3.5 · Data loader
   ========================================================= */
let scannerStocks = [];
let meta = null;

async function loadScanner() {
  const resp = await fetch('./data/scanner_index.json');
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const payload = await resp.json();
  if (!payload || !Array.isArray(payload.stocks)) {
    throw new Error('scanner_index.json 格式異常:缺少 stocks 陣列');
  }
  scannerStocks = payload.stocks;
  meta = payload.meta || null;
}

async function loadMeta() {
  try {
    const resp = await fetch('./data/meta.json');
    if (resp.ok) {
      const m = await resp.json();
      // scanner_index.meta 已含 last_updated;若 meta.json 有 last_full_build 更權威則覆蓋
      if (m.last_full_build) meta = { ...(meta || {}), last_full_build: m.last_full_build };
    }
  } catch { /* meta 非必需, 靜默失敗 */ }
}

function setStatus(mode) { document.body.dataset.status = mode; }

function applyMetaToTopbar() {
  if (!meta) return;
  const raw = meta.last_full_build || meta.last_updated;
  if (!raw) return;
  const date = String(raw).slice(0, 10);
  const label = document.querySelector('.status-btn span:last-child');
  if (label) label.textContent = `資料 ${date}`;
}

document.getElementById('retryBtn')?.addEventListener('click', () => location.reload());
```

### B5. 包裝原本的初始 render 呼叫

scanner.html 的 `<script>` 最尾端(v1 定版)為:

```javascript
bindControls();
renderAll();
```

以及可能還有 `let state = parseState();` 這類 module-level 初始化,scanner.html 不引用 `scannerStocks`(那是 `filterStocks()` 內部引用),因此**只需搬 `bindControls(); renderAll();`**。

將這兩行搬進以下 `boot()`:

```javascript
async function boot() {
  setStatus('loading');
  try {
    await loadScanner();
    await loadMeta();
    applyMetaToTopbar();

    // === ↓↓↓ 原本檔案尾端的初始 render 呼叫搬進這裡 ↓↓↓ ===
    bindControls();
    renderAll();
    // === ↑↑↑ 搬完後上面的原位置刪除 ↑↑↑ ===

    setStatus('ok');
  } catch (err) {
    document.getElementById('errorDetail').textContent =
      `${err.message || '未知錯誤'}。可能是網路異常或後端資料尚未部署。`;
    setStatus('error');
    console.error('[scanner.html] load failed:', err);
  }
}
boot();
```

**注意**:scanner.html 的 error 分支**不做** NotFoundError 判斷(scanner_index.json 缺失屬於部署問題,直接進 error 狀態即可)。

---

## §21 交付檢查清單(v3.5)

### 21.1 核心 grep gate(交付前用命令列驗證)

**對 stock.html**:

```bash
grep -c "const stock = {" stock.html          # 期望 0 (§Patch A3)
grep -c "fetch(\`./data/stocks/" stock.html   # 期望 ≥1 (§Patch A4)
grep -c "NotFoundError" stock.html            # 期望 ≥3 (class + throw + catch)
grep -c "setStatus(" stock.html               # 期望 ≥4 (loading + ok + notfound + error)
grep -c "boot()" stock.html                   # 期望 ≥1 (call at end)
grep -c "id=\"loadingPanel\"" stock.html      # 期望 1
grep -c "id=\"notFoundPanel\"" stock.html     # 期望 1
grep -c "id=\"errorPanel\"" stock.html        # 期望 1
```

**對 scanner.html**:

```bash
grep -c "const scannerStocks = \[" scanner.html    # 期望 0 (§Patch B3)
grep -c "fetch('./data/scanner_index.json')" scanner.html   # 期望 ≥1
grep -c "setStatus(" scanner.html                  # 期望 ≥3 (loading + ok + error)
grep -c "boot()" scanner.html                      # 期望 ≥1
grep -c "id=\"loadingPanel\"" scanner.html         # 期望 1
grep -c "id=\"errorPanel\"" scanner.html           # 期望 1
```

### 21.2 v2.2 / v1 繼承檢查(不得倒退)

- [ ] `grep -c "V1Reference" *.html` = 0
- [ ] 半形 `單位:` = 0(v2.2 §6.3)
- [ ] `[cite` 污染 = 0
- [ ] `localStorage|sessionStorage|indexedDB` = 0
- [ ] 全形冒號 `單位:` 仍存在(不得被 patch 誤刪)
- [ ] pickUnit / formatMoney / hasCL 邏輯仍存在(不得被 patch 誤動)
- [ ] Chart.js CDN 引用仍在

### 21.3 功能檢查(需在本機起 http server 測試)

**stock.html**:

```bash
cd project_root
python3 -m http.server 8000
# 開瀏覽器:
```

| 測試場景 | 期望結果 |
|---|---|
| `http://localhost:8000/stock.html` | 載入 6789,顯示 Detail 頁全部 8 段 |
| `http://localhost:8000/stock.html?id=6789` | 同上 |
| `http://localhost:8000/stock.html?id=2308` | 載入 2308,漏斗與資料表切換至「億」單位(rev=125,000 觸發 §23) |
| `http://localhost:8000/stock.html?id=9999` | 顯示「找不到股票 9999」+ 回到選股頁按鈕 |
| 網路中斷情況下開啟頁面 | 顯示「資料載入失敗」+ 重新載入按鈕 |
| 主內容區在 fetch 完成前 | `hidden`(不閃現空白畫面) |

**scanner.html**:

| 測試場景 | 期望結果 |
|---|---|
| `http://localhost:8000/scanner.html` | 載入 20 檔,結果表正常渲染 |
| `http://localhost:8000/scanner.html?industry=semi` | 已 URL 反填,只顯示半導體 |
| 手動 rename `data/scanner_index.json` → `xxx.json` | 顯示「資料載入失敗」|

### 21.4 URL 與 Back 行為

- [ ] `stock.html?id=6789` → 點回選股頁按鈕(notfound 情境) → 回 scanner.html
- [ ] scanner.html filter 操作後仍走 v1.1 SPEC 的 URL 序列化
- [ ] Back button 從 stock.html 回 scanner.html 保留原 filter 狀態

### 21.5 錯誤面板 UI

- [ ] 三個面板都用 SVG(無 emoji)
- [ ] 面板色彩:notfound 用 brass、error 用 coral、loading 用 brass 旋轉圈
- [ ] 手機版 min-height 縮小(§2.2 media query)

---

## §22 前次交付審計(繼承 v2.2 + Scanner v1 + v1.1)

**Layer 3.5 為首輪產出,無前次審計**。以下列出前 3 輪失誤模式作警惕:

1. 保留死代碼函數(v2.1 stock.html)→ §3 v2.2 已鎖
2. 半形冒號(v2.1 stock.html)→ §3 v2.2 已鎖
3. `[cite: N]` 註解污染(v2.1 Gemini)→ §3 v2.2 已鎖
4. 表格 cell 附加「百萬」後綴(v2)→ §6 v2.2 已鎖
5. localStorage 提案(v1 Scanner 討論階段)→ §3 Scanner v1 已鎖

Layer 3.5 新增可能失誤(GPT 需迴避):

- ❌ 把 fetch 錯誤靜默 catch,導致 loading 面板卡住不動
- ❌ 把 boot() 內 render 呼叫順序打亂,例如先 setStatus('ok') 再 render
- ❌ 把 async 改成 sync + setTimeout hack
- ❌ 沿用 hardcoded fallback,fetch 失敗時 fallback 到假資料
- ❌ 把 stock.html 的 mainContent 部分打包成 innerHTML 字串(這是破壞性 patch)

---

## §24 仲裁優先序

```
§3(絕對禁止)
 > §0(執行指令)
 > §1(資料契約 URL)
 > §4(狀態機)
 > §2(錯誤面板 UI 與 CSS)
 > §Patch A / B(patch 步驟)
 > §22(前次審計)
 > §21(檢查清單)
 > 其他章節
```

**如發現本 patch spec 與 v2.2 / v1 主 SPEC 衝突**,以主 SPEC 為準,本 patch spec 讓步。
**如發現本 patch 兩份 HTML 產出行為不對稱**(例如 stock 有 notfound 但 scanner 沒有 error),以 §Patch A / B 明文為準。

---

## 【本次 patch 任務結束】

GPT 完成 §Patch A + §Patch B 並通過 §21.1 全部 grep gate 後,將兩份修改後 HTML 回傳給使用者,由使用者交給 Claude 進行最終 audit。

**Claude 收到 GPT 交付後的 audit 檢查**(此段給 Claude,不給 GPT):

```bash
# stock.html
grep -c "const stock = {" stock.html                          # 0
grep -c "fetch(\`./data/stocks/" stock.html                   # ≥1
grep -c "NotFoundError" stock.html                            # ≥3
grep -c "loadStock\|loadMeta\|setStatus\|boot" stock.html     # ≥8
grep -c "id=\"loadingPanel\"\|id=\"notFoundPanel\"\|id=\"errorPanel\"" stock.html  # ≥3

# scanner.html
grep -c "const scannerStocks = \[" scanner.html               # 0
grep -c "fetch('./data/scanner_index.json')" scanner.html     # ≥1
grep -c "loadScanner\|loadMeta\|setStatus\|boot" scanner.html # ≥6
grep -c "id=\"loadingPanel\"\|id=\"errorPanel\"" scanner.html # ≥2

# 兩者共同的 v2.2 / v1 繼承檢查
grep -c "V1Reference\|Legacy\|Deprecated" *.html              # 0
python3 -c "import re;print(sum(len(re.findall(r'單位:', open(f).read())) for f in ['stock.html','scanner.html']))"  # 0 (半形)
grep -c "\[cite" *.html                                       # 0
grep -c "localStorage\|sessionStorage\|indexedDB" *.html      # 0
```

若全部通過 + 功能測試 §21.3 全部通過 → **Layer 3.5 定版**,專案完成 UI + Pipeline 整合閉環。
