"""
pipeline/models.py · v3.5.2 · 單一真理源 (Single Source of Truth)

集中定義所有 Scanner 策略 flag 與個股頁 s03 燈號的判定邏輯。
被 transform.py 與 build_scanner_row() 共用調用,確保前後端 100% 語意閉環。

╔═════════════════════════════════════════════════════════════════════╗
║ 【架構級財務不變式 · Financial Invariants · 全檔案適用】             ║
╠═════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║ FI-1 · 「營運槓桿釋放」的絕對前提:cur_op > 0                        ║
║        本業必須實質獲利。任何虧損公司(即使虧損收斂使 op_yoy 為正)  ║
║        都不得判為「營運槓桿釋放」· 這是財務語意的根本。              ║
║        v3.5.2 引入,治 v3.5.1 修好 _y() 公式後的次生 bug。          ║
║                                                                     ║
║ FI-2 · 「s03 綠字」的絕對前提:opgm > 0                             ║
║        毛利轉化率為負(本業虧損)· 費用效率無正向可言。             ║
║        絕對禁止「倒賠還被稱讚費用控管優異」。                        ║
║        v3.5.2 引入,治 v3.5.1 s03 對負 opgm 給綠字的 bug。          ║
║                                                                     ║
║ FI-3 · 「§1 標準公式」YoY 統一用 (cur - ly) / abs(ly) * 100        ║
║        分母永遠取絕對值。基期為負時公式不反號。                      ║
║        |ly| < 0.01 時回傳 None(交給 lowBase 標籤處理)。            ║
║                                                                     ║
║ FI-4 · 「§1 邊界規範」cur < 0 且 ly < 0 = 「虧損收斂」              ║
║        絕對禁止判為「營運好轉」。這是 §1 明文規定。                  ║
║        由 FI-1 具體落實(擋在型態 1/2/3 入口)。                     ║
║                                                                     ║
╠═════════════════════════════════════════════════════════════════════╣
║ 【邊界矩陣驗收標準 · Boundary Matrix】                              ║
║                                                                     ║
║ 每次修 pipeline 邏輯前,必須驗證以下所有邊界(禁忌情境):            ║
║                                                                     ║
║   邊界 A:虧損收斂 (cur<0 且 ly<0 · op_yoy 為正) → opLev 必 False   ║
║   邊界 B:由虧轉盈 (cur>0 且 ly<0 · turnedPositive=True)            ║
║   邊界 C:基期為 0 (ly=0 · lowBase=True · opYoY=None 或標籤化)      ║
║   邊界 D:QoQ 墜崖 (op_qoq<-15 且 om_qoq<-2) → opLev 必 False       ║
║   邊界 E:毛利為負 (gp<0 · 極端燒錢公司)                            ║
║   邊界 F:業外主導 (|noiRatio|>70 · 業外扭曲底線)                   ║
║                                                                     ║
║ 每次修 pipeline 後,必須抽驗:                                       ║
║   分層抽樣 15 檔 (型態 1/2/3 各 3 檔 + 虧損邊界 3 檔 + 對照組 3 檔) ║
║                                                                     ║
║ 全庫飄移警報(修完 rebuild 後):                                    ║
║   任一 flag 命中數變化 > 10% · 觸發人工檢查                         ║
║                                                                     ║
╚═════════════════════════════════════════════════════════════════════╝

【設計原則】
1. 純函式風格 · 只讀資料 · 不做 IO
2. 所有函式對輸入資料 None 值必嚴格 return None/False (不 padding)
3. 3 型態 (以量補價 / 高純度擴張 / 轉機修復) 用 OR 聯集 + 共通 QoQ 墜崖防呆
4. s03 用「個股自我歷史 8 季中位數」基準,廢除跨產業硬閾值

【對外主 API】
- compute_all_flags(quarterly, has_cl, gc) -> dict
    傳入完整 quarterly 陣列 + hasCL + gc,回傳所有策略 flag + s03Signal
- compute_op_lev_release(...)  -> (bool, str|None)
- compute_core_stable(...)     -> bool
- compute_s03_signal(...)      -> "red"|"green"|"yellow"
- compute_order_pile_up(...)   -> bool
- compute_three_up(...)        -> bool
- compute_momentum_turn(...)   -> bool

【資料契約】
所有 quarterly row 需含: rev, gp, op, np, noi, revYoY, opYoY, opQoQ
所有 derived (內部算) 產出: gm, om, nm, opgm 三率
"""

