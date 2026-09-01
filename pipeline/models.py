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
║ FI-6 · 「本業股本獲利率」金融業必為 null                             ║
║        industry == 'finance' → opToCapital* 全數 null                ║
║        不依賴 op is None 自然排除。金融業的營業利益/股本財務語意     ║
║        與一般產業不可比(收入為利差/手續費,非毛利模式)。            ║
║        v3.5.4 引入 · 對應「本業股本獲利率規格」使用者決策 #7。       ║
║                                                                     ║
║ FI-7 · [A2 獨立 Patch · 尚未落實 · 規劃於 v3.5.5]                   ║
║        opgm 分母必須 > 0(修「兩負相除污染 median」bug)              ║
║        待補規格:                                                    ║
║          (a) 只有 opgm_self_median > 0 才可用歷史中位數×1.2 判綠    ║
║          (b) 當季 gp<=0 需另定 red / not_applicable,非直接 yellow  ║
║        對應 backlog/v3.5.5_s03_median_fix.md                        ║
║                                                                     ║
║ FI-8 · 「資料新鮮度」stale 檔不進百分位 universe                     ║
║        latestQuarter != reference_quarter 的公司(含 latestQuarter    ║
║        為 None 或格式無效者),opCapitalDataStale=true,兩個          ║
║        percentile 必為 null。單季/TTM 值仍照留供 UI 顯示。          ║
║        v3.5.4 引入 · r3 明確含 None/invalid quarter 情境。          ║
║                                                                     ║
║ FI-9 · 「build lifecycle 契約」r2 引入 · r3 修正 4 條漏洞            ║
║  (a) partial build 不得清空全市場既有 percentile。r3:含既有 pct     ║
║      的 migrated row 也不動,只重算 stale flag。                    ║
║  (b) partial build 沿用 meta.op_capital_percentile_quarter,        ║
║      不因單一批次提早切季。                                          ║
║  (c) full build 才可切換 reference_quarter;r3 修正切季 gate:       ║
║      · 從最新季倒序找 eligible coverage >= 80% 者                   ║
║      · 有達標 → 允許切換(即使 existing 不同)                       ║
║      · 沒達標 + existing 仍存在 eligible rows → 維持 existing +     ║
║        warning(不因訊號不足就 drift)                              ║
║      · 只有 existing 不存在 or 首次建置 → fallback modal            ║
║      · full build 也必須讀 existing_reference_quarter,不固定 None   ║
║  (d) partial + mixed schema:完全不動任何既有 pct(既有 migrated     ║
║      pct 保留 · 新 build row 因 transform 產出時就是 None · 無需     ║
║      清空),只重算 stale flag,並發 warning。                      ║
║  (e) modal fallback 必須 deterministic:                            ║
║      · 不用 Counter.most_common(1)(encounter-order tie)            ║
║      · 同票時明文選最新季度(_quarter_sort_key 最大者)              ║
║      · 結果必須與 rows 排序完全無關                                 ║
║  (f) reference_quarter=None 時不得跨季計算 percentile,回傳空       ║
║      universe + warning。                                          ║
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
║   邊界 G:金融業 → opToCapital* = null, ineligible='finance'         ║
║   邊界 H:cs 缺值 (None/0/負) → 全 null, ineligible='cs_invalid'     ║
║   邊界 I:近 4 季 op 不齊 → opToCapitalTTM = null                    ║
║   邊界 J:相鄰季 CS 變化 max(|cs[i]/cs[i-1]-1|) >= 20%              ║
║          → capitalChangedTTM=true;若不足 5 季或 CS<=0 存在則 null   ║
║          TTM 仍照算(警示不擋計算 · 對應決策 #2)                    ║
║   邊界 K:百分位同值 → count(v <= current) / N * 100(CDF · 決策 D)  ║
║   邊界 L:latestQuarter != reference_quarter → opCapitalDataStale     ║
║          =true;兩個 percentile 為 null(FI-8);含 latestQuarter=    ║
║          None 或格式無效者(r3)                                     ║
║                                                                     ║
║   [r2 lifecycle 邊界 · FI-9]                                        ║
║   邊界 O:partial build + 混合/none migrated schema                 ║
║          → 完全不動既有 pct(含 migrated row 的 pct),              ║
║             只重算 stale flag,發 warning(r3 修正)                 ║
║   邊界 P:full build 選 reference_quarter                            ║
║          · 從最新季倒序找 eligible coverage >= 80%(切季 gate)      ║
║          · 未達標 + existing 仍存在 → 維持 existing + warning       ║
║          · 未達標 + existing 不存在/首次 → deterministic modal       ║
║                                                                     ║
║   [r3 新增邊界]                                                     ║
║   邊界 Q:modal fallback deterministic(非 Counter encounter order)  ║
║          · 同票時 tie-break: _quarter_sort_key 最大者(最新季度)    ║
║          · 結果與 rows 排序無關                                     ║
║   邊界 R:reference_quarter=None → 不得跨季計算 percentile,           ║
║          清空 universe,不發布 pct,warning                          ║
║                                                                     ║
║ 每次修 pipeline 後,必須執行:                                       ║
║   1) pytest pipeline/tests/test_op_to_capital.py -v                 ║
║   2) 分層抽樣 15 檔 (型態 1/2/3 各 3 檔 + 虧損邊界 3 檔 + 對照組 3 檔)║
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


