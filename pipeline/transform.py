"""
Transform layer: compute all business indicators from cached raw data.

Public API:
- build_detail(stock_id, info_df) -> dict matching Detail JSON schema (v2.2 §18)
- build_scanner_row(detail: dict) -> dict matching Scanner row schema (SPEC §18.2)

All formulas: v2.2 §7 + Scanner §7.5.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from . import config, ingest, models
from .finmind_client import FinMindClient

log = logging.getLogger(__name__)


# ============================================================
# v3 Insights Engine (per SPEC-insights-v3.1)
# ============================================================
# ============================================================
# YoY / 衍生指標計算
# ============================================================

def _pct(x, y):
    """安全百分比: x/y * 100. 若 y ~= 0 返回 None."""
    if y is None or abs(y) < 0.01:
        return None
    return (x / y) * 100.0


def _yoy_rate(now, ly):
    """YoY 成長率 %. now, ly 都要有值."""
    if now is None or ly is None or abs(ly) < 0.01:
        return None
    return (now - ly) / abs(ly) * 100.0


def _compute_derived(q):
    """計算 gm/om/nm/opgm 等衍生指標. 若 rev/gp 為 0, 相應指標為 None."""
    if not q:
        return {}
    rev = q.get('rev') or 0
    gp = q.get('gp') or 0
    op = q.get('op') or 0
    np = q.get('np') or 0
    noi = q.get('noi') or 0
    return {
        'rev': rev, 'gp': gp, 'op': op, 'np': np, 'noi': noi,
        'gm': _pct(gp, rev),
        'om': _pct(op, rev),
        'nm': _pct(np, rev),
        'opgm': _pct(op, gp),
    }


def _compute_yoy(now_d, ly_d):
    """計算 YoY 集. now_d/ly_d 是 _compute_derived 的結果."""
    if not ly_d:
        return None
    yoy = {
        'rev_yoy': _yoy_rate(now_d['rev'], ly_d['rev']),
        'gp_yoy':  _yoy_rate(now_d['gp'],  ly_d['gp']),
        'op_yoy':  _yoy_rate(now_d['op'],  ly_d['op']),
        'np_yoy':  _yoy_rate(now_d['np'],  ly_d['np']),
    }
    # pp 差 (百分點, 不是相除)
    for k in ('gm', 'om', 'nm', 'opgm'):
        now_v = now_d.get(k)
        ly_v  = ly_d.get(k)
        yoy[f'{k}_yoy_delta'] = (now_v - ly_v) if (now_v is not None and ly_v is not None) else None
    return yoy


def _noi_np_ratio(now_d):
    """業外淨額佔淨利比重 (帶符號). 若 |np| < 0.01 用 0.01 保護."""
    noi = now_d.get('noi', 0) or 0
    np  = now_d.get('np', 0) or 0
    denom = np if abs(np) >= 0.01 else 0.01
    return noi / denom


# ============================================================
# 11 種模式判定 (SPEC section 3.3 ~ section 3.13)
# ============================================================
# 每個 match_MXX(now, yoy) 只做布林判定, 不產出句型.
# 全部條件都用 and 邏輯串起, 有 None 就 return False.

def _all_not_none(*vals):
    return all(v is not None for v in vals)


def match_m01(now, yoy):
    """本業弱靠業外美化 (最高優先)"""
    om = now.get('om')
    op_yoy = yoy.get('op_yoy')
    np_yoy = yoy.get('np_yoy')
    if not _all_not_none(om, np_yoy):
        return False
    if not (om < 3.0 or (op_yoy is not None and op_yoy < 0)):
        return False
    if np_yoy < 30:
        return False
    r = _noi_np_ratio(now)
    return abs(r) > 0.5 and r > 0  # 業外正貢獻


def match_m02(now, yoy):
    """本業強但業外拖累"""
    op_yoy, np_yoy = yoy.get('op_yoy'), yoy.get('np_yoy')
    if not _all_not_none(op_yoy, np_yoy):
        return False
    if op_yoy <= 20 or np_yoy >= 0:
        return False
    r = _noi_np_ratio(now)
    return abs(r) > 0.25 and r < 0  # 業外負拖累


def match_m03(now, yoy):
    """負向營運槓桿"""
    rev_yoy, op_yoy = yoy.get('rev_yoy'), yoy.get('op_yoy')
    if not _all_not_none(rev_yoy, op_yoy):
        return False
    return (rev_yoy < 0
            and op_yoy < rev_yoy - 10
            and op_yoy < -15)


def match_m04(now, yoy):
    """費用失控"""
    gm_delta = yoy.get('gm_yoy_delta')
    op_yoy = yoy.get('op_yoy')
    rev_yoy = yoy.get('rev_yoy')
    if not _all_not_none(gm_delta, op_yoy, rev_yoy):
        return False
    return (abs(gm_delta) < 2
            and op_yoy < -15
            and rev_yoy > 0)


def match_m05(now, yoy):
    """殺價搶單 / 做白工 (v3.1 微調: gm_delta < -3)"""
    rev_yoy = yoy.get('rev_yoy')
    gm_delta = yoy.get('gm_yoy_delta')
    op_yoy = yoy.get('op_yoy')
    if not _all_not_none(rev_yoy, gm_delta, op_yoy):
        return False
    return (rev_yoy > 15
            and gm_delta < -3  # v3.1 微調 (原 -5)
            and op_yoy <= 0)


def match_m06(now, yoy):
    """營運槓桿釋放 (正向)"""
    rev_yoy, op_yoy = yoy.get('rev_yoy'), yoy.get('op_yoy')
    if not _all_not_none(rev_yoy, op_yoy):
        return False
    return (rev_yoy > 5
            and op_yoy > rev_yoy + 10
            and op_yoy > 20)


def match_m07(now, yoy):
    """產品組合優化"""
    rev_yoy = yoy.get('rev_yoy')
    gm_delta = yoy.get('gm_yoy_delta')
    om_delta = yoy.get('om_yoy_delta')
    if not _all_not_none(rev_yoy, gm_delta, om_delta):
        return False
    return (abs(rev_yoy) < 5
            and gm_delta > 3
            and om_delta > 3)


def match_m08(now, yoy):
    """強勁定價權"""
    rev_yoy = yoy.get('rev_yoy')
    gp_yoy = yoy.get('gp_yoy')
    gm_delta = yoy.get('gm_yoy_delta')
    if not _all_not_none(rev_yoy, gp_yoy, gm_delta):
        return False
    return (rev_yoy > 10
            and gp_yoy > rev_yoy + 3
            and gm_delta > 1)


def match_m09(now, yoy):
    """戰略投資期"""
    rev_yoy = yoy.get('rev_yoy')
    gp_yoy = yoy.get('gp_yoy')
    op_yoy = yoy.get('op_yoy')
    opgm_delta = yoy.get('opgm_yoy_delta')
    if not _all_not_none(rev_yoy, gp_yoy, op_yoy, opgm_delta):
        return False
    return (rev_yoy > 10
            and gp_yoy > 10
            and op_yoy < gp_yoy - 15
            and opgm_delta < -3)


def match_m10(now, yoy):
    """去蕪存菁 (主動縮量保利)"""
    rev_yoy = yoy.get('rev_yoy')
    gp_yoy = yoy.get('gp_yoy')
    op_yoy = yoy.get('op_yoy')
    if not _all_not_none(rev_yoy, gp_yoy, op_yoy):
        return False
    return (rev_yoy < -3
            and gp_yoy > 5
            and op_yoy > 5)


def match_m11(now, yoy):
    """規模效應放大 (薄利多銷)"""
    rev_yoy = yoy.get('rev_yoy')
    gm_delta = yoy.get('gm_yoy_delta')
    op_yoy = yoy.get('op_yoy')
    np_yoy = yoy.get('np_yoy')
    if not _all_not_none(rev_yoy, gm_delta, op_yoy, np_yoy):
        return False
    return (rev_yoy > 15
            and -3 < gm_delta < 1
            and op_yoy > 5
            and np_yoy > 5)


# ============================================================
# 判定引擎: 依優先序命中,先命中先出
# ============================================================

_MATCHERS = [
    ('M01', '本業弱靠業外美化', match_m01),
    ('M02', '本業強但業外拖累', match_m02),
    ('M03', '負向營運槓桿',     match_m03),
    ('M04', '費用失控',         match_m04),
    ('M05', '殺價搶單',         match_m05),
    ('M06', '營運槓桿釋放',     match_m06),
    ('M07', '產品組合優化',     match_m07),
    ('M08', '強勁定價權',       match_m08),
    ('M09', '戰略投資期',       match_m09),
    ('M10', '去蕪存菁',         match_m10),
    ('M11', '規模效應放大',     match_m11),
]


def detect_mode(now, yoy, flags=None):
    """依 section 3.2 優先序判定. 返回 (code, name) 或 (None, None).
    v3.5: 加 flags 參數 · M06 觸發改讀 flags['opLevRelease'](單一真理源) ·
          其他 mode 保持原 match_mXX 邏輯不變 · 文字模板一字不動。
    """
    if not yoy:
        return None, None
    for code, name, fn in _MATCHERS:
        try:
            # v3.5: M06 觸發改讀 compute_all_flags 產出的 flag(架構統一)
            if code == 'M06':
                if flags and flags.get('opLevRelease'):
                    return code, name
                continue
            if fn(now, yoy):
                return code, name
        except Exception:  # 判定失敗當作未命中,不 raise
            continue
    return None, None


# ============================================================
# 商業模式句型 (SPEC section 3)
# ============================================================

def _build_mode_text(code, now, yoy, stock_name):
    """依 mode code 產生完整判讀文字."""
    g = now.get('gm') or 0
    o = now.get('om') or 0
    op = now.get('opgm') or 0
    noi = now.get('noi') or 0
    np  = now.get('np') or 0
    noi_ratio = _noi_np_ratio(now) * 100  # 帶符號 %

    rev_yoy = yoy.get('rev_yoy')
    op_yoy = yoy.get('op_yoy')
    gm_delta = yoy.get('gm_yoy_delta')
    opgm_delta = yoy.get('opgm_yoy_delta')

    def _r1(v): return f"{v:.1f}" if v is not None else "—"
    def _s1(v): return f"{v:+.1f}" if v is not None else "—"

    if code == 'M01':
        return (f"每做 100 元生意,{stock_name}本業僅留下 {_r1(o)} 元營業利益,"
                f"但業外收益貢獻 {abs(noi_ratio):.0f}% 淨利,獲利大幅仰賴業外挹注,"
                f"本業轉換動能仍顯疲弱。")
    if code == 'M02':
        return (f"本業營運扎實,每做 100 元生意留下 {_r1(o)} 元營業利益"
                f"(年增 {_s1(op_yoy)}%),但受業外淨損 {noi:.0f} 拖累底線衰退。"
                f"可能為匯損、轉投資或一次性項目干擾,關注本業趨勢即可。")
    if code == 'M03':
        return (f"營收年減 {abs(rev_yoy or 0):.1f}%,但每 100 元營收留下的營業利益驟降至 {_r1(o)} 元"
                f"(年減 {abs(op_yoy or 0):.1f}%),獲利跌幅顯著大於營收跌幅,呈負向營運槓桿。"
                f"可能為訂單短期波動、一次性費用(訴訟/減損/匯損),或費用結構性上升(擴產折舊、加薪、研發加碼)。"
                f"建議查財報損益表「營業費用組成」附註釐清主因。")
    if code == 'M04':
        return (f"毛利率穩定在 {_r1(g)}%(每 100 元營收毛利 {_r1(g)} 元),但毛利轉化率降至 {_r1(op)}%"
                f"(較去年同期 {_s1(opgm_delta)}pp),本業獲利遭營業費用侵蝕。"
                f"可能為推銷/管理/研發費用膨脹,或一次性支出(權益金、專利授權、組織重整)。"
                f"建議查財報「營業費用組成」是否有科目異常跳升。")
    if code == 'M05':
        # op_yoy 顯示邏輯
        if op_yoy is None:
            op_disp = "反而衰退"
        elif op_yoy < -5:
            op_disp = f"反而衰退 {abs(op_yoy):.1f}%"
        elif op_yoy <= 5:
            op_disp = "幾乎未增"
        else:
            op_disp = f"僅微增 {op_yoy:.1f}%"
        return (f"營收擴張 {_s1(rev_yoy)}%,但每做 100 元生意的毛利大幅滑落至 {_r1(g)} 元"
                f"(年減 {abs(gm_delta or 0):.1f}pp),扣除管銷後營業利益{op_disp},"
                f"面臨殺價搶單或成本暴漲壓力,量增價跌,實質獲利未受惠於營收成長。")
    if code == 'M06':
        return (f"營收成長 {_s1(rev_yoy)}%,營業利益成長 {_s1(op_yoy)}%(每 100 元營收營業利益 {_r1(o)} 元),"
                f"獲利增速顯著高於營收增速,呈正向營運槓桿。"
                f"可能為固定成本被稀釋、產能利用率提升、產品組合改善,或費用管控成效。"
                f"建議查「毛利率」「營業費用率」QoQ 趨勢確認主要驅動。")
    if code == 'M07':
        gm_last = (now.get('gm') or 0) - (gm_delta or 0)
        return (f"營收規模持平({_s1(rev_yoy)}%),但每 100 元營收毛利由去年 {_r1(gm_last)} 元升至 {_r1(g)} 元"
                f"({_s1(gm_delta)}pp),獲利結構優化。"
                f"可能為產品組合調整(高毛利品占比上升)、原料成本下降、匯率利多,或製程效率改善。"
                f"建議對照公司近期法說會或年報「產品組合」揭露驗證。")
    if code == 'M08':
        return (f"營收成長 {_s1(rev_yoy)}% 且每 100 元營收毛利年增 {_s1(gm_delta)}pp(毛利率 {_r1(g)}%),"
                f"量與價同步擴張。"
                f"可能為產品供不應求(有定價權)、產品組合升級,或原料/匯率成本有利。"
                f"建議查產業循環位置與同業毛利對比確認定價力來源。")
    if code == 'M09':
        return (f"毛利率年增 {_s1(gm_delta)}pp(每 100 元營收毛利 {_r1(g)} 元),"
                f"但毛利轉化率暫降至 {_r1(op)}%(年減 {abs(opgm_delta or 0):.1f}pp),"
                f"毛利改善的同時營業費用比例上升。"
                f"可能為擴張期投資(研發、通路布建、新產能人力),或一次性費用(組織調整、專案啟動)。"
                f"建議查現金流量表「投資活動」或損益表「研發費用」占比趨勢驗證是否為主動投資。")
    if code == 'M10':
        return (f"營收年減 {abs(rev_yoy or 0):.1f}% 但每 100 元營收毛利提升至 {_r1(g)} 元"
                f"({_s1(gm_delta)}pp),營業利益反增 {_s1(op_yoy)}%,"
                f"呈現「營收萎縮但獲利改善」的精實化 pattern。"
                f"可能為主動淘汰低毛利訂單、產品組合調整、成本結構優化,或一次性費用去化。"
                f"建議查連續 2-3 季毛利率與費用率持續性,確認是結構性改善還是短期波動。")
    if code == 'M11':
        return (f"每做 100 元生意的毛利略降至 {_r1(g)} 元({_s1(gm_delta)}pp),"
                f"但營收規模大幅擴張 {_s1(rev_yoy)}% 帶動總獲利絕對金額持續墊高"
                f"(營益 YoY {_s1(op_yoy)}%),展現薄利多銷的規模效應。")
    return None


def _mode_tone(code):
    """SPEC section 3 每個模式的 tone."""
    return {
        'M01': 'red',    'M02': 'amber',
        'M03': 'red',    'M04': 'amber', 'M05': 'red',
        'M06': 'mint',   'M07': 'mint',  'M08': 'mint',
        'M09': 'amber',  'M10': 'mint',  'M11': 'mint',
    }.get(code, 'amber')


# ============================================================
# Fallback 主判讀 (SPEC section 5.2)
# ============================================================

def _build_fallback_primary(now, yoy, stock_name):
    """未命中商業模式時的常規拆解."""
    rev = now.get('rev', 0)
    g = now.get('gm') or 0
    o = now.get('om') or 0
    gm_minus_om = g - o
    rev_yoy = yoy.get('rev_yoy') if yoy else None

    rev_yi = rev / 100.0  # 百萬 -> 億
    rev_disp = f"{rev_yi:,.2f} 億"
    if yoy and rev_yoy is not None:
        rev_line = f"本季營收 {rev_disp}(YoY {rev_yoy:+.1f}%)"
    else:
        rev_line = f"本季營收 {rev_disp}"

    return (f"{rev_line}。每做 100 元生意賺進 {g:.1f} 元毛利,"
            f"其中 {o:.1f} 元順利轉化為本業利益,"
            f"另外 {gm_minus_om:.1f} 元被營業費用吃掉。")


# ============================================================
# s02-s04 常規輔助 (SPEC section 5.3, 帶去重)
# ============================================================

def _build_gm_insight(now, yoy, dedup=False):
    """s02: 毛利率評語. dedup=True 時簡短."""
    g = now.get('gm')
    gm_qoq = yoy.get('gm_yoy_delta') if yoy else None  # 註:實際 QoQ 需另傳,此處先用 YoY delta
    # SPEC section 5.3 用 QoQ. 若無 QoQ 資料則省略.
    if g is None:
        return {'id':'s02','kind':'supporting','tone':'amber',
                'mode_code':None,'mode_name':None,'text':'毛利率資料不足。'}
    qoq_str = f",QoQ {gm_qoq:+.1f}pp" if gm_qoq is not None else ""
    if dedup:
        # 簡短版
        return {'id':'s02','kind':'supporting','tone':'amber',
                'mode_code':None,'mode_name':None,
                'text': f"毛利率當前 {g:.1f}%,詳見主判讀。"}
    # 分級
    if g >= 40:
        text = f"毛利率 {g:.1f}%{qoq_str}。優質毛利水準,展現產品定價力。"
        tone = 'mint'
    elif g >= 25:
        text = f"毛利率 {g:.1f}%{qoq_str}。健康毛利區間,獲利基礎穩固。"
        tone = 'mint'
    elif g >= 15:
        text = f"毛利率 {g:.1f}%{qoq_str}。中等毛利,關注成本壓力。"
        tone = 'amber'
    else:
        text = f"毛利率 {g:.1f}%{qoq_str}。毛利偏低,檢視售價與原料成本壓力。"
        tone = 'amber'
    return {'id':'s02','kind':'supporting','tone':tone,
            'mode_code':None,'mode_name':None,'text':text}


def _build_opgm_insight(now, yoy, dedup=False, signal=None):
    """s03: 毛利轉化率評語.
    v3.5: 加 signal 參數(來自 models.compute_s03_signal · 個股自我歷史基準)。
          文字模板 4 段完全不動,signal 反向決定選哪段(貫徹單一真理源):
          - signal='red'    → 段 4 (侵蝕過重)
          - signal='green'  → 段 1 或 段 2 (依 opgm 高低)
          - signal='yellow' → 段 3 (偏高關注)
          - signal=None (fallback) → 保留原 4 段閾值邏輯(向後相容)
    """
    op = now.get('opgm')
    if op is None:
        return {'id':'s03','kind':'supporting','tone':'amber',
                'mode_code':None,'mode_name':None,'text':'毛利轉化率資料不足。'}
    eaten = 100 - op
    if dedup:
        return {'id':'s03','kind':'supporting','tone':'amber',
                'mode_code':None,'mode_name':None,
                'text': f"毛利轉化率當前 {op:.1f}%,詳見主判讀。"}

    # v3.5: signal 反向選段(文字內容一字不改,只換選段邏輯)
    if signal == 'red':
        text = f"毛利轉化率 {op:.1f}%,營業費用吃掉 {eaten:.1f}% 毛利。費用侵蝕過重,本業轉化效率不佳。"
        tone = 'red'
    elif signal == 'green':
        if op >= 70:
            text = f"毛利轉化率 {op:.1f}%,營業費用僅吃掉 {eaten:.1f}% 毛利。費用控制優異,毛利高效轉化為營業利益。"
        else:
            text = f"毛利轉化率 {op:.1f}%,營業費用吃掉 {eaten:.1f}% 毛利。費用控制良好,毛利多能轉化為營業利益。"
        tone = 'mint'
    elif signal == 'yellow':
        text = f"毛利轉化率 {op:.1f}%,營業費用吃掉 {eaten:.1f}% 毛利。費用比例偏高,關注是否結構性擴張。"
        tone = 'amber'
    else:
        # signal=None fallback:保留原 4 段絕對閾值邏輯(向後相容)
        if op >= 70:
            text = f"毛利轉化率 {op:.1f}%,營業費用僅吃掉 {eaten:.1f}% 毛利。費用控制優異,毛利高效轉化為營業利益。"
            tone = 'mint'
        elif op >= 50:
            text = f"毛利轉化率 {op:.1f}%,營業費用吃掉 {eaten:.1f}% 毛利。費用控制良好,毛利多能轉化為營業利益。"
            tone = 'mint'
        elif op >= 30:
            text = f"毛利轉化率 {op:.1f}%,營業費用吃掉 {eaten:.1f}% 毛利。費用比例偏高,關注是否結構性擴張。"
            tone = 'amber'
        else:
            text = f"毛利轉化率 {op:.1f}%,營業費用吃掉 {eaten:.1f}% 毛利。費用侵蝕過重,本業轉化效率不佳。"
            tone = 'red'
    return {'id':'s03','kind':'supporting','tone':tone,
            'mode_code':None,'mode_name':None,'text':text}


def _build_noi_insight(now, dedup=False):
    """s04: 業外評語."""
    noi = now.get('noi') or 0
    np = now.get('np') or 0
    ratio = _noi_np_ratio(now)
    abs_ratio = abs(ratio) * 100
    direction = "貢獻" if noi > 0 else "拖累"
    if dedup:
        return {'id':'s04','kind':'supporting','tone':'amber',
                'mode_code':None,'mode_name':None,
                'text': f"業外淨額 {noi:.2f},詳見主判讀。"}
    if abs_ratio < 5:
        text = "業外項目影響輕微,獲利穩定來自本業。"
        tone = 'mint'
    elif abs_ratio < 15:
        text = f"業外淨額 {noi:.2f},占淨利 {abs_ratio:.1f}%,影響有限。"
        tone = 'amber'
    elif abs_ratio < 30:
        text = f"業外淨額 {noi:.2f}({direction}),占淨利 {abs_ratio:.1f}%,建議追蹤是否為經常性。"
        tone = 'amber'
    else:
        text = f"業外淨額 {noi:.2f}({direction}),占淨利 {abs_ratio:.1f}%,顯著扭曲底線,關注一次性因素。"
        tone = 'red'
    return {'id':'s04','kind':'supporting','tone':tone,
            'mode_code':None,'mode_name':None,'text':text}


# ============================================================
# 主入口: insights_v3
# ============================================================

def insights_v3(quarterly: list[dict], stock_name: str, has_cl: bool = False, gc: bool = False) -> list[dict]:
    """
    產生 4~5 條 insights.

    Args:
        quarterly: 季度資料 list, 每 item 含 rev/gp/op/np/noi/eps/cl/capitalStock/clRatio.
                   時序為舊->新, 最後一筆為當季.
                   至少需 1 季 (無 YoY 則走 fallback).
        stock_name: 股票名稱, 用於句型.
        has_cl: 是否有合約負債 (v3.4). True 時追加 s05 敘述當季 CL 與佔股本比.
                金融股在上游已強制 has_cl=False, 保證不會出現 CL 相關文案.

    Returns:
        4 或 5 筆 dict list. 對應前端 s01-s05.
    """
    if not quarterly:
        # 空資料保底
        return [
            {'id': f's0{i+1}', 'kind':'supporting','tone':'amber',
             'mode_code':None,'mode_name':None,'text':'資料不足,無法產出判讀。'}
            for i in range(4)
        ]

    now_d = _compute_derived(quarterly[-1])
    ly_d  = _compute_derived(quarterly[-5]) if len(quarterly) >= 5 else None
    yoy   = _compute_yoy(now_d, ly_d)

    # v3.5: 單一真理源 · 一次算完所有 flag + s03Signal · 供 detect_mode 與 s03 共用
    flags = models.compute_all_flags(quarterly, has_cl, gc)

    # 判定模式(M06 觸發改讀 flags['opLevRelease'])
    code, name = detect_mode(now_d, yoy, flags=flags)

    # 主判讀 s01
    if code:
        text = _build_mode_text(code, now_d, yoy, stock_name)
        s01 = {
            'id': 's01',
            'kind': 'primary',
            'tone': _mode_tone(code),
            'mode_code': code,
            'mode_name': name,
            'text': text,
        }
    else:
        text = _build_fallback_primary(now_d, yoy, stock_name)
        s01 = {
            'id': 's01',
            'kind': 'supporting',
            'tone': 'amber',
            'mode_code': None,
            'mode_name': None,
            'text': text,
        }

    # s02-s04 常規輔助 (帶去重)
    dedup_gm   = code in ('M05', 'M07', 'M08')
    dedup_opgm = code in ('M04', 'M09')
    dedup_noi  = code in ('M01', 'M02')

    s02 = _build_gm_insight(now_d, yoy, dedup=dedup_gm)
    # v3.5: s03 tone/選段改依 signal(單一真理源)· 文字內容一字不動
    s03 = _build_opgm_insight(now_d, yoy, dedup=dedup_opgm, signal=flags.get('s03Signal'))
    s04 = _build_noi_insight(now_d, dedup=dedup_noi)

    result = [s01, s02, s03, s04]

    # v3.4: 訂單能見度 - 合約負債佔股本比 (僅 hasCL=True 追加)
    # 金融股上游已強制 hasCL=False,保證此區不會出現在金融股 UI
    if has_cl:
        last_q = quarterly[-1]
        cl = last_q.get('cl') or 0
        cs = last_q.get('capitalStock') or 0
        cl_ratio = last_q.get('clRatio')
        if cl > 0 and cs > 0 and cl_ratio is not None:
            times = cl_ratio / 100
            text = f"本季合約負債 {cl:,} 百萬元,佔普通股股本 {times:.1f} 倍({cl_ratio:.1f}%),反映訂單池水位。"
            result.append({
                'id': 's05',
                'kind': 'supporting',
                'tone': 'mint',
                'mode_code': None,
                'mode_name': None,
                'text': text,
            })

    return result
# ============================================================
# End of v3 insights engine
# ============================================================


# ============================================================
# Helpers
# ============================================================
def _q_label(date_str: str) -> str:
    """'2026-06-30' -> '2026/2Q'"""
    y, m, _ = date_str.split("-")
    q = (int(m) - 1) // 3 + 1
    return f"{y}/{q}Q"


def _fs_pivot(fs_df: pd.DataFrame) -> pd.DataFrame:
    """
    FinMind FS returns long format: (date, stock_id, type, value).
    Pivot to wide format with columns rev/gp/op/np/eps/noi.

    v3.3: FS_FIELD_MAP 改為 {dst: [候選 1, 候選 2, ...]},逐一嘗試。
    若全部 miss,log 實際 wide.columns 前 30 個到 GitHub Actions,方便反查真名。

    All monetary values divided by 1_000_000 (元 -> 百萬).
    """
    if fs_df.empty:
        return pd.DataFrame()
    wide = fs_df.pivot_table(
        index="date", columns="type", values="value", aggfunc="first"
    ).reset_index()
    # Debug: 列出全部 type 值(不限 40),幫助日後反查對映
    log.info("FS wide.columns (ALL, %d cols): %s", len(wide.columns), list(wide.columns))
    for dst, candidates in config.FS_FIELD_MAP.items():
        matched = None
        for src in candidates:
            if src in wide.columns:
                matched = src
                break
        if matched:
            if dst == "eps":
                wide[dst] = wide[matched]  # EPS 已是元
            else:
                wide[dst] = wide[matched] / 1_000_000  # 元 -> 百萬
            if matched != candidates[0]:
                # 用了非第一候選,值得 log 提醒(SPEC 過期了)
                log.info("FS field '%s' matched candidate '%s' (not first)", dst, matched)
        else:
            log.warning("FS field '%s' has NO match in candidates: %s", dst, candidates)
            wide[dst] = None
    keep = ["date"] + list(config.FS_FIELD_MAP.keys())
    return wide[keep].sort_values("date").reset_index(drop=True)


def _bs_pivot(bs_df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot BS long -> wide, extract contract liabilities.

    v3.3: BS_FIELD_MAP 改為 {dst: [候選 1, 候選 2, ...]},逐一嘗試。
    """
    if bs_df.empty:
        return pd.DataFrame(columns=["date", "cl"])
    wide = bs_df.pivot_table(
        index="date", columns="type", values="value", aggfunc="first"
    ).reset_index()
    # Debug: 列出全部 type 值(不限 40)
    log.info("BS wide.columns (ALL, %d cols): %s", len(wide.columns), list(wide.columns))
    for dst, candidates in config.BS_FIELD_MAP.items():
        matched = None
        for src in candidates:
            if src in wide.columns:
                matched = src
                break
        if matched:
            wide[dst] = wide[matched] / 1_000_000
            if matched != candidates[0]:
                log.info("BS field '%s' matched candidate '%s' (not first)", dst, matched)
        else:
            log.warning("BS field '%s' has NO match in candidates: %s", dst, candidates)
            wide[dst] = 0
    return wide[["date"] + list(config.BS_FIELD_MAP.keys())].sort_values("date").reset_index(drop=True)