from typing import Optional, Tuple, Dict, Any, List
import statistics


# ============================================================
# Helpers
# ============================================================

def _all_not_none(*vals) -> bool:
    return all(v is not None for v in vals)


def _pct(num, den) -> Optional[float]:
    """安全百分比:分母 <=0 或 None → return None"""
    if num is None or den is None or den == 0:
        return None
    return num / den * 100.0


def _derive(q: dict) -> dict:
    """從 quarterly row 計算 gm/om/nm/opgm 衍生指標"""
    if not q:
        return {}
    rev, gp, op, np_, noi = (q.get(k) or 0 for k in ('rev', 'gp', 'op', 'np', 'noi'))
    return {
        'rev': rev, 'gp': gp, 'op': op, 'np': np_, 'noi': noi,
        'gm':   _pct(gp, rev),
        'om':   _pct(op, rev),
        'nm':   _pct(np_, rev),
        'opgm': _pct(op, gp),
    }


def _median_opgm(quarterly_slice: List[dict]) -> Optional[float]:
    """給定 quarterly 切片,取所有有效 opgm 的中位數。None 若無有效資料"""
    values = []
    for q in quarterly_slice:
        d = _derive(q)
        if d.get('opgm') is not None:
            values.append(d['opgm'])
    if not values:
        return None
    return statistics.median(values)


def _mean_opgm(quarterly_slice: List[dict]) -> Optional[float]:
    """給定 quarterly 切片,取所有有效 opgm 的算術平均"""
    values = []
    for q in quarterly_slice:
        d = _derive(q)
        if d.get('opgm') is not None:
            values.append(d['opgm'])
    if not values:
        return None
    return statistics.mean(values)


# ============================================================
# 【核心】營運槓桿釋放 · 3 型態 OR 聯集 + QoQ 墜崖防呆
# ============================================================

def _match_type1_volume_expansion(rev_yoy, gp_yoy, gm_delta, op_yoy, cur_op) -> bool:
    """型態 1 · 以量補價 (EMS/封測/車零/大宗原料的主流劇本)
    revYoY 大幅衝高 · gmYoY 微負但非崩跌 · gpYoY 實質成長 · opYoY 遠超 revYoY
    
    ═══════ 財務不變式 (Financial Invariants · 禁止違反) ═══════
    INV-1: cur_op > 0
           本業必須實質獲利。虧損公司若因「虧損收斂」使 op_yoy 為正,
           不得判為「營運槓桿釋放」(這是虧損改善,不是槓桿釋放)。
           違反此不變式的示例:遠雄來 op=-9 但被誤判 (v3.5.1 bug 案例)
    INV-2: rev_yoy > 10.0
           營收要真的衝高,以量補價的核心是「量能翻倍」
    INV-3: -3.0 <= gm_delta <= 0.5
           毛利率可微跌 (讓利換量) 但不可崩跌 (成本失控)
    INV-4: op_yoy > rev_yoy + 5.0 且 op_yoy > 15.0
           營益增速跑贏營收 · 且絕對強度足夠
    ═══════════════════════════════════════════════════════════
    
    ═══════ 邊界矩陣 (Boundary Matrix · 修 code 前必跑) ═══════
    cur_op   ly_op   rev_yoy  op_yoy  gm_delta  預期判定  違反哪條
    ─────────────────────────────────────────────────────────
      100     50      +15      +30     -1.0     ✅ True   (正常命中)
     -10      -30     +15      +66     -1.0     ❌ False  INV-1
      100     50      +5       +30     -1.0     ❌ False  INV-2
      100     50      +15      +30     -5.0     ❌ False  INV-3
      100     50      +15      +18     -1.0     ❌ False  INV-4
    ═══════════════════════════════════════════════════════════
    """
    if not _all_not_none(rev_yoy, gp_yoy, gm_delta, op_yoy, cur_op):
        return False
    # v3.5.2: INV-1 · 本業必須實質獲利(禁止「虧損收斂」通過)
    if cur_op <= 0:
        return False
    return (rev_yoy > 10.0
            and gp_yoy > 0
            and -3.0 <= gm_delta <= 0.5
            and op_yoy >= rev_yoy + 5.0
            and op_yoy > 15.0)


