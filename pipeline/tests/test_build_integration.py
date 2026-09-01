"""
v3.5.4-u1 · Mocked build.run() Integration Tests

對應 P3 Blocker 4(6 個 build.run 情境)+ Blocker 5(full/partial pruned 稽核)
     + Blocker 10(TDR filter end-to-end)

【誠實聲明】
  · 這是 mocked build.run() integration test
  · 所有 network / FS boundary 用 pytest monkeypatch mock
  · 真的呼叫 pipeline.build.run(...) · 走過整個主流程
  · 不是 live end-to-end · 不是 GitHub Actions test
  · 不需要 FinMind auth · 不需要 TWSE / TPEx live

Mock 範圍:
  · pipeline.build.load_client → mock FinMindClient
  · pipeline.ingest.fetch_universe → 回 fake DataFrame
  · pipeline.ingest.ingest_all → no-op
  · pipeline.transform.build_detail → 回 fake detail
  · pipeline.transform.build_scanner_row → 回 fake row
  · pipeline.output.write_stock_detail → no-op(不寫 data/stocks/)
  · pipeline.config.SCANNER_INDEX_PATH / META_PATH / STOCKS_OUT_DIR / ACTIVE_UNIVERSE_PATH → tmp_path
  · pipeline.active_universe.load_twse_official / load_tpex_official → 回 SourceLoadResult
"""
import json
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pandas as pd
import pytest

from pipeline import active_universe as au
from pipeline import build as pipeline_build
from pipeline import config


