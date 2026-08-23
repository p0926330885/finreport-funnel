# 財報轉化漏斗 · 自動判讀引擎 v3 規格書(LOCKED SPEC)

**版本**:SPEC-insights-v3.1 · 2026-08-23 (LOCKED)
**類型**:業務語意 + 判讀引擎重構
**前置依賴**:SPEC v2.2(前端契約)、SPEC-pipeline-v1(資料流)
**產出對象**:`pipeline/transform.py` 內 `insights_v3()` 引擎、`stock.html` 名詞全域替換
**核心變動**:
1. 名詞統一:「落地率」→「毛利轉化率」(定義不變:OP/GP × 100%)
2. 判讀語意基座統一為「做 100 元生意 → 賺 X 元毛利 → 轉化 Y 元營益」
3. 引入 11 種商業模式動態診斷,依優先序命中
4. 未命中時 fallback 到基礎「做 100 元生意」常規拆解
5. `hasCL === false` 股票隱藏 CL 相關 tab

---

## §0. 給接手 AI(或未來 v4 SPEC 作者)的執行指令

1. 本 SPEC v3 **完全取代** v2.2 §11「自動判讀」章節。
2. 判定規則和優先序寫在 §3,不可隨意調整順序 —— 排序反映**財務重大性**(業外扭曲最優先)。
3. 每種模式的觸發條件是**互斥設計**(見 §3.13),不會多命中。若真的多命中,取優先序最高者。
4. 白話句型的填空變數命名固定在 §4,`transform.py` 產生 JSON 時欄位名不可改。
5. Fallback 是**強制底線**(§5),絕對不能讓自動判讀區塊空白。
6. 前端只負責顯示,判讀邏輯 100% 在 `insights_v3()` 內產生,寫入 JSON 供前端 fetch。

---

## §1. 名詞統一 · 全域替換對照表

### 1.1 主要替換

| 舊名詞(v2.2)| 新名詞(v3)| 說明 |
|---|---|---|
| 落地率 | **毛利轉化率** | 主要名詞 |
| 落地率 X% | 毛利轉化率 X% | 帶數字場景 |
| 毛利落地 | 毛利轉化 | 動詞用法 |
| 費用吃掉毛利 | 費用吃掉毛利 | **保留** · 這已經很直覺 |
| 營益 / 營業利益 | 營業利益 | 統一稱「營業利益」不用縮寫 |
| 本業獲利 | 本業利益 或 營業利益 | 統一 |

### 1.2 替換範圍(必須全域一致)

- `stock.html`:
  - 漏斗區塊(段轉化圖左側 chip)
  - 品質儀表(欄位標題)
  - 自動判讀(所有句型)
  - 資料表欄位標題
  - Tooltip 說明
- `scanner.html`:欄位標題(若有)
- `pipeline/transform.py`:`insights_v3()` 內所有範本字串
- 使用者可見的**每一個字元**都要換,不留舊詞

### 1.3 業務公式不變

```
毛利率 gm = GP / Rev × 100%
毛利轉化率 = OP / GP × 100%   (原:落地率)
營益率 om = OP / Rev × 100%
淨利率 nm = NP / Rev × 100%
營業費用吃掉毛利比例 = 1 - OP/GP = 1 - 毛利轉化率
```

**驗證**:毛利率 × 毛利轉化率 = 營益率
- 例:6.1% × 61.3% = 3.74% ≈ 營益率 3.8% ✅

---

## §2. 判讀語意基座 · 「做 100 元生意」統一視角

### 2.1 核心心智模型

**所有判讀都圍繞這個句型骨架**:

```
每做 100 元生意 →
    賺進 {gm} 元毛利 →
       其中 {om} 元順利轉化為本業利益 →
       其餘 {gm - om} 元被營業費用吃掉
```

具體填數字:
```
每做 100 元生意,鴻海賺進 6.1 元毛利,
其中 3.8 元順利轉化為本業利益,
另外 2.3 元被營業費用吃掉。
```

### 2.2 為什麼用這個視角

- **一元化基準**:所有百分比都可以直接換算成「幾元」,大腦不用轉換
- **順序符合損益表**:營收 → 毛利 → 營益 → 淨利,由上而下自然
- **好壞方向一致**:每個階段的「留下越多越好」,不會正負向混雜
- **跨股比較**:不同規模的公司都用同一把尺(100 元)

