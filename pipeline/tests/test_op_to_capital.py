"""
本業股本獲利率(v3.5.4-r3)· 邊界矩陣可執行測試

執行:
    pytest pipeline/tests/test_op_to_capital.py -v

r3 修正 · 對應 4 個 adversarial 漏洞:
  Prompt 2 · reference_quarter lifecycle
    · full build 也讀 existing_reference_quarter
    · coverage>=80% 是真正的切季 gate
    · 未達標且 existing 存在 → 維持 existing + warning(不 drift 到 modal)
    · deterministic modal(不用 Counter.most_common encounter order · 同票選最新季)
  Prompt 3 · mixed schema + missing quarter
    · partial_preserve_existing 不清任何 pct(含 migrated 既有)
    · reference_quarter=None → 空 universe + warning(不跨季)
    · latestQuarter=None/invalid 自然不進 universe(_is_stale=True)

測試層級聲明(誠實):
  本測試套為 models helper 級 unit tests(呼叫 pipeline.models.* + pipeline.transform._compute_cl_ratio)
  · 不呼叫 build.run(),不做真正的 CLI/subprocess/finmind 端對端整合測試
  · lifecycle 場景以 helper 級 rows fixture 模擬,不涉及 scanner_index.json 讀寫
"""
import pytest
from pipeline.models import (
    is_opcap_eligible,
    compute_op_to_capital,
    compute_op_capital_percentiles,
    determine_reference_quarter,
    _deterministic_modal_quarter,
    _quarter_sort_key,
)


# ============================================================
# fixtures
# ============================================================

def _q(rev, gp, op, cs, q_label='2026/2Q'):
    return {'rev': rev, 'gp': gp, 'op': op, 'capitalStock': cs, 'q': q_label}


def _v354_row(id, opCapQ=None, opCapTTM=None, latestQ='2026/2Q',
              ineligible=None, existing_q_pct=None, existing_ttm_pct=None,
              existing_stale=False):
    """v3.5.4 schema scanner row(所有 opcap 欄位齊全)"""
    return {
        'id': id,
        'latestQuarter': latestQ,
        'opCapitalIneligible': ineligible,
        'opToCapitalQuarter': opCapQ,
        'opToCapitalTTM': opCapTTM,
        'capitalChangedTTM': False,
        'capitalStock': 10000,
        'opToCapitalQuarterPercentile': existing_q_pct,
        'opToCapitalTTMPercentile': existing_ttm_pct,
        'opCapitalDataStale': existing_stale,
    }


def _legacy_row(id, existing_q_pct=None, existing_ttm_pct=None):
    """舊 schema row(無 opCapitalIneligible 等 v3.5.4 欄位)"""
    r = {'id': id, 'rev': 1000, 'gm': 30.0}
    if existing_q_pct is not None:
        r['opToCapitalQuarterPercentile'] = existing_q_pct
    if existing_ttm_pct is not None:
        r['opToCapitalTTMPercentile'] = existing_ttm_pct
    return r


TSMC_DELTA_QUARTERLY = [
    _q(112203, 39194, 16423, 25975, '2024/3Q'),
    _q(114202, 35136, 10701, 25975, '2024/4Q'),
    _q(118919, 37788, 14036, 25975, '2025/1Q'),
    _q(124035, 44049, 18669, 25975, '2025/2Q'),
    _q(150318, 52416, 24809, 25975, '2025/3Q'),
    _q(161613, 55903, 26418, 25975, '2025/4Q'),
    _q(159353, 58961, 28417, 25975, '2026/1Q'),
    _q(183256, 65308, 30570, 25975, '2026/2Q'),
]

WIWYNN_QUARTERLY = [
    _q(0, 0, 7905,  1858, '2024/3Q'),
    _q(0, 0, 8131,  1858, '2024/4Q'),
    _q(0, 0, 11981, 1858, '2025/1Q'),
    _q(0, 0, 15899, 1858, '2025/2Q'),
    _q(0, 0, 19572, 1858, '2025/3Q'),
    _q(0, 0, 16466, 1858, '2025/4Q'),
    _q(0, 0, 17458, 1858, '2026/1Q'),
    _q(0, 0, 20218, 5585, '2026/2Q'),
]


# ============================================================
# 邊界 G · 金融業排除
# ============================================================