# ============================================================
# 【v3.5.4】本業股本獲利率 · Operating Profit to Capital Stock
#
# 依據:2026-09-01「本業股本獲利率雙軸設計」使用者 9 條決策 + 6 項規格修正
#      2026-09-01 r2 修正:blocker 1(partial 清空)+ blocker 2(順序決定季度)
#      2026-09-01 r3 修正:adversarial tests 揭露 4 個新漏洞
#         · reference_quarter lifecycle:full build 也讀 existing;
#           coverage<80% + existing 存在 → 維持 existing 不 drift
#         · modal fallback deterministic:同票選最新季度,非 Counter encounter
#         · partial mixed schema preserve:含 migrated row 的既有 pct 也不動
#         · reference_quarter=None 不得跨季計算
#
# 對應規格:pipeline/models.py 檔頭 FI-6 / FI-8 / FI-9(r3 六條) + 邊界 G-R
#
# 決策對照(續):
#   #1  TTM 用 D 法        · sum(近4Q op) / capitalStock(最新季) * 100
#   #2  警示不擋計算      · capitalChangedTTM=true 時 TTM 仍照算
#   #3  完整 eligible universe · 只含 non-finance + cs>0 + op非null + non-stale
#   #4  不做分類          · 無 profitabilityType 欄位
#   #7  金融明確排除      · industry='finance' → ineligible='finance'
#   規格 A  q[-5:] 5 端點  · 但改用相鄰季度變化(規格 四.1)
#   規格 B  資料不足=null  · 不回傳 false
#   規格 C  百分位拆兩欄  · opToCapitalQuarterPercentile / TTMPercentile
#   規格 D  CDF 算法       · count(v <= current) / N * 100
#   規格 四.1 相鄰季度變化 · max(abs(cs[i]/cs[i-1] - 1)) >= 0.20
# ============================================================


def is_opcap_eligible(industry: Optional[str],
                       quarterly: List[dict]) -> Tuple[bool, Optional[str]]:
    """
    判定該檔是否具備「計算本業股本獲利率單季/TTM 值」的最低條件。

    Returns:
        (is_eligible, ineligible_reason)
        ineligible_reason ∈ {'finance', 'no_quarterly', 'cs_invalid', 'op_null', None}
    """
    if industry == 'finance':
        return (False, 'finance')
    if not quarterly:
        return (False, 'no_quarterly')
    cur = quarterly[-1]
    cs = cur.get('capitalStock')
    if cs is None or cs <= 0:
        return (False, 'cs_invalid')
    if cur.get('op') is None:
        return (False, 'op_null')
    return (True, None)


