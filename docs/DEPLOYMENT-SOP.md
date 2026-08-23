# 財報轉化漏斗 · GitHub 部署上線手把手 SOP

**目標**:把整個專案從本機推上 GitHub Public repo,設好 Actions 排程與 Pages,拿到一個真實可用的網址。
**預估時間**:30 分鐘(含 5-10 分鐘等 Backfill 跑完的空檔)
**你需要準備的帳號**:GitHub、FinMind
**你需要準備的工具**:git(命令列)**或** GitHub Desktop(GUI,推薦給第一次用 git 的人)

---

## Step 0. 準備工作(5 分鐘)

### 0.1 帳號註冊

**GitHub**(免費個人帳號):
1. 到 https://github.com/signup 註冊
2. 完成 email 驗證
3. 記住你的 username(以下範例用 `<user>` 代表你的 username)

**FinMind**(免費方案 600 req/hr):
1. 到 https://finmindtrade.com/ 註冊帳號
2. 登入後點右上角頭像 → 進入會員頁
3. 找到「API Token」欄位,**複製 token 字串**(通常是一長串 base64 like)
4. **暫時貼到你的記事本保存**,等下 Step 5 要用

### 0.2 工具安裝

**選一條路走即可**:

**路線 A(推薦第一次用 git 的人):GitHub Desktop**
- 到 https://desktop.github.com/ 下載
- 安裝完登入 GitHub 帳號

**路線 B(推薦有命令列基礎的人):git CLI**
- macOS:通常已內建,`git --version` 有回應即可
- Windows:https://git-scm.com/download/win 下載安裝
- Linux:`sudo apt install git` 或 `sudo dnf install git`

### 0.3 本機檔案準備

在你的電腦上建立一個工作資料夾,把以下所有檔案準備好:

```
finreport-funnel/                       ← 這個資料夾名稱隨你,以下用此名為例
├── stock.html                          ← Layer 3.5 定版檔(你手上有)
├── scanner.html                        ← Layer 3.5 定版檔(你手上有)
├── SPEC-integration-v1.md              ← 可留可不留(存檔用,不影響部署)
│
│  ↓↓↓ 以下全部來自 pipeline-project.tar.gz 解壓 ↓↓↓
├── SPEC-pipeline-v1.md
├── README.md
├── .gitignore
├── requirements.txt
├── .github/workflows/
│   ├── daily-build.yml
│   └── backfill.yml
├── pipeline/
│   ├── __init__.py
│   ├── build.py
│   ├── config.py
│   ├── finmind_client.py
│   ├── ingest.py
│   ├── mock_data.py
│   ├── output.py
│   └── transform.py
└── data/                               ← 這個先刪掉!(mock 資料,不進正式 repo)
    ├── stocks/
    ├── meta.json
    └── scanner_index.json
```

**重要:把整個 `data/` 資料夾刪除**。理由:
- 那是我用 mock 資料產出的假數據(6789 示範系統之類)
- 正式部署後由 Backfill workflow 從 FinMind 拉真實資料重建
- 保留會讓第一次 push 有雜訊資料

## Step 1. 修改設定,把 mock 換成真實股票(3 分鐘)

**這步不做的話,Backfill 會失敗,因為程式碼裡預設的股票 ID 都是虛構的。**

### 1.1 修改 `pipeline/config.py`

用文字編輯器打開 `pipeline/config.py`,滑到最底部,找到 `DEMO_UNIVERSE`:

**原本(mock 股票清單)**:
```python
DEMO_UNIVERSE = [
    "6789", "2451", "3037", "4919", "6488",
    "2618", "2882", "1102", "4576", "4108",
    "5522", "3260", "2308", "2603", "4174",
    "2412", "3711", "5871", "6415", "8046",
]
```

**修改成(真實台股清單,20 檔涵蓋主要產業)**:
```python
DEMO_UNIVERSE = [
    # 半導體
    "2330", "2454", "2379", "3711", "6415",
    # 電子零組件
    "2308", "2317", "3037", "8046", "6488",
    # 金融
    "2882", "2891", "5871", "2618", "2412",
    # 傳產
    "1102", "2603", "2385", "9910", "1216",
]
```