def test_finance_industry_excluded():
    result = compute_op_to_capital(TSMC_DELTA_QUARTERLY, 'finance')
    assert result['opToCapitalQuarter'] is None
    assert result['opCapitalIneligible'] == 'finance'
    assert result['latestQuarter'] == '2026/2Q'


def test_finance_excluded_even_with_valid_data():
    result = compute_op_to_capital([_q(1000, 500, 100, 10000)], 'finance')
    assert result['opCapitalIneligible'] == 'finance'


# ============================================================
# 邊界 H · cs 缺值
# ============================================================

def test_cs_zero_marked_cs_invalid():
    r = compute_op_to_capital([_q(1000, 500, 100, 0)], 'traditional')
    assert r['opCapitalIneligible'] == 'cs_invalid'
    assert r['opToCapitalQuarter'] is None


def test_cs_negative_marked_cs_invalid():
    assert compute_op_to_capital([_q(1000, 500, 100, -50)], 'traditional')['opCapitalIneligible'] == 'cs_invalid'


def test_cs_none_marked_cs_invalid():
    r = compute_op_to_capital([_q(1000, 500, 100, None)], 'traditional')
    assert r['opCapitalIneligible'] == 'cs_invalid'
    assert r['capitalStock'] is None


def test_op_none_marked_op_null():
    assert compute_op_to_capital([_q(1000, 500, None, 10000)], 'traditional')['opCapitalIneligible'] == 'op_null'


# ============================================================
# 邊界 I · TTM 資料不足
# ============================================================

def test_ttm_less_than_4_quarters_returns_none():
    r = compute_op_to_capital(TSMC_DELTA_QUARTERLY[-3:], 'traditional')
    assert r['opToCapitalQuarter'] is not None
    assert r['opToCapitalTTM'] is None


def test_ttm_with_op_null_in_period_returns_none():
    q = [dict(x) for x in TSMC_DELTA_QUARTERLY[-4:]]
    q[1]['op'] = None
    r = compute_op_to_capital(q, 'traditional')
    assert r['opToCapitalTTM'] is None
    assert r['opToCapitalQuarter'] is not None


# ============================================================
# 邊界 J · capitalChangedTTM
# ============================================================

def test_capital_stable_no_flag():
    assert compute_op_to_capital(TSMC_DELTA_QUARTERLY, 'traditional')['capitalChangedTTM'] is False


def test_capital_changed_triggers_flag():
    r = compute_op_to_capital(WIWYNN_QUARTERLY, 'traditional')
    assert r['capitalChangedTTM'] is True
    assert r['opToCapitalTTM'] is not None


def test_capital_changed_less_than_5q_returns_null():
    assert compute_op_to_capital(TSMC_DELTA_QUARTERLY[-4:], 'traditional')['capitalChangedTTM'] is None


def test_capital_changed_cs_invalid_in_period_returns_null():
    q = [dict(x) for x in TSMC_DELTA_QUARTERLY[-5:]]
    q[0]['capitalStock'] = 0
    assert compute_op_to_capital(q, 'traditional')['capitalChangedTTM'] is None


def test_capital_changed_uses_q_minus_5_endpoints():
    q = [_q(0, 0, 100, 1000, '2025/2Q'),
         _q(0, 0, 100, 1500, '2025/3Q'),
         _q(0, 0, 100, 1500, '2025/4Q'),
         _q(0, 0, 100, 1500, '2026/1Q'),
         _q(0, 0, 100, 1500, '2026/2Q')]
    assert compute_op_to_capital(q, 'traditional')['capitalChangedTTM'] is True


def test_capital_changed_exactly_20pct_boundary():
    q = [_q(0, 0, 100, 1000, '2025/2Q'),
         _q(0, 0, 100, 1200, '2025/3Q'),
         _q(0, 0, 100, 1200, '2025/4Q'),
         _q(0, 0, 100, 1200, '2026/1Q'),
         _q(0, 0, 100, 1200, '2026/2Q')]
    assert compute_op_to_capital(q, 'traditional')['capitalChangedTTM'] is True