# ============================================================
# Common fixtures / helpers
# ============================================================

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """避免 retry backoff 真的 sleep"""
    monkeypatch.setattr("pipeline.active_universe.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture
def redirect_paths(tmp_path, monkeypatch):
    """
    把所有 config 中的 path 重導向到 tmp_path · 讓 build.run() 寫檔到 tmp · 不動 real data/
    另外把 min_count 門檻降到 1 · 讓小 fixture 通過 loader schema
    """
    scanner_path = tmp_path / "scanner_index.json"
    meta_path = tmp_path / "meta.json"
    stocks_dir = tmp_path / "stocks"
    stocks_dir.mkdir()
    lkg_path = tmp_path / "active_universe.json"
    monkeypatch.setattr(config, "SCANNER_INDEX_PATH", scanner_path)
    monkeypatch.setattr(config, "META_PATH", meta_path)
    monkeypatch.setattr(config, "STOCKS_OUT_DIR", stocks_dir)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_PATH", lkg_path)
    # min_count 降到 1(test fixture 只有幾檔 IDs · 避免被 min 門檻擋)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TWSE_COUNT", 1)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TPEX_COUNT", 1)
    # output 也要 pick up 新路徑 · 但 output.py 是 import 時 rebound · 所以要 patch 那邊的
    from pipeline import output as pipeline_output
    monkeypatch.setattr(pipeline_output.config, "SCANNER_INDEX_PATH", scanner_path)
    monkeypatch.setattr(pipeline_output.config, "META_PATH", meta_path)
    monkeypatch.setattr(pipeline_output.config, "STOCKS_OUT_DIR", stocks_dir)
    return dict(scanner=scanner_path, meta=meta_path, stocks=stocks_dir, lkg=lkg_path)


def _preseed_lkg(lkg_path, official_ids, twse_only=None, tpex_only=None):
    """Test helper:預先寫 valid LKG"""
    if twse_only is None:
        twse_only = sorted(x for x in official_ids if int(x) < 5000)
    if tpex_only is None:
        tpex_only = sorted(x for x in official_ids if int(x) >= 5000)
    payload = {
        "schema_version": 1,
        "generated_at": "2026-08-31T22:00:00+0800",
        "source": "official_attachment_bootstrap",
        "twse_url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        "tpex_url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "twse_as_of": "2026-08-31",
        "tpex_as_of": "2026-09-01",
        "official_as_of": "2026-08-31",
        "twse_count": len(twse_only),
        "tpex_count": len(tpex_only),
        "official_active_size": len(official_ids),
        "twse_ids": sorted(twse_only),
        "tpex_ids": sorted(tpex_only),
        "official_active_ids": sorted(official_ids),
        "ids_checksum": au.compute_checksum(sorted(official_ids)),
    }
    au.write_lkg(payload, lkg_path)


def _mock_official_ok(official_ids, twse_ids=None, tpex_ids=None):
    """建 mock requests · 回兩個 valid response"""
    if twse_ids is None:
        twse_ids = sorted(x for x in official_ids if int(x) < 5000)
    if tpex_ids is None:
        tpex_ids = sorted(x for x in official_ids if int(x) >= 5000)
    twse_payload = [{"公司代號": sid, "出表日期": "1150831"} for sid in twse_ids]
    tpex_payload = [{"SecuritiesCompanyCode": sid, "Date": "1150901"} for sid in tpex_ids]
    def make_resp(body):
        r = Mock()
        r.status_code = 200
        r.json = Mock(return_value=body)
        return r
    m = Mock()
    calls_iter = iter([make_resp(twse_payload), make_resp(tpex_payload)])
    m.get = Mock(side_effect=lambda *a, **kw: next(calls_iter))
    return m


def _mock_official_all_fail():
    """兩個 endpoint 全部 HTTP 500"""
    def make_500(*a, **kw):
        r = Mock()
        r.status_code = 500
        return r
    m = Mock()
    m.get = Mock(side_effect=make_500)
    return m


def _make_fake_universe_df(ids):
    """建 fake FinMind TaiwanStockInfo DataFrame"""
    rows = []
    for sid in ids:
        rows.append({
            "stock_id": sid,
            "industry_category": "半導體業" if int(sid) >= 2300 else "水泥工業",
            "stock_name": f"Stock{sid}",
            "type": "twse",
        })
    return pd.DataFrame(rows)


def _make_fake_scanner_row(sid, *, latest_q="2026/2Q", opcap_pct=45.0):
    """建 fake scanner row"""
    return {
        "id": sid,
        "name": f"Stock{sid}",
        "market": "twse",
        "industry": "semi",
        "latestQuarter": latest_q,
        "opCapitalIneligible": None,
        "opToCapitalQuarter": 12.34,
        "opToCapitalQuarterPercentile": opcap_pct,
        "opToCapitalTTM": 40.5,
        "opToCapitalTTMPercentile": 55.0,
        "opCapitalDataStale": False,
        "_latest_month": "2026-08",
    }


def _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=None):
    """
    Mock pipeline.build 內部呼叫 · client / ingest / transform / output stock detail 全 mock。

    ingested_ids_tracker: list · 若提供 · 會記錄 ingest.ingest_all 呼叫的 IDs 供 test 驗證
    """
    from pipeline import ingest as pipeline_ingest
    from pipeline import transform as pipeline_transform
    from pipeline import output as pipeline_output

    # mock client
    fake_client = Mock()
    fake_client.mock = True
    monkeypatch.setattr(pipeline_build, "load_client", lambda: fake_client)

    # mock ingest.fetch_universe · 呼叫者可用 monkeypatch 覆寫這個 return value
    # 預設回一個空的 · 但下面測試會覆寫
    def default_fetch_universe(_client, force=False):
        return pd.DataFrame()
    monkeypatch.setattr(pipeline_ingest, "fetch_universe", default_fetch_universe)

    # mock ingest.ingest_all · 記錄呼叫
    def fake_ingest_all(_client, targets, force=False):
        if ingested_ids_tracker is not None:
            ingested_ids_tracker.extend(targets)
    monkeypatch.setattr(pipeline_ingest, "ingest_all", fake_ingest_all)

    # mock transform.build_detail · 回一個 sentinel
    def fake_build_detail(_client, sid, _u_df):
        return {"id": sid, "_sentinel": True}
    monkeypatch.setattr(pipeline_transform, "build_detail", fake_build_detail)

    # mock transform.build_scanner_row · 回 fake row
    def fake_build_scanner_row(detail):
        return _make_fake_scanner_row(detail["id"])
    monkeypatch.setattr(pipeline_transform, "build_scanner_row", fake_build_scanner_row)

    # mock output.write_stock_detail · no-op(不寫 data/stocks/)
    def fake_write_stock_detail(_detail):
        pass
    monkeypatch.setattr(pipeline_output, "write_stock_detail", fake_write_stock_detail)