**這 20 檔對應**(方便你確認):
- 半導體:2330 台積電、2454 聯發科、2379 瑞昱、3711 日月光投控、6415 矽力-KY
- 電子零組件:2308 台達電、2317 鴻海、3037 欣興、8046 南電、6488 環球晶
- 金融:2882 國泰金、2891 中信金、5871 中租-KY、2618 長榮航、2412 中華電信
- 傳產:1102 亞泥、2603 長榮、2385 群光、9910 豐泰、1216 統一

你可以自己微調這 20 檔的組成,只要都是**實際上市/上櫃**的股票代號即可。

### 1.2 修改 `stock.html` 的預設 fallback

打開 `stock.html`,搜尋(Ctrl+F / Cmd+F):

```
|| '6789'
```

應該只會找到一處(在 `loadStock` 函式內)。改成:

```
|| '2330'
```

這樣使用者直接開 `stock.html`(沒帶 `?id=xxx` 參數)時,預設顯示台積電。

### 1.3 儲存,確認 Step 1 已完成

**Checkpoint**:
- [ ] `pipeline/config.py` 的 DEMO_UNIVERSE 已換成真實股號
- [ ] `stock.html` 的 fallback 從 6789 改成 2330
- [ ] `data/` 資料夾已刪除

---

## Step 2. 建立 GitHub Public Repo(2 分鐘)

### 2.1 建立 repo

1. 到 https://github.com/new
2. 填寫欄位:
   - **Repository name**:`finreport-funnel`(或你想要的名字,以下用此名代稱)
   - **Description**:可留空或寫「財報轉化漏斗 · 台股基本面分析」
   - **Public**(必選 ⚠️ 不能選 Private,否則免費帳號沒 Pages)
   - **Add a README file**:**不要打勾**(你已有 README)
   - **Add .gitignore**:**不要選**(你已有)
   - **Choose a license**:留 None 或選 MIT(隨意)
3. 點綠色按鈕 `Create repository`

### 2.2 記下 repo URL

建好後你會看到頁面顯示:

```
https://github.com/<user>/finreport-funnel
```

**這串 URL 是你的 repo 位置,記住它**,Step 3 要用。

---

## Step 3. 首次 Push 檔案到 GitHub(5 分鐘)

**選一條路走**:

### 路線 A. GitHub Desktop(推薦新手)

1. 開啟 GitHub Desktop
2. 選單:`File` → `Add local repository`
3. 選你本機的 `finreport-funnel` 資料夾 → `Add repository`
4. 這時 Desktop 會問你是否要 publish,點 `Publish repository`
5. 對話框:
   - **Name**:`finreport-funnel`(自動帶入)
   - **Keep this code private**:**取消勾選 ⚠️**(必須是 Public)
6. 點 `Publish repository`
7. 等它跑完(通常 10-20 秒),你的檔案就上去了

### 路線 B. Git CLI

在終端機執行(替換 `<user>` 為你的 GitHub username):

```bash
cd path/to/finreport-funnel
git init
git branch -M main
git add .
git status                           # 檢查一下該有的檔都在
git commit -m "Initial: full project scaffold"
git remote add origin https://github.com/<user>/finreport-funnel.git
git push -u origin main
```

首次 push 若要求登入,用瀏覽器登入 GitHub 授權即可(現代 git 會自動導引)。

### 3.3 驗證檔案上去了

打開瀏覽器,到 `https://github.com/<user>/finreport-funnel`,你應該看到:

- 檔案列表:`stock.html`、`scanner.html`、`pipeline/`、`.github/`、`README.md`、`requirements.txt` 等
- 右側「About」空的沒關係
- 主頁下方會渲染 README.md 內容

**Checkpoint**:
- [ ] Repo 是 Public(URL 開起來不用登入就看得到)
- [ ] `pipeline/` 資料夾點進去有 8 個 .py 檔
- [ ] `.github/workflows/` 點進去有 2 個 .yml 檔
- [ ] 沒有 `data/` 資料夾(先前刪掉了)
- [ ] 沒有 `cache/` 資料夾(.gitignore 排除了)

---