def test_capital_changed_just_below_20pct_not_triggered():
    q = [_q(0, 0, 100, 1000, '2025/2Q'),
         _q(0, 0, 100, 1199, '2025/3Q'),
         _q(0, 0, 100, 1199, '2025/4Q'),
         _q(0, 0, 100, 1199, '2026/1Q'),
         _q(0, 0, 100, 1199, '2026/2Q')]
    assert compute_op_to_capital(q, 'traditional')['capitalChangedTTM'] is False


def test_capital_changed_adjacent_not_max_min():
    q = [_q(0, 0, 100, 1000, '2025/2Q'),
         _q(0, 0, 100, 1050, '2025/3Q'),
         _q(0, 0, 100, 1100, '2025/4Q'),
         _q(0, 0, 100, 1150, '2026/1Q'),
         _q(0, 0, 100, 1200, '2026/2Q')]
    assert compute_op_to_capital(q, 'traditional')['capitalChangedTTM'] is False


# ============================================================
# 台達電公式驗證
# ============================================================

def test_delta_electronics_quarterly_formula():
    assert compute_op_to_capital(TSMC_DELTA_QUARTERLY, 'traditional')['opToCapitalQuarter'] == 117.7


def test_delta_electronics_ttm_d_method():
    assert compute_op_to_capital(TSMC_DELTA_QUARTERLY, 'traditional')['opToCapitalTTM'] == 424.3


def test_delta_electronics_eligible():
    r = compute_op_to_capital(TSMC_DELTA_QUARTERLY, 'traditional')
    assert r['opCapitalIneligible'] is None
    assert r['capitalStock'] == 25975
    assert r['latestQuarter'] == '2026/2Q'


# ============================================================
# capitalStock 欄位輸出
# ============================================================

def test_capital_stock_output_when_eligible():
    assert compute_op_to_capital(TSMC_DELTA_QUARTERLY, 'traditional')['capitalStock'] == 25975


def test_capital_stock_output_when_ineligible_but_valid():
    r = compute_op_to_capital([_q(1000, 500, 100, 12345, '2026/2Q')], 'finance')
    assert r['opCapitalIneligible'] == 'finance'
    assert r['capitalStock'] == 12345


def test_capital_stock_none_when_cs_invalid():
    assert compute_op_to_capital([_q(1000, 500, 100, 0, '2026/2Q')], 'traditional')['capitalStock'] is None


# ============================================================
# clRatio 直接呼叫 transform 實作(規格 E · r2 已對齊使用者要求 5.6)
# ============================================================

def test_clratio_helper_handles_none_cs():
    from pipeline.transform import _compute_cl_ratio
    assert _compute_cl_ratio(100, None) is None
    assert _compute_cl_ratio(100, 0) is None
    assert _compute_cl_ratio(100, -5) is None
    assert _compute_cl_ratio(0, 1000) is None
    assert _compute_cl_ratio(-10, 1000) is None
    assert _compute_cl_ratio(None, 1000) is None
    assert _compute_cl_ratio(100, 1000) == 10.0
    assert _compute_cl_ratio(250, 1000) == 25.0
    assert _compute_cl_ratio(5000, 1000) == 500.0


# ============================================================
# 邊界 K · 百分位同值(CDF)
# ============================================================

def test_percentile_tied_values_same_rank():
    rows = [_v354_row(f'r{i}', opCapQ=v)
            for i, v in enumerate([5.0, 10.0, 10.0, 10.0, 20.0])]
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert stats['action'] == 'full_recompute'
    assert stats['q_universe_size'] == 5
    assert rows[0]['opToCapitalQuarterPercentile'] == 20.0
    assert rows[1]['opToCapitalQuarterPercentile'] == 80.0
    assert rows[2]['opToCapitalQuarterPercentile'] == 80.0
    assert rows[3]['opToCapitalQuarterPercentile'] == 80.0
    assert rows[4]['opToCapitalQuarterPercentile'] == 100.0


def test_percentile_ineligible_stays_none():
    rows = [_v354_row('a', opCapQ=10.0),
            _v354_row('b', ineligible='finance'),
            _v354_row('c', opCapQ=20.0)]
    compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert rows[0]['opToCapitalQuarterPercentile'] == 50.0
    assert rows[1]['opToCapitalQuarterPercentile'] is None
    assert rows[2]['opToCapitalQuarterPercentile'] == 100.0


