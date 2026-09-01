"""
v3.5.4-u1 · P3-r2 Remediation blocker tests

Blocker A · load_lkg 17 項 validator(malformed LKG 全 reject)
Blocker B · date 嚴格驗證 + freshness guard(candidate 日期倒退 → reject)
Blocker C · write_lkg 失敗 → 不使用未持久化 candidate · 用 LKG
Blocker D · git-dependent test 用 repo root · check=True · 不假通過
Blocker E · TDR fixture 走真 universe.build_universe · is_common_stock 排除 91xx
其他:duplicate warning 進 state.warnings · docstring 更新
"""
import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from pipeline import active_universe as au
from pipeline import build as pipeline_build
from pipeline import config


# P3-r3:repo root · 供 Initial LKG contract test 與 no-hardcoded-path regression 共用
# 不寫死絕對 sandbox 路徑 · 確保 test 可在任意 cwd 執行
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pipeline.active_universe.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _low_min(monkeypatch):
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TWSE_COUNT", 0)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TPEX_COUNT", 0)


# ============================================================
# helpers
# ============================================================

def _valid_lkg(twse_ids=None, tpex_ids=None,
                twse_as="2026-08-31", tpex_as="2026-09-01", official_as=None,
                source="official_attachment_bootstrap"):
    """建立一個 valid LKG payload · 供 test 修改用"""
    if twse_ids is None:
        twse_ids = ["1101", "2330"]
    if tpex_ids is None:
        tpex_ids = ["5000"]
    if official_as is None:
        official_as = min(twse_as, tpex_as)
    twse_ids = sorted(twse_ids)
    tpex_ids = sorted(tpex_ids)
    official = sorted(set(twse_ids) | set(tpex_ids))
    return {
        "schema_version": 1,
        "generated_at": "2026-08-31T22:00:00+0800",
        "source": source,
        "twse_url": config.ACTIVE_UNIVERSE_TWSE_URL,
        "tpex_url": config.ACTIVE_UNIVERSE_TPEX_URL,
        "twse_as_of": twse_as,
        "tpex_as_of": tpex_as,
        "official_as_of": official_as,
        "twse_count": len(twse_ids),
        "tpex_count": len(tpex_ids),
        "official_active_size": len(official),
        "twse_ids": twse_ids,
        "tpex_ids": tpex_ids,
        "official_active_ids": official,
        "ids_checksum": au.compute_checksum(official),
    }


def _mock_resp(*, status=200, body=None):
    r = Mock()
    r.status_code = status
    r.json = Mock(return_value=body)
    return r


def _mock_get_seq(*resps):
    it = iter(resps)
    m = Mock()
    m.get = Mock(side_effect=lambda *a, **kw: next(it))
    return m


def _twse_payload(ids, date="1150831"):
    return [{"公司代號": s, "出表日期": date} for s in ids]


def _tpex_payload(ids, date="1150901"):
    return [{"SecuritiesCompanyCode": s, "Date": date} for s in ids]


# ============================================================
# Blocker A · load_lkg 全面 validator(17 項)
# ============================================================