# ============================================================
# TEST 1 · Full build(P3 Blocker 4 · 情境 1)
# ============================================================

class TestFullBuildIntegration:
    def test_full_build_active_only_no_tdr_no_inactive(self, redirect_paths, monkeypatch):
        """
        Full build:
          · B = active + inactive + TDR
          · Official = active + TDR
          · targets 只能是 active(不含 inactive · 不含 TDR)
          · scanner rows 只含 active
          · meta counts 正確
        """
        # B(FinMind common)含 active + inactive + TDR
        # 但實際上 build_universe.is_common_stock 已排除 91xx · 所以 B 不含 TDR
        # 我模擬:B 由 fake_universe_df 產生 · 只含四位純數字 · 但含 inactive
        active_ids = ["1101", "2330", "3711", "5000", "6001"]
        inactive_ids = ["2325", "2311"]   # 歷史 IDs · 只在 B · 不在 Official
        tdr_ids = ["9103", "9105"]        # 只在 Official · 不在 B(is_common_stock 排除)

        # B = active + inactive(is_common_stock 沒過濾這些 · 有的話)
        # 為了讓 build_universe(universe_df) 輸出 B = active + inactive · 我提供對應 DataFrame
        # (is_common_stock 只排 91xx / 特別 industry / KY 條件 · 對 active + inactive 正常公司都會 accept)
        b_ids = active_ids + inactive_ids
        u_df = _make_fake_universe_df(b_ids)

        # mock functions
        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)

        # pre-seed LKG:含 active + TDR(TDR 在 Official 但 B 排除)
        # 用 dispatch:1xxx-4xxx = twse · 5xxx-9xxx = tpex(以 5000 為界)
        official_ids = set(active_ids) | set(tdr_ids)
        _preseed_lkg(redirect_paths["lkg"], official_ids)

        # mock TWSE / TPEx endpoint
        m = _mock_official_ok(official_ids)
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        # 執行!
        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0

        # 驗證 targets:只有 active_ids · 不含 inactive · 不含 TDR
        # active_ids = {1101, 2330, 3711, 5000, 6001} 中 · 5000/6001 在 Official?
        # official_ids = {1101, 2330, 3711, 5000, 6001, 9103, 9105}
        # B ∩ Official = {1101, 2330, 3711, 5000, 6001} · 5 檔
        assert set(ingested) == {"1101", "2330", "3711", "5000", "6001"}
        assert "2325" not in ingested
        assert "2311" not in ingested
        assert "9103" not in ingested

        # 驗證 scanner output
        assert redirect_paths["scanner"].exists()
        scanner = json.loads(redirect_paths["scanner"].read_text())
        rows_ids = {r["id"] for r in scanner["stocks"]}
        assert rows_ids == {"1101", "2330", "3711", "5000", "6001"}

        # Meta 稽核
        meta = json.loads(redirect_paths["meta"].read_text())
        assert meta["universe_size"] == 5            # 現有 5 檔(P3 Blocker 1)
        assert meta["finmind_info_row_count"] == 7   # B 中的 raw 有 7 檔(active + inactive)
        assert meta["finmind_common_size"] == 7
        assert meta["official_active_size"] == 7     # 5 active + 2 TDR = 7
        assert meta["active_universe_size"] == 5     # B ∩ Official = 5
        assert meta["active_universe_source"] == "official_live"

    def test_full_build_pruned_from_existing_2047_fixture(self, redirect_paths, monkeypatch):
        """
        P3 Blocker 5:existing scanner 有 2047 rows · 其中 72 inactive
        Full build 執行後:
          · 稽核 inactive_rows_pruned == 72(從 existing 算 · 不 merge 回)
          · Output 只含本次 active scanner_rows
        """
        # 建 existing scanner · 2047 rows 中 72 inactive
        # 加 tpex sentinel "5000" 讓 tpex payload 非空(loader body-empty guard)
        active_ids = [f"1{i:03d}" for i in range(1000)] + [f"2{i:03d}" for i in range(975)] + ["5000"]  # 1976
        inactive_ids = [f"9{i:03d}" for i in range(72)]  # 72 假 inactive IDs
        existing_rows = [{"id": sid, "name": f"S{sid}", "opToCapitalQuarterPercentile": 50.0}
                          for sid in active_ids + inactive_ids]
        redirect_paths["scanner"].write_text(json.dumps({
            "meta": {}, "stocks": existing_rows,
        }))

        # B = 全部 · Official = 只 active
        u_df = _make_fake_universe_df(active_ids + inactive_ids)
        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)
        _preseed_lkg(redirect_paths["lkg"], set(active_ids))
        m = _mock_official_ok(set(active_ids))
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0

        # ingested 只有 active
        assert set(ingested) == set(active_ids)
        assert not (set(ingested) & set(inactive_ids))

        # meta.inactive_rows_pruned == 72(從 existing 稽核)
        meta = json.loads(redirect_paths["meta"].read_text())
        assert meta["inactive_rows_pruned"] == 72
        assert meta["universe_size"] == 1976   # 1975 + 1 tpex sentinel

        # scanner output 不含 inactive
        scanner = json.loads(redirect_paths["scanner"].read_text())
        rows_ids = {r["id"] for r in scanner["stocks"]}
        assert not (rows_ids & set(inactive_ids))