### 2.3 語彙選擇原則

- **賺進**:用於毛利(有能力賺)
- **轉化為**:用於營業利益(從毛利到營益的過程)
- **留下**:用於淨利(最後真的入袋)
- **被吃掉**:用於費用(明顯負向)
- **貢獻 / 拖累**:用於業外(視方向)

---

## §3. 11 種商業模式動態診斷 · 優先序 + 判定規則

### 3.1 優先序邏輯

**排序原則**:財務重大性 + 是否需要**優先警示**使用者。

由高至低:
1. **業外扭曲類**(掩蓋真相):本業真相被業外美化/拖累,最需要提醒
2. **獲利雪崩類**(下修訊號):營收縮 + 費用失控 + 負向槓桿
3. **結構性變化類**(轉折):產品組合優化、定價權、營運槓桿釋放
4. **戰略投資類**(可解釋的短期陣痛):主動投資造成的營益承壓
5. **規模擴張類**(順風):薄利多銷、殺價搶單、常規成長

### 3.2 優先序總表

| # | 模式名 | 優先級 | 分類 |
|:-:|---|:-:|---|
| 1 | 本業弱靠業外美化 | ★★★★★ | 業外扭曲 |
| 2 | 本業強但業外拖累 | ★★★★★ | 業外扭曲 |
| 3 | 負向營運槓桿 | ★★★★ | 獲利雪崩 |
| 4 | 費用失控 | ★★★★ | 獲利雪崩 |
| 5 | 殺價搶單 / 做白工 | ★★★★ | 獲利雪崩 |
| 6 | 營運槓桿釋放 | ★★★ | 結構性 |
| 7 | 產品組合優化 | ★★★ | 結構性 |
| 8 | 強勁定價權 | ★★★ | 結構性 |
| 9 | 戰略投資期 | ★★ | 戰略 |
| 10 | 去蕪存菁 | ★★ | 戰略 |
| 11 | 規模效應放大 | ★ | 順風 |

**判定順序 = 上到下**。第一個命中即停止,不再檢查下面的模式。

### 3.3 模式 1 · 本業弱靠業外美化(最高優先)

**觸發條件**(必須全部滿足):
```python
om < 3.0                          # 本業營益率低於 3%
OR op_yoy < 0                     # 或本業 YoY 衰退
AND np_yoy > 30                   # 但淨利 YoY 暴增 >30%
AND abs(noi / np) > 0.5           # 業外淨額佔淨利比重 >50%
AND noi > 0                       # 業外為正貢獻(美化方向)
```

**動態句型範本**:
```
每做 100 元生意,{stock_name}本業僅留下 {om:.1f} 元營業利益,
但業外收益貢獻 {noi_ratio_of_np:.0f}% 淨利,獲利大幅仰賴業外挹注,
本業轉換動能仍顯疲弱。
```

**警示標籤**:`red`(紅框強調)

**範例**(某季):
- om = 2.5, np_yoy = +60%, noi/np = 0.65, noi = +180
- 輸出:「每做 100 元生意,XX 本業僅留下 2.5 元營業利益,但業外收益貢獻 65% 淨利,獲利大幅仰賴業外挹注,本業轉換動能仍顯疲弱。」

### 3.4 模式 2 · 本業強但業外拖累

**觸發條件**:
```python
op_yoy > 20                       # 本業 YoY 明顯成長
AND np_yoy < 0                    # 但淨利 YoY 衰退
AND abs(noi / np) > 0.25          # 業外佔淨利比重 >25%
AND noi < 0                       # 業外為負(拖累)
```

**動態句型**:
```
本業營運扎實,每做 100 元生意留下 {om:.1f} 元營業利益(年增 {op_yoy:+.1f}%),
但受業外淨損 {noi:.0f} 拖累,底線衰退。
可能為匯損、轉投資或一次性項目干擾,關注本業趨勢即可。
```

**警示標籤**:`amber`(黃框)

### 3.5 模式 3 · 負向營運槓桿

**觸發條件**:
```python
rev_yoy < 0                       # 營收衰退
AND op_yoy < rev_yoy - 10         # 營益衰退幅度遠大於營收
                                  # (例:營收 -5%,營益 -25%)
AND op_yoy < -15                  # 營益絕對衰退 >15%
```