class TestBlockerALoadLKGValidator:
    """對每一項 validation rule 各一個反例 · 全部應該 reject"""

    def _write_and_load(self, tmp_path, payload):
        p = tmp_path / "u.json"
        p.write_text(json.dumps(payload))
        return au.load_lkg(p)

    def test_valid_baseline_accepted(self, tmp_path):
        """先確認 baseline valid 通過(sanity check)"""
        p = _valid_lkg()
        assert self._write_and_load(tmp_path, p) is not None

    # rule 1
    def test_not_dict_rejected(self, tmp_path):
        p = tmp_path / "u.json"
        p.write_text(json.dumps(["not", "a", "dict"]))
        assert au.load_lkg(p) is None

    # rule 2
    def test_wrong_schema_version_rejected(self, tmp_path):
        p = _valid_lkg()
        p["schema_version"] = 999
        assert self._write_and_load(tmp_path, p) is None

    # rule 3
    def test_disallowed_source_rejected(self, tmp_path):
        for bad_src in ["last_known_good_after_drift_reject",   # runtime label · 不該持久化
                        "official_live_typo", "malicious", ""]:
            p = _valid_lkg()
            p["source"] = bad_src
            assert self._write_and_load(tmp_path, p) is None, f"{bad_src} should reject"

    # rule 4:每個必要欄位缺一個都 reject
    @pytest.mark.parametrize("missing_key", [
        "generated_at", "source", "twse_url", "tpex_url",
        "twse_as_of", "tpex_as_of", "official_as_of",
        "twse_count", "tpex_count", "official_active_size",
        "twse_ids", "tpex_ids", "official_active_ids", "ids_checksum",
    ])
    def test_missing_required_field_rejected(self, tmp_path, missing_key):
        p = _valid_lkg()
        del p[missing_key]
        assert self._write_and_load(tmp_path, p) is None

    # rule 5:IDs 不是 list
    def test_ids_not_list_rejected(self, tmp_path):
        p = _valid_lkg()
        p["twse_ids"] = "not_a_list"
        assert self._write_and_load(tmp_path, p) is None

    # rule 6:非四位純數字
    def test_non_four_digit_id_rejected(self, tmp_path):
        p = _valid_lkg(twse_ids=["1101", "ABC"])
        # 修 counts / official 讓其他 rule 過 · 只驗這個
        p["twse_count"] = 2
        p["official_active_ids"] = sorted(["1101", "ABC", "5000"])
        p["official_active_size"] = 3
        p["ids_checksum"] = au.compute_checksum(p["official_active_ids"])
        assert self._write_and_load(tmp_path, p) is None

    def test_five_digit_id_rejected(self, tmp_path):
        p = _valid_lkg(twse_ids=["12345"])
        p["twse_count"] = 1
        p["official_active_ids"] = sorted(["12345", "5000"])
        p["official_active_size"] = 2
        p["ids_checksum"] = au.compute_checksum(p["official_active_ids"])
        assert self._write_and_load(tmp_path, p) is None

    # rule 7:duplicates
    def test_duplicate_ids_rejected(self, tmp_path):
        p = _valid_lkg()
        p["twse_ids"] = ["1101", "1101", "2330"]
        p["twse_count"] = 3
        # union rule 會另外 catch · 這裡驗證 dup 本身即拒
        assert self._write_and_load(tmp_path, p) is None

    # rule 8:未 sorted
    def test_not_sorted_rejected(self, tmp_path):
        p = _valid_lkg()
        p["twse_ids"] = ["2330", "1101"]   # 顛倒
        p["official_active_ids"] = ["2330", "1101", "5000"]
        p["ids_checksum"] = au.compute_checksum(p["official_active_ids"])
        assert self._write_and_load(tmp_path, p) is None

    # rule 9-11:counts 對不上 arrays
    def test_twse_count_mismatch_rejected(self, tmp_path):
        p = _valid_lkg()
        p["twse_count"] = p["twse_count"] + 5
        assert self._write_and_load(tmp_path, p) is None

    def test_tpex_count_mismatch_rejected(self, tmp_path):
        p = _valid_lkg()
        p["tpex_count"] = p["tpex_count"] + 5
        assert self._write_and_load(tmp_path, p) is None

    def test_official_active_size_mismatch_rejected(self, tmp_path):
        p = _valid_lkg()
        p["official_active_size"] = p["official_active_size"] + 1
        assert self._write_and_load(tmp_path, p) is None

    # rule 12:union 不合
    def test_union_mismatch_rejected(self, tmp_path):
        p = _valid_lkg(twse_ids=["1101"], tpex_ids=["5000"])
        # 加一個 ID 在 official 但不在 twse+tpex
        p["official_active_ids"] = sorted(["1101", "5000", "9999"])
        p["official_active_size"] = 3
        p["ids_checksum"] = au.compute_checksum(p["official_active_ids"])
        assert self._write_and_load(tmp_path, p) is None

    # rule 13:checksum 錯
    def test_checksum_mismatch_rejected(self, tmp_path):
        p = _valid_lkg()
        p["ids_checksum"] = "sha256:" + "0" * 64
        assert self._write_and_load(tmp_path, p) is None

    # rule 14:date 不是合法 ISO date
    def test_invalid_iso_date_rejected(self, tmp_path):
        p = _valid_lkg()
        p["twse_as_of"] = "2026/08/31"  # 用 / 而非 -
        assert self._write_and_load(tmp_path, p) is None

    def test_impossible_date_rejected(self, tmp_path):
        p = _valid_lkg()
        p["tpex_as_of"] = "2026-02-30"
        assert self._write_and_load(tmp_path, p) is None

    # rule 15:official_as_of != min
    def test_official_as_of_not_min_rejected(self, tmp_path):
        p = _valid_lkg(twse_as="2026-08-31", tpex_as="2026-09-01",
                        official_as="2026-09-15")   # 不是 min
        assert self._write_and_load(tmp_path, p) is None

    # rule 16:count 低於 config min(暫時 restore min 到大值)
    def test_below_min_count_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TWSE_COUNT", 800)
        p = _valid_lkg()   # twse=2 < 800
        assert self._write_and_load(tmp_path, p) is None

    # rule 17:URL 與 config 不一致
    def test_wrong_twse_url_rejected(self, tmp_path):
        p = _valid_lkg()
        p["twse_url"] = "https://malicious.example.com/api"
        assert self._write_and_load(tmp_path, p) is None

    def test_wrong_tpex_url_rejected(self, tmp_path):
        p = _valid_lkg()
        p["tpex_url"] = "https://old-tpex.example.com/api"
        assert self._write_and_load(tmp_path, p) is None