def _match_type2_pure_expansion(rev_yoy, gm_delta, op_yoy, cur_op) -> bool:
    """型態 2 · 高純度擴張 (IC 設計/SaaS/高階利基硬體的黃金狀態)
    revYoY 成長 · gmYoY 持平或走揚 · opYoY 遠超 revYoY
    
    ═══════ 財務不變式 (Financial Invariants · 禁止違反) ═══════
    INV-1: cur_op > 0
           本業必須實質獲利。這是本次 v3.5.1 bug 的教訓:
           4174 浩鼎 op=-211 但 opYoY=+69% 被誤判為 pure。
           絕對禁止「本業還在虧損」的公司走「高純度擴張」語意。
    INV-2: rev_yoy > 0
           營收要成長 (排除衰退期)
    INV-3: gm_delta >= 0
           毛利率不惡化 (排除削價競爭假象)
    INV-4: op_yoy > rev_yoy + 5.0 且 op_yoy > 10.0
           營益增速跑贏營收 · 且絕對強度足夠
    ═══════════════════════════════════════════════════════════
    
    ═══════ 邊界矩陣 (Boundary Matrix · 修 code 前必跑) ═══════
    cur_op   ly_op   rev_yoy  op_yoy  gm_delta  預期判定  違反哪條
    ─────────────────────────────────────────────────────────
      100     50      +8      +30      +1.0     ✅ True   (正常命中)
     -9      -14      +8      +33.7    +1.0     ❌ False  INV-1 (遠雄來)
     -211    -125     +19.8   +69      +2.0     ❌ False  INV-1 (浩鼎)
      100     50      -1      +30      +1.0     ❌ False  INV-2
      100     50      +8      +30      -1.0     ❌ False  INV-3
      100     50      +8      +8       +1.0     ❌ False  INV-4
    ═══════════════════════════════════════════════════════════
    """
    if not _all_not_none(rev_yoy, gm_delta, op_yoy, cur_op):
        return False
    # v3.5.2: INV-1 · 本業必須實質獲利(禁止「虧損收斂」通過)
    if cur_op <= 0:
        return False
    return (rev_yoy > 0
            and gm_delta >= 0
            and op_yoy >= rev_yoy + 5.0
            and op_yoy > 10.0)


def _match_type3_turnaround(rev_yoy, op_yoy, cur_op, cur_rev, ly_op,
                             opgm, opgm_prev4q_mean) -> bool:
    """型態 3 · 轉機修復 (景氣循環股走出谷底的典型)
    revYoY 溫和 · 營益暴增 (由負轉正 or op_yoy > 100%) · OPGM 反彈幅度 >= 前4Q平均 * 1.3
    
    v3.5.1 (B1 最嚴):遵循「財務指標判讀規範 §2」·
    turned_positive 嚴格條件:
        1. ly_op < 0 (**排除基期為 0 的雜訊**,強制去年真虧損)
        2. cur_op > cur_rev * 0.02 (當季 OM ≥ 2%,確保實質獲利厚度)
    這樣才能排除「0 → 10」這種微弱訊號,只保留台塑/中鋼這類真轉機股。
    """
    if not _all_not_none(rev_yoy, cur_op, opgm, opgm_prev4q_mean):
        return False
    # 條件 1:營收溫和成長 (未見暴衝,排除已在型態 1/2 的情境)
    if not (0 < rev_yoy < 15):
        return False
    # 條件 2:營益暴增(B1 最嚴)
    # 由負轉正需同時滿足:去年真虧損 + 當季 OM ≥ 2%(實質獲利厚度)
    turned_positive = (
        ly_op is not None and ly_op < 0
        and cur_rev is not None and cur_rev > 0
        and cur_op > cur_rev * 0.02
    )
    surging = (op_yoy is not None and op_yoy > 100)
    if not (turned_positive or surging):
        return False
    # 條件 3:OPGM 反彈幅度 (不強制 V 型 · 只看反彈幅度)
    return opgm >= opgm_prev4q_mean * 1.3