**動態句型**:
```
營收年減 {rev_yoy_abs:.1f}%,但因廠房折舊與人事等固定費用剛性拖累,
每做 100 元生意留下的營業利益驟降至 {om:.1f} 元(年減 {op_yoy_abs:.1f}%),
觸發負向營運槓桿,獲利跌幅遠大於營收跌幅。
```

**警示標籤**:`red`

### 3.6 模式 4 · 費用失控

**觸發條件**:
```python
abs(gm_yoy) < 2                   # 毛利率年增變化 <2pp(穩定)
AND op_yoy < -15                  # 但營益 YoY 大跌 >15%
AND rev_yoy > 0                   # 且營收沒有衰退
```

**動態句型**:
```
毛利率穩定在 {gm:.1f}%,每做 100 元生意依然賺進 {gm:.1f} 元毛利,
但管理與行銷支出膨脹過快,毛利轉化率降至 {opgm:.1f}%
(較去年同期減少 {opgm_yoy_delta:+.1f}pp),本業獲利遭費用端侵蝕。
建議追蹤下季費用結構是否回穩。
```

**警示標籤**:`amber`

### 3.7 模式 5 · 殺價搶單 / 做白工

**觸發條件**:
```python
rev_yoy > 15                      # 營收擴張 >15%
AND gm_yoy_delta < -3             # 但毛利率 YoY 明顯下降 >3pp
                                  # (v3.1 微調: 原 -5pp,考慮台股電子代工低毛利)
AND op_yoy <= 0                   # 且營益不增反減
```

**動態句型**:
```
營收擴張 {rev_yoy:+.1f}%,但每做 100 元生意的毛利大幅滑落至 {gm:.1f} 元
(年減 {gm_yoy_delta:.1f}pp),扣除管銷後營業利益 {op_yoy_display},
面臨殺價搶單或成本暴漲壓力,量增價跌,實質獲利未受惠於營收成長。
```

**警示標籤**:`red`

其中 `op_yoy_display`:
- 若 op_yoy < -5:「反而衰退 X%」
- 若 -5 <= op_yoy <= 5:「幾乎未增」
- 若 op_yoy > 5:「僅微增 X%」

### 3.8 模式 6 · 營運槓桿釋放(正向)

**觸發條件**:
```python
rev_yoy > 5                       # 營收成長
AND op_yoy > rev_yoy + 10         # 營益 YoY 明顯 > 營收 YoY
AND op_yoy > 20                   # 營益絕對成長 >20%
```

**動態句型**:
```
每做 100 元生意賺進 {gm:.1f} 元毛利,能轉化 {om:.1f} 元為本業利益。
營收成長 {rev_yoy:+.1f}% 帶動固定費用稀釋,本業獲利爆發力
(YoY {op_yoy:+.1f}%)遠高於營收增幅,展現正向營運槓桿。
```

**警示標籤**:`mint`(綠框正面)

### 3.9 模式 7 · 產品組合優化

**觸發條件**:
```python
abs(rev_yoy) < 5                  # 營收幾乎持平
AND gm_yoy > 3                    # 毛利率 YoY 明顯提升 >3pp
AND om_yoy > 3                    # 營益率 YoY 明顯提升 >3pp
```

**動態句型**:
```
營收規模穩定({rev_yoy:+.1f}%),但每做 100 元生意的毛利
由去年同期 {gm_last:.1f} 元躍升至 {gm:.1f} 元
(+{gm_yoy_delta:.1f}pp),高毛利產品比重顯著拉升,獲利結構實質優化。
```

**警示標籤**:`mint`

### 3.10 模式 8 · 強勁定價權

**觸發條件**:
```python
rev_yoy > 10                      # 營收明顯成長
AND gp_yoy > rev_yoy + 3          # 毛利 YoY 明顯高於營收 YoY
AND gm_yoy > 1                    # 毛利率同時擴張
```

**動態句型**:
```
營收成長 {rev_yoy:+.1f}%,每做 100 元生意能賺進 {gm:.1f} 元毛利
(年增 {gm_yoy_delta:+.1f}pp)。公司具備轉嫁成本能力,
毛利空間隨規模同步擴大,展現定價權優勢。
```