def test_percentile_two_fields_independent():
    rows = [_v354_row('a', opCapQ=10.0, opCapTTM=None),
            _v354_row('b', opCapQ=20.0, opCapTTM=100.0),
            _v354_row('c', opCapQ=30.0, opCapTTM=200.0)]
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert stats['q_universe_size'] == 3
    assert stats['ttm_universe_size'] == 2
    assert rows[0]['opToCapitalQuarterPercentile'] == round(1/3*100, 2)
    assert rows[0]['opToCapitalTTMPercentile'] is None
    assert rows[1]['opToCapitalTTMPercentile'] == 50.0
    assert rows[2]['opToCapitalTTMPercentile'] == 100.0


# ============================================================
# 邊界 L · 資料新鮮度 stale(含 r3 · missing latestQuarter)
# ============================================================

def test_stale_quarter_not_in_percentile_universe():
    rows = [_v354_row('a', opCapQ=10.0, opCapTTM=40.0, latestQ='2026/2Q'),
            _v354_row('b', opCapQ=500.0, opCapTTM=2000.0, latestQ='2026/1Q'),
            _v354_row('c', opCapQ=20.0, opCapTTM=80.0, latestQ='2026/2Q')]
    compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert rows[1]['opCapitalDataStale'] is True
    assert rows[1]['opToCapitalQuarterPercentile'] is None
    assert rows[0]['opToCapitalQuarterPercentile'] == 50.0
    assert rows[2]['opToCapitalQuarterPercentile'] == 100.0


def test_stale_but_still_shows_values():
    rows = [_v354_row('a', opCapQ=50.0, opCapTTM=150.0, latestQ='2025/4Q')]
    compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert rows[0]['opToCapitalQuarter'] == 50.0
    assert rows[0]['opToCapitalTTM'] == 150.0
    assert rows[0]['opCapitalDataStale'] is True
    assert rows[0]['opToCapitalQuarterPercentile'] is None


# ============================================================
# 邊界 N · full build 前清空舊 pct
# ============================================================

def test_full_build_previous_percentiles_cleared_and_recomputed():
    rows = [_v354_row('a', opCapQ=10.0, existing_q_pct=99.99, existing_ttm_pct=77.77, existing_stale=True),
            _v354_row('b', opCapQ=20.0, existing_q_pct=88.88, existing_ttm_pct=66.66, existing_stale=True)]
    compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert rows[0]['opToCapitalTTMPercentile'] is None
    assert rows[1]['opToCapitalTTMPercentile'] is None
    assert rows[0]['opToCapitalQuarterPercentile'] == 50.0
    assert rows[1]['opToCapitalQuarterPercentile'] == 100.0
    assert rows[0]['opCapitalDataStale'] is False
    assert rows[1]['opCapitalDataStale'] is False


# ============================================================
# 綜合 current-quarter cohort
# ============================================================

def test_current_quarter_cohort_percentile_recomputes_correctly():
    rows = [_v354_row(f'c{i+1}', opCapQ=v)
            for i, v in enumerate([5.0, 10.0, 20.0, 50.0, 100.0])]
    rows.extend([_v354_row('s1', opCapQ=999.0, latestQ='2025/4Q'),
                 _v354_row('s2', opCapQ=888.0, latestQ='2024/1Q'),
                 _v354_row('f1', ineligible='finance')])
    compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert rows[0]['opToCapitalQuarterPercentile'] == 20.0
    assert rows[3]['opToCapitalQuarterPercentile'] == 80.0
    assert rows[4]['opToCapitalQuarterPercentile'] == 100.0
    assert rows[5]['opToCapitalQuarterPercentile'] is None
    assert rows[6]['opToCapitalQuarterPercentile'] is None
    assert rows[7]['opToCapitalQuarterPercentile'] is None


def test_is_eligible_helper():
    assert is_opcap_eligible('traditional', TSMC_DELTA_QUARTERLY) == (True, None)
    assert is_opcap_eligible('finance', TSMC_DELTA_QUARTERLY) == (False, 'finance')
    assert is_opcap_eligible('traditional', []) == (False, 'no_quarterly')
    assert is_opcap_eligible('traditional', [_q(1, 1, 1, None)]) == (False, 'cs_invalid')
    assert is_opcap_eligible('traditional', [_q(1, 1, None, 100)]) == (False, 'op_null')


# ============================================================
# r2 lifecycle 測試(繼續保留)
# ============================================================