def compute_op_to_capital(quarterly: List[dict],
                           industry: Optional[str]) -> Dict[str, Any]:
    """
    本業股本獲利率主 API · v3.5.4 資料層唯一真理源。

    Returns dict with 9 keys:
        opToCapitalQuarter / opToCapitalTTM / capitalChangedTTM /
        opCapitalIneligible / latestQuarter / capitalStock /
        opToCapitalQuarterPercentile / opToCapitalTTMPercentile /
        opCapitalDataStale

    公式:
        opToCapitalQuarter = op / capitalStock * 100
        opToCapitalTTM     = sum(近4季 op) / capitalStock(最新季) * 100    ← D 法
        capitalChangedTTM  = max(abs(css[i]/css[i-1] - 1) for i in 1..4) >= 0.20
    """
    result = {
        'opToCapitalQuarter':           None,
        'opToCapitalTTM':               None,
        'capitalChangedTTM':            None,
        'opCapitalIneligible':          None,
        'latestQuarter':                None,
        'capitalStock':                 None,
        'opToCapitalQuarterPercentile': None,
        'opToCapitalTTMPercentile':     None,
        'opCapitalDataStale':           False,
    }

    if quarterly:
        result['latestQuarter'] = quarterly[-1].get('q')

    eligible, reason = is_opcap_eligible(industry, quarterly)
    if not eligible:
        result['opCapitalIneligible'] = reason
        if quarterly and quarterly[-1].get('capitalStock') is not None:
            cs_raw = quarterly[-1].get('capitalStock')
            if isinstance(cs_raw, (int, float)) and cs_raw > 0:
                result['capitalStock'] = cs_raw
        return result

    cur = quarterly[-1]
    cs = cur.get('capitalStock')
    op = cur.get('op')
    result['capitalStock'] = cs
    result['opToCapitalQuarter'] = round(op / cs * 100, 1)

    if len(quarterly) >= 4:
        last4_ops = [r.get('op') for r in quarterly[-4:]]
        if all(x is not None for x in last4_ops):
            result['opToCapitalTTM'] = round(sum(last4_ops) / cs * 100, 1)

    if len(quarterly) >= 5:
        css5 = [r.get('capitalStock') for r in quarterly[-5:]]
        if all(x is not None and x > 0 for x in css5):
            max_adj_change = max(
                abs(css5[i] / css5[i - 1] - 1) for i in range(1, 5)
            )
            # round 到 6 位避免浮點誤差(如 1200/1000-1 = 0.19999...)
            result['capitalChangedTTM'] = (round(max_adj_change, 6) >= 0.20)

    return result


# ============================================================
# 【v3.5.4-r3】build lifecycle helpers · FI-9
# ============================================================

_OPCAP_SCHEMA_KEY = 'opCapitalIneligible'


def _row_has_opcap_schema(row: dict) -> bool:
    """判定 row 是否為 v3.5.4 schema(有本業股本獲利率欄位群)。"""
    return _OPCAP_SCHEMA_KEY in row


def _quarter_sort_key(q_label: Optional[str]) -> Tuple[int, int]:
    """
    將 'YYYY/NQ' 轉為可排序的 tuple(供 tie-break 用)。
    如 '2026/2Q' → (2026, 2);None 或格式錯誤 → (0, 0)。
    """
    if not q_label or '/' not in q_label:
        return (0, 0)
    try:
        year_s, quarter_s = q_label.split('/', 1)
        year = int(year_s)
        quarter_num = int(''.join(c for c in quarter_s if c.isdigit()) or '0')
        return (year, quarter_num)
    except (ValueError, AttributeError):
        return (0, 0)