**警示標籤**:`mint`

### 3.11 模式 9 · 戰略投資期

**觸發條件**:
```python
rev_yoy > 10                      # 營收雙位數成長
AND gp_yoy > 10                   # 毛利同步雙位數成長
AND op_yoy < gp_yoy - 15          # 但營益承壓(增幅遠不如毛利)
AND opgm_yoy_delta < -3           # 毛利轉化率年減 >3pp
```

**動態句型**:
```
每做 100 元生意能穩健賺取 {gm:.1f} 元毛利(年增 {gm_yoy_delta:+.1f}pp),
但前期研發與市場開拓費用吃掉較多毛利,
毛利轉化率暫降至 {opgm:.1f}%(年減 {opgm_yoy_delta_abs:.1f}pp),
屬於主動性擴張投資。關注後續費用效率是否兌現。
```

**警示標籤**:`amber`(中性,不是負面)

### 3.12 模式 10 · 去蕪存菁(主動縮量保利)

**觸發條件**:
```python
rev_yoy < -3                      # 營收明顯衰退
AND gp_yoy > 5                    # 但毛利逆勢成長
AND op_yoy > 5                    # 且營益也逆勢成長
```

**動態句型**:
```
營收規模雖縮水 {rev_yoy_abs:.1f}%,但淘汰低毛利訂單後,
每做 100 元生意的毛利提升至 {gm:.1f} 元(年增 {gm_yoy_delta:+.1f}pp),
留下的營業利益反增 {op_yoy:+.1f}%,整體體質更為扎實。
```

**警示標籤**:`mint`

### 3.13 模式 11 · 規模效應放大(薄利多銷)

**觸發條件**:
```python
rev_yoy > 15                      # 營收大幅擴張
AND -3 < gm_yoy < 1               # 毛利率持平或微降
AND op_yoy > 5                    # 但營益總額仍成長
AND np_yoy > 5                    # 淨利總額也成長
```

**動態句型**:
```
每做 100 元生意的毛利略降至 {gm:.1f} 元({gm_yoy_delta_signed}pp),
但營收規模大幅擴張 {rev_yoy:+.1f}% 帶動總獲利絕對金額持續墊高
(營益 YoY {op_yoy:+.1f}%),展現薄利多銷的規模效應。
```

**警示標籤**:`mint`

### 3.14 互斥性檢查(避免多命中)

**依優先序判定 = 天然互斥**。額外檢查:

| 模式 | 與哪個互斥 | 為什麼 |
|---|---|---|
| 1 vs 2 | NP 方向相反 | 業外方向必不同 |
| 3 vs 6 | Rev 方向相反 | 一個 Rev 負,一個 Rev 正 |
| 5 vs 8 | GM 變化方向相反 | 一個 GM 大降,一個 GM 擴張 |
| 5 vs 11 | GM 變化幅度不同 | 5 是 GM 大降,11 是持平 |
| 6 vs 9 | OP 方向相反 | 6 是 OP 爆發,9 是 OP 承壓 |

**若疑似邊界值同時命中**:優先序決定,取編號小者。

---

## §4. 判讀輸出 JSON Schema

### 4.1 每檔股票的 `insights` 欄位結構

```json
{
  "id": "2317",
  "quarterly": [ ... ],
  "monthly": [ ... ],
  "insights": [
    {
      "id": "s01",
      "kind": "primary",
      "tone": "red",
      "mode_code": "M01",
      "mode_name": "本業弱靠業外美化",
      "text": "每做 100 元生意,鴻海本業僅留下 2.5 元營業利益,但業外收益貢獻 65% 淨利,獲利大幅仰賴業外挹注,本業轉換動能仍顯疲弱。"
    },
    {
      "id": "s02",
      "kind": "supporting",
      "tone": "amber",
      "mode_code": null,
      "mode_name": null,
      "text": "毛利率 6.1%,QoQ -0.1pp。毛利偏低,檢視售價與原料成本壓力。"
    },
    {
      "id": "s03",
      "kind": "supporting",
      "tone": "mint",
      "mode_code": null,
      "mode_name": null,
      "text": "毛利轉化率 61.3%,營業費用吃掉 38.7% 毛利。費用控制良好,毛利多能轉化為營業利益。"
    },
    {
      "id": "s04",
      "kind": "supporting",
      "tone": "amber",
      "mode_code": null,
      "mode_name": null,
      "text": "業外淨損 -247.47 拖累底線,占營業利益 26.1%。追蹤匯損、轉投資是否為一次性。"
    }
  ]
}
```

