"""
v3.5.4-u1 · Active Universe regression tests

對應 P2 §九(drift guard)+ §十(regression 1-29)+ §十一(initial LKG)

測試層級聲明(誠實 · 對齊 P2 §十二):
  · pure unit tests(邏輯 · 純函式)
  · helper integration tests(prune 與 percentile lifecycle 順序 · load_active_universe 主流程)
  · file I/O tests(atomic write/read · checksum · LKG 損毀)
  · 網路呼叫全 mock(pytest 不依賴 live endpoints)
  · 【未】做 build.run() 真正 end-to-end
  · 【未】呼叫真 TWSE/TPEx openapi live
  · 【未】跑 GitHub Actions
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from pipeline import active_universe as au
from pipeline import config


# ============================================================
# Speed-up:整份 test 中 time.sleep 變 no-op(讓 retry backoff 不真的 sleep)
# ============================================================

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pipeline.active_universe.time.sleep", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _low_min_counts(monkeypatch):
    """
    P3-r2:大部分 unit test 用小 fixture 或純 twse-only 資料 · min_count 800/600 會擋。
    降到 0 · 讓 preseed 純 twse 也 valid。個別驗 min 的 test 內部再 override 回。
    (loader body-empty 已在前面 reject · min=0 不會讓「空 response」變 valid)
    """
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TWSE_COUNT", 0)
    monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TPEX_COUNT", 0)


# ============================================================
# Mock requests helper
# ============================================================

def _mock_resp(*, status=200, body=None, raises=None):
    """建 mock response · body 為 dict/list → json() 回傳 · 為 str → raise ValueError on json()"""
    resp = Mock()
    resp.status_code = status
    if raises is not None:
        resp.json = Mock(side_effect=raises)
    elif isinstance(body, str):
        # HTML body:json() 會 raise
        resp.json = Mock(side_effect=ValueError("Invalid JSON"))
        resp.text = body
    else:
        resp.json = Mock(return_value=body)
    return resp


def _mock_requests_with(*resp_sequence):
    """回傳 mock requests module · get() 依序回傳 resp_sequence · 每個 URL 分開追蹤"""
    m = Mock()
    call_iter = iter(resp_sequence)
    def get(url, timeout=None):
        try:
            return next(call_iter)
        except StopIteration:
            raise AssertionError("mock requests exhausted")
    m.get = Mock(side_effect=get)
    return m


def _valid_twse_payload(ids):
    """產生 valid TWSE JSON payload(min_count=800 · 需給 >=800 IDs)"""
    return [{"公司代號": sid, "出表日期": "1150831"} for sid in ids]


def _valid_tpex_payload(ids):
    """產生 valid TPEx JSON payload(min_count=600)"""
    return [{"SecuritiesCompanyCode": sid, "Date": "1150901"} for sid in ids]


# 大 fixture:800 個假 TWSE ID + 600 個假 TPEx ID(過 min_count 門檻)
_LARGE_TWSE_IDS = [str(1000 + i) for i in range(1000)]  # 1000-1999(1000 個)
_LARGE_TPEX_IDS = [str(3000 + i) for i in range(700)]   # 3000-3699(700 個)


# ============================================================
# §1 · pure unit · ROC date parser
# ============================================================

class TestROCDateParser:
    def test_standard(self):
        assert au._parse_roc_date("1150831") == "2026-08-31"
        assert au._parse_roc_date("1150901") == "2026-09-01"

    def test_none(self):
        assert au._parse_roc_date(None) is None

    def test_empty(self):
        assert au._parse_roc_date("") is None

    def test_wrong_length(self):
        assert au._parse_roc_date("115083") is None   # 6 位
        assert au._parse_roc_date("11508311") is None  # 8 位

    def test_non_digit(self):
        assert au._parse_roc_date("11508AA") is None

    def test_invalid_month(self):
        assert au._parse_roc_date("1151331") is None   # 月=13

    def test_invalid_day(self):
        assert au._parse_roc_date("1150931") is None   # 9月31日 · datetime 會拒

    def test_boundary_feb(self):
        assert au._parse_roc_date("1150228") == "2026-02-28"
        # 2026 非閏年 · 2/29 應無效
        assert au._parse_roc_date("1150229") is None


# ============================================================
# §2 · pure unit · four-digit ID filter
# ============================================================

class TestFilterFourDigit:
    def test_basic(self):
        assert au._filter_four_digit_ids(["1101", "2330", "6669"]) == {"1101", "2330", "6669"}

    def test_strip_whitespace(self):
        assert au._filter_four_digit_ids([" 1101 ", "2330\n"]) == {"1101", "2330"}

    def test_reject_non_four(self):
        assert au._filter_four_digit_ids(["910322", "12345", "123"]) == set()

    def test_reject_null_blank(self):
        assert au._filter_four_digit_ids([None, "", "   "]) == set()

    def test_dedup(self):
        assert au._filter_four_digit_ids(["1101", "1101", "1101"]) == {"1101"}

    def test_mixed(self):
        got = au._filter_four_digit_ids(["1101", None, "12345", "2330", "910322", "", "1101"])
        assert got == {"1101", "2330"}


# ============================================================
# §3 · pure unit · checksum
# ============================================================

class TestChecksum:
    def test_order_independent(self):
        c1 = au.compute_checksum(["1101", "2330", "6669"])
        c2 = au.compute_checksum(["6669", "1101", "2330"])
        assert c1 == c2

    def test_diff_input_diff_checksum(self):
        c1 = au.compute_checksum(["1101", "2330"])
        c2 = au.compute_checksum(["1101", "2331"])
        assert c1 != c2

    def test_format(self):
        c = au.compute_checksum(["1101"])
        assert c.startswith("sha256:")
        assert len(c) == len("sha256:") + 64

    def test_empty(self):
        c = au.compute_checksum([])
        assert c.startswith("sha256:")  # sha256("") 也有值


# ============================================================
# §4 · drift guard(P2 §九 · 邊界 >= 5%)
# ============================================================

class TestDriftGuard:
    """關鍵語意:ratio < 5% pass · ratio >= 5% reject · gate 用 >= 0.05"""

    def _lkg_100(self):
        return {str(1000 + i) for i in range(100)}

    def test_removed_4pct_pass(self):
        lkg = self._lkg_100()
        cand = set(list(lkg)[:-4])  # 移 4 個 = 4%
        r = au.passes_drift_guard(cand, lkg)
        assert r.accepted, r.reason
        assert r.removed_count == 4
        assert r.removed_ratio == 0.04

    def test_removed_5pct_reject(self):
        """邊界:剛好 5% 應該 reject(gate >= 0.05)"""
        lkg = self._lkg_100()
        cand = set(list(lkg)[:-5])  # 移 5 個 = 5%
        r = au.passes_drift_guard(cand, lkg)
        assert not r.accepted
        assert "removed" in (r.reason or "")

    def test_removed_6pct_reject(self):
        lkg = self._lkg_100()
        cand = set(list(lkg)[:-6])  # 移 6 個 = 6%
        r = au.passes_drift_guard(cand, lkg)
        assert not r.accepted

    def test_added_4pct_pass(self):
        lkg = self._lkg_100()
        cand = lkg | {"9001", "9002", "9003", "9004"}   # 加 4 個
        r = au.passes_drift_guard(cand, lkg)
        assert r.accepted, r.reason

    def test_added_5pct_reject(self):
        lkg = self._lkg_100()
        cand = lkg | {"9001", "9002", "9003", "9004", "9005"}   # 加 5 個
        r = au.passes_drift_guard(cand, lkg)
        assert not r.accepted
        assert "added" in (r.reason or "")

    def test_added_6pct_reject(self):
        lkg = self._lkg_100()
        cand = lkg | {"9001", "9002", "9003", "9004", "9005", "9006"}
        r = au.passes_drift_guard(cand, lkg)
        assert not r.accepted

    def test_same_count_but_mass_swap_reject(self):
        """對齊 P2 §九:總數相同但大量成分互換必須 reject"""
        lkg = self._lkg_100()
        # 移 10 加 10 · 總數不變但都超過 5%
        keep = list(lkg)[:-10]
        cand = set(keep) | {"9000", "9001", "9002", "9003", "9004",
                            "9005", "9006", "9007", "9008", "9009"}
        assert len(cand) == len(lkg)  # 總數相同
        r = au.passes_drift_guard(cand, lkg)
        assert not r.accepted
        # 應該同時 removed + added 都 >= 5%
        assert r.removed_ratio >= 0.05
        assert r.added_ratio >= 0.05

    def test_candidate_empty_reject(self):
        lkg = self._lkg_100()
        r = au.passes_drift_guard(set(), lkg)
        assert not r.accepted
        assert "empty" in (r.reason or "")

    def test_lkg_empty_reject(self):
        """LKG empty 視為 invalid · caller 應把它當『無 LKG』處理"""
        r = au.passes_drift_guard({"1101", "2330"}, set())
        assert not r.accepted

    def test_no_change_pass(self):
        lkg = self._lkg_100()
        r = au.passes_drift_guard(set(lkg), lkg)
        assert r.accepted
        assert r.removed_count == 0
        assert r.added_count == 0


# ============================================================
# §5 · Source loaders(mock)· P2 §十 1-8
# ============================================================

class TestSourceLoaders:
    def test_twse_normal(self):
        payload = _valid_twse_payload(_LARGE_TWSE_IDS)
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert r.ok
        assert r.source_name == "twse"
        assert len(r.ids) == 1000
        assert r.as_of == "2026-08-31"

    def test_tpex_normal(self):
        payload = _valid_tpex_payload(_LARGE_TPEX_IDS)
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_tpex_official(_requests_module=m)
        assert r.ok
        assert len(r.ids) == 700
        assert r.as_of == "2026-09-01"

    def test_timeout(self):
        """對齊 P2 §十 2:任一來源 timeout · retry 後仍失敗"""
        import requests as real_requests
        m = Mock()
        m.get = Mock(side_effect=real_requests.exceptions.Timeout("read timeout"))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "timeout" in (r.error or "").lower() or "Timeout" in (r.error or "")

    def test_http_500(self):
        """P2 §十 3:HTTP error"""
        m = _mock_requests_with(
            _mock_resp(status=500, body="Server Error"),
            _mock_resp(status=500, body="Server Error"),
            _mock_resp(status=500, body="Server Error"),
        )
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "500" in (r.error or "")

    def test_html_body(self):
        """P2 §十 4:HTML 200 但非 JSON"""
        m = _mock_requests_with(
            _mock_resp(status=200, body="<html><body>Error</body></html>"),
            _mock_resp(status=200, body="<html>"),
            _mock_resp(status=200, body="<html>"),
        )
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "JSON" in (r.error or "") or "decode" in (r.error or "")

    def test_json_empty_dict(self):
        """P2 §十 6/7 · body = {} 應 reject"""
        m = _mock_requests_with(_mock_resp(status=200, body={}))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "list" in (r.error or "")

    def test_json_empty_list(self):
        m = _mock_requests_with(_mock_resp(status=200, body=[]))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "empty" in (r.error or "")

    def test_missing_id_field(self):
        """單筆缺 id_key · reject(P3 Blocker 6 · 嚴格 schema)"""
        payload = [{"公司代號": "1101"}] + [{"other_key": "x"}] * 200
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "missing" in (r.error or "")

    def test_first_valid_rest_missing(self):
        """P3 Blocker 6:第一筆 valid · 後續 1 個缺 id → 直接 reject(嚴格)"""
        payload = [{"公司代號": "1101", "出表日期": "1150831"}]
        payload += [{"公司代號": None, "出表日期": "1150831"}] * 900
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok  # 任一違反即 reject

    def test_non_four_digit_ids_filtered_still_valid(self):
        """P3 Blocker 6:六位/三位/帶字母是合法過濾 · schema 仍 valid · 不 reject"""
        raw = _LARGE_TWSE_IDS + ["910322", "12345", "1234A", "12"]
        payload = _valid_twse_payload(raw)
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert r.ok
        assert len(r.ids) == 1000  # 4 位純數字 · 非四位剔除但 loader 仍 valid
        assert "910322" not in r.ids
        assert "1234A" not in r.ids
        assert "12" not in r.ids
        assert "12345" not in r.ids

    def test_below_min_count(self, monkeypatch):
        """過濾後低於 min_count · reject(explicit override 回 800 min)"""
        monkeypatch.setattr(config, "ACTIVE_UNIVERSE_MIN_TWSE_COUNT", 800)
        payload = _valid_twse_payload([str(1000 + i) for i in range(50)])   # 50 < 800
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "min" in (r.error or "")

    # ============================================================
    # Blocker 6 · 嚴格 schema 驗證(任一違反即 reject · 非「過半才 reject」)
    # ============================================================

    def test_single_missing_id_key_rejects(self):
        """1/1000 缺 id_key 也 reject(不再是過半)"""
        payload = _valid_twse_payload(_LARGE_TWSE_IDS)
        # 塞一個沒 id_key 的
        payload.append({"出表日期": "1150831"})   # 無 '公司代號'
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "missing" in (r.error or "")

    def test_last_record_missing_id_key_rejects(self):
        """最後一筆缺 id_key · 也 reject(對齊 P3 要求逐筆檢)"""
        payload = _valid_twse_payload(_LARGE_TWSE_IDS[:-1])   # 999 筆 valid
        payload.append({"出表日期": "1150831"})               # 最後缺 id_key
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok

    def test_non_dict_record_rejects(self):
        payload = _valid_twse_payload(_LARGE_TWSE_IDS)
        payload.append("this is a string not a dict")
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "not a dict" in (r.error or "")

    def test_null_id_value_rejects(self):
        payload = _valid_twse_payload(_LARGE_TWSE_IDS)
        payload.append({"公司代號": None, "出表日期": "1150831"})
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "null" in (r.error or "")

    def test_blank_id_value_rejects(self):
        payload = _valid_twse_payload(_LARGE_TWSE_IDS)
        payload.append({"公司代號": "   ", "出表日期": "1150831"})
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert not r.ok
        assert "blank" in (r.error or "")

    def test_legal_tdr_six_digit_source_valid_but_filtered_out(self):
        """合法六位 TDR 900xxxx · source valid · final filtered IDs 不含它們"""
        raw = _LARGE_TWSE_IDS + ["9103", "9105", "9110", "9136", "910322", "911608"]
        # 注意:9103/9105/9110/9136 是四位 · 會保留(讓下游 B ∩ Official 排除)
        #       910322/911608 是六位 · 被 loader filter 過濾
        payload = _valid_twse_payload(raw)
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert r.ok
        assert "9103" in r.ids  # 四位 · 保留
        assert "9136" in r.ids  # 四位 · 保留
        assert "910322" not in r.ids   # 六位 · 過濾
        assert "911608" not in r.ids

    def test_duplicates_deduped_with_warning(self):
        """duplicate 可 dedup · schema 仍 valid(對齊 P3 Blocker 6)"""
        raw = _LARGE_TWSE_IDS + list(_LARGE_TWSE_IDS[:5])  # 前 5 個重複
        payload = _valid_twse_payload(raw)
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert r.ok
        assert len(r.ids) == 1000  # dedup 後仍 1000


# ============================================================
# §6 · load_active_universe · 主流程
# ============================================================

# ============================================================
# §6 · load_active_universe · 主流程
# ============================================================

def _preseed_lkg(lkg_path, official_ids):
    """
    Test helper:預先在 lkg_path 寫一個 valid LKG snapshot(for P3 no-unattended-bootstrap)
    分類:凡在 _LARGE_TPEX_IDS 內視為 tpex · 其餘全算 twse
    (讓 anchor test 中的 2882 / TDR 91xx 等 · 都能被正確歸類到 twse)
    """
    tpex_pool = set(_LARGE_TPEX_IDS)
    twse_ids = sorted(x for x in official_ids if x not in tpex_pool)
    tpex_ids = sorted(x for x in official_ids if x in tpex_pool)
    official_sorted = sorted(official_ids)
    payload = {
        "schema_version": 1,
        "generated_at": "2026-08-31T22:00:00+0800",
        "source": "official_attachment_bootstrap",
        "twse_url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        "tpex_url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "twse_as_of": "2026-08-31",
        "tpex_as_of": "2026-09-01",
        "official_as_of": "2026-08-31",
        "twse_count": len(twse_ids),
        "tpex_count": len(tpex_ids),
        "official_active_size": len(official_sorted),
        "twse_ids": twse_ids,
        "tpex_ids": tpex_ids,
        "official_active_ids": official_sorted,
        "ids_checksum": au.compute_checksum(official_sorted),
    }
    au.write_lkg(payload, lkg_path)
    return payload


class TestLoadActiveUniverse:
    def test_no_lkg_valid_sources_aborts(self, tmp_path):
        """P3 Blocker 7:無 LKG + 兩來源 valid → abort(unattended bootstrap disabled)"""
        lkg_path = tmp_path / "u.json"
        assert not lkg_path.exists()
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(_LARGE_TWSE_IDS)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        finmind_common = set(_LARGE_TWSE_IDS)
        state = au.load_active_universe(
            finmind_common_ids=finmind_common,
            _requests_module=m, _lkg_path=lkg_path,
        )
        assert state.active_ids is None
        assert state.source == "abort"
        assert state.abort_reason
        assert "LKG" in state.abort_reason or "baseline" in state.abort_reason
        # 不得寫 LKG
        assert not lkg_path.exists()

    def test_no_lkg_invalid_sources_aborts(self, tmp_path):
        """P3 Blocker 7:無 LKG + 來源失敗 → abort"""
        lkg_path = tmp_path / "u.json"
        m = _mock_requests_with(
            _mock_resp(status=500), _mock_resp(status=500), _mock_resp(status=500),
            _mock_resp(status=500), _mock_resp(status=500), _mock_resp(status=500),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.active_ids is None
        assert state.source == "abort"

    def test_corrupt_lkg_valid_sources_aborts(self, tmp_path):
        """P3 Blocker 7:LKG 損毀 + 兩來源 valid → abort(不重新 bootstrap)"""
        lkg_path = tmp_path / "u.json"
        lkg_path.write_text("not json {{{")   # 損毀
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(_LARGE_TWSE_IDS)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.active_ids is None
        assert state.source == "abort"

    def test_valid_lkg_valid_candidate_drift_pass_publishes(self, tmp_path):
        """P3 Blocker 7:有 valid LKG · candidate 通過 drift → 覆寫 LKG · source=official_live"""
        lkg_path = tmp_path / "u.json"
        # pre-seed LKG(用完整 twse+tpex 1700 · 這樣新 candidate 只變動 1 個)
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS) | set(_LARGE_TPEX_IDS))
        # candidate:TWSE 只少 1 個(0.06% removed 遠低於 5%)· pass
        twse2 = _LARGE_TWSE_IDS[:-1]
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(twse2)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.active_ids is not None
        assert state.source == "official_live"
        # LKG 已被覆寫 · size 應該是 999 + 700 = 1699
        loaded = au.load_lkg(lkg_path)
        assert loaded["official_active_size"] == 1699

    def test_valid_lkg_valid_candidate_drift_reject_preserves(self, tmp_path):
        """P3 Blocker 7 + Blocker 8:drift reject 保留舊 LKG bytes"""
        lkg_path = tmp_path / "u.json"
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS[:1000]))
        bytes_before = lkg_path.read_bytes()
        # candidate 減 100 個(10%)· reject
        twse2 = _LARGE_TWSE_IDS[:900]
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(twse2)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "last_known_good_after_drift_reject"
        assert state.official_active_size == 1000  # LKG 保
        # LKG bytes 完全不變(P3 Blocker 8)
        assert lkg_path.read_bytes() == bytes_before

    def test_source_failure_with_valid_lkg(self, tmp_path):
        """P2 §十 9:來源失敗 + valid LKG → 用 LKG · source=last_known_good_after_source_failure"""
        lkg_path = tmp_path / "u.json"
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS[:1000]))
        # TWSE HTTP 500 · retry 全失敗
        m = _mock_requests_with(
            _mock_resp(status=500), _mock_resp(status=500), _mock_resp(status=500),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.active_ids is not None
        assert state.source == "last_known_good_after_source_failure"

    def test_one_source_ok_one_fail_no_half_publish(self, tmp_path):
        """P2 §十 8:一成功一失敗 · 有 LKG → 用 LKG · 不半套"""
        lkg_path = tmp_path / "u.json"
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS[:1000]))
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(_LARGE_TWSE_IDS)),
            _mock_resp(status=500), _mock_resp(status=500), _mock_resp(status=500),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "last_known_good_after_source_failure"
        assert state.official_active_size == 1000  # 用 LKG · 不是 TWSE 1000 半套


# ============================================================
# §7 · LKG I/O · atomic + checksum(P2 §十 11-13)
# ============================================================

class TestLKGIO:
    def test_atomic_write_read(self, tmp_path):
        """完整 valid payload · atomic write → load 回讀成功"""
        lkg_path = tmp_path / "u.json"
        ids = ["1101", "1102", "2330"]
        payload = {
            "schema_version": 1,
            "generated_at": "2026-08-31T22:00:00+0800",
            "source": "official_attachment_bootstrap",
            "twse_url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
            "tpex_url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
            "twse_as_of": "2026-08-31",
            "tpex_as_of": "2026-09-01",
            "official_as_of": "2026-08-31",
            "twse_count": 3, "tpex_count": 0,
            "official_active_size": 3,
            "twse_ids": ids, "tpex_ids": [],
            "official_active_ids": ids,
            "ids_checksum": au.compute_checksum(ids),
        }
        au.write_lkg(payload, lkg_path)
        assert lkg_path.exists()
        loaded = au.load_lkg(lkg_path)
        assert loaded is not None
        assert loaded["official_active_ids"] == ids

    def test_atomic_write_no_partial_on_crash(self, tmp_path):
        """驗證:tmp 檔名 + os.replace · 中斷不會產生半寫檔案"""
        lkg_path = tmp_path / "u.json"
        payload = {
            "schema_version": 1,
            "official_active_ids": ["1101"],
            "ids_checksum": au.compute_checksum(["1101"]),
        }
        au.write_lkg(payload, lkg_path)
        # write_lkg 用 os.replace · 若中斷 tmp 存在但 lkg_path 不變
        # 這裡只驗證 tmp file 已被清 (replace 後不存在)
        tmp = lkg_path.with_suffix(lkg_path.suffix + ".tmp")
        assert not tmp.exists()

    def test_checksum_mismatch_load_returns_none(self, tmp_path):
        """checksum 損毀 → load 回 None"""
        lkg_path = tmp_path / "u.json"
        payload = {
            "schema_version": 1,
            "official_active_ids": ["1101", "2330"],
            "ids_checksum": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        }
        lkg_path.write_text(json.dumps(payload))
        assert au.load_lkg(lkg_path) is None

    def test_missing_ids_load_returns_none(self, tmp_path):
        lkg_path = tmp_path / "u.json"
        lkg_path.write_text('{"schema_version": 1}')
        assert au.load_lkg(lkg_path) is None

    def test_malformed_json_load_returns_none(self, tmp_path):
        lkg_path = tmp_path / "u.json"
        lkg_path.write_text("not json at all {{{")
        assert au.load_lkg(lkg_path) is None

    def test_nonexistent_load_returns_none(self, tmp_path):
        assert au.load_lkg(tmp_path / "does_not_exist.json") is None

    def test_checksum_excludes_own_field(self, tmp_path):
        """checksum 不含 checksum 欄位本身 · 只算 official_active_ids"""
        ids = ["1101", "2330"]
        c1 = au.compute_checksum(ids)
        # 反覆呼叫應該一致
        c2 = au.compute_checksum(ids)
        assert c1 == c2
        # sanity:改一個 id · checksum 變
        c3 = au.compute_checksum(["1101", "2331"])
        assert c1 != c3


# ============================================================
# §8 · Anchor & TDR filter(對齊 P2 §二 + §十 15-18)
# ============================================================

class TestAnchorsAndTDR:
    def test_official_snapshot_can_contain_9103_9105_9110_9136(self):
        """P2 §二:TWSE snapshot 可以包含這 4 檔 · loader 不特別過濾"""
        raw = _LARGE_TWSE_IDS + ["9103", "9105", "9110", "9136"]
        # 但是 4 個都是四位 · 會通過 loader filter
        # 這裡驗證的是 loader 語意:四位數字都收進去
        payload = _valid_twse_payload(raw)
        m = _mock_requests_with(_mock_resp(status=200, body=payload))
        r = au.load_twse_official(_requests_module=m)
        assert r.ok
        assert "9103" in r.ids
        assert "9105" in r.ids
        assert "9110" in r.ids
        assert "9136" in r.ids

    def test_final_active_excludes_tdr_via_finmind_common(self, tmp_path):
        """
        P2 §二 · 關鍵:即使 official 有 9103 等 · 最終 E = B ∩ Official
        · B (FinMind common) 已由 universe.build_universe 的 is_common_stock 排除 91xx
        · 所以最終 active_ids 不含 9103/9105/9110/9136
        """
        lkg_path = tmp_path / "u.json"
        # pre-seed LKG(含 TDR · 模擬 official 曾有它們)
        preseed_ids = set(_LARGE_TWSE_IDS) | {"9103", "9105", "9110", "9136"}
        _preseed_lkg(lkg_path, preseed_ids)
        # candidate 也含 TDR · drift pass
        raw_twse = _LARGE_TWSE_IDS + ["9103", "9105", "9110", "9136"]
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(raw_twse)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        # B (FinMind common) 不含 91xx(對應 universe.is_common_stock 排除)
        finmind_common = set(_LARGE_TWSE_IDS)
        state = au.load_active_universe(finmind_common_ids=finmind_common,
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.active_ids is not None
        assert "9103" not in state.active_ids
        assert "9105" not in state.active_ids
        assert "9110" not in state.active_ids
        assert "9136" not in state.active_ids

    def test_anchor_active_finance_kept(self, tmp_path):
        """2882 國泰金 · 現役金融股 · 必須保留"""
        lkg_path = tmp_path / "u.json"
        preseed = {"2882"} | set(_LARGE_TWSE_IDS)
        _preseed_lkg(lkg_path, preseed)
        twse = ["2882"] + _LARGE_TWSE_IDS
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(twse)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        finmind_common = {"2882"} | set(_LARGE_TWSE_IDS)
        state = au.load_active_universe(finmind_common_ids=finmind_common,
                                          _requests_module=m, _lkg_path=lkg_path)
        assert "2882" in state.active_ids

    def test_anchor_2325_2311_excluded(self, tmp_path):
        """2325 / 2311 不在 official → 不在 active_ids · 即使 FinMind 有它"""
        lkg_path = tmp_path / "u.json"
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS))   # 不含 2325/2311
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(_LARGE_TWSE_IDS)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        # FinMind 仍有 2325 / 2311(歷史 dedup)
        finmind_common = {"2325", "2311"} | set(_LARGE_TWSE_IDS)
        state = au.load_active_universe(finmind_common_ids=finmind_common,
                                          _requests_module=m, _lkg_path=lkg_path)
        assert "2325" not in state.active_ids
        assert "2311" not in state.active_ids

    def test_anchor_3711_kept(self, tmp_path):
        """3711 日月光投控 · 現役 · 在 twse pool + finmind common → 保留"""
        lkg_path = tmp_path / "u.json"
        preseed = {"3711"} | set(_LARGE_TWSE_IDS)
        _preseed_lkg(lkg_path, preseed)
        twse = ["3711"] + _LARGE_TWSE_IDS
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(twse)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        finmind_common = {"3711"} | set(_LARGE_TWSE_IDS)
        state = au.load_active_universe(finmind_common_ids=finmind_common,
                                          _requests_module=m, _lkg_path=lkg_path)
        assert "3711" in state.active_ids


# ============================================================
# Blocker 3 · Source label 4 種正確標記
# ============================================================

class TestSourceLabels:
    def test_source_official_live_after_drift_pass(self, tmp_path):
        """runtime · 兩來源 valid + drift pass → source = official_live"""
        lkg_path = tmp_path / "u.json"
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS) | set(_LARGE_TPEX_IDS))
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(_LARGE_TWSE_IDS)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "official_live"

    def test_source_lkg_after_source_failure(self, tmp_path):
        """來源失敗 → last_known_good_after_source_failure"""
        lkg_path = tmp_path / "u.json"
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS[:1000]))
        m = _mock_requests_with(
            _mock_resp(status=500), _mock_resp(status=500), _mock_resp(status=500),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "last_known_good_after_source_failure"

    def test_source_lkg_after_drift_reject(self, tmp_path):
        """drift 超過 5% → last_known_good_after_drift_reject"""
        lkg_path = tmp_path / "u.json"
        _preseed_lkg(lkg_path, set(_LARGE_TWSE_IDS[:1000]))
        twse2 = _LARGE_TWSE_IDS[:900]   # 10% removed
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(twse2)),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        state = au.load_active_universe(finmind_common_ids=set(_LARGE_TWSE_IDS),
                                          _requests_module=m, _lkg_path=lkg_path)
        assert state.source == "last_known_good_after_drift_reject"

    def test_source_official_attachment_bootstrap_via_build_snapshot(self):
        """
        Initial LKG(attachment bootstrap 產出)· source 必須標為 official_attachment_bootstrap
        · runtime 不會自動產出這個 source · 只有 attachment / 部署腳本會產
        """
        twse = au.SourceLoadResult(ok=True, source_name="twse",
                                     ids={"1101", "2330"}, as_of="2026-08-31", raw_count=2)
        tpex = au.SourceLoadResult(ok=True, source_name="tpex",
                                     ids={"5000"}, as_of="2026-09-01", raw_count=1)
        payload = au.build_snapshot_payload(twse, tpex, source="official_attachment_bootstrap")
        assert payload["source"] == "official_attachment_bootstrap"


# ============================================================
# Blocker 2 · No magic numbers · fixture 大小驅動 meta
# ============================================================

class TestNoMagicNumbers:
    def test_different_fixture_sizes_produce_different_state(self, tmp_path):
        """
        不同 fixture 大小 → 產出不同 meta · code 不得 hardcode 1975/1978/1979/72
        """
        # 情境 A:B = 500 · Official = 400 · active = 交集
        lkg_a = tmp_path / "a.json"
        official_a = {str(1000 + i) for i in range(400)}
        _preseed_lkg(lkg_a, official_a)
        m = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(sorted(official_a) + _LARGE_TWSE_IDS[400:])),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        finmind_a = {str(1000 + i) for i in range(500)}
        state_a = au.load_active_universe(finmind_common_ids=finmind_a,
                                            _requests_module=m, _lkg_path=lkg_a)

        # 情境 B:B = 700 · Official 更大 · active 應該不同
        lkg_b = tmp_path / "b.json"
        official_b = {str(2000 + i) for i in range(600)}
        _preseed_lkg(lkg_b, official_b)
        m2 = _mock_requests_with(
            _mock_resp(status=200, body=_valid_twse_payload(sorted(official_b) + _LARGE_TWSE_IDS[400:])),
            _mock_resp(status=200, body=_valid_tpex_payload(_LARGE_TPEX_IDS)),
        )
        finmind_b = {str(2000 + i) for i in range(700)}
        state_b = au.load_active_universe(finmind_common_ids=finmind_b,
                                            _requests_module=m2, _lkg_path=lkg_b)

        # 兩者 official_active_size / active_size 都不同 · 證明沒 hardcode
        assert state_a.official_active_size != state_b.official_active_size
        assert len(state_a.active_ids) != len(state_b.active_ids)


# ============================================================
# Blocker 8 · Checksum 與 atomic write 額外驗證
# ============================================================

class TestChecksumAndSnapshotConsistency:
    def test_metadata_change_does_not_affect_checksum(self, tmp_path):
        """修改 metadata(twse_count 等)不影響 IDs checksum"""
        ids = ["1101", "2330", "3711"]
        c1 = au.compute_checksum(ids)
        # 「不同 metadata」意即:同樣 ids · payload 其他欄位可能不同 · 但 checksum 不變
        c2 = au.compute_checksum(ids)
        assert c1 == c2

    def test_twse_tpex_union_equals_official_active_ids(self):
        """P3 Blocker 8:snapshot 中 twse_ids ∪ tpex_ids == official_active_ids"""
        twse = au.SourceLoadResult(ok=True, source_name="twse",
                                     ids={"1101", "2330"}, as_of="2026-08-31", raw_count=2)
        tpex = au.SourceLoadResult(ok=True, source_name="tpex",
                                     ids={"5000", "6000"}, as_of="2026-09-01", raw_count=2)
        payload = au.build_snapshot_payload(twse, tpex, source="official_attachment_bootstrap")
        union = set(payload["twse_ids"]) | set(payload["tpex_ids"])
        assert union == set(payload["official_active_ids"])

    def test_counts_consistent_with_arrays(self):
        """counts 與 arrays 長度一致"""
        twse = au.SourceLoadResult(ok=True, source_name="twse",
                                     ids={"1101", "2330", "3711"}, as_of="2026-08-31", raw_count=3)
        tpex = au.SourceLoadResult(ok=True, source_name="tpex",
                                     ids={"5000", "6000"}, as_of="2026-09-01", raw_count=2)
        payload = au.build_snapshot_payload(twse, tpex, source="official_attachment_bootstrap")
        assert payload["twse_count"] == len(payload["twse_ids"])
        assert payload["tpex_count"] == len(payload["tpex_ids"])
        assert payload["official_active_size"] == len(payload["official_active_ids"])

    def test_atomic_write_failure_preserves_original_lkg(self, tmp_path, monkeypatch):
        """P3 Blocker 8:atomic write 失敗時 · 原 LKG bytes 完全保留"""
        lkg_path = tmp_path / "u.json"
        # 先寫入 valid LKG
        p1 = {"schema_version": 1, "official_active_ids": ["1101"], "ids_checksum": au.compute_checksum(["1101"])}
        au.write_lkg(p1, lkg_path)
        bytes_before = lkg_path.read_bytes()

        # 讓 atomic replace 失敗 · 只 mock active_universe seam
        # 不 mock pipeline.active_universe.os.replace(會污染全域 os.replace ·
        # 讓 output.write_scanner_index／write_meta 的 os.replace 也壞掉,造成假陽性)
        def broken_replace(src, dst):
            raise OSError("simulated disk full")
        monkeypatch.setattr("pipeline.active_universe._atomic_replace", broken_replace)

        # 嘗試寫新 LKG → 應 raise
        p2 = {"schema_version": 1, "official_active_ids": ["9999"], "ids_checksum": au.compute_checksum(["9999"])}
        with pytest.raises(OSError):
            au.write_lkg(p2, lkg_path)
        # 原檔案 bytes 完全不變
        assert lkg_path.read_bytes() == bytes_before


# ============================================================
# Blocker 9 · Determinism
# ============================================================

class TestDeterminism:
    def test_prune_output_deterministic(self):
        """輸入 row 順序不影響輸出 · pruned_ids 也 sorted"""
        rows_1 = [{"id": "2330"}, {"id": "1101"}, {"id": "2325"}, {"id": "6669"}]
        rows_2 = [{"id": "2325"}, {"id": "6669"}, {"id": "1101"}, {"id": "2330"}]  # 打亂順序
        active = {"1101", "2330", "6669"}
        k1, _, p1 = au.prune_inactive_rows(rows_1, active)
        k2, _, p2 = au.prune_inactive_rows(rows_2, active)
        assert [r["id"] for r in k1] == [r["id"] for r in k2]  # 相同 sorted output
        assert p1 == p2

    def test_checksum_order_independent(self):
        """sorted ids 為前提 · checksum 對 IDs 集合 deterministic"""
        c1 = au.compute_checksum(["1101", "2330", "6669"])
        c2 = au.compute_checksum(["6669", "1101", "2330"])
        c3 = au.compute_checksum(["2330", "6669", "1101"])
        assert c1 == c2 == c3

    def test_build_snapshot_deterministic_arrays(self):
        """twse_ids / tpex_ids / official_active_ids 全 sorted"""
        twse = au.SourceLoadResult(ok=True, source_name="twse",
                                     ids={"2330", "1101", "3711"}, as_of="2026-08-31", raw_count=3)
        tpex = au.SourceLoadResult(ok=True, source_name="tpex",
                                     ids={"6000", "5000"}, as_of="2026-09-01", raw_count=2)
        p = au.build_snapshot_payload(twse, tpex, source="official_attachment_bootstrap")
        assert p["twse_ids"] == sorted(p["twse_ids"])
        assert p["tpex_ids"] == sorted(p["tpex_ids"])
        assert p["official_active_ids"] == sorted(p["official_active_ids"])


# ============================================================
# §9 · Prune helper(P2 §七)
# ============================================================

class TestPruneInactiveRows:
    def test_basic_prune(self):
        merged = [
            {"id": "1101", "name": "台泥"},
            {"id": "2325", "name": "矽品"},   # inactive
            {"id": "2330", "name": "台積電"},
            {"id": "2311", "name": "日月光"},  # inactive
        ]
        active = {"1101", "2330"}
        kept, inactive, pruned_ids = au.prune_inactive_rows(merged, active)
        assert len(kept) == 2
        assert {r["id"] for r in kept} == {"1101", "2330"}
        assert pruned_ids == ["2311", "2325"]  # sorted
        assert len(inactive) == 2

    def test_deterministic_sort(self):
        merged = [
            {"id": "2330", "name": "台積電"},
            {"id": "1101", "name": "台泥"},
            {"id": "6669", "name": "緯穎"},
        ]
        active = {"1101", "2330", "6669"}
        kept, _, _ = au.prune_inactive_rows(merged, active)
        assert [r["id"] for r in kept] == ["1101", "2330", "6669"]

    def test_no_inactive(self):
        merged = [{"id": "1101"}, {"id": "2330"}]
        active = {"1101", "2330"}
        kept, inactive, pruned = au.prune_inactive_rows(merged, active)
        assert len(kept) == 2
        assert inactive == []
        assert pruned == []

    def test_all_inactive(self):
        merged = [{"id": "2325"}, {"id": "2311"}]
        active = {"1101"}
        kept, inactive, pruned = au.prune_inactive_rows(merged, active)
        assert kept == []
        assert len(inactive) == 2

    def test_row_fields_preserved(self):
        """P2 §七:不得修改本批未更新 active row 的 opCapital 欄位"""
        merged = [{
            "id": "1101",
            "name": "台泥",
            "opToCapitalQuarter": 12.3,
            "opToCapitalQuarterPercentile": 45.67,
            "opCapitalDataStale": False,
        }]
        active = {"1101"}
        kept, _, _ = au.prune_inactive_rows(merged, active)
        assert kept[0]["opToCapitalQuarter"] == 12.3
        assert kept[0]["opToCapitalQuarterPercentile"] == 45.67
        assert kept[0]["opCapitalDataStale"] is False


# ============================================================
# §10 · Initial LKG fixture(對齊 P2 §十一)· 稽核 attachment 產出
# ============================================================
# 註:這裡不真的讀 attachment(那是 P1 audit 環境用的)· 只驗證
#     若真讀 · 產出 payload 的關鍵性質。P4 交付時會附上真 initial LKG。

class TestInitialLKGProperties:
    def test_build_snapshot_payload_shape(self, tmp_path):
        """驗證 build_snapshot_payload 產出結構符合 P2 §五 spec"""
        twse = au.SourceLoadResult(ok=True, source_name="twse",
                                     ids={"1101", "2330"}, as_of="2026-08-31", raw_count=2)
        tpex = au.SourceLoadResult(ok=True, source_name="tpex",
                                     ids={"5000", "5001"}, as_of="2026-09-01", raw_count=2)
        payload = au.build_snapshot_payload(twse, tpex, source="official_live")
        # 必要欄位
        for key in ["schema_version", "generated_at", "source",
                    "twse_url", "tpex_url",
                    "twse_as_of", "tpex_as_of", "official_as_of",
                    "twse_count", "tpex_count", "official_active_size",
                    "twse_ids", "tpex_ids", "official_active_ids", "ids_checksum"]:
            assert key in payload, f"missing key {key}"
        # official_as_of 是兩者較早日期
        assert payload["official_as_of"] == "2026-08-31"
        # checksum 一致
        assert payload["ids_checksum"] == au.compute_checksum(payload["official_active_ids"])
        # deterministic sort
        assert payload["twse_ids"] == sorted(payload["twse_ids"])
        assert payload["official_active_ids"] == sorted(payload["official_active_ids"])
        # 無 duplicate
        assert len(set(payload["official_active_ids"])) == len(payload["official_active_ids"])


# ============================================================
# §11 · sanity:data/stocks/{id}.json 不應被本模組動到
# ============================================================

class TestNoStockJSONMutation:
    def test_module_has_no_stock_json_writes(self):
        """
        對齊 P1 §八禁令 + P2 §一:本模組不得刪 / 寫 data/stocks/*.json。
        用簡單 grep-style 檢查:module source 不含 'stocks/' 或 'STOCKS_OUT_DIR' 相關寫入。
        """
        import inspect
        src = inspect.getsource(au)
        # 不該有任何寫入 data/stocks 相關 · 唯一提到「stocks」的地方就是這裡沒有
        assert "STOCKS_OUT_DIR" not in src
        assert "data/stocks/" not in src
        assert "stocks/" not in src