## Step 4. 設定 FINMIND_TOKEN Secret(2 分鐘)

### 4.1 進入 Secrets 頁

1. 在 repo 頁上方點 `Settings`(靠右邊的齒輪 icon)
2. 左側選單:`Secrets and variables` → `Actions`
3. 點右上角綠色按鈕 `New repository secret`

### 4.2 填入 token

- **Name**:`FINMIND_TOKEN`(**大小寫必須一模一樣**,程式碼 hard-code 讀這個名字)
- **Secret**:貼上你 Step 0.1 保存的 FinMind token
- 點 `Add secret`

### 4.3 驗證

回到 `Actions secrets and variables` 頁,你會看到:

```
Repository secrets
🔒 FINMIND_TOKEN                          Updated now
```

**Token 內容不會再顯示**(這是正常的,GitHub 保護機密),但可以看到名字。

**Checkpoint**:
- [ ] Secret 名稱是 `FINMIND_TOKEN`(全大寫,底線分隔)
- [ ] Secret 列表出現 🔒 標記

---

## Step 5. 首次執行 Backfill(5-10 分鐘)

### 5.1 觸發 workflow

1. Repo 頁上方點 `Actions`
2. 左側 workflow 清單會看到:
   - `Backfill`
   - `Daily build`
3. 點 `Backfill`
4. 右側點 `Run workflow` 綠色按鈕
5. 對話框:
   - **Use workflow from**:留 `Branch: main`
   - **本次最多處理幾檔**:留 `0`(0 = 全部)
6. 點綠色 `Run workflow`

### 5.2 等待執行

1. 頁面會出現一個新的執行記錄,狀態黃色圓圈「in progress」
2. 點進去看即時 log
3. 展開 `Run backfill (forced refresh)` 步驟,你會看到類似:
   ```
   [12:34:56] INFO build: Client loaded (mock=False)
   [12:34:57] INFO pipeline.ingest: Fetching universe from FinMind
   [12:35:04] INFO build: Universe: 2400 stocks
   [12:35:04] INFO pipeline.ingest: Ingest starting: 20 stocks, force=True
   [12:35:11] INFO pipeline.ingest: Ingest progress: 10 / 20
   [12:35:18] INFO pipeline.ingest: Ingest progress: 20 / 20
   [12:35:18] INFO pipeline.ingest: Ingest complete: 20 stocks
   [12:35:19] INFO build: Built 20 stock detail files (fail=0)
   [12:35:19] INFO build: Wrote scanner index: scanner_index.json (20 stocks)
   ```

**首次執行預期耗時**:每檔約 7.2 秒 × 3 個 dataset(FS、BS、Rev) × 20 檔 = 約 7 分鐘。

### 5.3 檢查結果

執行完成後(綠色勾勾),點展開最下面的 `Commit updated data` 步驟,應該看到:

```
[main abc1234] Backfill 2026-08-22
 22 files changed, ... insertions(+)
 create mode 100644 data/meta.json
 create mode 100644 data/scanner_index.json
 create mode 100644 data/stocks/1102.json
 create mode 100644 data/stocks/1216.json
 ...
```

回到 repo 主頁,重新整理,你應該看到多了個 `data/` 資料夾。點進去看:
- `data/stocks/` 內有 20 個 `.json`
- `data/scanner_index.json` 存在
- `data/meta.json` 存在

**打開 `data/meta.json` 檢查關鍵指標**:
```json
{
  "built_ok": 20,           ← 期望 20,若小於 20 表示有股票 fetch 失敗
  "built_fail": 0,          ← 期望 0
  "universe_size": 2400+,   ← 大於 2000 表示 TaiwanStockInfo 抓到全市場
  ...
}
```

### 5.4 若 Backfill 失敗

**常見狀況**:

**A. 402 rate limit**
- 現象:log 出現 `FinMind rate limit hit, backing off`
- 對策:
  - 若最終還是失敗:等 1 小時 rate limit 重置後 rerun
  - 若成功:代表 backoff 有效,無需處理

**B. 401 unauthorized**
- 現象:log 出現 `HTTP 401` 或 `token invalid`
- 對策:
  - 檢查 Step 4 的 secret 是否設對(名稱、token 值)
  - 確認 FinMind 網頁的 token 沒過期,重新複製一次