### 4.2 欄位定義

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | string | 判讀序號 `s01`, `s02`, ... |
| `kind` | enum | `primary`(主判讀,商業模式)/ `supporting`(常規輔助)|
| `tone` | enum | `mint`(綠 正面)/ `amber`(黃 中性)/ `red`(紅 警示)|
| `mode_code` | string \| null | 若命中商業模式,`M01`~`M11`;否則 null |
| `mode_name` | string \| null | 商業模式名稱;fallback 為 null |
| `text` | string | 完整判讀文字 |

### 4.3 產出規則

每檔股票**永遠產出 4 條 insights**(對應前端 01/02/03/04 卡片):

- **s01 主判讀**:
  - 若命中商業模式 → `kind: "primary"`, `mode_code: "M01"~"M11"`
  - 若未命中 → `kind: "supporting"`, `mode_code: null`(fallback,見 §5)
- **s02-s04 輔助判讀**:一律 `kind: "supporting"`,涵蓋毛利率、毛利轉化率、業外三個面向

### 4.4 tone 決定 UI 顏色

前端根據 tone 決定卡片邊框色/序號色:
- `mint` → CSS var `--mint`
- `amber` → CSS var `--amber`
- `red` → CSS var `--coral`

---

## §5. Fallback · 未命中 11 種模式時的常規拆解

### 5.1 觸發條件

11 種模式**均未命中**。

### 5.2 Fallback 主判讀範本

```
本季營收 {rev:.2f} 億(YoY {rev_yoy:+.1f}%)。
每做 100 元生意賺進 {gm:.1f} 元毛利,
其中 {om:.1f} 元順利轉化為本業利益,
另外 {gm_minus_om:.1f} 元被營業費用吃掉。
```

**具體例**(某季 rev=1500, gm=45.2, om=32.5, rev_yoy=8.5):
```
本季營收 1,500.00 億(YoY +8.5%)。
每做 100 元生意賺進 45.2 元毛利,
其中 32.5 元順利轉化為本業利益,
另外 12.7 元被營業費用吃掉。
```

**tone**:`amber`(中性)
**mode_code**:null
**mode_name**:null

### 5.3 輔助判讀 s02-s04(fallback 時也產出)

**s02 · 毛利率評語**:

| gm 範圍 | 句型 | tone |
|---|---|---|
| gm >= 40 | 「毛利率 {gm:.1f}%,QoQ {gm_qoq:+.1f}pp。優質毛利水準,展現產品定價力。」| mint |
| 25 <= gm < 40 | 「毛利率 {gm:.1f}%,QoQ {gm_qoq:+.1f}pp。健康毛利區間,獲利基礎穩固。」| mint |
| 15 <= gm < 25 | 「毛利率 {gm:.1f}%,QoQ {gm_qoq:+.1f}pp。中等毛利,關注成本壓力。」| amber |
| gm < 15 | 「毛利率 {gm:.1f}%,QoQ {gm_qoq:+.1f}pp。毛利偏低,檢視售價與原料成本壓力。」| amber |

**s03 · 毛利轉化率評語**:

| opgm 範圍 | 句型 | tone |
|---|---|---|
| opgm >= 70 | 「毛利轉化率 {opgm:.1f}%,營業費用僅吃掉 {100-opgm:.1f}% 毛利。費用控制優異,毛利高效轉化為營業利益。」| mint |
| 50 <= opgm < 70 | 「毛利轉化率 {opgm:.1f}%,營業費用吃掉 {100-opgm:.1f}% 毛利。費用控制良好,毛利多能轉化為營業利益。」| mint |
| 30 <= opgm < 50 | 「毛利轉化率 {opgm:.1f}%,營業費用吃掉 {100-opgm:.1f}% 毛利。費用比例偏高,關注是否結構性擴張。」| amber |
| opgm < 30 | 「毛利轉化率 {opgm:.1f}%,營業費用吃掉 {100-opgm:.1f}% 毛利。費用侵蝕過重,本業轉化效率不佳。」| red |

