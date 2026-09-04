"""
v3.5.4-u1 · P3 全面對抗性測試(§末列舉的攻擊面)

【誠實聲明】網路呼叫全 mocked · 不 live。

涵蓋:
  · --force 不繞過 validation / drift / LKG
  · DEMO_UNIVERSE 不繞過 active filter
  · active stale / finance / stocks/*.json 保留
  · models.py / transform.py / output.py / universe.py / test_op_to_capital.py 無 diff
  · Duplicate handling
"""
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from pipeline import active_universe as au
from pipeline import build as pipeline_build
from pipeline import config


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pipeline.active_universe.time.sleep", lambda *_a, **_kw: None)


# ============================================================
# helpers
# ============================================================

def _preseed_lkg(lkg_path, official_ids):
    # tpex pool 由 test 決定 · adversarial 用 "5xxx" IDs 當 tpex
    tpex = sorted(x for x in official_ids if int(x) >= 5000)
    twse = sorted(x for x in official_ids if int(x) < 5000)
    payload = {
        "schema_version": 1, "generated_at": "2026-08-31T22:00:00+0800",
        "source": "official_attachment_bootstrap",
        "twse_url": config.ACTIVE_UNIVERSE_TWSE_URL,
        "tpex_url": config.ACTIVE_UNIVERSE_TPEX_URL,
        "twse_as_of": "2026-08-31", "tpex_as_of": "2026-09-01",
        "official_as_of": "2026-08-31",
        "twse_count": len(twse), "tpex_count": len(tpex),
        "official_active_size": len(official_ids),
        "twse_ids": twse, "tpex_ids": tpex,
        "official_active_ids": sorted(official_ids),
        "ids_checksum": au.compute_checksum(sorted(official_ids)),
    }
    au.write_lkg(payload, lkg_path)


def _mock_official_ok(official_ids):
    twse = sorted(x for x in official_ids if int(x) < 5000)
    tpex = sorted(x for x in official_ids if int(x) >= 5000)
    tp = [{"公司代號": s, "出表日期": "1150831"} for s in twse]
    tx = [{"SecuritiesCompanyCode": s, "Date": "1150901"} for s in tpex]
    def mk(body):
        r = Mock(); r.status_code = 200; r.json = Mock(return_value=body); return r
    it = iter([mk(tp), mk(tx)])
    m = Mock(); m.get = Mock(side_effect=lambda *a, **kw: next(it))
    return m


def _mock_official_all_fail():
    def mk(*a, **kw):
        r = Mock(); r.status_code = 500; return r
    m = Mock(); m.get = Mock(side_effect=mk)
    return m


def _make_udf(ids):
    return pd.DataFrame([{"stock_id": s, "industry_category": "半導體業",
                          "stock_name": f"S{s}", "type": "twse"} for s in ids])


def _stub_pipeline(monkeypatch, udf, ingested_tracker=None):
    """
    Mock pipeline · fetch_universe 直接用傳入的 udf。
    這樣就沒有 monkeypatch 順序問題。
    """
    from pipeline import ingest, transform, output
    fake = Mock(); fake.mock = True
    monkeypatch.setattr(pipeline_build, "load_client", lambda: fake)
    monkeypatch.setattr(ingest, "fetch_universe", lambda c, force=False: udf)
    def _ingest_all(c, targets, force=False):
        if ingested_tracker is not None:
            ingested_tracker.extend(targets)
    monkeypatch.setattr(ingest, "ingest_all", _ingest_all)
    monkeypatch.setattr(transform, "build_detail", lambda c, sid, u: {"id": sid})
    monkeypatch.setattr(transform, "build_scanner_row",
                        lambda d: {"id": d["id"], "name": f"S{d['id']}", "market": "twse",
                                   "industry": "semi", "latestQuarter": "2026/2Q",
                                   "opCapitalIneligible": None,
                                   "opToCapitalQuarter": 12.34,
                                   "opToCapitalQuarterPercentile": 45.0,
                                   "opToCapitalTTM": 40.5,
                                   "opToCapitalTTMPercentile": 55.0,
                                   "opCapitalDataStale": False,
                                   "_latest_month": "2026-08"})
    monkeypatch.setattr(output, "write_stock_detail", lambda d: None)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    scanner = tmp_path / "s.json"
    meta = tmp_path / "m.json"
    stocks = tmp_path / "stocks"; stocks.mkdir()
    lkg = tmp_path / "u.json"
    monkeypatch.setattr(config, "SCANNER_INDEX_PATH", scanner)
    monkeypatch.setattr(config, "META_PATH", meta)
    monkeypatch.setattr(config, "STOCKS_OUT_DIR", stocks)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_PATH", lkg)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TWSE_COUNT", 1)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TPEX_COUNT", 1)
    from pipeline import output as po
    monkeypatch.setattr(po.config, "SCANNER_INDEX_PATH", scanner)
    monkeypatch.setattr(po.config, "META_PATH", meta)
    monkeypatch.setattr(po.config, "STOCKS_OUT_DIR", stocks)
    return dict(scanner=scanner, meta=meta, stocks=stocks, lkg=lkg)


