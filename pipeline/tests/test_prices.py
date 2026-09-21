"""v3.6 K 線資料層測試(不連網)"""
from __future__ import annotations

import json

import pytest

from pipeline import prices


@pytest.fixture
def tmp_prices(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "PRICES_DIR", tmp_path / "prices")
    monkeypatch.setattr(prices, "PRICES_RECENT_PATH", tmp_path / "prices_recent.json")
    monkeypatch.setattr(prices, "PRICE_HISTORY_START", "2024-01-01")
    return tmp_path


def _fm(d, o, h, l, c, v=1_234_000):
    return {"date": d, "stock_id": "2330", "open": o, "max": h, "min": l, "close": c,
            "Trading_Volume": v, "Trading_money": 0, "spread": 0, "Trading_turnover": 0}


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch(self, dataset, **params):
        self.calls.append((dataset, params))
        start = params.get("start_date", "0000")
        return [r for r in self.rows if r["date"] >= start]


def test_parse_dates():
    assert prices.parse_roc_or_iso("1150918") == "2026-09-18"
    assert prices.parse_roc_or_iso("115/09/18") == "2026-09-18"
    assert prices.parse_roc_or_iso("20260918") == "2026-09-18"
    assert prices.parse_roc_or_iso("2026-09-18") == "2026-09-18"
    assert prices.parse_roc_or_iso("abc") is None
    assert prices.parse_roc_or_iso("1151340") is None


def test_make_bar_rules():
    assert prices.make_bar("2026-01-02", "1,000.5", "1,010", "990", "1005", "12,345,678") == \
        ["2026-01-02", 1000.5, 1010, 990, 1005, 12346]
    # 無成交 / 缺值
    assert prices.make_bar("2026-01-02", "--", "--", "--", "--", "0") is None
    assert prices.make_bar("2026-01-02", 0, 0, 0, 0, 0) is None
    # 高低自動修正
    assert prices.make_bar("2026-01-02", 10, 9, 11, 10.5, 0)[2:4] == [11, 9]


def test_history_full_then_incremental(tmp_prices):
    rows = [_fm("2024-12-30", 10, 11, 9, 10.5), _fm("2024-12-31", 10.5, 12, 10, 11),
            _fm("2025-01-02", 11, 11.5, 10.8, 11.2)]
    c = FakeClient(rows)
    assert prices.update_price_history(c, "2330")
    assert c.calls[0][1]["start_date"] == "2024-01-01"
    d = tmp_prices / "prices" / "2330"
    idx = json.loads((d / "index.json").read_text())
    assert idx["years"] == [2024, 2025] and idx["first"] == "2024-12-30" and idx["last"] == "2025-01-02"
    assert len(json.loads((d / "2024.json").read_text())) == 2

    # 第二次:只從最後一筆所在年份抓 · 舊年份保留
    c.rows.append(_fm("2025-01-03", 11.2, 11.6, 11, 11.5))
    assert prices.update_price_history(c, "2330")
    assert c.calls[1][1]["start_date"] == "2025-01-01"
    idx = json.loads((d / "index.json").read_text())
    assert idx["years"] == [2024, 2025] and idx["first"] == "2024-12-30" and idx["last"] == "2025-01-03"
    assert prices.read_history_tail("2330", 3)[-1][0] == "2025-01-03"
    assert [b[0] for b in prices.read_history_tail("2330", 3)] == ["2024-12-31", "2025-01-02", "2025-01-03"]


def test_history_never_raises(tmp_prices):
    class Boom:
        def fetch(self, *a, **k):
            raise RuntimeError("x")
    assert prices.update_price_history(Boom(), "2330") is False
    # mock client 回傳不相干欄位 → 不寫檔
    assert prices.update_price_history(FakeClient([{"date": "2024-01-02", "value": 1}]), "2330") is False
    assert not (tmp_prices / "prices" / "2330").exists()


def test_parse_official_payloads():
    twse = [{"Date": "1150918", "Code": "2330", "Name": "台積電", "TradeVolume": "30,000,000",
             "TradeValue": "1", "OpeningPrice": "1,200.00", "HighestPrice": "1,215.00",
             "LowestPrice": "1,195.00", "ClosingPrice": "1,210.00", "Change": "5", "Transaction": "1"},
            {"Date": "1150918", "Code": "0050", "OpeningPrice": "1", "HighestPrice": "1",
             "LowestPrice": "1", "ClosingPrice": "1", "TradeVolume": "1"},
            {"Date": "1150918", "Code": "1101", "OpeningPrice": "", "HighestPrice": "",
             "LowestPrice": "", "ClosingPrice": "", "TradeVolume": "0"}]
    out = prices.parse_twse_day_all(twse)
    assert out["2330"] == ["2026-09-18", 1200, 1215, 1195, 1210, 30000]
    assert "1101" not in out                  # 當日無成交 → 不產生 K 棒

    tpex = [{"Date": "1150918", "SecuritiesCompanyCode": "3093", "CompanyName": "港建",
             "Close": "45.40", "Change": "+0.65", "Open": "44.80", "High": "45.90", "Low": "44.50",
             "TradingShares": "2,300,000"},
            {"Date": "1150918", "SecuritiesCompanyCode": "006201", "Close": "1", "Open": "1",
             "High": "1", "Low": "1", "TradingShares": "1"}]
    assert prices.parse_tpex_day_all(tpex) == {"3093": ["2026-09-18", 44.8, 45.9, 44.5, 45.4, 2300]}