# ============================================================
# Blocker B · Date 嚴格 + Freshness guard
# ============================================================

class TestBlockerBDateStrict:
    def test_missing_date_key_rejects(self):
        """P3-r2 Blocker B:record 缺 date_key → source invalid"""
        payload = [{"公司代號": "1101"}] * 3   # 缺 出表日期
        m = _mock_get_seq(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "date" in (r.error or "").lower()

    def test_null_date_rejects(self):
        payload = [{"公司代號": "1101", "出表日期": None}]
        m = _mock_get_seq(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "null" in (r.error or "")

    def test_blank_date_rejects(self):
        payload = [{"公司代號": "1101", "出表日期": "   "}]
        m = _mock_get_seq(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "blank" in (r.error or "")

    def test_unparseable_date_rejects(self):
        payload = [{"公司代號": "1101", "出表日期": "not-a-date"}]
        m = _mock_get_seq(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "ROC" in (r.error or "") or "parse" in (r.error or "")

    def test_two_distinct_dates_reject(self):
        """同來源包含兩個出表日期 → source invalid"""
        payload = [
            {"公司代號": "1101", "出表日期": "1150831"},
            {"公司代號": "2330", "出表日期": "1150901"},  # 不同日期!
        ]
        m = _mock_get_seq(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "distinct" in (r.error or "")


class TestBlockerBFreshnessGuard:
    def test_same_dates_pass(self, tmp_path):
        """candidate 日期相同 → pass"""
        lkg_path = tmp_path / "u.json"
        au.write_lkg(_valid_lkg(twse_ids=["1101", "2330"], tpex_ids=["5000"]), lkg_path)
        # candidate 相同日期
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(["1101", "2330"])),
            _mock_resp(status=200, body=_tpex_payload(["5000"])),
        )
        state = au.load_active_universe(finmind_common_ids={"1101", "2330", "5000"},
                                         _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "official_live"

    def test_newer_dates_pass(self, tmp_path):
        """candidate 兩者更新 → pass"""
        lkg_path = tmp_path / "u.json"
        au.write_lkg(_valid_lkg(twse_ids=["1101", "2330"], tpex_ids=["5000"],
                                  twse_as="2026-08-01", tpex_as="2026-08-01"), lkg_path)
        # candidate 更新日期(2026-08-31 · 2026-09-01)
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(["1101", "2330"], date="1150831")),
            _mock_resp(status=200, body=_tpex_payload(["5000"], date="1150901")),
        )
        state = au.load_active_universe(finmind_common_ids={"1101", "2330", "5000"},
                                         _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "official_live"

    def test_twse_date_regression_rejects(self, tmp_path):
        """TWSE 日期倒退 → freshness reject · 保 LKG"""
        lkg_path = tmp_path / "u.json"
        au.write_lkg(_valid_lkg(twse_ids=["1101"], tpex_ids=["5000"],
                                  twse_as="2026-09-01", tpex_as="2026-09-01",
                                  official_as="2026-09-01"), lkg_path)
        bytes_before = lkg_path.read_bytes()
        # candidate 日期倒退到 8/1
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(["1101"], date="1150801")),
            _mock_resp(status=200, body=_tpex_payload(["5000"], date="1150901")),
        )
        state = au.load_active_universe(finmind_common_ids={"1101", "5000"},
                                         _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "last_known_good_after_freshness_reject"
        assert state.schema_status == "freshness_rejected"
        # LKG bytes 完全不變
        assert lkg_path.read_bytes() == bytes_before
        # warnings 含日期資訊
        assert any("twse" in w.lower() and "2026-08-01" in w for w in state.warnings)

    def test_tpex_date_regression_rejects(self, tmp_path):
        lkg_path = tmp_path / "u.json"
        au.write_lkg(_valid_lkg(twse_ids=["1101"], tpex_ids=["5000"],
                                  twse_as="2026-09-01", tpex_as="2026-09-01",
                                  official_as="2026-09-01"), lkg_path)
        bytes_before = lkg_path.read_bytes()
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(["1101"], date="1150901")),
            _mock_resp(status=200, body=_tpex_payload(["5000"], date="1150801")),
        )
        state = au.load_active_universe(finmind_common_ids={"1101", "5000"},
                                         _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "last_known_good_after_freshness_reject"
        assert lkg_path.read_bytes() == bytes_before

    def test_both_regress_rejects(self, tmp_path):
        lkg_path = tmp_path / "u.json"
        au.write_lkg(_valid_lkg(twse_ids=["1101"], tpex_ids=["5000"],
                                  twse_as="2026-09-01", tpex_as="2026-09-01",
                                  official_as="2026-09-01"), lkg_path)
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(["1101"], date="1150801")),
            _mock_resp(status=200, body=_tpex_payload(["5000"], date="1150801")),
        )
        state = au.load_active_universe(finmind_common_ids={"1101", "5000"},
                                         _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "last_known_good_after_freshness_reject"
        # warning 應同時列 TWSE 與 TPEx
        warning = "; ".join(state.warnings)
        assert "twse" in warning.lower()
        assert "tpex" in warning.lower()


# ============================================================
# Blocker C · write_lkg 失敗 fallback
# ============================================================

class TestBlockerCWriteFailure:
    def test_write_failure_uses_lkg_not_candidate(self, tmp_path, monkeypatch):
        """
        candidate 增加新 ID · write_lkg 失敗 →
          · returned active_ids 是舊 LKG · 不是 candidate
          · source = last_known_good_after_write_failure
          · 原 LKG bytes 完全不變
        用 100 個 LKG · candidate 只加 1 個(1% added · 通過 drift 5%)
        """
        lkg_path = tmp_path / "u.json"
        base_twse = [f"1{i:03d}" for i in range(100)]
        base_tpex = [f"5{i:03d}" for i in range(50)]
        au.write_lkg(_valid_lkg(twse_ids=base_twse, tpex_ids=base_tpex), lkg_path)
        bytes_before = lkg_path.read_bytes()

        # candidate 只加 1 個 twse "2330"(1/150 = 0.67% · 通過 drift)
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(base_twse + ["2330"])),
            _mock_resp(status=200, body=_tpex_payload(base_tpex)),
        )
        # 只 mock active_universe 的 LKG atomic replace seam ·
        # 不用 "pipeline.active_universe.os.replace"(那會污染全域 os.replace)
        def broken_replace(src, dst):
            raise OSError("simulated disk full")
        monkeypatch.setattr("pipeline.active_universe._atomic_replace", broken_replace)

        state = au.load_active_universe(
            finmind_common_ids=set(base_twse + base_tpex + ["2330"]),
            _requests_module=m, _lkg_path=lkg_path,
        )
        # 用 LKG · 不用 candidate → 2330 不在 active
        assert "2330" not in state.active_ids
        assert state.source == "last_known_good_after_write_failure"
        assert state.schema_status == "lkg_write_failed"
        # LKG bytes 完全不變
        assert lkg_path.read_bytes() == bytes_before
        # warning 提到寫入失敗
        assert any("write" in w.lower() and "fail" in w.lower() for w in state.warnings)

    def test_write_failure_via_build_run(self, tmp_path, monkeypatch):
        """
        build.run() 整條路徑 · write_lkg 失敗 → Scanner 不採用未持久化 candidate
        · meta.active_universe_source = last_known_good_after_write_failure
        · ingest targets 不含新 ID
        """
        scanner_path = tmp_path / "s.json"
        meta_path = tmp_path / "m.json"
        stocks_dir = tmp_path / "stocks"; stocks_dir.mkdir()
        lkg_path = tmp_path / "u.json"
        monkeypatch.setattr(config, "SCANNER_INDEX_PATH", scanner_path)
        monkeypatch.setattr(config, "META_PATH", meta_path)
        monkeypatch.setattr(config, "STOCKS_OUT_DIR", stocks_dir)
        monkeypatch.setattr(config, "ACTIVE_UNIVERSE_PATH", lkg_path)
        from pipeline import output as po
        monkeypatch.setattr(po.config, "SCANNER_INDEX_PATH", scanner_path)
        monkeypatch.setattr(po.config, "META_PATH", meta_path)
        monkeypatch.setattr(po.config, "STOCKS_OUT_DIR", stocks_dir)

        # 大 fixture:LKG 150 個 · candidate 加 1 個(0.67% · pass drift)
        base_twse = [f"1{i:03d}" for i in range(100)]
        base_tpex = [f"5{i:03d}" for i in range(50)]
        au.write_lkg(_valid_lkg(twse_ids=base_twse, tpex_ids=base_tpex), lkg_path)
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(base_twse + ["2330"])),
            _mock_resp(status=200, body=_tpex_payload(base_tpex)),
        )
        monkeypatch.setattr("pipeline.active_universe.requests", m)
        # 只 mock active_universe 的 LKG atomic replace seam ·
        # 全域 os.replace 必須留給 output.write_scanner_index／write_meta 使用
        def broken_replace(src, dst):
            raise OSError("disk full")
        monkeypatch.setattr("pipeline.active_universe._atomic_replace", broken_replace)

        from pipeline import ingest, transform
        fake_client = Mock(); fake_client.mock = True
        monkeypatch.setattr(pipeline_build, "load_client", lambda: fake_client)
        u_df = pd.DataFrame([{"stock_id": s, "industry_category": "半導體業",
                               "stock_name": f"S{s}", "type": "twse"}
                              for s in base_twse + base_tpex + ["2330"]])
        monkeypatch.setattr(ingest, "fetch_universe", lambda c, force=False: u_df)
        ingested = []
        monkeypatch.setattr(ingest, "ingest_all",
                            lambda c, targets, force=False: ingested.extend(targets))
        monkeypatch.setattr(transform, "build_detail", lambda c, sid, u: {"id": sid})
        monkeypatch.setattr(transform, "build_scanner_row", lambda d: {
            "id": d["id"], "name": f"S{d['id']}", "market": "twse", "industry": "semi",
            "latestQuarter": "2026/2Q", "opCapitalIneligible": None,
            "opToCapitalQuarter": 12.34, "opToCapitalQuarterPercentile": 45.0,
            "opToCapitalTTM": 40.5, "opToCapitalTTMPercentile": 55.0,
            "opCapitalDataStale": False, "_latest_month": "2026-08",
        })
        monkeypatch.setattr(po, "write_stock_detail", lambda d: None)

        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0
        # ingested 不含 2330(用 LKG · 只有 base_twse + base_tpex)
        assert "2330" not in ingested
        assert set(ingested) == set(base_twse) | set(base_tpex)
        # meta 標記正確
        meta = json.loads(meta_path.read_text())
        assert meta["active_universe_source"] == "last_known_good_after_write_failure"
        assert meta["active_universe_schema_status"] == "lkg_write_failed"


