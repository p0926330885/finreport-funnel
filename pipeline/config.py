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
    "info":     "TaiwanStockInfo",                   # 股票基本資料
    "fs":       "TaiwanStockFinancialStatements",    # 綜合損益表 (季)
    "bs":       "TaiwanStockBalanceSheet",           # 資產負債表 (季,合約負債在此)
    "revenue":  "TaiwanStockMonthRevenue",           # 月營收
    "price":    "TaiwanStockPrice",                  # 日 K (可選,未來擴充)
}

# ============================================================
# Business constants (matches SPEC v2.2 §7, Scanner §7.5)
# ============================================================
HAS_CL_THRESHOLD = 0.15                  # v2.2 §4.2: max(近8Q CL) / max(近8Q rev) > 0.15
QUARTERLY_HISTORY_QUARTERS = 8           # Detail 頁 8 季
MONTHLY_HISTORY_MONTHS = 26              # 24 for 12MA + 2 buffer
GOLDEN_CROSS_LOOKBACK_DAYS = 30          # Scanner §7.6

THRESHOLD_TO_YI = 50000                  # v2.2 §23 (百萬 -> 億 切換閾值)

# Health thresholds (v2.2 §7.3)
HEALTH_THRESHOLDS = {
    "gm":       {"green": 25.0, "yellow": 15.0, "absolute": False},
    "om":       {"green":  8.0, "yellow":  3.0, "absolute": False},
    "nm":       {"green":  5.0, "yellow":  0.0, "absolute": False},
    "noiRatio": {"green": 25.0, "yellow": 70.0, "absolute": True},  # <25% 綠, <70% 黃
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
    # 生技
    "生技醫療業":       "biotech",
    "生技醫療":         "biotech",
    # 機械
    "電機機械":         "machinery",
    # 傳產 (default catch)
    "水泥工業":         "traditional",
    "食品工業":         "traditional",
    "紡織纖維":         "traditional",
    "鋼鐵工業":         "traditional",
    "汽車工業":         "traditional",
    "航運業":           "traditional",
    "觀光事業":         "traditional",
    "貿易百貨":         "traditional",
    "營建":             "traditional",
    "建材營造":         "traditional",
    "通信網路業":       "traditional",
    "其他電子業":       "traditional",
    # 金融
    "金融保險":         "finance",
    "金融保險業":       "finance",
    "金融":             "finance",
}
INDUSTRY_DEFAULT = "traditional"

# ============================================================
# FinMind FS 科目 -> 我方欄位對照 (SPEC §5.2)
# 註: FinMind FS API 回傳結構為 (date, stock_id, type, value)
# type 值來自財報 XBRL 標籤, 常見值如下 (以 FinMind 實際欄位為準,
# 若有變動需在此表更新)
# ============================================================
FS_FIELD_MAP = {
    "Revenue":                          "rev",   # 營業收入合計
    "GrossProfit":                      "gp",    # 營業毛利 (毛損)淨額
    "OperatingIncome":                  "op",    # 營業利益
    "IncomeAfterTaxes":                 "np",    # 本期淨利 (淨損)
    "EPS":                              "eps",   # 基本每股盈餘
    # 業外 = 稅前淨利 - 營業利益
    # 或直接 NonoperatingIncomeAndExpenses (視 FinMind 是否提供)
    "TotalNonoperatingIncomeAndExpenses": "noi",
}

# BS 科目對照 (合約負債)
BS_FIELD_MAP = {
    "ContractLiabilities-Current": "cl",     # 合約負債 - 流動
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
# 首版限縮於 SPEC §18.1 20 檔示範清單, 上線後改為全市場 upsert
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
