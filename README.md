# 財報轉化漏斗 · FinMind Project

台股基本面分析工具,每日自動更新。前端純靜態 HTML,資料由 Python 管線每日從 FinMind 拉取。

## 網址

上線後:
- 選股掃描:`https://<user>.github.io/FinMind_Project/scanner.html`
- 個股詳細:`https://<user>.github.io/FinMind_Project/stock.html?id=2330`

## 目錄結構

```
├── stock.html                  Detail 頁前端
├── scanner.html                Scanner 頁前端
├── data/                       (workflow 產生,不進手動 commit)
│   ├── stocks/{id}.json
│   ├── scanner_index.json
│   └── meta.json
├── pipeline/                   Python 資料管線
│   ├── build.py                主 orchestrator
│   ├── config.py               常數 + DEMO_UNIVERSE
│   ├── finmind_client.py       FinMind API wrapper
│   ├── ingest.py               抓資料 + parquet 快取
│   ├── transform.py            算指標
│   ├── output.py               寫 JSON
│   ├── mock_data.py            本機測試用
│   └── __init__.py
├── .github/workflows/
│   ├── daily-build.yml         每日 22:00 TPE 自動更新
│   └── backfill.yml            手動觸發全量重建
├── requirements.txt
├── .gitignore
└── README.md
```

## 部署三步驟

1. 建 GitHub Public repo,上傳所有檔案
2. Settings → Secrets → 加 `FINMIND_TOKEN`
3. Actions → Backfill → Run workflow(首次 5-10 分鐘)
4. Settings → Pages → main branch,root

穩態運轉後每天 22:00 台北時間自動更新,不需要人工介入。

## 本機測試

```bash
pip install -r requirements.txt
FINMIND_MOCK=1 python -m pipeline.build --backfill
python3 -m http.server 8000
# 瀏覽 http://localhost:8000/stock.html
```