# ============================================================
# Blocker D · git-dependent test 不假通過(元測試)
# ============================================================

class TestBlockerDGitTestNotFalsePositive:
    """驗證修正後的 TestUntouchedFilesHaveNoDiff 不會在 bogus 路徑假通過"""

    def test_repo_root_resolves_correctly(self):
        """對齊 P3-r2 Blocker D:用 Path(__file__).parents[2] · 不寫死絕對路徑

        P3-r3:forbidden literal 用字串拼接構造 · 避免測試檔自身命中掃描器
        """
        from pipeline.tests import test_adversarial
        src = Path(test_adversarial.__file__).read_text()
        # 用拼接避免此測試檔自己被 no-hardcoded-path 掃描器命中
        forbidden = "/tmp/" + "u1_impl"
        assert forbidden not in src, \
            f"test_adversarial 不得寫死 {forbidden}(P3-r3 已禁止 path-dependent test)"

    def test_diff_uses_check_true(self):
        """subprocess.run 必須 check=True 或明確 assert returncode == 0"""
        from pipeline.tests import test_adversarial
        src = Path(test_adversarial.__file__).read_text()
        # 至少要有一種:check=True · 或 assert returncode ==
        assert ("check=True" in src) or ("returncode == 0" in src) or ("returncode==0" in src), \
            "subprocess.run must use check=True or assert returncode"


