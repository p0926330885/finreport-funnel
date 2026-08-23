# 財報轉化漏斗 · Financial Conversion Funnel

> 台股基本面分析工具 · 純靜態網頁 + Python 資料管線 · 每日自動更新

**🌐 Live Demo**
- 選股掃描:https://p0926330885.github.io/finreport-funnel/scanner.html
- 個股詳細:https://p0926330885.github.io/finreport-funnel/stock.html

---

## ✨ 產品特色

- **📊 損益轉化漏斗**:視覺化營收 → 毛利 → 營業利益 → 淨利的每一段轉化率
- **🧠 11 種商業模式判讀**:自動辨識定價權、營運槓桿、費用失控等情境,用白話解說
- **🔍 多維度篩選**:毛利率、營益率、營收 YoY 等 slider 篩選 + 4 種策略模板
- **📱 響應式設計**:手機、平板、桌機都能用
- **🔄 每日自動更新**:GitHub Actions 每天台北時間 22:00 抓最新 FinMind 資料
- **🆓 完全免費運行**:GitHub Pages + Actions,無 server 成本

---

## 🎯 使用場景

- 快速篩選符合特定財務條件的股票
- 深入分析單一股票的損益結構
- 判斷公司獲利是本業紮實還是業外美化
- 追蹤季度成長率趨勢與轉折

---

## 🏗️ 技術架構

```
┌─────────────────┐
│  FinMind API    │  (資料來源, 免費 500 req/hr)
└────────┬────────┘
         │ 每天 22:00 TPE / 每月月營收公布日
         ▼
┌─────────────────┐
│ GitHub Actions  │  (Python pipeline)
│  ├── daily      │
│  ├── backfill   │
│  └── scheduled  │
└────────┬────────┘
         │ 產出 JSON
         ▼
┌─────────────────┐
│  data/*.json    │  (每檔股票一個 ~2-3 KB)
└────────┬────────┘
         │ fetch
         ▼
┌─────────────────┐
│ GitHub Pages    │  (純靜態 HTML/CSS/JS)
│  ├── stock.html │
│  └── scanner.html│
└─────────────────┘
```

---

## 📁 目錄結構

```
finreport-funnel/
├── stock.html                    # 個股詳細頁
├── scanner.html                  # 選股掃描頁
├── data/                         # JSON 資料(pipeline 產出)
│   ├── stocks/{id}.json          # 每檔股票詳細資料
│   ├── scanner_index.json        # 選股掃描索引
│   └── meta.json                 # 系統元資料
├── pipeline/                     # Python 資料管線
│   ├── build.py                  # 主流程
│   ├── config.py                 # 常數 / universe 篩選
│   ├── transform.py              # 業務指標計算 + insights 引擎
│   ├── ingest.py                 # FinMind 資料抓取
│   ├── finmind_client.py         # API client
│   ├── output.py                 # 寫入 JSON
│   ├── mock_data.py              # 本地測試用
│   └── __init__.py
├── .github/workflows/            # GitHub Actions 排程
│   ├── daily-build.yml           # 每天 22:00 TPE
│   ├── backfill-scheduled.yml    # 每天 03:30 TPE(全市場分批)
│   └── backfill.yml              # 手動觸發(demo 20 檔)
├── docs/                         # 專案設計文件(SPEC + SOP)
│   ├── README.md                 # 文件索引
│   ├── SPEC-*.md                 # 各版本規格書
│   └── DEPLOYMENT-SOP.md         # 部署 SOP
├── cache/                        # 資料快取(部分進 git)
│   └── backfill_state.json       # backfill 進度追蹤
├── requirements.txt              # Python 依賴
├── .gitignore
└── README.md                     # 你在看的檔案
```

---

## 🚀 本機開發

### 前置需求
- Python 3.12+
- FinMind API Token(免費申請 https://finmindtrade.com/)

### 安裝

```bash
git clone https://github.com/p0926330885/finreport-funnel.git
cd finreport-funnel
pip install -r requirements.txt
```

### 執行 pipeline

```bash
# 20 檔 demo mode(用 mock 資料快速測試)
FINMIND_MOCK=1 python -m pipeline.build --demo

# 用真 FinMind 資料
export FINMIND_TOKEN=你的_token
python -m pipeline.build --demo

# 全市場批次(v2)
python -m pipeline.build --scheduled-batch
```

### 本機起 web server 測試前端

```bash
python -m http.server 8000
# 瀏覽器打開 http://localhost:8000/scanner.html
```

---

## 📖 文件

完整設計 SPEC 和演化史請看 [`docs/`](./docs/) 資料夾。

**新手建議閱讀順序**:
1. [`docs/README.md`](./docs/README.md) · 文件索引(先看這個)
2. [`docs/DEPLOYMENT-SOP.md`](./docs/DEPLOYMENT-SOP.md) · 部署上線 SOP
3. [`docs/SPEC-insights-v3.md`](./docs/SPEC-insights-v3.md) · 最新判讀引擎(v3.1 LOCKED)

---

## 🔧 技術細節

- **資料更新頻率**:
  - 財報:季度公布日後隔天更新(3/5/8/11 月中旬)
  - 月營收:每月 10 日左右
  - Daily build:每天 22:00 TPE 增量更新
- **資料範圍**:
  - v1:DEMO_UNIVERSE 20 檔(上市權值股)
  - v2:全市場 ~1,700 檔(規劃中,分 5 批自動接力)
- **判讀引擎**:11 種商業模式 + fallback,見 [`docs/SPEC-insights-v3.md`](./docs/SPEC-insights-v3.md)

---

## 📄 授權

- **程式碼**:MIT License
- **資料**:遵守 [FinMind ToS](https://finmindtrade.com/)

---

## 🙏 致謝

- **資料源**:[FinMind](https://finmindtrade.com/) · 台灣金融資料 API
- **架構設計**:與 AI 協作完成(對話式產品開發實驗)