# ============================================================
# §1 · --force 不繞過 validation / drift / LKG
# ============================================================

class TestForceDoesNotBypassGuards:
    def test_force_still_aborts_when_no_lkg_and_source_fails(self, paths, monkeypatch):
        """--force + 無 LKG + 來源失敗 → 仍然 abort"""
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_all_fail())
        ingested = []
        _stub_pipeline(monkeypatch, _make_udf(["1101", "2330"]), ingested)
        rc = pipeline_build.run(mode="backfill", stock_ids=None, force=True, batch=None)
        assert rc == 1
        assert ingested == []
        assert not paths["lkg"].exists()

    def test_force_still_aborts_when_no_lkg_and_valid_sources(self, paths, monkeypatch):
        """--force + 無 LKG + 兩來源 valid → 仍然 abort(no unattended bootstrap)"""
        official = {"1101", "2330", "5000"}
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_ok(official))
        ingested = []
        _stub_pipeline(monkeypatch, _make_udf(list(official)), ingested)
        rc = pipeline_build.run(mode="backfill", stock_ids=None, force=True, batch=None)
        assert rc == 1
        assert not paths["lkg"].exists()

    def test_force_still_respects_drift_reject(self, paths, monkeypatch):
        """--force + 有 LKG + candidate drift 太大 → 仍 reject · 用 LKG"""
        lkg_ids = {str(1000 + i) for i in range(100)} | {str(5000 + i) for i in range(100)}
        _preseed_lkg(paths["lkg"], lkg_ids)
        cand = {str(1000 + i) for i in range(50)} | {str(5000 + i) for i in range(100)}  # 50% removed
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_ok(cand))
        ingested = []
        _stub_pipeline(monkeypatch, _make_udf(sorted(lkg_ids)), ingested)
        rc = pipeline_build.run(mode="backfill", stock_ids=None, force=True, batch=None)
        assert rc == 0
        meta = json.loads(paths["meta"].read_text())
        assert meta["active_universe_source"] == "last_known_good_after_drift_reject"
        assert meta["official_active_size"] == 200  # 用 LKG · 不是 candidate 150


# ============================================================
# §2 · DEMO_UNIVERSE 不繞過 active filter
# ============================================================

class TestDemoUniverseFilteredByActive:
    def test_demo_universe_intersected_with_active(self, paths, monkeypatch):
        """USE_FULL_UNIVERSE=False + DEMO 含 inactive → 只保 active"""
        monkeypatch.setattr(config, "USE_FULL_UNIVERSE", False)
        monkeypatch.setattr(config, "DEMO_UNIVERSE", ["2325", "2330", "3711"])
        official = {"2330", "3711", "5000"}
        _preseed_lkg(paths["lkg"], official)
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_ok(official))
        ingested = []
        _stub_pipeline(monkeypatch, _make_udf(["2325", "2330", "3711"]), ingested)
        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0
        assert "2325" not in ingested
        assert set(ingested) == {"2330", "3711"}


# ============================================================
# §3 · Active rows preserved
# ============================================================