def _qoq_cliff_defense(op_qoq, om_qoq_delta) -> bool:
    """共通 QoQ 墜崖防呆:兩者同時成立才排除 (排除高點反轉/基期陷阱)
    - op_qoq < -15%(營益絕對值大跌)
    - om_qoq_delta < -2.0pp(營益率 pp 差劇降)
    """
    if not _all_not_none(op_qoq, om_qoq_delta):
        return False  # 資料不足時不觸發防呆 (寧錯放不錯殺)
    return op_qoq < -15.0 and om_qoq_delta < -2.0


def compute_op_lev_release(cur_q: dict, prev_q: Optional[dict],
                            ly_q: Optional[dict],
                            opgm_prev4q_mean: Optional[float]) -> Tuple[bool, Optional[str]]:
    """
    營運槓桿釋放主 API · 3 型態 OR 聯集 + QoQ 墜崖防呆
    
    Args:
        cur_q: 當季 quarterly row
        prev_q: 上一季 quarterly row (供 om_qoq_delta 計算)
        ly_q: 去年同季 quarterly row (供 turned_positive 判斷)
        opgm_prev4q_mean: 前 4 季 OPGM 平均 (型態 3 用)
    
    Returns:
        (opLevRelease: bool, opLevType: "volume"|"pure"|"turnaround"|None)
    """
    if not cur_q:
        return (False, None)
    
    now_d = _derive(cur_q)
    rev_yoy = cur_q.get('revYoY')
    gp_yoy  = cur_q.get('gpYoY')
    op_yoy  = cur_q.get('opYoY')
    op_qoq  = cur_q.get('opQoQ')
    cur_op  = now_d.get('op')
    opgm    = now_d.get('opgm')
    
    # gm_delta = 當季毛利率 - 去年同季毛利率 (pp)
    ly_d = _derive(ly_q) if ly_q else {}
    gm_delta = None
    if now_d.get('gm') is not None and ly_d.get('gm') is not None:
        gm_delta = now_d['gm'] - ly_d['gm']
    
    # om_qoq_delta = 當季營益率 - 上季營益率 (pp)
    prev_d = _derive(prev_q) if prev_q else {}
    om_qoq_delta = None
    if now_d.get('om') is not None and prev_d.get('om') is not None:
        om_qoq_delta = now_d['om'] - prev_d['om']
    
    # 共通防呆:QoQ 墜崖 → 直接排除
    if _qoq_cliff_defense(op_qoq, om_qoq_delta):
        return (False, None)
    
    # 3 型態依序檢測 · 命中則直接回傳 (優先權: turnaround > volume > pure)
    # 註:轉機修復優先權高,因由負轉正是最強訊號
    ly_op = ly_d.get('op') if ly_d else None
    cur_rev = now_d.get('rev')
    if _match_type3_turnaround(rev_yoy, op_yoy, cur_op, cur_rev, ly_op, opgm, opgm_prev4q_mean):
        return (True, "turnaround")
    # v3.5.2: 型態 1/2 加傳 cur_op(INV-1 · 禁止虧損公司通過)
    if _match_type1_volume_expansion(rev_yoy, gp_yoy, gm_delta, op_yoy, cur_op):
        return (True, "volume")
    if _match_type2_pure_expansion(rev_yoy, gm_delta, op_yoy, cur_op):
        return (True, "pure")
    
    return (False, None)


# ============================================================
# 【核心】本業獲利穩健 coreStable
# ============================================================