def test_partial_build_does_not_clear_published_percentiles():
    """r2 · Blocker 1:partial + all_migrated 不會讓 pct 全變 None"""
    rows = [_v354_row(f'old_{i}', opCapQ=float(i),
                      existing_q_pct=round(i / 500 * 100, 2))
            for i in range(1, 501)]
    rows.append(_v354_row('new_a', opCapQ=250.5))
    rows.append(_v354_row('new_b', opCapQ=999.0))
    rows.append(_v354_row('new_c', ineligible='finance'))
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=False)
    assert stats['action'] == 'partial_recompute'
    assert stats['schema_status'] == 'all_migrated'
    non_none = sum(1 for r in rows if r['opToCapitalQuarterPercentile'] is not None)
    assert stats['q_universe_size'] == 502
    assert non_none == 502
    assert rows[-1]['opToCapitalQuarterPercentile'] is None


def test_reference_quarter_independent_of_row_order():
    """r2 · Blocker 2:reference_quarter 與 row order 無關"""
    import random
    base = ([_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(85)]
            + [_v354_row(f'q1_{i}', opCapQ=10.0, latestQ='2026/1Q') for i in range(10)]
            + [_v354_row(f'old_{i}', opCapQ=10.0, latestQ='2025/4Q') for i in range(5)])
    r_orig = list(base)
    r_rev = list(reversed(base))
    r_shuf = list(base); random.Random(42).shuffle(r_shuf)
    a, _ = determine_reference_quarter(r_orig, None, is_full_build=True)
    b, _ = determine_reference_quarter(r_rev, None, is_full_build=True)
    c, _ = determine_reference_quarter(r_shuf, None, is_full_build=True)
    assert a == b == c == '2026/2Q'


def test_partial_new_quarter_does_not_switch_reference_early():
    """r2 · Blocker 2:partial 沿用既有 · 不因 85% Q3 而切季"""
    rows = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(85)]
            + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(15)])
    ref, warning = determine_reference_quarter(rows, existing_reference_quarter='2026/2Q', is_full_build=False)
    assert ref == '2026/2Q'
    assert warning is None


def test_full_build_switches_after_coverage_threshold():
    """r2 · full build 達 85% Q3 覆蓋 → 切到 Q3"""
    rows = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(85)]
            + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(15)])
    ref, warning = determine_reference_quarter(rows, existing_reference_quarter='2026/2Q', is_full_build=True)
    assert ref == '2026/3Q'
    assert warning is None


def test_partial_none_migrated_does_not_touch_legacy():
    """r2 · partial + 全 legacy · 完全不動 legacy row"""
    rows = [_legacy_row('l1', existing_q_pct=11.11),
            _legacy_row('l2', existing_q_pct=22.22)]
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=False)
    assert stats['action'] == 'partial_preserve_existing'
    assert stats['schema_status'] == 'none_migrated'
    assert rows[0]['opToCapitalQuarterPercentile'] == 11.11
    assert rows[1]['opToCapitalQuarterPercentile'] == 22.22


def test_full_build_with_legacy_still_wipes_and_computes_on_migrated():
    """r2 · full build 帶 legacy · migrated row 仍 full_recompute"""
    rows = [_legacy_row('l1', existing_q_pct=99.99),
            _v354_row('m1', opCapQ=10.0),
            _v354_row('m2', opCapQ=20.0)]
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert stats['action'] == 'full_recompute'
    assert stats['schema_status'] == 'mixed'
    assert rows[0]['opToCapitalQuarterPercentile'] == 99.99
    assert rows[1]['opToCapitalQuarterPercentile'] == 50.0
    assert rows[2]['opToCapitalQuarterPercentile'] == 100.0


def test_meta_reference_quarter_returned_in_stats():
    rows = [_v354_row('a', opCapQ=10.0, latestQ='2026/2Q')]
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    assert stats['reference_quarter'] == '2026/2Q'
    assert stats['q_universe_size'] == 1
    assert stats['ttm_universe_size'] == 0


