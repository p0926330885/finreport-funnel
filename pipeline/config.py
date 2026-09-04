"""
Pipeline config constants.

Locked by SPEC-pipeline-v1 §5, §7, §12. Do not adjust without SPEC revision.
"""
from __future__ import annotations
from pathlib import Path

# ============================================================
# FinMind API
# ============================================================
FINMIND_API_BASE = "https://api.finmindtrade.com/api/v4"

# Free tier: 600 requests / hour
# We conservatively cap at 500 to leave headroom for retry & other consumers.
RATE_LIMIT_PER_HOUR = 500
RATE_LIMIT_INTERVAL_SECONDS = 3600 / RATE_LIMIT_PER_HOUR  # ~7.2s per request

# Retry on transient errors
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

# ============================================================
# Datasets used (SPEC §5)
# ============================================================
DATASETS = {
    "info":    "TaiwanStockInfo",                  # 股票基本資料
    "fs":      "TaiwanStockFinancialStatements",   # 綜合損益表 (季)
    "bs":      "TaiwanStockBalanceSheet",          # 資產負債表 (季) - for 合約負債
    "revenue": "TaiwanStockMonthRevenue",          # 月營收
}

# ============================================================
# Business thresholds
# ============================================================
HAS_CL_THRESHOLD = 0.15                  # v2.2 §4.2: max(近8Q CL) / max(近8Q rev) > 0.15
QUARTERLY_HISTORY_QUARTERS = 20          # Detail 頁 20 季 (5 年)
QUARTERLY_YOY_BUFFER = 4                 # 額外拉 4 季,用於算最終 20 季前 4 季的 YoY (不出現在輸出)
MONTHLY_HISTORY_MONTHS = 71              # v3.4: 60 顯示視窗 + 11 暖機期
                                         # 前端 stock.html slice 掉前 11 個月不顯示,但用於均線計算
                                         # 讓 12MA 從第 1 個顯示月份就有值(如台達電 3MA 從 2021-09 起,不再有前 2 月留白)
                                         # 對新股(如 7705 三商 · monthly < 71)自動 fallback 現有邏輯
GOLDEN_CROSS_LOOKBACK_DAYS = 30          # Scanner §7.6

THRESHOLD_TO_YI = 50000                  # v2.2 §23 (百萬 -> 億 切換閾值)

# Health score thresholds (SPEC §17)
HEALTH_THRESHOLDS = {
    "gm":  {"green": 25, "yellow": 15},   # gross margin
    "om":  {"green": 8,  "yellow": 3},    # operating margin
    "nm":  {"green": 5,  "yellow": 2},    # net margin
    "noi": {"green": 25, "yellow": 70},   # |NOI/NP| (absolute)
}

# ============================================================
# Industry mapping: FinMind industry_category -> Scanner code (SPEC §18.3)
# ============================================================
INDUSTRY_MAP = {
    # 半導體
    "半導體業":         "semi",
    "半導體":           "semi",
    # 電子零組件
    "電子零組件業":     "component",
    "光電業":           "component",
    "電腦及週邊設備業": "component",
    # 系統整合 (資訊服務)
    "資訊服務業":       "integration",
    "電子通路業":       "integration",
    # 化工
    "化學工業":         "chemical",
    "化學生技醫療":     "chemical",
    "塑膠工業":         "chemical",
    # 生技 (v3.4 Phase 2: 從化工拆出來)
    "生技醫療業":       "biotech",
    "生技醫療":         "biotech",
    # 機械
    "電機機械":         "machinery",
    # 建材營造 (v3.4 Phase 2: 從 traditional 拆出,採完工比例法,前端顯示特殊備註)
    "建材營造業":       "construction",
    "建材營造":         "construction",
    "營建":             "construction",
    "營建業":           "construction",
    # 公用事業 (v3.4 Phase 2)
    "油電燃氣業":       "utility",
    # 傳產 (default catch)
    "水泥工業":         "traditional",
    "食品工業":         "traditional",
    "紡織纖維":         "traditional",
    "鋼鐵工業":         "traditional",
    "汽車工業":         "traditional",
    "航運業":           "traditional",
    "觀光事業":         "traditional",
    "觀光餐旅":         "traditional",
    "貿易百貨":         "traditional",
    "貿易百貨業":       "traditional",
    "通信網路業":       "traditional",
    "其他電子業":       "traditional",
    "玻璃陶瓷":         "traditional",
    "造紙工業":         "traditional",
    "橡膠工業":         "traditional",
    "其他業":           "traditional",
    "其他":             "traditional",
    "電子工業":         "traditional",
    # 金融
    "金融保險":         "finance",
    "金融保險業":       "finance",
    "金融":             "finance",
}
INDUSTRY_DEFAULT = "traditional"

