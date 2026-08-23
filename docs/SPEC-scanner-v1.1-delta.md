# Scanner SPEC v1.1 增量修訂(附加於 SPEC-scanner-v1)

**版本**:SPEC-scanner-v1.1 · 2026-08-22
**類型**:增量修訂(基於 GPT v1 交付實作的良性收斂)
**適用**:與 SPEC-scanner-v1 併用,以本文優先

---

## §14.3 URL 編碼規則(修訂,升為正式規範)

原文保留,新增第 5 條:

5. **【v1.1 升為正式規範】模板已隱含的 filter 值不重複寫入 URL**。若當前套用了 template 且某 range 值等於該 template 的預設 range,則該 range key 不寫入 URL。

實作範本(GPT v1 已通過驗證):

```javascript
function templateBaseRange(key) {
  const template = templates[state.template];
  return template?.ranges[key] || null;
}

// serializeState 內部
Object.entries(rangeDefs).forEach(([key, def]) => {
  const value = state.ranges[key];
  if (!sameRange(value, [def.min, def.max]) &&
      !sameRange(value, templateBaseRange(key) || [NaN, NaN])) {
    params.set(key, value.join(','));
  }
});
```

**效果**:套用「訂單池累積、認列跟上」模板時,URL 僅為 `?template=order-pile-up`,不冗餘寫出對應的 filter keys。使用者手動微調後,template 標記自動移除,實際 filter values 才寫入。

---

## §12.2 排序(修訂)

原文允許兩種做法(「無反應」或「文字字典序,不進 URL」)。**v1.1 收斂為單一標準做法**:

- 所有 8 欄位皆可排序
- 名稱、產業、股號:預設按文字字典序 asc(使用 `String.localeCompare('zh-Hant')`)
- 其他數值欄位:預設 desc
- **排序狀態一律寫入 URL**(`sort=name,asc` / `sort=score,desc` 等)
- 例外:預設狀態 `sort=score,desc` **不寫入 URL**,以保持精簡

實作範本:

```javascript
document.getElementById('resultsHead').addEventListener('click', event => {
  const button = event.target.closest('[data-sort]');
  if (!button) return;
  const key = button.dataset.sort;
  if (state.sortKey === key) state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
  else {
    state.sortKey = key;
    state.sortDir = ['name', 'industry', 'id'].includes(key) ? 'asc' : 'desc';
  }
  state.page = 1;
  renderAll();
  syncUrl('push');
});
```

排序比較函式必須處理 null:

```javascript
rows.sort((a, b) => {
  let first = a[state.sortKey], second = b[state.sortKey];
  if (first == null) return 1;   // null 永遠排到最後
  if (second == null) return -1;
  const direction = state.sortDir === 'asc' ? 1 : -1;
  if (typeof first === 'string') return first.localeCompare(second, 'zh-Hant') * direction;
  return (first - second) * direction;
});
```

---

## §17 Tooltip 對照表(修訂,同步 v1 已實作內容)

「命中統計」tooltip 補正實作行為的描述:

原文:「符合當前所有條件的股票數量。若啟用合約負債相關條件,分母排除無合約負債的股票。」

**v1 已實作**分母的智能調整,對應命中率分母也從全市場 20 檔調整為「有 CL 的 10 檔」。此 tooltip 描述正確,無需修訂。

---

## §21 檢查清單(v1.1 補入)

原有全部保留,新增:

- [ ] URL 序列化實作 `templateBaseRange` 邏輯,套用模板時不重複寫出 filter keys
- [ ] 文字欄位(股號/名稱/產業)可排序、使用 `localeCompare('zh-Hant')`、預設 asc
- [ ] 排序 `sort` 值寫入 URL,但預設值 `score,desc` 不寫入
- [ ] 排序比較函式正確處理 null(null 排到最後)

---

**v1.1 修訂結束**
本文為 SPEC-scanner-v1 的增量修訂,原 v1 全部條目除本文明述修訂者外一律保留。
下一版全新 SPEC 產出時應併入本文為 SPEC-scanner-v2 的 baseline。