# ============================================================
# Blocker E · TDR fixture 走真 universe.build_universe
# ============================================================

class TestBlockerETDRThroughRealBuildUniverse:
    def test_tdr_excluded_by_real_is_common_stock(self, tmp_path, monkeypatch):
        """
        FinMind universe_df 含 active + inactive + 4 檔 TDR
        · 呼叫 real universe.build_universe() · 其 is_common_stock 排除 91xx
        · 最終 active_universe_size / targets / scanner rows 都不含 TDR
        """
        scanner_path = tmp_path / "s.json"
        meta_path = tmp_path / "m.json"
        stocks_dir = tmp_path / "stocks"; stocks_dir.mkdir()
        lkg_path = tmp_path / "u.json"
        monkeypatch.setattr(config, "SCANNER_INDEX_PATH", scanner_path)
        monkeypatch.setattr(config, "META_PATH", meta_path)
        monkeypatch.setattr(config, "STOCKS_OUT_DIR", stocks_dir)
        monkeypatch.setattr(config, "ACTIVE_UNIVERSE_PATH", lkg_path)
        from pipeline import output as po
        monkeypatch.setattr(po.config, "SCANNER_INDEX_PATH", scanner_path)
        monkeypatch.setattr(po.config, "META_PATH", meta_path)
        monkeypatch.setattr(po.config, "STOCKS_OUT_DIR", stocks_dir)

        active_ids = ["1101", "2330", "3711", "5000"]
        inactive_ids = ["2325", "2311"]
        tdr_ids = ["9103", "9105", "9110", "9136"]
        # FinMind universe_df 【真的】含所有 IDs(active + inactive + TDR)
        # 讓 real universe.build_universe() 執行 is_common_stock filter
        all_ids = active_ids + inactive_ids + tdr_ids
        u_df = pd.DataFrame([{"stock_id": s, "industry_category": "半導體業",
                               "stock_name": f"S{s}", "type": "twse"} for s in all_ids])

        # LKG + official 含 active + TDR
        official = set(active_ids) | set(tdr_ids)
        au.write_lkg(_valid_lkg(twse_ids=sorted(x for x in official if int(x) < 5000),
                                  tpex_ids=sorted(x for x in official if int(x) >= 5000)),
                     lkg_path)
        # candidate 也含 TDR
        official_sorted_twse = sorted(x for x in official if int(x) < 5000)
        official_sorted_tpex = sorted(x for x in official if int(x) >= 5000)
        m = _mock_get_seq(
            _mock_resp(status=200, body=_twse_payload(official_sorted_twse)),
            _mock_resp(status=200, body=_tpex_payload(official_sorted_tpex)),
        )
        monkeypatch.setattr("pipeline.active_universe.requests", m)

        # stub pipeline · **但不 mock universe.build_universe** · 讓 real is_common_stock 執行
        from pipeline import ingest, transform
        fake_client = Mock(); fake_client.mock = True
        monkeypatch.setattr(pipeline_build, "load_client", lambda: fake_client)
        monkeypatch.setattr(ingest, "fetch_universe", lambda c, force=False: u_df)
        ingested = []
        monkeypatch.setattr(ingest, "ingest_all",
                            lambda c, targets, force=False: ingested.extend(targets))
        monkeypatch.setattr(transform, "build_detail", lambda c, sid, u: {"id": sid})
        monkeypatch.setattr(transform, "build_scanner_row", lambda d: {
            "id": d["id"], "name": f"S{d['id']}", "market": "twse", "industry": "semi",
            "latestQuarter": "2026/2Q", "opCapitalIneligible": None,
            "opToCapitalQuarter": 12.34, "opToCapitalQuarterPercentile": 45.0,
            "opToCapitalTTM": 40.5, "opToCapitalTTMPercentile": 55.0,
            "opCapitalDataStale": False, "_latest_month": "2026-08",
        })
        monkeypatch.setattr(po, "write_stock_detail", lambda d: None)

        rc = pipeline_build.run(mode="daily", stock_ids=None, force=False, batch=None)
        assert rc == 0

        # ingest 不含 TDR(是 real build_universe 排除)
        for tdr in tdr_ids:
            assert tdr not in ingested, f"TDR {tdr} 竟然被 ingest(is_common_stock 失效?)"
        # ingest 不含 inactive(是 official filter 排除)
        for inact in inactive_ids:
            assert inact not in ingested

        # meta 驗證
        meta = json.loads(meta_path.read_text())
        # finmind_info_row_count 是原始 universe_df 大小(含 TDR)
        assert meta["finmind_info_row_count"] == len(all_ids)   # 10
        # finmind_common_size 是 B(real build_universe 排除 TDR)
        # active_ids + inactive_ids = 6 · 沒 TDR
        assert meta["finmind_common_size"] == 6
        # active_universe_size = B ∩ Official = active_ids only = 4
        assert meta["active_universe_size"] == 4

        # scanner 不含 TDR
        scanner = json.loads(scanner_path.read_text())
        rows_ids = {r["id"] for r in scanner["stocks"]}
        for tdr in tdr_ids:
            assert tdr not in rows_ids