# ============================================================
# Phase 2: 特殊產業處理規則 (Pipeline 端)
# 前端 stock.html 內 industryNotes 表提供 UI 顯示,兩者要保持同步。
# ============================================================
# 強制 hasCL=false 的產業 (金融保險業無實質 IFRS 15 訂單池)
INDUSTRY_FORCE_NO_CL = {"finance"}

# 需要在前端顯示黃色備註提示的產業
# (實際文案在 stock.html 內 industryNotes 定義)
INDUSTRY_NOTED = {"finance", "construction"}

# ============================================================
# FinMind FS 科目 -> 我方欄位對照 (SPEC §5.2)
# 註: FinMind FS API 回傳結構為 (date, stock_id, type, value)
# type 值來自財報 XBRL 標籤,常見值如下。
#
# v3.3 改為多重候選清單:每個內部欄位提供多個可能的 FinMind key。
# _fs_pivot / _bs_pivot 會依序嘗試,取第一個 match 的。
# 若全部 miss,transform.py 內的 log.info 會列出實際 wide.columns,
# 方便日後從 GitHub Actions log 反查真名再更新此表。
# ============================================================
FS_FIELD_MAP = {
    "rev": ["Revenue", "OperatingRevenue"],
    "gp":  ["GrossProfit", "GrossProfitLoss"],
    "op":  ["OperatingIncome", "OperatingIncomeLoss"],
    "noi": [
        "TotalNonoperatingIncomeAndExpense",   # ← FinMind 真名 (v3.3 log 確認,無 's')
        "TotalNonOperatingIncomeAndExpenses",  # SPEC 原值 (備用)
        "NonoperatingIncomeAndExpense",        # 無 Total 前綴
    ],
    "np":  ["NetIncome", "NetIncomeLoss", "IncomeAfterTaxes"],
    "eps": ["EPS", "BasicEPS", "EarningsPerShare"],
}

# BS 科目對照 (合約負債-流動 + 普通股股本)
BS_FIELD_MAP = {
    "cl": [
        "CurrentContractLiabilities",    # ← FinMind 真名(Current 在前,v3.3 log 確認)
        "ContractLiabilities-Current",   # SPEC 原值(備用)
        "ContractLiabilitiesCurrent",    # 無破折號
        "ContractLiabilities",           # 無 -Current 後綴
        "ContractLiability-Current",     # 單數 Liability
        "ContractLiability",             # 單數 + 無後綴
    ],
    # v3.4: 普通股股本(用於 CL 佔股本比計算)
    "capitalStock": [
        "CapitalStock",     # FinMind 主名(BS log 已確認存在)
        "ShareCapital",     # fallback
    ],
}

# ============================================================
# Paths
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
RAW_DIR = CACHE_DIR / "raw"
DATA_DIR = BASE_DIR / "data"
STOCKS_OUT_DIR = DATA_DIR / "stocks"
META_PATH = DATA_DIR / "meta.json"
SCANNER_INDEX_PATH = DATA_DIR / "scanner_index.json"
BACKFILL_STATE_PATH = CACHE_DIR / "backfill_state.json"