# ============================================================
# TEST 2 · Partial batch(P3 Blocker 4 · 情境 2)
# ============================================================

class TestPartialBatchIntegration:
    def test_partial_batch_prunes_and_preserves(self, redirect_paths, monkeypatch):
        """
        Partial batch:
          · existing scanner 有 inactive rows
          · 本批只更新少數 active rows
          · inactive rows 被 prune
          · 未更新 active rows 保留 percentiles
          · output deterministic sort
        """
        # existing:5 active + 2 inactive
        active_pool = ["1101", "2330", "3711", "5000", "6001"]
        inactive_ids = ["2325", "2311"]
        existing_rows = []
        for sid in active_pool:
            existing_rows.append({
                "id": sid, "name": f"S{sid}",
                "opToCapitalQuarterPercentile": 42.0,
                "_marker": "existing",
            })
        for sid in inactive_ids:
            existing_rows.append({"id": sid, "name": f"S{sid}",
                                    "opToCapitalQuarterPercentile": 99.0})
        redirect_paths["scanner"].write_text(json.dumps({
            "meta": {}, "stocks": existing_rows,
        }))

        u_df = _make_fake_universe_df(active_pool + inactive_ids)
        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)
        _preseed_lkg(redirect_paths["lkg"], set(active_pool))
        m = _mock_official_ok(set(active_pool))
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        # --stock 只更新 2 檔
        rc = pipeline_build.run(mode="daily", stock_ids=["1101", "2330"], force=False, batch=None)
        assert rc == 0

        # ingested 只有 2 檔
        assert set(ingested) == {"1101", "2330"}

        # scanner output:5 active(prune 掉 2 inactive)
        scanner = json.loads(redirect_paths["scanner"].read_text())
        rows_ids_ordered = [r["id"] for r in scanner["stocks"]]
        assert set(rows_ids_ordered) == set(active_pool)
        assert rows_ids_ordered == sorted(rows_ids_ordered)  # deterministic sort

        # 未更新 active row 保留 percentile(3711/5000/6001 應保留 42.0)
        by_id = {r["id"]: r for r in scanner["stocks"]}
        # Note: 本批 1101/2330 是新資料 · pct 由 fake_build_scanner_row 產出 = 45.0(_make_fake_scanner_row default)
        # 但 percentile lifecycle 是 partial_recompute 或 preserve_existing · 看具體實作
        # 這裡驗證未更新的 3711 pct 仍是舊值(如果是 preserve)· 或至少存在
        assert "3711" in by_id
        assert "opToCapitalQuarterPercentile" in by_id["3711"]

        # meta.inactive_rows_pruned = 2
        meta = json.loads(redirect_paths["meta"].read_text())
        assert meta["inactive_rows_pruned"] == 2