# ============================================================
# 其他 · duplicate warning 進 state.warnings(不能只 log 消失)
# ============================================================

class TestDuplicateWarningPropagates:
    def test_duplicate_ids_warning_in_state(self, tmp_path):
        """duplicate IDs 的 warning 必須進 ActiveUniverseState.warnings"""
        lkg_path = tmp_path / "u.json"
        au.write_lkg(_valid_lkg(twse_ids=["1101", "2330"], tpex_ids=["5000"]), lkg_path)
        # candidate TWSE payload 含 duplicate
        payload = _twse_payload(["1101", "2330", "2330", "1101"])   # 有 dup
        m = _mock_get_seq(
            _mock_resp(status=200, body=payload),
            _mock_resp(status=200, body=_tpex_payload(["5000"])),
        )
        state = au.load_active_universe(finmind_common_ids={"1101", "2330", "5000"},
                                         _requests_module=m, _lkg_path=lkg_path)
        # warnings 應含 duplicate 訊息
        assert any("duplicate" in w.lower() for w in state.warnings), \
            f"warnings 不含 duplicate: {state.warnings}"


# ============================================================
# 其他 · docstring 與 code 一致(元測試)
# ============================================================

class TestDocstringConsistency:
    def test_no_stale_bootstrap_docstring(self):
        """load_active_universe docstring 不得再寫『無 LKG 直接發布』"""
        import inspect
        doc = inspect.getdoc(au.load_active_universe) or ""
        assert "直接發布" not in doc, "docstring 過時 · 已改成 abort"
        assert "accept candidate 直接發布" not in doc
        # 反過來 · 應含 abort 或 no_lkg 或 disallow 等關鍵字
        assert any(k in doc for k in ["abort", "disallow", "no_lkg", "unattended"]), \
            "docstring 應說明 no-LKG abort 行為"