# ============================================================
# Detail JSON builder
# ============================================================
def build_detail(client: FinMindClient, stock_id: str, universe_df: pd.DataFrame) -> Optional[dict]:
    """
    Build one stock's Detail JSON (matches v2.2 §18 schema).
    Returns None on missing data.
    """
    # Basic info
    info_rows = universe_df[universe_df["stock_id"] == stock_id]
    if info_rows.empty:
        log.warning("Stock %s not in universe", stock_id)
        return None
    info = info_rows.iloc[0]
    name = info.get("stock_name", "")
    raw_industry = info.get("industry_category", "")
    industry = config.INDUSTRY_MAP.get(raw_industry, config.INDUSTRY_DEFAULT)
    market_raw = str(info.get("type", "")).lower()
    market = "twse" if "twse" in market_raw or market_raw == "twse" else "otc"

    # Fetch raw
    fs_df = ingest.fetch_financial_statements(client, stock_id)
    bs_df = ingest.fetch_balance_sheet(client, stock_id)
    rev_df = ingest.fetch_month_revenue(client, stock_id)

    fs_wide = _fs_pivot(fs_df)
    bs_wide = _bs_pivot(bs_df)

    if fs_wide.empty:
        log.warning("No FS data for %s", stock_id)
        return None

    # Merge FS + BS by date. 拉 20+4 buffer = 24 季,前 4 只用於算 YoY 不出現在輸出
    FULL_QUARTERS = config.QUARTERLY_HISTORY_QUARTERS + config.QUARTERLY_YOY_BUFFER
    merged = fs_wide.merge(bs_wide, on="date", how="left").fillna({"cl": 0})
    merged = merged.tail(FULL_QUARTERS).reset_index(drop=True)

    # 內部 helper: 安全數值轉換,None/NaN/Inf 統一返 None
    # 修 v3.3:金融股某些季度 FinMind 未 populate EPS (返 NaN),需清理避免 JSON 產生 NaN token
    def _n(x, digits=None):
        if x is None:
            return None
        try:
            v = float(x)
            if v != v or v == float('inf') or v == float('-inf'):  # NaN or Inf
                return None
            if digits is None:
                return int(round(v))
            return round(v, digits)
        except (TypeError, ValueError):
            return None

    # 內部 helper: 安全百分比變化 (今 vs 過去), 若基期 ~0 / 缺值 / NaN 返回 None
    def _y(now, past):
        n = _n(now, 4)
        p = _n(past, 4)
        if n is None or p is None or abs(p) < 0.01:
            return None
        return round((n / p - 1) * 100, 1)

    # 先建 full_quarterly (24 筆) 含各 YoY/QoQ
    full_quarterly = []
    for i, row in merged.iterrows():
        py = merged.iloc[i - 4].to_dict() if i >= 4 else None
        pq = merged.iloc[i - 1].to_dict() if i >= 1 else None
        noi_val = _n(row["noi"], 1)
        cl_val = _n(row["cl"]) or 0
        # v3.4: 普通股股本(百萬)+ 合約負債佔股本比 (%)
        cs_val = _n(row.get("capitalStock")) or 0
        cl_ratio = round(cl_val / cs_val * 100, 1) if (cs_val > 0 and cl_val > 0) else None
        full_quarterly.append({
            "q":   _q_label(str(row["date"])[:10]),
            "cl":  cl_val,
            "capitalStock": cs_val,
            "clRatio": cl_ratio,
            "rev": _n(row["rev"]) or 0,
            "gp":  _n(row["gp"]),
            "op":  _n(row["op"]),
            "noi": noi_val if noi_val is not None else 0,
            "np":  _n(row["np"]),
            "eps": _n(row["eps"], 2),
            # v3.2: pipeline 直接產出 YoY/QoQ,前端不再自算
            "revYoY": _y(row["rev"], py["rev"]) if py else None,
            "gpYoY":  _y(row["gp"],  py["gp"])  if py else None,
            "opYoY":  _y(row["op"],  py["op"])  if py else None,
            "npYoY":  _y(row["np"],  py["np"])  if py else None,
            "epsYoY": _y(row["eps"], py["eps"]) if py else None,
            "clYoY":  _y(row["cl"],  py["cl"])  if py else None,
            "revQoQ": _y(row["rev"], pq["rev"]) if pq else None,
            "gpQoQ":  _y(row["gp"],  pq["gp"])  if pq else None,
            "opQoQ":  _y(row["op"],  pq["op"])  if pq else None,
            "npQoQ":  _y(row["np"],  pq["np"])  if pq else None,
            "epsQoQ": _y(row["eps"], pq["eps"]) if pq else None,
            "clQoQ":  _y(row["cl"],  pq["cl"])  if pq else None,
        })

    # 算當年累加 revCum/epsCum,再算 revCumYoY/epsCumYoY (i-4 期比較)
    def _year_of(q_label):
        return q_label.split("/")[0]

    for i, item in enumerate(full_quarterly):
        year = _year_of(item["q"])
        same_year_slice = [x for x in full_quarterly[: i + 1] if _year_of(x["q"]) == year]
        # 累加時 None 視為 0 (已在 _n 內確保無 NaN)
        item["revCum"] = sum((x["rev"] or 0) for x in same_year_slice)
        item["epsCum"] = round(sum((x["eps"] or 0) for x in same_year_slice), 2)
    # 累計 YoY:必須「當前年累加季數」== 「去年同期年累加季數」才有比較意義。
    # 例:當前 2022/3Q 累加 3 季 (Q1+Q2+Q3),py 2021/3Q 也必須累加 3 季才對稱。
    # 若 py 落在 full_quarterly 起頭附近,同年前面幾季不在陣列裡,累加不對稱 → 設 None。
    def _acc_count(idx):
        year = _year_of(full_quarterly[idx]["q"])
        cnt = 0
        for j in range(idx, -1, -1):
            if _year_of(full_quarterly[j]["q"]) != year:
                break
            cnt += 1
        return cnt

    for i, item in enumerate(full_quarterly):
        if i >= 4 and _acc_count(i) == _acc_count(i - 4):
            py = full_quarterly[i - 4]
            item["revCumYoY"] = _y(item["revCum"], py["revCum"])
            item["epsCumYoY"] = _y(item["epsCum"], py["epsCum"])
        else:
            item["revCumYoY"] = None
            item["epsCumYoY"] = None

    # 截尾成最終 QUARTERLY_HISTORY_QUARTERS 季輸出 (前 4 buffer 丟棄)
    quarterly = full_quarterly[-config.QUARTERLY_HISTORY_QUARTERS:]

    # hasCL determination (v2.2 §4.2) — 用最終 quarterly (20 季) 判斷
    max_cl = max((x["cl"] for x in quarterly), default=0)
    max_rev = max((x["rev"] for x in quarterly), default=0)
    has_cl = bool(max_rev and (max_cl / max_rev) > config.HAS_CL_THRESHOLD)

    # v3.4 防呆:金融股(銀行/保險/證券)的「合約負債」是保險合約負債之類,
    # 與訂單池概念完全不同,強制排除 CL 相關 UI 出現。
    if industry == "finance":
        has_cl = False

    # Monthly: 26 months, [year-month, revenue]
    monthly = []
    if not rev_df.empty:
        rev_sorted = rev_df.sort_values("date")
        for _, row in rev_sorted.iterrows():
            date_str = str(row["date"])[:7]  # YYYY-MM
            monthly.append([date_str, round(float(row["revenue"]) / 1_000_000)])
        monthly = monthly[-config.MONTHLY_HISTORY_MONTHS:]

    # v3: 產生自動判讀 (SPEC-insights-v3.1)
    # v3.4: 傳 has_cl,啟用時追加 s05 CL 佔股本比敘述
    # v3.5: 先算 gc(單一真理源之一,供 momentumTurn flag)
    gc_flag = _detect_golden_cross(monthly)
    try:
        insights = insights_v3(quarterly, name, has_cl=has_cl, gc=gc_flag)
    except Exception as exc:
        log.warning("insights_v3 failed for %s: %s", stock_id, exc)
        insights = []

    return {
        "id": stock_id,
        "name": name,
        "industry": industry,
        "market": market,
        "hasCL": has_cl,
        "quarterly": quarterly,
        "monthly": monthly,
        "insights": insights,
    }