def _deterministic_modal_quarter(counts) -> Optional[str]:
    """
    FI-9(e) · 邊界 Q:deterministic modal quarter。

    · 不使用 Counter.most_common(1) 的 encounter-order tie behavior
    · 明文 tie-break:count 相同時,選 _quarter_sort_key 最大者(最新季度)
    · 完全與 rows 排序無關(僅取決於 count 與 quarter 字面值)

    Args:
        counts: dict-like mapping quarter_label -> count(通常是 Counter)
    """
    if not counts:
        return None
    items = list(counts.items())
    # 主鍵:count(大→前);同票 tie-break:_quarter_sort_key(新→前)
    items.sort(key=lambda kv: (kv[1], _quarter_sort_key(kv[0])), reverse=True)
    return items[0][0]


def determine_reference_quarter(
    rows: List[dict],
    existing_reference_quarter: Optional[str],
    is_full_build: bool,
    coverage_threshold: float = 0.80,
) -> Tuple[Optional[str], Optional[str]]:
    """
    FI-9(c) · 邊界 P:決定百分位計算所用的 reference_quarter。

    r3 修正(相對 r2):
      · full build 也讀 existing_reference_quarter(r2 固定傳 None)
      · full build 未達 80% coverage + existing 仍存在 eligible rows
        → 維持 existing + warning(不 drift 到弱訊號 modal)
      · modal fallback 使用 deterministic tie-break(FI-9(e))

    決策邏輯(依序):
      · partial build(is_full_build=False):
          · existing 有值 → 沿用,無 warning(FI-9(b))
          · 無 existing(首次 partial · 極罕見)→ deterministic modal + warning

      · full build(is_full_build=True):
          1. 從最新季倒序找 eligible coverage >= threshold 的季度
             · 有 → 允許切季(切季 gate · 即使 existing 不同)
          2. 沒達標 + existing 仍存在於 eligible rows
             · → 維持 existing + warning(FI-9(c))
          3. 沒達標 + existing 不存在於 eligible rows(或 existing=None · 首次)
             · → deterministic modal + warning

    row order 無關性(FI-9(e) · 邊界 Q):
      · 所有邏輯只用 Counter 與 _deterministic_modal_quarter
      · 不使用 first()/Counter.most_common(1) 等 encounter-dependent API
    """
    from collections import Counter
    eligible_qs = [
        r.get('latestQuarter')
        for r in rows
        if _row_has_opcap_schema(r)
           and r.get('opCapitalIneligible') is None
           and r.get('latestQuarter')
    ]

    # 無 eligible row 極端情況
    if not eligible_qs:
        if existing_reference_quarter is not None:
            return (existing_reference_quarter,
                    f"no eligible rows to determine reference_quarter; "
                    f"keeping existing {existing_reference_quarter}")
        return (None,
                "no eligible rows and no existing reference_quarter")

    counter = Counter(eligible_qs)
    total = len(eligible_qs)
    eligible_qs_set = set(counter.keys())

    # partial mode:沿用既有 · 不切季(FI-9(b))
    if not is_full_build:
        if existing_reference_quarter is not None:
            return (existing_reference_quarter, None)
        # 首次 partial(無既有 meta)· deterministic modal
        modal_q = _deterministic_modal_quarter(counter)
        return (modal_q,
                f"partial build without existing reference_quarter; "
                f"fallback to deterministic modal {modal_q}")

    # full build 邏輯(r3 修正)· FI-9(c)
    # 步驟 1:從最新季倒序找 coverage >= threshold 的季度 → 切季 gate
    for q in sorted(counter.keys(), key=_quarter_sort_key, reverse=True):
        if counter[q] / total >= coverage_threshold:
            return (q, None)

    # 步驟 2:未達標 + existing 仍存在於 eligible rows → 維持 existing + warning
    if existing_reference_quarter is not None and existing_reference_quarter in eligible_qs_set:
        return (existing_reference_quarter,
                f"full build: no quarter reached {int(coverage_threshold*100)}% coverage; "
                f"keeping existing {existing_reference_quarter} "
                f"(present in {counter[existing_reference_quarter]}/{total} eligible rows)")

    # 步驟 3:未達標 + existing 不在 eligible(或首次)→ deterministic modal + warning
    modal_q = _deterministic_modal_quarter(counter)
    if existing_reference_quarter is not None:
        return (modal_q,
                f"full build: no quarter reached {int(coverage_threshold*100)}% coverage, "
                f"and existing {existing_reference_quarter} not in eligible rows; "
                f"fallback to deterministic modal {modal_q}")
    return (modal_q,
            f"full build (first time): no quarter reached {int(coverage_threshold*100)}% coverage; "
            f"fallback to deterministic modal {modal_q}")