**s04 · 業外評語**:

| noi/np 範圍 | 句型 | tone |
|---|---|---|
| abs(noi/np) < 0.05 | 「業外項目影響輕微,獲利穩定來自本業。」| mint |
| 0.05 <= abs(noi/np) < 0.15 | 「業外淨額 {noi:.2f},占淨利 {abs_noi_ratio:.1f}%,影響有限。」| amber |
| 0.15 <= abs(noi/np) < 0.30 | 「業外淨額 {noi:.2f}({noi_direction}),占淨利 {abs_noi_ratio:.1f}%,建議追蹤是否為經常性。」| amber |
| abs(noi/np) >= 0.30 | 「業外淨額 {noi:.2f}({noi_direction}),占淨利 {abs_noi_ratio:.1f}%,顯著扭曲底線,關注一次性因素。」| red |

其中 `noi_direction`:
- noi > 0 → `貢獻`
- noi < 0 → `拖累`

### 5.4 命中商業模式時 s02-s04 邏輯

命中 M01-M11 時,s01 使用商業模式範本,**s02-s04 仍用 §5.3 常規邏輯**產出,但避免和 s01 重複強調的面向。

**去重規則**:
- 若 s01 已強調業外(M01, M02),s04 改為簡短提醒
- 若 s01 已強調費用(M04, M09),s03 改為簡短提醒
- 若 s01 已強調毛利率變化(M05, M07, M08),s02 改為簡短提醒

避免同一個面向出現兩次相同分析,提高資訊密度。

---

## §6. hasCL 隱藏 CL Tab 契約

### 6.1 觸發條件

```javascript
stock.hasCL === false
```

### 6.2 隱藏範圍

- 季度資料表右上「合約負債明細」tab 完全隱藏(display: none)
- 若使用者透過 URL 或 state 觸發 view='cl',自動 fallback 到 view='std'
- Tab 導覽陣列不包含 CL 選項

### 6.3 CSS 契約

```css
/* 已存在: */
.growth-tab[data-growth="cl"][hidden] { display: none; }

/* 需追加: */
[data-view-tab="cl"][hidden] { display: none !important; }
```

### 6.4 JS 契約

`stock.html` 內 `bindControls()` 或 boot 之後:

```javascript
if (!stock.hasCL) {
  document.querySelectorAll('[data-view-tab="cl"], [data-growth="cl"]').forEach(el => {
    el.hidden = true;
  });
  // 防止 state 停在 cl
  if (state.view === 'cl') state.view = 'std';
  if (state.growth === 'cl') state.growth = 'rev';
}
```

---

## §7. 判讀引擎實作契約(給 pipeline transform.py)

### 7.1 函數簽名

```python
def insights_v3(quarterly: list[dict], stock_name: str) -> list[dict]:
    """
    產生自動判讀。
    
    Args:
        quarterly: 8 季資料,最後一筆為當季
        stock_name: 股票名稱(用於句型)
    
    Returns:
        4 筆 insight dict list,對應前端 s01-s04
    """
```

### 7.2 輸入資料契約

每筆 quarterly item 需含:
- `q`, `rev`, `gp`, `op`, `np`, `noi`, `eps`
- 計算衍生:`gm = gp/rev*100`, `om = op/rev*100`, `nm = np/rev*100`, `opgm = op/gp*100`

### 7.3 YoY 計算契約

需取 `quarterly[-5]`(去年同季)作 YoY 基準:

- `rev_yoy = (rev - rev_ly) / rev_ly × 100`
- `gp_yoy`, `op_yoy`, `np_yoy` 同理
- `gm_yoy_delta = gm_now - gm_ly`(**percentage points 相減,不是相除**)
- `opgm_yoy_delta = opgm_now - opgm_ly`

**特殊處理**:
- 若去年同季資料缺失 → 所有 YoY 為 null → 只跑不需 YoY 的模式判定 + fallback
- 若 rev_ly ≈ 0(< 0.01)→ YoY 顯示 `—`,避免除以 0
- 若 rev_ly < 0 → 罕見(通常小公司)→ YoY = null

### 7.4 模式判定流程