# ============================================================
# Initial LKG 契約不變(P3-r2 §其他 3)
# ============================================================

class TestInitialLKGContract:
    def test_initial_lkg_unchanged(self):
        """P3-r2 要求:initial LKG 必須維持
        P3-r3:改用 _REPO_ROOT · 不寫死絕對 sandbox 路徑
        """
        p = _REPO_ROOT / "data" / "active_universe.json"
        # 用 real config(不 monkeypatch min)
        original_min_t = config.ACTIVE_UNIVERSE_MIN_TWSE_COUNT
        original_min_p = config.ACTIVE_UNIVERSE_MIN_TPEX_COUNT
        config.ACTIVE_UNIVERSE_MIN_TWSE_COUNT = 800
        config.ACTIVE_UNIVERSE_MIN_TPEX_COUNT = 600
        try:
            loaded = au.load_lkg(p)
        finally:
            config.ACTIVE_UNIVERSE_MIN_TWSE_COUNT = original_min_t
            config.ACTIVE_UNIVERSE_MIN_TPEX_COUNT = original_min_p
        assert loaded is not None, "initial LKG 應通過 validator"
        assert loaded["source"] == "official_attachment_bootstrap"
        assert loaded["twse_count"] == 1089
        assert loaded["tpex_count"] == 890
        assert loaded["official_active_size"] == 1979
        assert loaded["ids_checksum"] == \
            "sha256:0d0ee2556b333eee429f2f1067065f3d0e29e666e6113e38e27feb256b4fbbf8"