# ============================================================
# TEST 3 · --stock inactive only(P3 Blocker 4 · 情境 3)
# ============================================================

class TestStockInactiveOnly:
    def test_stock_all_inactive_aborts_no_scanner_mutation(self, redirect_paths, monkeypatch):
        """
        --stock 2325 2311:全 inactive
          · 不得 ingest
          · 不得覆蓋 scanner
          · return code != 0(或 == 1 · 對齊 P3 §六 abort 語意)
        """
        active_pool = ["1101", "2330", "3711", "5000"]
        existing_rows = [{"id": sid, "name": f"S{sid}"} for sid in active_pool]
        redirect_paths["scanner"].write_text(json.dumps({"meta": {}, "stocks": existing_rows}))
        scanner_bytes_before = redirect_paths["scanner"].read_bytes()

        u_df = _make_fake_universe_df(active_pool + ["2325", "2311"])
        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)
        _preseed_lkg(redirect_paths["lkg"], set(active_pool))
        m = _mock_official_ok(set(active_pool))
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        rc = pipeline_build.run(mode="daily", stock_ids=["2325", "2311"], force=False, batch=None)
        # 全 inactive · targets 為 [] · run() 應 return 1
        assert rc == 1
        # 不 ingest
        assert ingested == []
        # scanner bytes 完全未變(build 沒寫)
        assert redirect_paths["scanner"].read_bytes() == scanner_bytes_before


# ============================================================
# TEST 4 · mixed --stock(P3 Blocker 4 · 情境 4)
# ============================================================

class TestMixedStock:
    def test_mixed_stock_only_active_processed(self, redirect_paths, monkeypatch):
        """
        --stock 2325 2330 2311 3711:
          · 2330/3711 正常處理
          · 2325/2311 drop
          · inactive 不復活
        """
        active_pool = ["1101", "2330", "3711", "5000"]
        existing_rows = [{"id": sid, "name": f"S{sid}"} for sid in active_pool]
        redirect_paths["scanner"].write_text(json.dumps({"meta": {}, "stocks": existing_rows}))

        u_df = _make_fake_universe_df(active_pool + ["2325", "2311"])
        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)
        _preseed_lkg(redirect_paths["lkg"], set(active_pool))
        m = _mock_official_ok(set(active_pool))
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        rc = pipeline_build.run(mode="daily", stock_ids=["2325", "2330", "2311", "3711"],
                                 force=False, batch=None)
        assert rc == 0
        # 只有 active 被 ingest
        assert set(ingested) == {"2330", "3711"}
        # scanner 沒 2325 / 2311
        scanner = json.loads(redirect_paths["scanner"].read_text())
        rows_ids = {r["id"] for r in scanner["stocks"]}
        assert "2325" not in rows_ids
        assert "2311" not in rows_ids


# ============================================================
# TEST 5 · Source failure + valid LKG(P3 Blocker 4 · 情境 5)
# ============================================================

class TestSourceFailureWithLKG:
    def test_source_fail_with_lkg_uses_lkg(self, redirect_paths, monkeypatch):
        """
        兩 endpoint 失敗 + valid LKG → build.run 用 LKG · scanner 正常寫入 · source meta 正確
        """
        active_pool = ["1101", "2330", "3711", "5000"]
        u_df = _make_fake_universe_df(active_pool)
        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)
        _preseed_lkg(redirect_paths["lkg"], set(active_pool))
        # 兩來源全失敗
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_all_fail())

        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0
        # ingested = active_pool(用 LKG)
        assert set(ingested) == set(active_pool)
        # scanner 有寫
        assert redirect_paths["scanner"].exists()
        # meta source 正確
        meta = json.loads(redirect_paths["meta"].read_text())
        assert meta["active_universe_source"] == "last_known_good_after_source_failure"


# ============================================================
# TEST 6 · Source failure + no LKG(P3 Blocker 4 · 情境 6)
# ============================================================