def test_partial_first_time_without_existing_meta_uses_modal():
    rows = ([_v354_row(f'a_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(80)]
            + [_v354_row(f'b_{i}', opCapQ=10.0, latestQ='2026/1Q') for i in range(20)])
    ref, warning = determine_reference_quarter(rows, existing_reference_quarter=None, is_full_build=False)
    assert ref == '2026/2Q'
    assert warning is not None
    assert 'partial' in warning.lower()


# ============================================================
# ============================================================
# r3 新增 · Prompt 2 · reference_quarter lifecycle 修正
# ============================================================
# ============================================================

def test_reference_quarter_tie_is_order_independent():
    """
    r3 · Prompt 4 測試 2:同票時 tie-break 必須明文且與 rows 排序無關。
    tie-break 規則:_quarter_sort_key 最大者(最新季度)。
    """
    import random
    # Q2/Q3 同票(各 50 檔)
    rows_a = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(50)]
              + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(50)])
    rows_b = list(reversed(rows_a))
    rows_c = list(rows_a); random.Random(1).shuffle(rows_c)
    rows_d = list(rows_a); random.Random(2).shuffle(rows_d)
    # first-time(existing=None)· fallback 走 deterministic modal · 應該是最新季 2026/3Q
    a, _ = determine_reference_quarter(rows_a, None, is_full_build=True)
    b, _ = determine_reference_quarter(rows_b, None, is_full_build=True)
    c, _ = determine_reference_quarter(rows_c, None, is_full_build=True)
    d, _ = determine_reference_quarter(rows_d, None, is_full_build=True)
    assert a == b == c == d == '2026/3Q', \
        f"tie-break not deterministic: {a} {b} {c} {d}"

    # 直接驗證 _deterministic_modal_quarter helper
    from collections import Counter
    for perm in [['2026/3Q', '2026/2Q'], ['2026/2Q', '2026/3Q']]:
        c = Counter(perm * 50)  # 兩者各 50
        assert _deterministic_modal_quarter(c) == '2026/3Q', \
            f"deterministic modal wrong for insertion order {perm}"


def test_full_below_80_keeps_existing_reference():
    """
    r3 · Prompt 4 測試 3:existing=Q2,full build Q3=60%、Q2=40%(無達 80%)
    reference 必須維持 Q2 + warning。
    """
    rows = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(60)]
            + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(40)])
    ref, warning = determine_reference_quarter(
        rows, existing_reference_quarter='2026/2Q', is_full_build=True)
    assert ref == '2026/2Q', f"expected keep Q2, got {ref}"
    assert warning is not None
    assert '80%' in warning or 'coverage' in warning
    assert 'keeping existing' in warning or 'existing' in warning


def test_full_switches_only_when_new_quarter_reaches_80():
    """
    r3 · Prompt 4 測試 4:coverage>=80% 是真正的切季 gate。
    · Q3=79%(1 檔缺)→ 不切
    · Q3=80% 剛好 → 切
    """
    # Case A:Q3=79%(79 檔 Q3 + 21 檔 Q2)
    rows_79 = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(79)]
               + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(21)])
    ref_a, warn_a = determine_reference_quarter(
        rows_79, existing_reference_quarter='2026/2Q', is_full_build=True)
    assert ref_a == '2026/2Q', \
        f"79% must NOT switch (gate is >=80%); got {ref_a}"
    assert warn_a is not None  # 應有 warning(未達標,保留 existing)

    # Case B:Q3=80%(80 檔 Q3 + 20 檔 Q2)
    rows_80 = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(80)]
               + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(20)])
    ref_b, warn_b = determine_reference_quarter(
        rows_80, existing_reference_quarter='2026/2Q', is_full_build=True)
    assert ref_b == '2026/3Q', \
        f"80% must switch (gate is >=80%); got {ref_b}"
    assert warn_b is None  # 達標 · 無 warning


def test_full_below_80_and_existing_not_in_eligible_falls_back_to_modal():
    """
    r3 邊界補強:full 未達 80% 且 existing 不存在於 eligible → deterministic modal + warning。
    """
    # existing=2025/1Q 但 eligible rows 都是 2026/2Q 與 2026/3Q · 沒 2025/1Q
    rows = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(60)]
            + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(40)])
    ref, warning = determine_reference_quarter(
        rows, existing_reference_quarter='2025/1Q', is_full_build=True)
    # 沒達 80% · existing 也不在 eligible · → deterministic modal(最新 Q3)
    assert ref == '2026/3Q', f"expected fallback modal 2026/3Q, got {ref}"
    assert warning is not None
    assert 'not in eligible' in warning or 'modal' in warning