for _p in (RAW_DIR, STOCKS_OUT_DIR, DATA_DIR, CACHE_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ============================================================
# Universe (which stocks to build for)
# ============================================================
# v3.4 Phase 2: 全市場擴充
#   USE_FULL_UNIVERSE = True  → 從 FinMind TaiwanStockInfo 過濾出全市場約 1,700 檔
#   USE_FULL_UNIVERSE = False → 沿用 DEMO_UNIVERSE 20 檔示範清單 (dev/backward-compat)
USE_FULL_UNIVERSE = True

# 分批策略: 全市場 1,700 檔分 7 批,每天 03:30 TPE 執行下一批
# 一週跑完全部 · 週而復始
BATCH_COUNT = 7

# ============================================================
# v3.5.4-u1: Active Universe (現役上市櫃母體)
# ============================================================
# 對應 P1 audit + P2 §四~§十一
#
# 產品定義:
#   Active Universe = B ∩ Official
#     B = FinMind common stocks (universe.build_universe 產出)
#     Official = TWSE 現有上市 ∪ TPEx 現有上櫃 (四位純數字 IDs)
#
# 官方端點(openapi · 無 auth):
ACTIVE_UNIVERSE_TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
ACTIVE_UNIVERSE_TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

# 官方 payload 中的股票代號 key
ACTIVE_UNIVERSE_TWSE_ID_KEY = "公司代號"
ACTIVE_UNIVERSE_TPEX_ID_KEY = "SecuritiesCompanyCode"

# 官方 payload 中的出表日期 key
ACTIVE_UNIVERSE_TWSE_DATE_KEY = "出表日期"  # 民國 YYYMMDD 格式
ACTIVE_UNIVERSE_TPEX_DATE_KEY = "Date"      # 民國 YYYMMDD 格式

# HTTP 呼叫參數(對齊 P2 §四:connect/read timeout · bounded retry)
ACTIVE_UNIVERSE_CONNECT_TIMEOUT = 10       # 秒
ACTIVE_UNIVERSE_READ_TIMEOUT = 30          # 秒
ACTIVE_UNIVERSE_MAX_RETRIES = 3
ACTIVE_UNIVERSE_RETRY_BACKOFF = 3          # 秒(指數 base)

# Schema 最低門檻(對齊 P2 §四:body 必須非空 list + 完整性驗證)
# 用防護性下限值 · 避免對方端點回 5 檔就發布為有效資料
ACTIVE_UNIVERSE_MIN_TWSE_COUNT = 800       # 目前 1089 · 500-2000 為合理帶
ACTIVE_UNIVERSE_MIN_TPEX_COUNT = 600       # 目前 890 · 400-1500 為合理帶

# Drift guard(對齊 P2 §三 + §九)
# ratio < 5% 接受 · ratio >= 5% 拒絕(邊界 >= 0.05)
# removed 與 added 分別計算(不只比總數 · 避免同數量大量成分互換)
ACTIVE_UNIVERSE_DRIFT_MAX_REMOVED_RATIO = 0.05
ACTIVE_UNIVERSE_DRIFT_MAX_ADDED_RATIO = 0.05

# Last-known-good snapshot 路徑(對齊 P2 §五 · git 追蹤)
ACTIVE_UNIVERSE_PATH = DATA_DIR / "active_universe.json"

# LKG schema version(未來 schema 演進用)
ACTIVE_UNIVERSE_SCHEMA_VERSION = 1

# 首版限縮於 SPEC §18.1 20 檔示範清單, USE_FULL_UNIVERSE=False 時使用
DEMO_UNIVERSE = [
    # 半導體
    "2330",  # 台積電
    "2454",  # 聯發科
    "2379",  # 瑞昱
    "3711",  # 日月光投控
    "6415",  # 矽力-KY
    # 電子零組件
    "2308",  # 台達電
    "2317",  # 鴻海
    "3037",  # 欣興
    "8046",  # 南電
    "6488",  # 環球晶
    # 金融
    "2882",  # 國泰金
    "2891",  # 中信金
    "5871",  # 中租-KY
    "2618",  # 長榮航
    "2412",  # 中華電信
    # 傳產
    "1102",  # 亞泥
    "2603",  # 長榮
    "2385",  # 群光
    "9910",  # 豐泰
    "1216",  # 統一
]