# ============================================================
# P3-r3 · Regression:pipeline/tests/test_*.py 不得寫死絕對 sandbox 路徑
# ============================================================
# 背景:P3-r2 的 test_initial_lkg_unchanged 寫死了一個具體 sandbox 絕對路徑,
#      造成 Codex 在他自己的 sandbox 執行時 1 個 test failed(212 → 211 passed)
# 此 regression 掃描全部 test_*.py · 確保未來不再引入 path-dependent test
# 自身防命中:forbidden literal 用字串拼接構造 · 掃描器不會命中此檔本身

class TestNoHardcodedSandboxPath:
    """P3-r3 regression:pipeline/tests/test_*.py 內不得出現硬編碼 sandbox 路徑"""

    def test_no_test_hardcodes_sandbox_path(self):
        # forbidden 用拼接構造 · 避免此測試檔自身被掃描器命中
        forbidden = "/tmp/" + "u1_impl"

        tests_dir = _REPO_ROOT / "pipeline" / "tests"
        assert tests_dir.is_dir(), \
            f"tests_dir 不存在:{tests_dir}(_REPO_ROOT 解析可能錯誤)"

        hits = []
        for py in sorted(tests_dir.glob("test_*.py")):
            src = py.read_text(encoding="utf-8")
            if forbidden in src:
                hits.append(str(py.relative_to(_REPO_ROOT)))

        assert not hits, (
            f"發現寫死 {forbidden} 的 test 檔(P3-r3 已禁止 path-dependent test · "
            "改用 _REPO_ROOT = Path(__file__).resolve().parents[2]):\n"
            + "\n".join(f"  - {h}" for h in hits)
        )

    def test_repo_root_resolution_is_correct(self):
        """驗證 _REPO_ROOT 的定義:pipeline/ 與 data/ 都必須存在"""
        assert (_REPO_ROOT / "pipeline").is_dir(), \
            f"_REPO_ROOT/pipeline 不存在:{_REPO_ROOT}"
        assert (_REPO_ROOT / "data").is_dir(), \
            f"_REPO_ROOT/data 不存在:{_REPO_ROOT}"
        # initial LKG 檔案本身必須存在(對齊 TestInitialLKGContract)
        assert (_REPO_ROOT / "data" / "active_universe.json").is_file(), \
            f"initial LKG 不存在:{_REPO_ROOT}/data/active_universe.json"