def compute_core_stable(cur_q: dict,
                         opgm_self_median: Optional[float]) -> bool:
    """
    本業獲利穩健 · 極致本業純度 + 動能 + 效率健康
    
    - Core Ratio = OP / (OP + NOI) >= 93%  (業外比重 <= 5%,無業外虛胖)
    - op_yoy >= 0                          (本業不衰退)
    - om >= 5.0                            (營益率健康)
    - opgm >= opgm_self_median             (轉化效率不劣於自身歷史)
    """
    if not cur_q:
        return False
    now_d = _derive(cur_q)
    op = now_d.get('op')
    noi = now_d.get('noi')
    om = now_d.get('om')
    opgm = now_d.get('opgm')
    op_yoy = cur_q.get('opYoY')
    
    if not _all_not_none(op, noi, om, opgm, op_yoy, opgm_self_median):
        return False
    if op <= 0:
        return False  # 虧損公司不論
    
    # Core Ratio = OP / PBT · PBT = OP + NOI
    pbt = op + noi
    if pbt <= 0:
        return False  # 稅前虧損不論
    core_ratio = op / pbt
    
    return (core_ratio >= 0.93
            and op_yoy >= 0
            and om >= 5.0
            and opgm >= opgm_self_median)


# ============================================================
# 【核心】s03 費用診斷 · 個股自我歷史 8 季基準
# ============================================================

def compute_s03_signal(cur_q: dict, prev_q: Optional[dict],
                        opgm_self_median: Optional[float],
                        history_quarter_count: int) -> str:
    """
    s03 燈號判定 · 廢除跨產業硬閾值,改用個股自身歷史 8 季 OPGM 中位數
    
    ═══════ 財務不變式 (Financial Invariants · 禁止違反) ═══════
    INV-1: op > 0 才可判綠字
           本業必須實質獲利。「費用轉化效率優異」的財務語意 = 本業獲利。
           
           ⚠️ 為什麼檢查 op 而非 opgm?
              opgm = op / gp 有數學陷阱:
              當 op<0 且 gp<0 時(如 4174 浩鼎 op=-211, gp=-14)· 
              opgm = (-211)/(-14) = +15% 看似正 · 但財務語意是「燒錢燒到毛利負」。
              只檢查 opgm > 0 會漏掉這種極端案例 · 必須直接檢查 op > 0。
           
           絕對禁止「倒賠還被稱讚費用控管優異」。
           違反案例(v3.5.1 bug):2712 遠雄來 op=-9 判綠字 / 4174 浩鼎 op=-211 判綠字
    INV-2: 資料不足(<4 季)一律走黃字
           分母極端 · 沒有夠強的統計基礎判紅綠
    INV-3: 燈號優先權 紅 > 綠 > 黃(投資人風控保護)
    ═══════════════════════════════════════════════════════════
    
    ═══════ 邊界矩陣 (Boundary Matrix) ═══════
    op       gp       opgm  median   op_qoq  om_qoq  預期判定  違反哪條
    ─────────────────────────────────────────────────────────────
      100    200     50    40       任意    任意    green    (絕對值優)
      -9     26     -34.6  -20      任意    任意    yellow   (v3.5.2 修好 · 原 bug: green)
      -211  -14    +15.07  +12      任意    任意    yellow   (4174 兩負相除陷阱 · v3.5.2 修好)
      -8     20    -40      -30    -30     -3      red      (虧損擴大)
      40     100    40      60     -30     -3      red      (顯著低於歷史+QoQ 墜崖)
      100    200    50      60      任意    任意    yellow   (未達 median*1.2)
    ═══════════════════════════════════════════════════════════
    
    Args:
        cur_q: 當季 quarterly row
        prev_q: 上季 quarterly row (供 om_qoq_delta)
        opgm_self_median: 過去 8 季 OPGM 中位數
        history_quarter_count: 有效歷史季數 (< 4 直接走黃字)
    
    Returns:
        "red" | "green" | "yellow"
    """
    if not cur_q:
        return "yellow"
    # INV-2: 資料不足 fallback:< 4 季一律黃字,不判紅綠 (資料太少不做警告)
    if history_quarter_count < 4 or opgm_self_median is None:
        return "yellow"
    
    now_d = _derive(cur_q)
    opgm = now_d.get('opgm')
    op_yoy = cur_q.get('opYoY')
    op_qoq = cur_q.get('opQoQ')
    
    # om_qoq_delta 現算
    prev_d = _derive(prev_q) if prev_q else {}
    om_qoq_delta = None
    if now_d.get('om') is not None and prev_d.get('om') is not None:
        om_qoq_delta = now_d['om'] - prev_d['om']
    
    if opgm is None:
        return "yellow"
    
    # 🔴 紅字:當季 OPGM 顯著低於歷史 (< median * 0.75) 且 QoQ 仍在向下跳水
    # v3.5.2 加碼:opgm 為負且比較嚴重時也紅字(避免「深度虧損公司走綠字」bug)
    red_condition_1 = opgm < opgm_self_median * 0.75
    red_condition_2 = ((op_qoq is not None and op_qoq < -15.0)
                       or (om_qoq_delta is not None and om_qoq_delta < -2.0))
    if red_condition_1 and red_condition_2:
        return "red"
    
    # v3.5.2 修法 B (升級 · 邊界矩陣 E): INV-1 · 本業必須實質獲利才能綠字
    # ⚠️ 兩負相除得正的數學陷阱:4174 浩鼎 op=-211 且 gp=-14
    #     → opgm = (-211)/(-14) = +15% (看似正)但財務語意是「燒錢燒到毛利都負」
    # 只檢查 opgm > 0 不夠 · 必須直接檢查 op > 0(本業實質獲利)
    op_val = now_d.get('op')
    if op_val is None or op_val <= 0:
        return "yellow"
    
    # 🟢 綠字:當季 OPGM 顯著高於歷史 (>= median * 1.2) 
    #        或 絕對值優秀 (>= 50%) 且本業成長
    #        (op > 0 已由上方 INV-1 gate 保證 · 綠字語意成立)
    green_condition_1 = opgm >= opgm_self_median * 1.2
    green_condition_2 = (opgm >= 50.0 and op_yoy is not None and op_yoy > 0)
    if green_condition_1 or green_condition_2:
        return "green"
    
    # 🟡 黃字:其餘常態區間 (花仙子等結構性 20-30% 產業穩定落此)
    return "yellow"