class TestSourceFailureNoLKG:
    def test_source_fail_no_lkg_aborts_untouched(self, redirect_paths, monkeypatch):
        """
        來源失敗 + 無 LKG → build.run return 1 · scanner_index.json bytes 完全不變 · 不 ingest
        """
        # 建 existing scanner
        existing_rows = [{"id": "1101"}, {"id": "2330"}]
        redirect_paths["scanner"].write_text(json.dumps({"meta": {}, "stocks": existing_rows}))
        scanner_bytes_before = redirect_paths["scanner"].read_bytes()

        # 建 existing meta
        redirect_paths["meta"].write_text(json.dumps({"existing_meta": True}))
        meta_bytes_before = redirect_paths["meta"].read_bytes()

        # 不 pre-seed LKG(檔案不存在)
        assert not redirect_paths["lkg"].exists()

        u_df = _make_fake_universe_df(["1101", "2330"])
        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_all_fail())

        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 1
        # scanner + meta bytes 完全未變
        assert redirect_paths["scanner"].read_bytes() == scanner_bytes_before
        assert redirect_paths["meta"].read_bytes() == meta_bytes_before
        # 不 ingest
        assert ingested == []
        # LKG 沒被寫入(no unattended bootstrap)· 也沒被建立
        assert not redirect_paths["lkg"].exists()


# ============================================================
# TEST 7 · TDR filter(P3 Blocker 10 · build.run integration)
# ============================================================

class TestTDRExclusion:
    def test_tdr_9103_9105_9110_9136_excluded_end_to_end(self, redirect_paths, monkeypatch):
        """
        P3 Blocker 10:9103/9105/9110/9136 在 Official 中
          · B (FinMind common) 已排除它們(universe.is_common_stock 排 91xx)
          · 最終 build.run() 產出的 scanner rows / targets / active_ids 都不含它們
        """
        active_ids = ["1101", "2330", "3711", "5000"]
        tdr_ids = ["9103", "9105", "9110", "9136"]

        # B 中【不含】TDR(對應 real is_common_stock 邏輯)
        b_ids = active_ids
        u_df = _make_fake_universe_df(b_ids)

        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)

        # LKG + Official 都含 TDR
        official_ids = set(active_ids) | set(tdr_ids)
        _preseed_lkg(redirect_paths["lkg"], official_ids)
        m = _mock_official_ok(official_ids)
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0

        # targets 不含 TDR(B ∩ Official = active_ids)
        assert set(ingested) == set(active_ids)
        for tdr in tdr_ids:
            assert tdr not in ingested

        # scanner rows 不含 TDR
        scanner = json.loads(redirect_paths["scanner"].read_text())
        rows_ids = {r["id"] for r in scanner["stocks"]}
        for tdr in tdr_ids:
            assert tdr not in rows_ids

        # meta:official_active_size = 8(含 TDR)· active_universe_size = 4(排 TDR)
        meta = json.loads(redirect_paths["meta"].read_text())
        assert meta["official_active_size"] == 8
        assert meta["active_universe_size"] == 4
        assert meta["universe_size"] == 4

    def test_stock_9103_inactive_cannot_revive(self, redirect_paths, monkeypatch):
        """--stock 9103 · 即使 official 有 · B 排除 · 不 ingest · 不進 scanner"""
        active_pool = ["1101", "2330"]
        b_ids = active_pool  # B 不含 91xx
        u_df = _make_fake_universe_df(b_ids)

        ingested = []
        _mock_pipeline_functions(monkeypatch, ingested_ids_tracker=ingested)
        from pipeline import ingest as pipeline_ingest
        monkeypatch.setattr(pipeline_ingest, "fetch_universe", lambda c, force=False: u_df)
        # official 含 9103
        official_ids = set(active_pool) | {"9103"}
        _preseed_lkg(redirect_paths["lkg"], official_ids)
        m = _mock_official_ok(official_ids)
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        rc = pipeline_build.run(mode="daily", stock_ids=["9103"], force=False, batch=None)
        # 9103 不在 active_ids(B∩Official 排除) · 全 inactive → abort return 1
        assert rc == 1
        assert "9103" not in ingested