# ============================================================
# Scanner row builder
# ============================================================
def _pct_change(current: float, base: float) -> Optional[float]:
    if base is None or base == 0:
        return None
    return (current / base - 1) * 100


def _visibility_months(cl: float, monthly: list) -> Optional[float]:
    """CL / (avg of last 3 months revenue). Returns None if cl==0."""
    if not cl or not monthly or len(monthly) < 3:
        return None
    last_3 = monthly[-3:]
    avg = sum(m[1] for m in last_3) / 3
    if avg == 0:
        return None
    return cl / avg


def _detect_golden_cross(monthly: list, lookback_days: int = 30) -> bool:
    """
    Golden cross (3MA vs 12MA) within lookback days.
    We interpret 'days' as 'months' here since monthly granularity.
    """
    if not monthly or len(monthly) < 13:
        return False
    values = [m[1] for m in monthly]
    n = len(values)
    lookback_months = 1  # scanner spec: within 1 month
    for i in range(n - lookback_months, n):
        if i < 12:
            continue
        ma3_now = sum(values[i-2:i+1]) / 3
        ma12_now = sum(values[i-11:i+1]) / 12
        ma3_prev = sum(values[i-3:i]) / 3
        ma12_prev = sum(values[i-12:i]) / 12
        if ma3_prev <= ma12_prev and ma3_now > ma12_now:
            return True
    return False