# ============================================================
# 【原樣搬遷】訂單池累積、認列跟上 orderPileUp
# ============================================================

def compute_order_pile_up(has_cl: bool, cl_yoy: Optional[float],
                          rev_yoy: Optional[float]) -> bool:
    """訂單池累積、認列跟上 · 原 scanner.html 邏輯搬遷,規則不變
    hasCL && clYoY >= 30 && revYoY >= 15
    """
    if not has_cl:
        return False
    if not _all_not_none(cl_yoy, rev_yoy):
        return False
    return cl_yoy >= 30 and rev_yoy >= 15


# ============================================================
# 【原樣搬遷】三率同升 threeUp
# ============================================================

def compute_three_up(gm_qoq: Optional[float], om_qoq: Optional[float],
                      nm_qoq: Optional[float]) -> bool:
    """三率同升 · 原 scanner.html 邏輯搬遷,規則不變
    gmQoQ >= 0 && omQoQ >= 0 && nmQoQ >= 0
    """
    if not _all_not_none(gm_qoq, om_qoq, nm_qoq):
        return False
    return gm_qoq >= 0 and om_qoq >= 0 and nm_qoq >= 0


# ============================================================
# 【原樣搬遷】動能轉正 momentumTurn
# ============================================================

def compute_momentum_turn(gc: Optional[bool]) -> bool:
    """動能轉正 · 原 scanner.html 邏輯搬遷,規則不變
    stock.gc === true (3MA vs 12MA 黃金交叉)
    """
    return bool(gc)


# ============================================================
# 【聚合主 API】compute_all_flags · 一次算全部
# ============================================================