class TestActiveRowsPreserved:
    def test_active_stale_row_preserved_across_partial_build(self, paths, monkeypatch):
        """active + opCapitalDataStale=True · partial build 保留"""
        existing_rows = [
            {"id": "1101", "name": "S1101", "opCapitalDataStale": True,
             "opToCapitalQuarterPercentile": 33.0},
            {"id": "2330", "name": "S2330", "opCapitalDataStale": False,
             "opToCapitalQuarterPercentile": 88.0},
        ]
        paths["scanner"].write_text(json.dumps({"meta": {}, "stocks": existing_rows}))
        official = {"1101", "2330", "3711", "5000"}
        _preseed_lkg(paths["lkg"], official)
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_ok(official))
        ingested = []
        _stub_pipeline(monkeypatch, _make_udf(["1101", "2330", "3711"]), ingested)
        # partial 只更新 2330
        rc = pipeline_build.run(mode="daily", stock_ids=["2330"], force=False, batch=None)
        assert rc == 0
        scanner = json.loads(paths["scanner"].read_text())
        by_id = {r["id"]: r for r in scanner["stocks"]}
        # 1101 active stale 未更新 · 保留(prune 只移 inactive)
        assert "1101" in by_id
        assert by_id["1101"].get("opCapitalDataStale") is True

    def test_active_finance_preserved(self, paths, monkeypatch):
        """2882 國泰金 · active finance · 保留"""
        official = {"1101", "2882", "3711", "5000"}
        _preseed_lkg(paths["lkg"], official)
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_ok(official))
        ingested = []
        _stub_pipeline(monkeypatch, _make_udf(list(official)), ingested)
        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0
        scanner = json.loads(paths["scanner"].read_text())
        assert "2882" in {r["id"] for r in scanner["stocks"]}


class TestStocksJSONNotDeleted:
    def test_build_run_never_deletes_stocks_dir(self, paths, monkeypatch):
        """data/stocks/*.json 不得刪除"""
        (paths["stocks"] / "2325.json").write_text('{"id": "2325"}')
        (paths["stocks"] / "2330.json").write_text('{"id": "2330"}')
        official = {"1101", "2330", "5000"}
        _preseed_lkg(paths["lkg"], official)
        monkeypatch.setattr("pipeline.active_universe.requests", _mock_official_ok(official))
        ingested = []
        _stub_pipeline(monkeypatch, _make_udf(["1101", "2330", "2325"]), ingested)
        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0
        assert (paths["stocks"] / "2325.json").exists()   # 未刪
        assert (paths["stocks"] / "2330.json").exists()

    def test_active_universe_module_never_touches_stocks_dir(self):
        """active_universe.py 源碼不含 stocks/ 相關寫入"""
        import inspect
        src = inspect.getsource(au)
        assert "STOCKS_OUT_DIR" not in src
        assert "data/stocks/" not in src
        assert "stocks/" not in src


# ============================================================
# §4 · 不變檔 diff verify(P3 §末)· P3-r2 Blocker D:用 repo root · check=True
# ============================================================

# 用 Path(__file__).parents[2] 取 repo root · 不寫死 sandbox 路徑
_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestUntouchedFilesHaveNoDiff:
    """驗證未動的檔真的未動 · 用 repo root + check=True 防止假通過"""

    def _diff(self, path):
        r = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "diff", "--", path],
            capture_output=True, text=True, check=True,   # check=True · 非 0 raise
        )
        # returncode == 0 已由 check=True 保證 · 額外 assert 顯示語意
        assert r.returncode == 0
        # 若 git 說了 fatal(如 not a repo)· 也 fail
        assert "fatal" not in r.stderr.lower(), f"git error: {r.stderr}"
        return r.stdout

    def test_models_py_unchanged(self):
        assert self._diff("pipeline/models.py") == ""

    def test_transform_py_unchanged(self):
        assert self._diff("pipeline/transform.py") == ""

    def test_output_py_unchanged(self):
        assert self._diff("pipeline/output.py") == ""

    def test_universe_py_unchanged(self):
        assert self._diff("pipeline/universe.py") == ""

    def test_test_op_to_capital_unchanged(self):
        assert self._diff("pipeline/tests/test_op_to_capital.py") == ""


# ============================================================
# §5 · Duplicate handling
# ============================================================

class TestDuplicateHandling:
    def test_duplicate_ids_deduped_at_snapshot_build(self):
        twse = au.SourceLoadResult(ok=True, source_name="twse",
                                    ids={"1101"}, as_of="2026-08-31", raw_count=3)
        tpex = au.SourceLoadResult(ok=True, source_name="tpex",
                                    ids={"5000"}, as_of="2026-09-01", raw_count=1)
        p = au.build_snapshot_payload(twse, tpex, source="official_attachment_bootstrap")
        assert p["twse_count"] == 1
        assert p["official_active_size"] == 2

    def test_checksum_stable_for_deduped_set(self):
        c1 = au.compute_checksum(["1101", "2330"])
        c2 = au.compute_checksum(list(set(["1101", "1101", "2330", "2330"])))
        assert c1 == c2
