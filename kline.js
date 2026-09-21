/* =========================================================
   v3.6 · 個股頁 K 線圖(TradingView Lightweight Charts v5)
   資料:
     data/prices/{id}/index.json   → { years:[...], first, last }
     data/prices/{id}/{year}.json  → [[date,o,h,l,c,vol張], ...]
     data/prices_recent.json       → { bars:{ id:[[...]...] } }(每日收盤後更新,補最新幾天)
   使用:stock.html 載入完個股後呼叫 initKline(stockId)
   ========================================================= */
(function () {
  'use strict';

  const UP = '#f23645';      // 紅漲(台股慣例)
  const DOWN = '#089981';    // 綠跌
  const MA_COLORS = ['#f5c542', '#c084fc', '#38bdf8', '#fb923c'];
  const MA_DEFAULT = [{ p: 10, on: true }, { p: 20, on: true }, { p: 60, on: true }, { p: 200, on: true }];
  const RANGES = { '3M': 63, '6M': 126, '1Y': 250, '3Y': 750, 'ALL': Infinity };
  const LS_MA = 'klineMA.v1';
  const LS_RANGE = 'klineRange.v1';
  const INITIAL_YEARS = 4;   // 先載 4 年(3 年視窗 + 200 日均線暖機),往左捲再自動補

  const store = {
    get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };

  function loadMaSettings() {
    const s = store.get(LS_MA, null);
    if (!Array.isArray(s) || s.length !== MA_DEFAULT.length) return MA_DEFAULT.map(x => ({ ...x }));
    return s.map((x, i) => ({
      p: Number.isInteger(x && x.p) && x.p >= 2 && x.p <= 400 ? x.p : MA_DEFAULT[i].p,
      on: !!(x && x.on),
    }));
  }

  async function getJson(url) {
    try {
      const r = await fetch(url, { cache: 'no-cache' });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  let recentPromise = null;
  function loadRecent() {
    if (!recentPromise) recentPromise = getJson('./data/prices_recent.json');
    return recentPromise;
  }

  function fmtNum(v, digits) {
    if (v == null || !isFinite(v)) return '—';
    return Number(v).toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function pxDigits(v) { return v >= 1000 ? 0 : v >= 100 ? 1 : 2; }
  function fmtPx(v) { return v == null ? '—' : fmtNum(v, pxDigits(v) === 0 ? 0 : 2); }

  function sma(bars, period) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < bars.length; i++) {
      sum += bars[i][4];
      if (i >= period) sum -= bars[i - period][4];
      if (i >= period - 1) out.push({ time: bars[i][0], value: +(sum / period).toFixed(4) });
    }
    return out;
  }

  window.initKline = async function initKline(stockId) {
    const card = document.getElementById('klineCard');
    const box = document.getElementById('klineChart');
    const msg = document.getElementById('klineMsg');
    if (!card || !box) return;
    const showMsg = (t) => { msg.textContent = t; msg.hidden = !t; };

    if (!window.LightweightCharts) { showMsg('K 線元件載入失敗(可能是網路擋住 CDN),請重新整理。'); return; }
    const LC = window.LightweightCharts;

    // ---------- 資料 ----------
    const idx = await getJson(`./data/prices/${stockId}/index.json`);
    const allYears = idx && Array.isArray(idx.years) ? idx.years.slice().sort((a, b) => a - b) : [];
    const loadedYears = new Set();
    const byDate = new Map();
    let bars = [];

    async function loadYears(years) {
      const todo = years.filter(y => !loadedYears.has(y));
      if (!todo.length) return false;
      const parts = await Promise.all(todo.map(y => getJson(`./data/prices/${stockId}/${y}.json`)));
      todo.forEach((y, i) => {
        loadedYears.add(y);
        (parts[i] || []).forEach(b => { if (Array.isArray(b) && b.length >= 6) byDate.set(b[0], b); });
      });
      return true;
    }
    function rebuildBars() {
      bars = Array.from(byDate.values()).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    }
    const olderYearsLeft = () => allYears.filter(y => !loadedYears.has(y));

    await loadYears(allYears.slice(-INITIAL_YEARS));
    const recent = await loadRecent();
    const recentBars = recent && recent.bars && recent.bars[stockId];
    if (Array.isArray(recentBars)) {
      // 官方每日資料補在歷史檔之後(同日期以官方為準)
      recentBars.forEach(b => { if (Array.isArray(b) && b.length >= 6) byDate.set(b[0], b); });
    }
    rebuildBars();

    if (!bars.length) {
      showMsg('這檔的 K 線資料還在建置中,下一輪資料更新後就會出現。');
      card.classList.add('kline-empty');
      return;
    }
    showMsg('');
    const sub = document.getElementById('klineSub');
    if (sub) sub.textContent = `日K · 紅漲綠跌 · 資料至 ${bars[bars.length - 1][0]}`;

    // ---------- 圖表 ----------
    const chart = LC.createChart(box, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#8a94a8',
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: 11,
        panes: { separatorColor: '#262c3d', separatorHoverColor: 'rgba(212,165,116,0.25)' },
        attributionLogo: true,
      },
      grid: { vertLines: { color: 'rgba(38,44,61,0.45)' }, horzLines: { color: 'rgba(38,44,61,0.45)' } },
      rightPriceScale: { borderColor: '#262c3d' },
      timeScale: { borderColor: '#262c3d', rightOffset: 4, minBarSpacing: 0.5 },
      crosshair: { mode: LC.CrosshairMode.Normal },
      localization: { locale: 'zh-TW', dateFormat: 'yyyy-MM-dd' },
    });

    const candle = chart.addSeries(LC.CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN, priceLineVisible: true,
    });
    const volume = chart.addSeries(LC.HistogramSeries, {
      priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false,
    }, 1);
    try {
      const panes = chart.panes();
      panes[0].setStretchFactor(3);
      if (panes[1]) panes[1].setStretchFactor(1);
    } catch (e) {}

    let maCfg = loadMaSettings();
    const maSeries = maCfg.map((_, i) => chart.addSeries(LC.LineSeries, {
      color: MA_COLORS[i], lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    }));
    let maData = [];

    function setAllData() {
      candle.setData(bars.map(b => ({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] })));
      volume.setData(bars.map((b, i) => {
        const prev = i > 0 ? bars[i - 1][4] : b[1];
        const up = b[4] > prev || (b[4] === prev && b[4] >= b[1]);
        return { time: b[0], value: b[5], color: up ? 'rgba(242,54,69,0.55)' : 'rgba(8,153,129,0.55)' };
      }));
      applyMa();
    }
    function applyMa() {
      maData = maCfg.map(m => (m.on ? sma(bars, m.p) : []));
      maSeries.forEach((s, i) => { s.setData(maData[i]); s.applyOptions({ visible: maCfg[i].on }); });
      renderMaControls();
      updateLegend(null);
    }

    // ---------- 圖例(十字線) ----------
    const legend = document.getElementById('klineLegend');
    const barIndex = new Map();
    function reindex() { barIndex.clear(); bars.forEach((b, i) => barIndex.set(b[0], i)); }
    function updateLegend(time) {
      if (!legend) return;
      const i = time != null && barIndex.has(time) ? barIndex.get(time) : bars.length - 1;
      const b = bars[i];
      if (!b) { legend.innerHTML = ''; return; }
      const prev = i > 0 ? bars[i - 1][4] : null;
      const chg = prev ? b[4] - prev : null;
      const pct = prev ? (chg / prev) * 100 : null;
      const cls = chg == null || chg === 0 ? 'flat' : chg > 0 ? 'up' : 'down';
      const sign = chg > 0 ? '+' : '';
      const maHtml = maCfg.map((m, k) => {
        if (!m.on) return '';
        const j = i - (m.p - 1);
        const pt = maData[k] && j >= 0 ? maData[k][j] : null;
        return `<span style="color:${MA_COLORS[k]}">MA${m.p} ${pt ? fmtPx(pt.value) : '—'}</span>`;
      }).join('');
      legend.innerHTML =
        `<div class="kl-row"><span class="kl-date">${b[0]}</span>` +
        `<span>開 <b class="${cls}">${fmtPx(b[1])}</b></span><span>高 <b class="${cls}">${fmtPx(b[2])}</b></span>` +
        `<span>低 <b class="${cls}">${fmtPx(b[3])}</b></span><span>收 <b class="${cls}">${fmtPx(b[4])}</b></span>` +
        (chg == null ? '' : `<span class="${cls}">${sign}${fmtPx(chg)} (${sign}${pct.toFixed(2)}%)</span>`) +
        `<span>量 <b>${fmtNum(b[5], 0)}</b> 張</span></div>` +
        `<div class="kl-row kl-ma">${maHtml}</div>`;
    }
    const pad2 = n => String(n).padStart(2, '0');
    const normTime = t => (t && typeof t === 'object' && 'year' in t) ? `${t.year}-${pad2(t.month)}-${pad2(t.day)}` : t;
    chart.subscribeCrosshairMove(param => updateLegend(param && param.time ? normTime(param.time) : null));

    // ---------- 均線設定 ----------
    const maBox = document.getElementById('klineMaControls');
    function renderMaControls() {
      if (!maBox || maBox.dataset.ready) {
        if (maBox) maCfg.forEach((m, i) => {
          const chip = maBox.querySelector(`[data-ma-i="${i}"]`);
          if (chip) chip.classList.toggle('off', !m.on);
        });
        return;
      }
      maBox.dataset.ready = '1';
      maBox.innerHTML = maCfg.map((m, i) => `
        <label class="kl-ma-chip ${m.on ? '' : 'off'}" data-ma-i="${i}" title="點色塊開關 · 數字可改天數(2–400)">
          <button type="button" class="kl-ma-toggle" data-ma-toggle="${i}" aria-label="顯示/隱藏均線 ${i + 1}" style="--c:${MA_COLORS[i]}"></button>
          <span>MA</span><input type="number" min="2" max="400" step="1" inputmode="numeric" value="${m.p}" data-ma-input="${i}" aria-label="均線 ${i + 1} 天數">
        </label>`).join('') +
        `<button type="button" class="kl-ma-reset" id="klineMaReset" title="恢復預設 10/20/60/200">重設</button>`;
      maBox.addEventListener('click', e => {
        const t = e.target.closest('[data-ma-toggle]');
        if (t) { e.preventDefault(); const i = +t.dataset.maToggle; maCfg[i].on = !maCfg[i].on; store.set(LS_MA, maCfg); applyMa(); return; }
        if (e.target.closest('#klineMaReset')) {
          maCfg = MA_DEFAULT.map(x => ({ ...x }));
          store.set(LS_MA, maCfg);
          maBox.querySelectorAll('[data-ma-input]').forEach((inp, i) => { inp.value = maCfg[i].p; });
          applyMa();
        }
      });
      maBox.addEventListener('change', e => {
        const inp = e.target.closest('[data-ma-input]');
        if (!inp) return;
        const i = +inp.dataset.maInput;
        let v = Math.round(Number(inp.value));
        if (!isFinite(v)) v = maCfg[i].p;
        v = Math.min(400, Math.max(2, v));
        inp.value = v;
        maCfg[i].p = v; maCfg[i].on = true;
        store.set(LS_MA, maCfg);
        applyMa();
      });
    }

    // ---------- 區間切換 ----------
    const rangeBox = document.getElementById('klineRange');
    let range = store.get(LS_RANGE, '1Y');
    if (!(range in RANGES)) range = '1Y';
    async function setRange(key) {
      range = key; store.set(LS_RANGE, key);
      if (rangeBox) rangeBox.querySelectorAll('[data-range]').forEach(b => b.classList.toggle('active', b.dataset.range === key));
      const need = RANGES[key];
      if (need === Infinity) {
        if (await loadYears(olderYearsLeft())) { rebuildBars(); reindex(); setAllData(); }
        chart.timeScale().fitContent();
        return;
      }
      // 視窗 + 最長均線暖機都要有資料
      const maxMa = Math.max(...maCfg.filter(m => m.on).map(m => m.p), 0);
      while (bars.length < need + maxMa && olderYearsLeft().length) {
        await loadYears(olderYearsLeft().slice(-2));
        rebuildBars(); reindex(); setAllData();
      }
      const n = bars.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - need), to: n + 3 });
    }
    if (rangeBox) rangeBox.addEventListener('click', e => {
      const b = e.target.closest('[data-range]');
      if (b) setRange(b.dataset.range);
    });

    // ---------- 往左捲到底 → 自動載入更早年份 ----------
    let loadingOlder = false;
    chart.timeScale().subscribeVisibleLogicalRangeChange(async lr => {
      if (!lr || loadingOlder || lr.from > 20 || !olderYearsLeft().length) return;
      loadingOlder = true;
      const before = bars.length;
      await loadYears(olderYearsLeft().slice(-2));
      rebuildBars(); reindex();
      const added = bars.length - before;
      const cur = chart.timeScale().getVisibleLogicalRange();
      setAllData();
      if (cur && added > 0) chart.timeScale().setVisibleLogicalRange({ from: cur.from + added, to: cur.to + added });
      loadingOlder = false;
    });

    reindex();
    setAllData();
    await setRange(range);
    card.classList.add('kline-ready');
  };
})();