**C. 部分股票 built_fail**
- 現象:`built_ok: 15, built_fail: 5`
- 對策:
  - 展開 workflow log 找 `Failed to build XXXX` 訊息
  - 通常是那幾檔股號打錯,或 FinMind 對該股票無資料
  - 修改 config.py 換成別的股號、re-run backfill

**D. workflow 完全跑不起來**
- 現象:Actions 頁沒看到 workflow 觸發
- 對策:
  - 檢查 `.github/workflows/backfill.yml` 有推上 GitHub
  - Settings → Actions → General → 確認 Actions 是 enabled

**Checkpoint**:
- [ ] Backfill workflow 顯示綠勾勾(成功)
- [ ] `data/meta.json` 的 `built_ok` = 20
- [ ] `data/stocks/` 內有 20 個 JSON
- [ ] `data/scanner_index.json` 存在

---

## Step 6. 開啟 GitHub Pages(2 分鐘)

### 6.1 設定 Pages

1. Repo 上方點 `Settings`
2. 左側選單:`Pages`
3. 中間主區:
   - **Source**:選 `Deploy from a branch`
   - **Branch**:選 `main`,資料夾選 `/ (root)`
4. 點 `Save`

### 6.2 等待 Pages 生效

1. 頁面上方會出現一個藍色框:「Your site is live at https://<user>.github.io/finreport-funnel/」
   - **首次啟用可能顯示綠色構建圖示,再過 1-2 分鐘才生效**
2. 也可以到 `Actions` 頁,看到多了一個 `pages-build-deployment` workflow 執行完

### 6.3 拿到你的網址

你的 3 個核心 URL:

| 用途 | URL |
|---|---|
| 選股掃描 | `https://<user>.github.io/finreport-funnel/scanner.html` |
| 個股詳細(預設 2330) | `https://<user>.github.io/finreport-funnel/stock.html` |
| 個股詳細(指定) | `https://<user>.github.io/finreport-funnel/stock.html?id=2454` |

**收藏這幾個網址**。

---

## Step 7. 驗收 6 個測試場景(3 分鐘)

打開瀏覽器,一個一個測:

### 7.1 場景 A:Scanner 頁能正常載入

**開**:`https://<user>.github.io/finreport-funnel/scanner.html`

**預期**:
- 上方 topbar 顯示「資料 2026-08-22」(當天日期)
- 命中統計顯示「命中 20 檔 / 全市場 20 檔 (100.0%)」
- 結果表列出 20 檔真實股票(2330 台積電、2454 聯發科 等)
- 每檔的當季營收、YoY、營益率、健康度分數、能見度都有數字

### 7.2 場景 B:Detail 頁預設載入台積電

**開**:`https://<user>.github.io/finreport-funnel/stock.html`

**預期**:
- Topbar 資料日期正確
- Hero 顯示「2330 台灣積體電路製造」(或簡稱)
- 訂單能見度、營收漏斗、品質儀表、自動判讀、成長率、月營收動能、資料表 全部有資料
- 因為台積電 rev 遠超 500 億,漏斗與資料表**應該自動切換到「億」單位顯示**(這是 SPEC v2.2 §23 的關鍵測試)

### 7.3 場景 C:Detail 頁指定其他股票

**開**:`https://<user>.github.io/finreport-funnel/stock.html?id=2454`

**預期**:載入聯發科的資料。

### 7.4 場景 D:404 錯誤處理

**開**:`https://<user>.github.io/finreport-funnel/stock.html?id=9999`

**預期**:顯示「找不到股票 9999」+「回到選股頁」按鈕(SVG 放大鏡圖示)。

### 7.5 場景 E:Scanner 點列跳 Detail

**開** scanner.html,點結果表任一列。

**預期**:跳轉到對應 `stock.html?id={股號}`,顯示該股票的完整 Detail 頁。

### 7.6 場景 F:Scanner 篩選 + URL 分享

**開** scanner.html,拉動「毛利率」slider 到 25-100%。