def build_scanner_row(detail: dict) -> Optional[dict]:
    """
    Compute Scanner row from Detail JSON.
    Returns row matching Scanner SPEC §18.2 schema.
    """
    q = detail["quarterly"]
    if len(q) < 2:
        return None
    cur = q[-1]
    prev = q[-2]
    yr_ago = q[-5] if len(q) >= 5 else None

    rev = cur["rev"]
    revYoY = _pct_change(cur["rev"], yr_ago["rev"]) if yr_ago else None

    gm = (cur["gp"] / cur["rev"] * 100) if cur["gp"] and cur["rev"] else None
    om = (cur["op"] / cur["rev"] * 100) if cur["op"] and cur["rev"] else None
    nm = (cur["np"] / cur["rev"] * 100) if cur["np"] and cur["rev"] else None
    noiRatio = (cur["noi"] / cur["np"] * 100) if cur["noi"] is not None and cur["np"] else None

    gmPrev = (prev["gp"] / prev["rev"] * 100) if prev["gp"] and prev["rev"] else None
    omPrev = (prev["op"] / prev["rev"] * 100) if prev["op"] and prev["rev"] else None
    nmPrev = (prev["np"] / prev["rev"] * 100) if prev["np"] and prev["rev"] else None

    gmQoQ = (gm - gmPrev) if gm is not None and gmPrev is not None else None
    omQoQ = (om - omPrev) if om is not None and omPrev is not None else None
    nmQoQ = (nm - nmPrev) if nm is not None and nmPrev is not None else None

    vis = _visibility_months(cur["cl"], detail["monthly"]) if detail["hasCL"] else None
    clYoY = _pct_change(cur["cl"], yr_ago["cl"]) if detail["hasCL"] and yr_ago else None
    gc = _detect_golden_cross(detail["monthly"])

    # v3.4 P1: clRatio 直接抽 · quarterly 最新季已有預算值
    clRatio = cur.get("clRatio") if detail["hasCL"] else None

    # v3.4 P2: opYoY 直接抽 · quarterly 最新季已有預算值(給「營運槓桿釋放」模板)
    opYoY = cur.get("opYoY")

    # v3.4 P2: gmYoY 需自算 pp 差值 · quarterly 只有 gpYoY(毛利成長率 pct),沒有 gmYoY(毛利率 pp 差值)
    gm_year_ago = None
    if yr_ago and yr_ago.get("gp") and yr_ago.get("rev"):
        gm_year_ago = yr_ago["gp"] / yr_ago["rev"] * 100
    gmYoY = (gm - gm_year_ago) if gm is not None and gm_year_ago is not None else None

    # v3.5: 單一真理源 · 一次算出所有策略 flag + s03 燈號(供前端 5 個策略模板純讀)
    #       Scanner 前端徹底降級為 View · 不再寫任何業務判定邏輯
    flags = models.compute_all_flags(q, detail.get("hasCL", False), gc)

    return {
        "id":       detail["id"],
        "name":     detail["name"],
        "industry": detail["industry"],
        "market":   detail["market"],
        "hasCL":    detail["hasCL"],
        "rev":      rev,
        "revYoY":   round(revYoY, 1) if revYoY is not None else None,
        "gm":       round(gm, 1)     if gm     is not None else None,
        "om":       round(om, 1)     if om     is not None else None,
        "nm":       round(nm, 1)     if nm     is not None else None,
        "noiRatio": round(noiRatio, 1) if noiRatio is not None else None,
        "gmQoQ":    round(gmQoQ, 1)  if gmQoQ  is not None else None,
        "omQoQ":    round(omQoQ, 1)  if omQoQ  is not None else None,
        "nmQoQ":    round(nmQoQ, 1)  if nmQoQ  is not None else None,
        "gc":       gc,
        "vis":      round(vis, 1)    if vis    is not None else None,
        "clYoY":    round(clYoY, 1)  if clYoY  is not None else None,
        # v3.4 P1: clRatio 加入 scanner_index 供 sidebar slider 與動態欄位使用
        "clRatio":  round(clRatio, 1) if clRatio is not None else None,
        # v3.4 P2: opYoY + gmYoY 加入 scanner_index (仍保留供其他統計 / debug 用)
        "opYoY":    round(opYoY, 1)  if opYoY  is not None else None,
        "gmYoY":    round(gmYoY, 1)  if gmYoY  is not None else None,
        # v3.5: 策略 flag(單一真理源產出 · 前端純讀無業務邏輯)
        "opLevRelease": flags["opLevRelease"],
        "opLevType":    flags["opLevType"],
        "coreStable":   flags["coreStable"],
        "orderPileUp":  flags["orderPileUp"],
        "threeUp":      flags["threeUp"],
        "momentumTurn": flags["momentumTurn"],
        "s03Signal":    flags["s03Signal"],
    }