def _is_stale(row: dict, reference_quarter: Optional[str]) -> bool:
    """
    FI-8 · 邊界 L:row.latestQuarter != reference_quarter → stale。
    r3:latestQuarter is None 或格式無效者,自然 != reference_quarter → stale。
    reference_quarter=None 時不做 stale 判定(呼叫端已擋)。
    """
    if reference_quarter is None:
        return False
    return row.get('latestQuarter') != reference_quarter


def compute_op_capital_percentiles(
    rows: List[dict],
    reference_quarter: Optional[str],
    is_full_build: bool,
) -> Dict[str, Any]:
    """
    百分位計算 + 資料新鮮度標記 · in-place 修改 rows。

    r3 修正(相對 r2):
      · partial_preserve_existing 分支:**完全不動任何既有 pct**
        (含 migrated row 的既有 pct · r2 錯把 migrated pct 清成 None)
        · 新 build 的 row 因 transform 產出時 pct 就是 None · 無需清空
        · 只重算 migrated row 的 stale flag(語意上仍需正確)
        · warning 文案改為誠實描述「保留一切 pct」不再有語意衝突
      · 新增分支:reference_quarter=None → 空 universe + warning(FI-9(f) · 邊界 R)

    Returns:
        {
            'reference_quarter':   str | None,
            'q_universe_size':     int,
            'ttm_universe_size':   int,
            'schema_status':       'all_migrated' | 'mixed' | 'none_migrated',
            'action':              'full_recompute' | 'partial_recompute' |
                                   'partial_preserve_existing' | 'no_reference_quarter',
            'warnings':            List[str],
        }

    row order 無關性:所有邏輯只用 row 屬性 filter/map,不依 rows list 順序。
    reference_quarter 由呼叫端(build.py)透過 determine_reference_quarter 傳入,
    本函式不決定 ref_q。
    """
    warnings: List[str] = []

    migrated_rows = [r for r in rows if _row_has_opcap_schema(r)]
    legacy_rows = [r for r in rows if not _row_has_opcap_schema(r)]

    if not migrated_rows and not legacy_rows:
        return {
            'reference_quarter': reference_quarter,
            'q_universe_size': 0,
            'ttm_universe_size': 0,
            'schema_status': 'none_migrated',
            'action': 'partial_preserve_existing',
            'warnings': ['no rows to process'],
        }

    if not legacy_rows:
        schema_status = 'all_migrated'
    elif not migrated_rows:
        schema_status = 'none_migrated'
    else:
        schema_status = 'mixed'

    # ------------------------------------------------------------
    # 分支 1(r3):partial + (mixed / none_migrated)
    # FI-9(a)+(d) · 邊界 O:完全不動任何既有 pct;只重算 stale flag
    # ------------------------------------------------------------
    if not is_full_build and schema_status != 'all_migrated':
        # r3:不清任何 pct(migrated row 的既有 pct 保留 · legacy row 完全不動)
        # 新 build 的 migrated row 因 transform 產出時 pct 就是 None,無需額外處理
        # 只重算 migrated row 的 stale flag(語意仍需正確)
        stale_updated = 0
        for r in migrated_rows:
            if r.get('opCapitalIneligible') is None:
                new_stale = _is_stale(r, reference_quarter) if reference_quarter is not None else False
                if r.get('opCapitalDataStale') != new_stale:
                    stale_updated += 1
                r['opCapitalDataStale'] = new_stale
        warnings.append(
            f"partial build with {schema_status} schema: preserved all existing percentiles "
            f"({len(migrated_rows)} migrated + {len(legacy_rows)} legacy rows). "
            f"Percentile values kept as-is (new build rows already have percentile=None from "
            f"transform layer). Stale flag re-evaluated for {stale_updated} migrated rows. "
            f"Run a full build to publish fresh percentiles for the entire universe."
        )
        return {
            'reference_quarter': reference_quarter,
            'q_universe_size': 0,
            'ttm_universe_size': 0,
            'schema_status': schema_status,
            'action': 'partial_preserve_existing',
            'warnings': warnings,
        }

    # ------------------------------------------------------------
    # 分支 2(r3 新增):reference_quarter=None
    # FI-9(f) · 邊界 R:不得跨季計算,清空 universe 不發布 pct
    # ------------------------------------------------------------
    if reference_quarter is None:
        for r in migrated_rows:
            r['opToCapitalQuarterPercentile'] = None
            r['opToCapitalTTMPercentile'] = None
            r['opCapitalDataStale'] = False
        action = 'full_recompute' if is_full_build else 'partial_recompute'
        warnings.append(
            "reference_quarter is None: cannot compute percentiles without a reference "
            "quarter (would risk mixing cohorts across quarters). Universe emptied to 0, "
            "no percentiles published."
        )
        return {
            'reference_quarter': None,
            'q_universe_size': 0,
            'ttm_universe_size': 0,
            'schema_status': schema_status,
            'action': 'no_reference_quarter',
            'warnings': warnings,
        }

    # ------------------------------------------------------------
    # 分支 3:full_recompute 或 partial_recompute(all_migrated)
    # 兩者 pct 計算邏輯一致(差別在 reference_quarter 來源)
    # ------------------------------------------------------------
    action = 'full_recompute' if is_full_build else 'partial_recompute'

    # 清空 migrated_rows 的 pct + stale flag(這些將被重新計算)
    for r in migrated_rows:
        r['opToCapitalQuarterPercentile'] = None
        r['opToCapitalTTMPercentile'] = None
        r['opCapitalDataStale'] = False

    # 標 stale:latestQuarter != reference_quarter → stale(含 None / invalid)
    for r in migrated_rows:
        if r.get('opCapitalIneligible') is None:
            r['opCapitalDataStale'] = _is_stale(r, reference_quarter)

    # 建 universe:eligible + non-stale + 對應欄位有值
    def _is_universe_member(r, field):
        return (r.get('opCapitalIneligible') is None
                and not r.get('opCapitalDataStale', False)
                and r.get(field) is not None)

    q_vals = [r['opToCapitalQuarter'] for r in migrated_rows
              if _is_universe_member(r, 'opToCapitalQuarter')]
    ttm_vals = [r['opToCapitalTTM'] for r in migrated_rows
                if _is_universe_member(r, 'opToCapitalTTM')]

    n_q = len(q_vals)
    n_t = len(ttm_vals)

    # CDF: count(v <= current) / N * 100(規格 D · 邊界 K)
    if n_q > 0:
        for r in migrated_rows:
            if _is_universe_member(r, 'opToCapitalQuarter'):
                v = r['opToCapitalQuarter']
                count = sum(1 for x in q_vals if x <= v)
                r['opToCapitalQuarterPercentile'] = round(count / n_q * 100, 2)

    if n_t > 0:
        for r in migrated_rows:
            if _is_universe_member(r, 'opToCapitalTTM'):
                v = r['opToCapitalTTM']
                count = sum(1 for x in ttm_vals if x <= v)
                r['opToCapitalTTMPercentile'] = round(count / n_t * 100, 2)

    return {
        'reference_quarter': reference_quarter,
        'q_universe_size': n_q,
        'ttm_universe_size': n_t,
        'schema_status': schema_status,
        'action': action,
        'warnings': warnings,
    }