**預期**:
- 結果表即時篩掉毛利率 <25% 的股票
- 網址列自動變成 `scanner.html?gm=25,100`(或類似)
- 複製這網址到新分頁打開,篩選狀態保留

**Checkpoint**:
- [ ] 6 個場景全通過
- [ ] Scanner 頁看到真實台股名稱(不是「示範系統」)
- [ ] Detail 頁 2330 的漏斗顯示「單位:億」

---

## Step 8. 確認每日排程已啟用(1 分鐘)

### 8.1 檢查 daily-build cron

1. Repo 上方 `Actions`
2. 左側點 `Daily build`
3. 中間應該看到:
   - 空的執行歷史(還沒到觸發時間)
   - 上方藍色提示:「This workflow has a workflow_dispatch event trigger」

排程時間是 **UTC 14:00 = 台北時間 22:00**,每天固定執行。

### 8.2 (可選)手動測試一次 daily 觸發

1. 在 Daily build 頁右上點 `Run workflow`
2. Branch 選 `main` → 綠色 `Run workflow`
3. 通常 <2 分鐘完成(增量模式,cache 命中,不會重抓所有資料)
4. 完成後 `data/meta.json` 的 `last_full_build` 應更新為現在時間

**Checkpoint**:
- [ ] Daily build workflow 存在
- [ ] 手動觸發成功執行完
- [ ] `data/meta.json` 時間有更新

---

## 🎉 完成上線 · 你現在有的成果

- **兩個真實網址**在跑,任何人都可以訪問
- **每日 22:00 自動更新**,無需人工介入
- **完全免費**,GitHub 帳戶沒開通任何付費項目
- **可分享**:把 scanner.html 網址丟給朋友,他們也能用篩選 + URL 分享回你

---

## 部署後的維運節奏

### 每週:
- 隨手打開 `Actions` 頁瞄一眼 Daily build 有沒有紅叉(有紅叉點進去看 log)

### 每月:
- 打開 `data/meta.json`(GitHub 上或本機 pull 下來),確認 `built_ok` 每天都 = 20

### 每季(財報公布月:5、8、11、次年 3):
- 公布日隔天檢查 Detail 頁的最新一季資料有沒有進來
- 若沒有,可能 FinMind 尚未 ingest,等 1-2 天

### 每年:
- 檢查 `requirements.txt` 依賴版本(pandas / pyarrow / requests)是否需要更新
- 檢查 `actions/*@v4` 版本(GitHub 可能出 v5)

---

## 進階議題(選擇性)

### 想擴充到全市場 1800+ 檔?

修改 `pipeline/config.py`:
```python
# 把 DEMO_UNIVERSE 註解掉,改用動態全 universe
# 或在 build.py 內改寫 targets = universe_df['stock_id'].tolist()
```

然後 backfill workflow 需**分次執行 5-7 次**(見 SPEC-pipeline §12)。

### 想加自訂網域?

Settings → Pages → Custom domain 填入你的網域(如 `funnel.example.com`),按指示設 DNS CNAME。

### 想在 Detail 頁加同業對照?

開 v2 議題,回來找我出 SPEC-v3 增修規格。

---

## 常見問題快速索引

| 症狀 | 對策 |
|---|---|
| Pages 顯示 404 | Settings → Pages 確認 branch 是 `main`,root |
| Scanner 顯示「資料載入失敗」 | `data/scanner_index.json` 是否已 commit? |
| Detail 頁顯示「找不到股票」 | URL 的 `id` 是否在 DEMO_UNIVERSE 內? |
| Workflow 401 錯誤 | FINMIND_TOKEN secret 名稱或值錯誤 |
| Workflow 402 錯誤 | Rate limit,等 1 小時再試 |
| Workflow 超時 | Backfill 首次可能碰上,分批跑或減 DEMO_UNIVERSE 數量 |
| 部分股票 built_fail | 該股號可能已下市或 FinMind 無資料,換掉 |
| 資料日期沒更新 | 檢查 Daily build workflow 是否有紅叉 |
| 想手動觸發更新 | Actions → Daily build → Run workflow |

---

**本 SOP 結束**
完成上線後,若遇到任何問題,回到對話丟訊息給我,我幫你 debug。