def test_full_first_time_without_existing_falls_back_to_modal():
    """
    r3:full 首次(existing=None)· 未達 80% → deterministic modal + warning。
    """
    rows = ([_v354_row(f'q3_{i}', opCapQ=10.0, latestQ='2026/3Q') for i in range(50)]
            + [_v354_row(f'q2_{i}', opCapQ=10.0, latestQ='2026/2Q') for i in range(30)]
            + [_v354_row(f'q1_{i}', opCapQ=10.0, latestQ='2026/1Q') for i in range(20)])
    ref, warning = determine_reference_quarter(
        rows, existing_reference_quarter=None, is_full_build=True)
    assert ref == '2026/3Q'  # modal
    assert warning is not None
    assert 'first time' in warning or 'modal' in warning


# ============================================================
# r3 新增 · Prompt 3 · mixed schema + missing quarter
# ============================================================

def test_partial_mixed_schema_preserves_existing_migrated_percentiles():
    """
    r3 · Prompt 4 測試 1:migrated row 既有 pct=77.77,混入 legacy row,
    partial 執行後 migrated row 的 pct 必須仍為 77.77(r2 錯誤地清空為 None)。
    """
    rows = [
        _legacy_row('legacy_a'),
        _v354_row('mig_a', opCapQ=10.0, existing_q_pct=77.77, existing_ttm_pct=88.88),
        _v354_row('mig_b', opCapQ=20.0, existing_q_pct=55.55, existing_ttm_pct=66.66),
    ]
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=False)
    assert stats['action'] == 'partial_preserve_existing'
    assert stats['schema_status'] == 'mixed'
    # r3 關鍵斷言:既有 migrated pct 必須保留
    assert rows[1]['opToCapitalQuarterPercentile'] == 77.77
    assert rows[1]['opToCapitalTTMPercentile'] == 88.88
    assert rows[2]['opToCapitalQuarterPercentile'] == 55.55
    assert rows[2]['opToCapitalTTMPercentile'] == 66.66
    # stale flag 仍會重算
    assert rows[1]['opCapitalDataStale'] is False  # latestQ = 2026/2Q = ref
    assert rows[2]['opCapitalDataStale'] is False
    # warning 存在且不能又「preserved」又「cleared」矛盾
    assert stats['warnings']
    for w in stats['warnings']:
        assert 'cleared' not in w.lower(), \
            f"warning still says 'cleared' after r3 fix: {w}"


def test_partial_mixed_stale_flag_recomputed_but_pct_preserved():
    """
    r3:mixed 分支下 stale flag 依 reference_quarter 重算,但既有 pct 保留。
    情境:migrated row 既有 pct=42.0、stale=False,但 latestQ=2025/4Q(舊季)
    partial 執行後應:pct 保留 42.0 · stale 更新為 True。
    """
    rows = [
        _legacy_row('legacy_a'),
        _v354_row('mig_stale', opCapQ=10.0, latestQ='2025/4Q',
                  existing_q_pct=42.0, existing_stale=False),
    ]
    compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=False)
    assert rows[1]['opToCapitalQuarterPercentile'] == 42.0  # 保留
    assert rows[1]['opCapitalDataStale'] is True  # 重算


def test_missing_latest_quarter_excluded_from_percentiles():
    """
    r3 · Prompt 4 測試 5:latestQuarter=None 的 row · percentile 必須 None,
    不進 q/ttm universe size。
    """
    rows = [
        _v354_row('a', opCapQ=10.0, latestQ='2026/2Q'),
        _v354_row('b', opCapQ=20.0, latestQ=None),      # missing latestQuarter
        _v354_row('c', opCapQ=30.0, latestQ='2026/2Q'),
        _v354_row('d', opCapQ=40.0, latestQ='invalid'), # 格式無效
    ]
    stats = compute_op_capital_percentiles(rows, reference_quarter='2026/2Q', is_full_build=True)
    # universe 只含 latestQuarter='2026/2Q' 的兩檔
    assert stats['q_universe_size'] == 2, \
        f"expected 2, got {stats['q_universe_size']} (missing/invalid quarter must be excluded)"
    # 有效檔正確算 pct
    assert rows[0]['opToCapitalQuarterPercentile'] == 50.0
    assert rows[2]['opToCapitalQuarterPercentile'] == 100.0
    # missing/invalid latestQuarter → pct=None, stale=True
    assert rows[1]['opToCapitalQuarterPercentile'] is None
    assert rows[1]['opCapitalDataStale'] is True
    assert rows[3]['opToCapitalQuarterPercentile'] is None
    assert rows[3]['opCapitalDataStale'] is True