def compute_all_flags(quarterly: List[dict], has_cl: bool,
                       gc: Optional[bool]) -> Dict[str, Any]:
    """
    聚合入口 · 傳入完整 quarterly 陣列,回傳所有 flag + s03Signal
    
    此函式為前端 scanner_index.json 產出的**唯一真理源**,
    同時也給 transform.py 的 M06 觸發與 s03 燈號判定共用調用。
    
    Args:
        quarterly: 完整 quarterly 陣列 (升序,最新在最後)
        has_cl: 是否有合約負債
        gc: 黃金交叉狀態
    
    Returns:
        {
            "opLevRelease": bool,
            "opLevType":    "volume"|"pure"|"turnaround"|None,
            "coreStable":   bool,
            "orderPileUp":  bool,
            "threeUp":      bool,
            "momentumTurn": bool,
            "s03Signal":    "red"|"green"|"yellow",
        }
    """
    # 資料極少時直接回傳全 False + 黃字
    if not quarterly:
        return {
            "opLevRelease": False, "opLevType": None,
            "coreStable": False, "orderPileUp": False,
            "threeUp": False, "momentumTurn": bool(gc),
            "s03Signal": "yellow",
            "turnedPositive": False, "lowBase": False,
        }
    
    cur_q = quarterly[-1]
    prev_q = quarterly[-2] if len(quarterly) >= 2 else None
    ly_q = quarterly[-5] if len(quarterly) >= 5 else None
    
    # 歷史統計:前 4 季 OPGM 平均 (型態 3 用)
    opgm_prev4q_mean = None
    if len(quarterly) >= 5:
        opgm_prev4q_mean = _mean_opgm(quarterly[-5:-1])  # 前 4 季 (不含當季)
    
    # 歷史統計:過去 8 季 OPGM 中位數 (coreStable / s03 用)
    hist_slice = quarterly[-9:-1] if len(quarterly) >= 9 else quarterly[:-1]
    history_quarter_count = len(hist_slice)
    opgm_self_median = _median_opgm(hist_slice) if history_quarter_count >= 4 else None
    
    # 各項判定
    op_lev_release, op_lev_type = compute_op_lev_release(
        cur_q, prev_q, ly_q, opgm_prev4q_mean)
    
    core_stable = compute_core_stable(cur_q, opgm_self_median)
    
    # 原樣搬遷 3 個
    order_pile_up = compute_order_pile_up(
        has_cl, cur_q.get('clYoY'), cur_q.get('revYoY'))
    
    # gm_qoq / om_qoq / nm_qoq 需現算 (quarterly 只有絕對值 QoQ,沒有率的 pp QoQ)
    now_d = _derive(cur_q)
    prev_d = _derive(prev_q) if prev_q else {}
    gm_qoq = _delta_pp(now_d.get('gm'), prev_d.get('gm'))
    om_qoq = _delta_pp(now_d.get('om'), prev_d.get('om'))
    nm_qoq = _delta_pp(now_d.get('nm'), prev_d.get('nm'))
    three_up = compute_three_up(gm_qoq, om_qoq, nm_qoq)
    
    momentum_turn = compute_momentum_turn(gc)
    
    s03_signal = compute_s03_signal(
        cur_q, prev_q, opgm_self_median, history_quarter_count)
    
    # v3.5.1: 新增 UI 標籤旗標(遵循「財務指標判讀規範 §1 邊界定義」)
    # turnedPositive: 去年同期 op<0 且當季 op>0 · 用於前端「轉虧為盈」標籤
    #   (語意寬於 turnaround type · 只判 UI 標籤 · 不影響選股嚴謹度)
    # lowBase: 去年同期 op 絕對值極小(|ly_op| < 1)或當季 opYoY 為 None
    #   用於前端「基期偏低」標籤 · 消除使用者對「-2561%」怪值的困惑
    ly_op_val = _derive(ly_q).get('op') if ly_q else None
    cur_op_val = _derive(cur_q).get('op')
    turned_positive_label = bool(
        ly_op_val is not None and ly_op_val < 0
        and cur_op_val is not None and cur_op_val > 0
    )
    low_base_label = bool(
        cur_q.get('opYoY') is None  # 修好公式後,分母失真自然為 None
        or (ly_op_val is not None and abs(ly_op_val) < 1)  # 或去年基期絕對值極小
    )
    
    return {
        "opLevRelease": op_lev_release,
        "opLevType":    op_lev_type,
        "coreStable":   core_stable,
        "orderPileUp":  order_pile_up,
        "threeUp":      three_up,
        "momentumTurn": momentum_turn,
        "s03Signal":    s03_signal,
        # v3.5.1: UI 標籤(獨立於選股 flag · 供前端渲染「轉虧為盈」/「基期偏低」)
        "turnedPositive": turned_positive_label,
        "lowBase":        low_base_label,
    }


def _delta_pp(now_v, prev_v):
    """兩率之間的 pp 差(現算)"""
    if now_v is None or prev_v is None:
        return None
    return now_v - prev_v