```python
def detect_mode(now: dict, yoy: dict) -> str | None:
    """
    依 §3 優先序判定商業模式.
    Returns: mode_code 'M01'~'M11' or None
    """
    # 若 YoY 缺失 → 跳過所有需要 YoY 的模式 (M01-M11 都需要)
    if yoy is None:
        return None
    
    if match_m01(now, yoy): return 'M01'
    if match_m02(now, yoy): return 'M02'
    if match_m03(now, yoy): return 'M03'
    ...
    if match_m11(now, yoy): return 'M11'
    return None
```

### 7.5 產出 4 條 insights 流程

```python
def insights_v3(quarterly, stock_name):
    now = quarterly[-1]
    ly = quarterly[-5] if len(quarterly) >= 5 else None
    yoy = compute_yoy(now, ly) if ly else None
    
    mode = detect_mode(now, yoy)
    
    if mode:
        s01 = build_mode_insight(mode, now, yoy, stock_name)
    else:
        s01 = build_fallback_insight(now, yoy, stock_name)  # §5.2
    
    # s02-s04 常規輔助 (帶去重)
    s02 = build_gm_insight(now, ly, dedup=(mode in ['M05','M07','M08']))
    s03 = build_opgm_insight(now, ly, dedup=(mode in ['M04','M09']))
    s04 = build_noi_insight(now, dedup=(mode in ['M01','M02']))
    
    return [s01, s02, s03, s04]
```

### 7.6 寫入 JSON

`pipeline/transform.py::build_detail()` 產出 stock JSON 時,呼叫 `insights_v3()`,結果寫入 `detail["insights"]`。

**現有 SPEC v2.2 的 `insights` 欄位被完全取代**。

---

## §8. 前端呈現契約

### 8.1 卡片結構(不變,SPEC v2.2 §11 沿用)

```html
<div class="insight-card" data-tone="{tone}">
  <span class="insight-num">01</span>
  <p class="insight-text">{text}</p>
</div>
```

### 8.2 tone → CSS 對應

```css
.insight-card[data-tone="mint"]  { border-left: 3px solid var(--mint); }
.insight-card[data-tone="amber"] { border-left: 3px solid var(--amber); }
.insight-card[data-tone="red"]   { border-left: 3px solid var(--coral); }

.insight-card[data-tone="mint"]  .insight-num { color: var(--mint); }
.insight-card[data-tone="amber"] .insight-num { color: var(--amber); }
.insight-card[data-tone="red"]   .insight-num { color: var(--coral); }
```

### 8.3 render 邏輯(stock.html)

```javascript
function renderInsights() {
  const container = document.getElementById('insightsPanel');
  container.innerHTML = stock.insights.map((ins, i) => `
    <div class="insight-card" data-tone="${ins.tone}">
      <span class="insight-num">${String(i + 1).padStart(2, '0')}</span>
      <p class="insight-text">${escapeHtml(ins.text)}</p>
    </div>
  `).join('');
}
```

**注意**:v2.2 原本前端可能有寫死判讀邏輯。v3 之後,**前端不再產生任何判讀語句,100% 依賴 fetch 的 JSON**。

### 8.4 Backward compat

若舊資料(v1 backfill 產出)沒有 `insights` 欄位 → 顯示「判讀生成中,請於下次 daily-build 後查看」。

---

## §9. 測試案例(給 transform.py 用的 fixture)

### 9.1 每種模式至少一個 fixture

| 模式 | 測試 stock | 情境描述 |
|---|---|---|
| M01 | 假 A | om=2.0, np_yoy=+80%, noi/np=0.7 |
| M02 | 假 B | op_yoy=+35%, np_yoy=-10%, noi/np=-0.4 |
| M03 | 假 C | rev_yoy=-8%, op_yoy=-30% |
| M04 | 假 D | gm 穩定 22%, op_yoy=-22%, rev_yoy=+3% |
| M05 | 假 E | rev_yoy=+25%, gm_yoy=-8pp, op_yoy=-5% |
| M06 | 假 F | rev_yoy=+10%, op_yoy=+35% |
| M07 | 假 G | rev_yoy=+1%, gm_yoy=+5pp, om_yoy=+5pp |
| M08 | 假 H | rev_yoy=+18%, gp_yoy=+25%, gm_yoy=+3pp |
| M09 | 假 I | rev_yoy=+30%, gp_yoy=+28%, op_yoy=+8% |
| M10 | 假 J | rev_yoy=-8%, gp_yoy=+10%, op_yoy=+15% |
| M11 | 假 K | rev_yoy=+20%, gm_yoy=-1pp, op_yoy=+12% |
| Fallback | 假 L | 所有 YoY 都在 ±3% 範圍,平淡 |