def test_merge_recent_keeps_last_n_and_backfills():
    existing = {"bars": {"2330": [["2026-09-16", 1, 1, 1, 1, 1], ["2026-09-17", 2, 2, 2, 2, 2]],
                         "9999": [["2026-06-01", 1, 1, 1, 1, 1]]}}
    new = {"2330": ["2026-09-18", 3, 3, 3, 3, 3], "2317": ["2026-09-18", 5, 5, 5, 5, 5]}
    tail = {"2317": [["2026-09-17", 4, 4, 4, 4, 4]]}
    m = prices.merge_recent(existing, new, tail_loader=lambda sid, n: tail.get(sid, []), keep=2)
    assert [b[0] for b in m["bars"]["2330"]] == ["2026-09-17", "2026-09-18"]
    assert [b[0] for b in m["bars"]["2317"]] == ["2026-09-17", "2026-09-18"]
    assert "9999" not in m["bars"]          # 久未交易被移除
    assert m["asOf"] == "2026-09-18"
    # 同日重跑 → 冪等
    m2 = prices.merge_recent(m, new, keep=2)
    assert m2["bars"] == m["bars"]


# ============================================================
# v3.6.1
# ============================================================
def test_parse_twse_rwd():
    payload = {"stat": "OK", "date": "20260921",
               "fields": ["證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數"],
               "data": [["2330", "台積電", "40,893,000", "1", "2,450.00", "2,470.00", "2,440.00", "2,465.00", "+5.00", "1"],
                        ["0050", "元大台灣50", "1", "1", "1", "1", "1", "1", "0", "1"],
                        ["1101", "台泥", "0", "0", "--", "--", "--", "--", "0", "0"]]}
    out = prices.parse_twse_rwd(payload)
    assert out["2330"] == ["2026-09-21", 2450, 2470, 2440, 2465, 40893]
    assert "1101" not in out
    assert prices.parse_twse_rwd({"stat": "很抱歉,沒有符合條件的資料!"}) == {}
    assert prices.parse_twse_rwd({"stat": "OK", "date": "20260921", "fields": ["x"], "data": []}) == {}


def test_merge_recent_accepts_multiple_days():
    new = {"2330": [["2026-09-18", 1, 1, 1, 1, 1], ["2026-09-21", 2, 2, 2, 2, 2]]}
    m = prices.merge_recent(None, new, keep=5)
    assert [b[0] for b in m["bars"]["2330"]] == ["2026-09-18", "2026-09-21"]


def test_extend_from_recent_saves_finmind(tmp_prices, monkeypatch):
    rows = [_fm("2026-09-16", 10, 11, 9, 10.5), _fm("2026-09-17", 10.5, 12, 10, 11)]
    c = FakeClient(rows)
    monkeypatch.setattr(prices, "PRICE_HISTORY_START", "2026-01-01")
    assert prices.update_price_history(c, "2330") and len(c.calls) == 1
    # 官方每日資料和歷史有重疊 → 直接延長 · 不呼叫 FinMind
    (tmp_prices / "prices_recent.json").write_text(json.dumps({"bars": {"2330": [
        ["2026-09-17", 10.5, 12, 10, 11, 1234], ["2026-09-18", 11, 11.5, 10.8, 11.2, 900],
        ["2026-09-21", 11.2, 11.9, 11.1, 11.8, 800]]}}))
    monkeypatch.setattr(prices, "_recent_cache", None)
    assert prices.update_price_history(c, "2330") and len(c.calls) == 1
    idx = json.loads((tmp_prices / "prices" / "2330" / "index.json").read_text())
    assert idx["last"] == "2026-09-21"
    assert [b[0] for b in prices.read_history_tail("2330", 10)] == ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]

    # 有缺口(官方資料最早一根晚於歷史最後一天)→ 交給 FinMind
    (tmp_prices / "prices_recent.json").write_text(json.dumps({"bars": {"2330": [
        ["2026-09-30", 12, 12, 12, 12, 1]]}}))
    monkeypatch.setattr(prices, "_recent_cache", None)
    prices.update_price_history(c, "2330")
    assert len(c.calls) == 2

    # 超過對帳天數 → 一定走 FinMind
    idxp = tmp_prices / "prices" / "2330" / "index.json"
    idx = json.loads(idxp.read_text()); idx["verified"] = "2026-01-01"; idxp.write_text(json.dumps(idx))
    (tmp_prices / "prices_recent.json").write_text(json.dumps({"bars": {"2330": [
        ["2026-09-17", 10.5, 12, 10, 11, 1234]]}}))
    monkeypatch.setattr(prices, "_recent_cache", None)
    prices.update_price_history(c, "2330")
    assert len(c.calls) == 3