def test_none_reference_does_not_mix_quarters():
    """
    r3 · Prompt 4 測試 6:reference_quarter=None → 不發布 percentile + warning。
    保護:即使有多個季度的 row,也不得混算成單一 universe。
    """
    rows = [
        _v354_row('q2_a', opCapQ=10.0, latestQ='2026/2Q'),
        _v354_row('q3_a', opCapQ=20.0, latestQ='2026/3Q'),
        _v354_row('q2_b', opCapQ=30.0, latestQ='2026/2Q'),
    ]
    stats = compute_op_capital_percentiles(rows, reference_quarter=None, is_full_build=True)
    assert stats['action'] == 'no_reference_quarter'
    assert stats['q_universe_size'] == 0
    assert stats['ttm_universe_size'] == 0
    assert stats['warnings']
    combined_warning = ' '.join(stats['warnings']).lower()
    assert 'reference' in combined_warning
    assert 'none' in combined_warning or 'null' in combined_warning
    # 所有 row 的 pct 必為 None(即使原本有值)
    for r in rows:
        assert r['opToCapitalQuarterPercentile'] is None


def test_none_reference_partial_also_does_not_publish():
    """r3 邊界補強:partial + all_migrated + reference_quarter=None 同樣不發布"""
    rows = [_v354_row('a', opCapQ=10.0, latestQ='2026/2Q')]
    stats = compute_op_capital_percentiles(rows, reference_quarter=None, is_full_build=False)
    assert stats['action'] == 'no_reference_quarter'
    assert stats['q_universe_size'] == 0
    assert stats['warnings']


# ============================================================
# r3 · deterministic modal helper 直接驗證
# ============================================================

def test_deterministic_modal_tie_break_selects_newest_quarter():
    """_deterministic_modal_quarter:同 count 時選 _quarter_sort_key 最大者"""
    from collections import Counter
    # 三個季度同票 50 · 應選最新 2026/3Q
    c = Counter()
    c['2026/1Q'] = 50
    c['2026/3Q'] = 50
    c['2026/2Q'] = 50
    assert _deterministic_modal_quarter(c) == '2026/3Q'

    # 一個明顯多數
    c2 = Counter({'2026/1Q': 100, '2026/3Q': 50, '2026/2Q': 30})
    assert _deterministic_modal_quarter(c2) == '2026/1Q'


def test_deterministic_modal_empty():
    from collections import Counter
    assert _deterministic_modal_quarter(Counter()) is None
    assert _deterministic_modal_quarter({}) is None


def test_quarter_sort_key_ordering():
    """_quarter_sort_key 產生合理的比較 tuple"""
    assert _quarter_sort_key('2026/2Q') > _quarter_sort_key('2026/1Q')
    assert _quarter_sort_key('2026/1Q') > _quarter_sort_key('2025/4Q')
    assert _quarter_sort_key('2026/3Q') > _quarter_sort_key('2026/2Q')
    # invalid → (0,0)
    assert _quarter_sort_key(None) == (0, 0)
    assert _quarter_sort_key('') == (0, 0)
    assert _quarter_sort_key('invalid') == (0, 0)


# ============================================================
# A2 預留(v3.5.5 獨立)· 明確標「非測試覆蓋」
# ============================================================

@pytest.mark.skip(reason="A2 not implemented · placeholder only (NOT test coverage) · v3.5.5")
def test_a2_placeholder_negative_gp_opgm_returns_none():
    pass


@pytest.mark.skip(reason="A2 not implemented · placeholder only (NOT test coverage) · v3.5.5 決策一.1(a)")
def test_a2_placeholder_green_requires_positive_median():
    pass


@pytest.mark.skip(reason="A2 not implemented · placeholder only (NOT test coverage) · v3.5.5 決策一.2(b)")
def test_a2_placeholder_current_gp_negative_handling():
    pass