### 9.2 邊界案例

- **YoY 缺失**:quarterly 只有 3 季 → 全部走 fallback
- **rev_ly ≈ 0**:小公司剛從虧損轉盈 → YoY 顯示 `—`
- **np < 0**:當季虧損 → `abs(noi/np)` 的分母是負數 → 特殊處理
- **np = 0**:分母為 0 → 用 `abs(noi/(np + 0.01))` 避免

### 9.3 冒煙測試

```python
# 台積電 2026/2Q 預估
fixture_2330 = {
    'now': {'rev': 12703, 'gp': 8603, 'op': 7666, 'np': 7067, 'noi': -598, ...},
    'ly':  {'rev': 9337,  'gp': 5473, 'op': 4634, 'np': 3974, 'noi': -100, ...}
}
# 預期:M06 (營運槓桿釋放) 或 M08 (定價權)
# rev_yoy = +36%, op_yoy = +65%
# → M06 命中 (op_yoy > rev_yoy + 10)
```

---

## §10. 檢查清單(交付前)

### 10.1 SPEC 完整性

- [x] §1 名詞替換表完整(所有出現的地方)
- [x] §2 語意基座定義清楚
- [x] §3 11 種模式 + 優先序 + 觸發條件 + 句型
- [x] §4 JSON schema
- [x] §5 fallback 條件 + 4 種輔助判讀範本
- [x] §6 hasCL 隱藏契約
- [x] §7 引擎函數簽名 + 流程
- [x] §8 前端契約(tone / render)
- [x] §9 測試 fixture

### 10.2 業務邏輯自洽

- [x] 11 種模式互斥性檢查(§3.14)
- [x] 優先序反映財務重大性
- [x] Fallback 保證每檔股票都有 4 條 insights
- [x] tone 選擇符合直覺(mint=好, red=警示, amber=中性)

### 10.3 實作可行性

- [x] transform.py 修改範圍明確(只動 `insights_v2()` → 改為 `insights_v3()`)
- [x] stock.html 修改範圍明確(名詞全域替換 + CL tab 隱藏)
- [x] 不需改動 pipeline 其他部分(ingest, output, config 都不動)
- [x] Backward compat:舊資料無 insights 欄位時 fallback 顯示

### 10.4 部署後驗收(供 v4 SPEC 追蹤)

- [ ] 20 檔 backfill 後,每檔 JSON 都有 4 條 insights
- [ ] 前端顯示無「落地率」殘留
- [ ] 台積電 s01 應該命中 M06(營運槓桿釋放)或 M08(定價權)
- [ ] 鴻海 s01 若命中 M01(本業弱靠業外美化)→ 需要人工檢視是否正確
- [ ] 至少 3 檔股票 s01 命中不同模式(驗證引擎多樣性)

---

## §11. 仲裁優先序

```
§0(執行指令)
 > §1(名詞統一,全域生效)
 > §3(11 種模式定義是產品核心)
 > §5(fallback 是底線)
 > §4(JSON schema 是前後端契約)
 > §7(實作契約)
 > §8(前端契約)
 > §6(hasCL 隱藏)
 > §2 §9(語意基座 / 測試)
```

**若 v3 與 v2.2 衝突**:v3 為準(v2.2 §11 完全 supersede)。
**若 v3 與 SPEC-pipeline-v1 衝突**:pipeline 資料契約以 pipeline-v1 為準,v3 只影響 `insights` 欄位產生邏輯。

---

## §12. 未來 v4 議題(記錄,不動)

以下想法**先不做**,留給 v4:

- 判讀多語版本(英文)
- 使用者可訂閱「判讀告警」(如 M03/M05 命中通知)
- 商業模式趨勢圖(連續幾季命中同一模式 = 結構性趨勢)
- 產業橫比(半導體同業都命中 M08 = 產業景氣線索)

---

**規格書結束**
版本 SPEC-insights-v3 · 2026-08-23
預期實作時程:transform.py 更新 30 分,stock.html 名詞替換 15 分,測試 15 分
