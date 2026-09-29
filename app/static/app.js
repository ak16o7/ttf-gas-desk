/* TTF Gas Desk v1 — front end. No build step, Plotly bundled locally. Same patterns as DE Power Desk. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const REFRESH_S = Math.max(60, parseInt(document.body.dataset.refresh, 10) || 300);
  const NF0 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 });
  const NF1 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const NF2 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  const NF3 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3, minimumFractionDigits: 3 });
  const MWH_PER_MMBTU = 0.29307107;
  const P_TH_TO_GBP_MWH = 1 / 100 / 0.0293071;
  const MWH_PER_T_API2 = 6000 * 4.1868 / 3600;
  const EF_GAS = 0.053942 * 3.412141, EF_COAL = 0.34056, GWH_PER_MCM = 10.57275; // gas: t/MWh Hs (HHV), coal: t/MWh NAR
  const MONTHS_DE = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
  const AREA_NAMES = { EU: 'EU', DE: 'Deutschland', NL: 'Niederlande', FR: 'Frankreich', IT: 'Italien', AT: 'Österreich', BE: 'Belgien', ES: 'Spanien', PL: 'Polen' };
  const HUB_NAMES = { TTF: 'TTF', THE: 'THE', NBP: 'NBP', JKM: 'JKM', HH: 'Henry Hub' };
  const ENDPOINTS = ['market', 'germany', 'storage', 'norway', 'lng', 'weather', 'global'];

  const store = {
    get(k, d) { try { const v = localStorage.getItem('gasdesk.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('gasdesk.' + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };
  const state = {
    data: {}, errors: {}, loading: false, timer: null,
    hub: store.get('hub', 'TTF'), unit: store.get('unit', 'eur'), normal: store.get('normal', 'n10'),
    stripKind: 'month', spreadSel: store.get('spreadSel', 'ttf_winsum'), stArea: store.get('stArea', 'EU'),
    basket: store.get('basket', 'eu'), params: store.get('params', null), consKind: store.get('consKind', 'total'),
  };

  // ---------- formatting ----------
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const fmt = (v, nf = NF2) => (isNum(v) ? nf.format(v) : '—');
  const sgn = (v, nf = NF2) => (isNum(v) ? (v > 0 ? '+' : v < 0 ? '−' : '±') + nf.format(Math.abs(v)) : '—');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const hhmm = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' });
  };
  const dm = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.` : '—');
  const dmy = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(2, 4)}` : '—');
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const alpha = (hex, a) => {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };
  const avg = (xs) => { const v = xs.filter(isNum); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };

  // ---------- units ----------
  const fx = () => (state.data.market && state.data.market.fx) || {};
  const unitLabel = () => (state.unit === 'usd' ? 'USD/MMBtu' : 'EUR/MWh');
  function toEur(sym, v) {
    if (!isNum(v)) return null;
    const { EURUSD: usd, EURGBP: gbp } = fx();
    if (sym === 'TTF' || sym === 'THE') return v;
    if (sym === 'NBP') return gbp ? v * P_TH_TO_GBP_MWH / gbp : null;
    if (sym === 'JKM' || sym === 'HH') return usd ? v / MWH_PER_MMBTU / usd : null;
    return null;
  }
  function conv(sym, v, unit = state.unit) {
    if (!isNum(v)) return null;
    if (unit === 'native') return v;
    if (unit === 'usd' && (sym === 'JKM' || sym === 'HH')) return v;
    const e = toEur(sym, v);
    if (unit === 'eur') return e;
    const usd = fx().EURUSD;
    return isNum(e) && usd ? e * usd * MWH_PER_MMBTU : null;
  }
  const eurToUsd = (v) => (isNum(v) && fx().EURUSD ? v * fx().EURUSD * MWH_PER_MMBTU : null);
  const priceNf = () => (state.unit === 'usd' ? NF3 : NF2);

  // ---------- small UI helpers ----------
  function setSig(id, { value, sub = '', foot = '', tag = null }) {
    const el = $(id);
    el.querySelector('[data-value]').innerHTML = value;
    el.querySelector('[data-sub]').innerHTML = sub;
    el.querySelector('[data-foot]').innerHTML = foot;
    const t = el.querySelector('[data-tag]');
    t.className = 'tag' + (tag ? ' ' + tag.cls : '');
    t.textContent = tag ? tag.text : '';
    t.classList.toggle('hidden', !tag);
  }
  // Direction heuristics for TTF: thresholds are desk rules of thumb, not a model.
  const dir = (v, thr, positiveIs) => {
    if (!isNum(v) || Math.abs(v) < thr) return null;
    const up = positiveIs === 'bull' ? v > 0 : v < 0;
    return up ? { cls: 'bull', text: 'bullish' } : { cls: 'bear', text: 'bearish' };
  };
  function chip(text, cls = '', title = '') {
    const ico = cls === 'ok' ? '✓' : cls === 'warn' ? '!' : cls === 'bad' ? '×' : '·';
    return `<span class="chip ${cls}" title="${esc(title)}"><span class="ico">${ico}</span>${esc(text)}</span>`;
  }
  const stateCls = (s) => (s === 'ok' ? 'ok' : s === 'stale' || s === 'not_configured' ? 'warn' : 'bad');
  function sourceChips(id, sources) {
    const el = $(id);
    if (!el) return;
    el.innerHTML = (sources || []).map((s) => chip(
      s.source + (s.state === 'stale' ? ' (alt)' : ''), stateCls(s.state),
      [s.as_of ? `Stand ${new Date(s.as_of).toLocaleString('de-DE', { timeZone: 'Europe/Berlin' })}` : '', s.error || ''].filter(Boolean).join(' · '),
    )).join('');
  }
  const SRC = { last: ['Live', 'live'], settle: ['Settle', 'live'], settle_prev: ['Settle D-1', ''], synth: ['Synth', ''] };
  const srcTag = (src, title = '') => (src ? `<span class="src ${SRC[src] ? SRC[src][1] : ''}" title="${esc(title)}">${esc(SRC[src] ? SRC[src][0] : src)}</span>` : '');
  const metric = (label, value, small = '') => `<div class="metric"><span>${esc(label)}</span><strong>${value}</strong><small>${small}</small></div>`;
  function errorBox(id, what, err) {
    const el = $(id);
    if (el) el.innerHTML = `<div class="err"><b>${esc(what)} nicht verfügbar.</b> ${esc(err || '')}</div>`;
  }

  // ---------- chart helpers ----------
  const PLOT_CFG = { displayModeBar: false, responsive: true };
  const narrow = () => window.innerWidth < 700;
  function baseLayout(extra = {}) {
    const grid = cssVar('--line-soft'), muted = cssVar('--muted');
    return Object.assign({
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: cssVar('--text-2'), size: 11, family: 'Inter, system-ui, sans-serif' },
      margin: { l: narrow() ? 44 : 56, r: 14, t: narrow() ? 62 : 34, b: 34 },
      hovermode: 'x unified',
      hoverlabel: { bgcolor: cssVar('--surface-2'), bordercolor: cssVar('--line'), font: { color: cssVar('--text') } },
      legend: { orientation: 'h', x: 0, y: 1.02, yanchor: 'bottom', font: { size: 11 } },
      xaxis: { gridcolor: grid, linecolor: grid, zeroline: false, color: muted, showspikes: true, spikemode: 'across', spikethickness: 1, spikecolor: cssVar('--line'), spikedash: 'solid' },
      yaxis: { gridcolor: grid, zerolinecolor: cssVar('--line'), color: muted, separatethousands: true },
      separators: ',.',
      barcornerradius: 3,
    }, extra);
  }
  const ax = (base, extra) => Object.assign({}, base, extra);
  function plot(id, traces, layout) {
    const el = $(id);
    if (!window.Plotly) { el.innerHTML = '<div class="empty">Plotly nicht geladen.</div>'; return; }
    if (!traces.some((t) => t.x && t.x.length)) {
      Plotly.purge(el);
      el.innerHTML = '<div class="empty">Keine Daten veröffentlicht.</div>';
      return;
    }
    if (el.querySelector('.empty')) el.innerHTML = '';
    Plotly.react(el, traces, layout, PLOT_CFG);
  }
  const lineTrace = (x, y, name, color, extra = {}) => Object.assign({
    type: 'scatter', mode: 'lines', x, y, name, line: { color, width: 2 }, connectgaps: false,
  }, extra);
  function signedBars(x, y, name, unit, yaxis = 'y2', colors = null, nf = ',.2f') {
    const pos = colors ? colors[0] : cssVar('--pos'), neg = colors ? colors[1] : cssVar('--neg');
    return {
      type: 'bar', x, y, name, yaxis, showlegend: false,
      marker: { color: y.map((v) => (isNum(v) && v < 0 ? neg : pos)) },
      hovertemplate: `%{y:+${nf}} ${unit}<extra>${name}</extra>`,
    };
  }
  const deMonthTicks = () => {
    const vals = [], text = [];
    for (let m = 0; m < 12; m++) { vals.push(`2001-${String(m + 1).padStart(2, '0')}-01`); text.push(MONTHS_DE[m]); }
    return { tickvals: vals, ticktext: text };
  };
  const doyDate = (i) => { const d = new Date(Date.UTC(2001, 0, 1 + i)); return d.toISOString().slice(0, 10); };

  // ---------- data access ----------
  const M = () => state.data.market;
  const curve = (sym) => ((M() && M().curves[sym]) || []);
  const today = () => (M() && M().today) || new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
  function front(sym, kind = 'month', n = 0) {
    const t = today();
    const rows = curve(sym).filter((r) => r.kind === kind && isNum(r.mark) && r.start > t);
    return rows[n] || null;
  }
  function season(sym, which, n = 0) {
    const t = today();
    const rows = curve(sym).filter((r) => r.kind === 'season' && r.key.startsWith(which) && isNum(r.mark) && r.start > t);
    return rows[n] || null;
  }
  const spread = (id) => ((M() && M().spreads) || []).find((s) => s.id === id);

  // ---------- params (switching / LNG) ----------
  const PARAM_INPUTS = { eta_gas: 'etaGas', eta_coal: 'etaCoal', hh_mult: 'hhMult', toll: 'toll', ship_nwe: 'shipNwe', ship_asia: 'shipAsia', regas_nwe: 'regas' };
  function initParams(defaults) {
    state.params = Object.assign({}, defaults, state.params || {});
    for (const [k, id] of Object.entries(PARAM_INPUTS)) {
      const el = $(id);
      if (!el) continue;
      el.value = state.params[k];
      el.oninput = () => {
        const v = parseFloat(el.value);
        if (!isNum(v)) return;
        state.params[k] = v;
        store.set('params', state.params);
        renderSwitch(); renderArb(); renderSignals();
      };
    }
  }

  // ================================================================= panels
  function renderCurve() {
    const m = M();
    if (!m) return;
    const sym = state.hub;
    const rows = curve(sym);
    const months = rows.filter((r) => r.kind === 'month' && isNum(r.mark)).slice(0, 36);
    const unit = unitLabel();
    const x = months.map((r) => r.label);
    const y = months.map((r) => conv(sym, r.mark));
    const y1w = months.map((r) => (isNum(r.chg_1w) ? conv(sym, r.mark - r.chg_1w) : null));
    const y1m = months.map((r) => (isNum(r.chg_1m) ? conv(sym, r.mark - r.chg_1m) : null));
    const dd = months.map((r) => (isNum(r.chg_d) ? conv(sym, r.chg_d) : null));
    const hf = state.unit === 'usd' ? ',.3f' : ',.2f';
    const traces = [
      lineTrace(x, y1m, 'vor 1 Monat', cssVar('--s3'), { hovertemplate: `%{y:${hf}}` }),
      lineTrace(x, y1w, 'vor 1 Woche', cssVar('--s2'), { hovertemplate: `%{y:${hf}}` }),
      lineTrace(x, y, 'Aktuell', cssVar('--s1'), { mode: 'lines+markers', marker: { size: 6, color: cssVar('--s1'), line: { color: cssVar('--surface'), width: 1.5 } }, line: { color: cssVar('--s1'), width: 2.5 }, hovertemplate: `%{y:${hf}}` }),
      signedBars(x, dd, 'Δ Tag', unit, 'y2', null, hf),
    ];
    const L = baseLayout();
    plot('chCurve', traces, baseLayout({
      xaxis: ax(L.xaxis, { type: 'category', anchor: 'y2', tickangle: 0, nticks: narrow() ? 6 : 18 }),
      yaxis: ax(L.yaxis, { domain: [0.34, 1], title: { text: unit, font: { size: 10 } }, tickformat: ',.0f' }),
      yaxis2: ax(L.yaxis, { domain: [0, 0.24], title: { text: 'Δ Tag', font: { size: 10 } }, tickformat: state.unit === 'usd' ? ',.2f' : ',.1f' }),
      bargap: 0.35,
    }));

    const pick = [['M1', front(sym, 'month', 0)], ['M2', front(sym, 'month', 1)], ['Q+1', front(sym, 'quarter', 0)],
      ['Winter', season(sym, 'WIN', 0)], ['Sommer', season(sym, 'SUM', 0)], ['Cal+1', front(sym, 'cal', 0)]];
    const nf = priceNf();
    $('mCurve').innerHTML = pick.filter(([, r]) => r).slice(0, 4).map(([lab, r]) => metric(
      `${HUB_NAMES[sym]} ${lab} · ${r.label}`, `${fmt(conv(sym, r.mark), nf)}<span class="u">${unit}</span>`,
      `Δ Tag ${sgn(conv(sym, r.chg_d), nf)} · Δ 1W ${sgn(conv(sym, r.chg_1w), nf)}`)).join('');
    renderCurveTable();
  }

  function renderCurveTable() {
    const sym = state.hub;
    const rows = curve(sym).filter((r) => r.kind === state.stripKind);
    const nf = priceNf();
    const native = M().units[sym];
    const showNative = !((state.unit === 'eur' && (sym === 'TTF' || sym === 'THE')) || (state.unit === 'usd' && (sym === 'JKM' || sym === 'HH')));
    $('curveNote').textContent = `${rows.length} Kontrakte · Werte in ${unitLabel()}${showNative ? ` (nativ ${native})` : ''} · FX EURUSD ${fmt(fx().EURUSD, NF3)}, EURGBP ${fmt(fx().EURGBP, NF3)}`;
    const head = `<thead><tr><th>Kontrakt</th><th class="num">Mark</th><th>Quelle</th><th class="num">Δ Tag</th><th class="num">Δ 1W</th><th class="num">Δ 1M</th><th class="num">Δ 3M</th>${showNative ? `<th class="num">Nativ</th>` : ''}<th class="num">Settle</th><th class="num">Vol.</th><th>Letzter Trade</th></tr></thead>`;
    const body = rows.map((r) => {
      const title = r.src === 'last' ? `Trade ${hhmm(r.last_time)}` : r.settle_date ? `Settlement ${dmy(r.settle_date)}` : '';
      return `<tr><td>${esc(r.label)}</td><td class="num"><b>${fmt(conv(sym, r.mark), nf)}</b></td><td>${srcTag(r.src, title)}</td>
        <td class="num">${sgn(conv(sym, r.chg_d), nf)}</td><td class="num">${sgn(conv(sym, r.chg_1w), nf)}</td><td class="num">${sgn(conv(sym, r.chg_1m), nf)}</td><td class="num">${sgn(conv(sym, r.chg_3m), nf)}</td>
        ${showNative ? `<td class="num dim">${fmt(r.mark, sym === 'HH' || sym === 'JKM' ? NF3 : NF2)}</td>` : ''}
        <td class="num dim">${fmt(conv(sym, r.settle), nf)} <span class="dim">${r.settle_date ? dm(r.settle_date) : ''}</span></td>
        <td class="num dim">${isNum(r.volume) ? NF0.format(r.volume) : '—'}</td><td class="dim">${r.last_time ? `${dm(new Date(r.last_time).toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' }))} ${hhmm(r.last_time)}` : '—'}</td></tr>`;
    }).join('');
    $('tCurve').innerHTML = head + `<tbody>${body || '<tr><td colspan="11" class="dim">Keine Kontrakte dieser Art gelistet.</td></tr>'}</tbody>`;
  }

  // Spread units follow the global toggle only for EUR/MWh-based spreads.
  function spreadUnit(s) {
    if (s.unit === 'EUR/MWh' && state.unit === 'usd') return ['USD/MMBtu', (v) => eurToUsd(v), NF3];
    return [s.unit, (v) => v, s.unit === '%' ? NF1 : s.unit === 'USD/MMBtu' ? NF3 : NF2];
  }
  function renderSpreads() {
    const m = M();
    if (!m) return;
    const groups = [];
    for (const s of m.spreads) {
      let g = groups.find((x) => x.name === s.group);
      if (!g) { g = { name: s.group, items: [] }; groups.push(g); }
      g.items.push(s);
    }
    if (!m.spreads.some((s) => s.id === state.spreadSel && !s.missing)) state.spreadSel = (m.spreads.find((s) => !s.missing) || {}).id;
    const head = '<thead><tr><th>Spread</th><th>Legs</th><th class="num">Wert</th><th>Einheit</th><th class="num">Δ Tag</th><th class="num">Δ 1W</th><th class="num">Δ 1M</th><th>1J-Spanne</th><th class="num">Perzentil</th></tr></thead>';
    let body = '';
    for (const g of groups) {
      body += `<tr class="group"><td colspan="9">${esc(g.name)}</td></tr>`;
      for (const s of g.items) {
        if (s.missing) { body += `<tr><td>${esc(s.name)}</td><td colspan="8" class="dim">Kontrakt nicht gelistet oder ohne Preis</td></tr>`; continue; }
        const [u, f, nf] = spreadUnit(s);
        const pos = isNum(s.value) && isNum(s.min_1y) && isNum(s.max_1y) && s.max_1y > s.min_1y ? Math.min(100, Math.max(0, (s.value - s.min_1y) / (s.max_1y - s.min_1y) * 100)) : null;
        const legs = s.legs.map((l) => srcTag(l.src, `${l.sym} ${l.label}`)).join(' ');
        const why = isNum(s.leg_gap_min) && s.leg_gap_min > 60 ? `Letzte Trades der Beine ${NF0.format(s.leg_gap_min)} min auseinander` : 'Mindestens ein Bein heute ohne Trade';
        const basis = s.basis === 'settle_prev' ? ` <span class="src" title="${esc(why)}: Bewertung auf Settlement-Basis D-1">Settle</span>` : '';
        body += `<tr class="click${s.id === state.spreadSel ? ' sel' : ''}" data-id="${esc(s.id)}" tabindex="0" title="${esc(s.note || '')}">
          <td>${esc(s.name)}</td><td class="dim">${esc(s.legs_label)} ${legs}</td>
          <td class="num"><b>${fmt(f(s.value), nf)}</b>${basis}</td><td class="dim">${esc(u)}</td>
          <td class="num">${sgn(f(s.chg_d), nf)}</td><td class="num">${sgn(f(s.chg_1w), nf)}</td><td class="num">${sgn(f(s.chg_1m), nf)}</td>
          <td><span class="rng" title="${fmt(f(s.min_1y), nf)} … ${fmt(f(s.max_1y), nf)}">${pos == null ? '' : `<i style="left:${pos.toFixed(1)}%"></i>`}</span> <span class="dim">${fmt(f(s.min_1y), nf)} … ${fmt(f(s.max_1y), nf)}</span></td>
          <td class="num">${isNum(s.pctile_1y) ? NF0.format(s.pctile_1y) + ' %' : '—'}</td></tr>`;
      }
    }
    $('tSpreads').innerHTML = head + `<tbody>${body}</tbody>`;
    $('tSpreads').querySelectorAll('tr.click').forEach((tr) => {
      const pickRow = () => { state.spreadSel = tr.dataset.id; store.set('spreadSel', state.spreadSel); renderSpreads(); };
      tr.addEventListener('click', pickRow);
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickRow(); } });
    });
    renderSpreadChart();
  }
  function renderSpreadChart() {
    const s = spread(state.spreadSel);
    if (!s || s.missing) { $('spreadTitle').textContent = '—'; plot('chSpread', [], baseLayout()); return; }
    const [u, f, nf] = spreadUnit(s);
    $('spreadTitle').innerHTML = `${esc(s.name)} <small>${esc(s.legs_label)} · ${esc(u)} · ${esc(s.note || '')}</small>`;
    const h = s.history || [];
    const x = h.map((p) => p[0]);
    const y = h.map((p) => f(p[1]));
    const hf = nf === NF3 ? ',.3f' : nf === NF1 ? ',.1f' : ',.2f';
    const traces = [lineTrace(x, y, s.name, cssVar('--s1'), { hovertemplate: `%{y:${hf}} ${u}<extra></extra>` })];
    const cur = f(s.value);
    if (isNum(cur)) {
      traces.push({ type: 'scatter', mode: 'markers+text', x: [today()], y: [cur], name: 'Aktuell', showlegend: false,
        marker: { size: 9, color: cssVar('--s1'), line: { color: cssVar('--surface'), width: 2 } },
        text: [fmt(cur, nf)], textposition: 'middle left', textfont: { color: cssVar('--text'), size: 11 }, hovertemplate: `Aktuell %{y:${hf}}<extra></extra>` });
    }
    const L = baseLayout();
    plot('chSpread', traces, baseLayout({
      showlegend: false, margin: { l: 56, r: 14, t: 10, b: 34 },
      xaxis: ax(L.xaxis, { type: 'date', tickformat: '%m/%y', hoverformat: '%d.%m.%Y' }),
      yaxis: ax(L.yaxis, { zeroline: true, tickformat: hf === ',.3f' ? ',.2f' : ',.1f', title: { text: u, font: { size: 10 } } }),
    }));
  }

  function renderSwitch() {
    const m = M();
    if (!m || !state.params) return;
    const { eta_gas: eg, eta_coal: ec } = state.params;
    const usd = fx().EURUSD;
    const rows = (m.switching || []).map((r) => {
      const api2th = isNum(r.api2) && usd ? r.api2 / usd / MWH_PER_T_API2 : null;
      const g = isNum(r.ttf) && isNum(r.eua) ? (r.ttf + r.eua * EF_GAS) / eg : null;
      const c = isNum(api2th) && isNum(r.eua) ? (api2th + r.eua * EF_COAL) / ec : null;
      const sw = isNum(c) && isNum(r.eua) ? c * eg - r.eua * EF_GAS : null;
      const css = isNum(r.power) && isNum(g) ? r.power - g : null;
      const cds = isNum(r.power) && isNum(c) ? r.power - c : null;
      return Object.assign({}, r, { api2th, g, c, sw, css, cds, gap: isNum(sw) && isNum(r.ttf) ? r.ttf - sw : null });
    });
    const x = rows.map((r) => r.label);
    const L = baseLayout();
    plot('chSwitch', [
      { type: 'bar', x, y: rows.map((r) => r.g), name: 'SRMC Gas', marker: { color: cssVar('--s1') }, hovertemplate: '%{y:,.1f} EUR/MWh' },
      { type: 'bar', x, y: rows.map((r) => r.c), name: 'SRMC Kohle', marker: { color: cssVar('--s2') }, hovertemplate: '%{y:,.1f} EUR/MWh' },
      { type: 'scatter', mode: 'markers', x, y: rows.map((r) => r.power), name: 'Strom DE Base', marker: { color: cssVar('--s4'), size: 11, symbol: 'line-ew-open', line: { width: 3, color: cssVar('--s4') } }, hovertemplate: '%{y:,.1f} EUR/MWh' },
    ], baseLayout({ barmode: 'group', bargap: 0.45, bargroupgap: 0.1, xaxis: ax(L.xaxis, { type: 'category' }), yaxis: ax(L.yaxis, { title: { text: 'EUR/MWh el', font: { size: 10 } }, tickformat: ',.0f' }) }));
    const head = '<thead><tr><th>Strip</th><th class="num">TTF</th><th class="num" title="API2 in USD/t, umgerechnet">API2 €/MWh<sub>th</sub></th><th class="num">EUA</th><th class="num">Strom Base</th><th class="num">SRMC Gas</th><th class="num">SRMC Kohle</th><th class="num">Clean Spark</th><th class="num">Clean Dark</th><th class="num">Switch-TTF</th></tr></thead>';
    const body = rows.map((r) => `<tr><td>${esc(r.tag)} <span class="dim">${esc(r.label)}</span></td><td class="num">${fmt(r.ttf)}</td>
      <td class="num" title="${esc(`API2 ${r.api2_label || ''}: ${fmt(r.api2)} USD/t`)}">${fmt(r.api2th)}</td>
      <td class="num" title="${esc(r.eua_label || '')}">${fmt(r.eua)}</td>
      <td class="num" title="${esc(`${r.power_label || ''} ${SRC[r.power_src] ? SRC[r.power_src][0] : ''}`)}">${fmt(r.power)}</td>
      <td class="num">${fmt(r.g, NF1)}</td><td class="num">${fmt(r.c, NF1)}</td>
      <td class="num"><b>${sgn(r.css, NF1)}</b></td><td class="num"><b>${sgn(r.cds, NF1)}</b></td>
      <td class="num" title="TTF − Switch-TTF: ${sgn(r.gap)}">${fmt(r.sw)}</td></tr>`).join('');
    $('tSwitch').innerHTML = head + `<tbody>${body || '<tr><td colspan="10" class="dim">Keine Daten</td></tr>'}</tbody><tfoot><tr><td colspan="10" class="dim">EUR/MWh<sub>el</sub>; TTF/API2 je MWh<sub>th</sub>, EUA in EUR/t. Clean Spark > Clean Dark ⇒ Gas vor Kohle in der Merit-Order. Ohne Transport, variable O&amp;M und Netzentgelte. * = aus Teilkontrakten zusammengesetzt.</td></tr></tfoot>`;
  }

  function arbRows() {
    const m = M();
    if (!m || !state.params) return [];
    const p = state.params;
    return (m.lng_arb || []).map((r) => {
      const ttf = eurToUsd(r.ttf);
      const nwe = isNum(ttf) ? ttf - p.regas_nwe - p.ship_nwe : null;
      const asia = isNum(r.jkm) ? r.jkm - p.ship_asia : null;
      const best = [nwe, asia].filter(isNum);
      const b = best.length ? Math.max(...best) : null;
      const feed = isNum(r.hh) ? p.hh_mult * r.hh : null;
      const varM = isNum(b) && isNum(feed) ? b - feed : null;
      return Object.assign({}, r, { ttfUsd: ttf, nwe, asia, feed, varM, fullM: isNum(varM) ? varM - p.toll : null,
        jt: isNum(r.jkm) && isNum(ttf) ? r.jkm - ttf : null, dest: isNum(asia) && isNum(nwe) ? (asia > nwe ? 'Asien' : 'Europa') : isNum(nwe) ? 'Europa' : '—' });
    });
  }
  function renderArb() {
    const rows = arbRows();
    if (!rows.length) return;
    const x = rows.map((r) => r.label);
    const L = baseLayout();
    plot('chArb', [
      lineTrace(x, rows.map((r) => r.nwe), 'Netback NWE', cssVar('--s1'), { mode: 'lines+markers', marker: { size: 7 }, hovertemplate: '%{y:,.2f} $' }),
      lineTrace(x, rows.map((r) => r.asia), 'Netback Asien', cssVar('--s2'), { mode: 'lines+markers', marker: { size: 7 }, hovertemplate: '%{y:,.2f} $' }),
      lineTrace(x, rows.map((r) => r.feed), `Feedgas ${NF0.format(state.params.hh_mult * 100)} % HH`, cssVar('--s4'), { mode: 'lines+markers', marker: { size: 7 }, hovertemplate: '%{y:,.2f} $' }),
    ], baseLayout({ xaxis: ax(L.xaxis, { type: 'category' }), yaxis: ax(L.yaxis, { title: { text: 'USD/MMBtu', font: { size: 10 } }, tickformat: ',.0f', rangemode: 'tozero' }) }));
    const diffFreight = state.params.ship_asia - state.params.ship_nwe - state.params.regas_nwe;
    const head = `<thead><tr><th>Monat</th><th class="num">TTF $</th><th class="num">JKM $</th><th class="num">JKM − TTF</th><th class="num">Netback NWE</th><th class="num">Netback Asien</th><th>Ziel</th><th class="num">Feedgas</th><th class="num">Marge var.</th><th class="num">Marge voll</th></tr></thead>`;
    const body = rows.map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${fmt(r.ttfUsd, NF3)}</td><td class="num">${fmt(r.jkm, NF3)} ${r.jkm_src ? srcTag(r.jkm_src) : ''}</td>
      <td class="num">${sgn(r.jt, NF3)}</td><td class="num">${fmt(r.nwe)}</td><td class="num">${fmt(r.asia)}</td><td>${esc(r.dest)}</td><td class="num">${fmt(r.feed)}</td>
      <td class="num"><b>${sgn(r.varM)}</b>${isNum(r.varM) && r.varM < 0 ? ' <span class="flag" title="Netback unter variablen Kosten">Cancel</span>' : ''}</td><td class="num">${sgn(r.fullM)}</td></tr>`).join('');
    $('tArb').innerHTML = head + `<tbody>${body}</tbody><tfoot><tr><td colspan="10" class="dim">Asien lohnt, wenn JKM − TTF > Fracht Asien − Fracht NWE − Regas = ${sgn(diffFreight)} $/MMBtu. JKM nur wo auf ICE gelistet.</td></tr></tfoot>`;
  }

  function renderStorage() {
    const d = state.data.storage;
    if (!d) return;
    sourceChips('srcStorage', d.sources);
    const areas = d.areas || {};
    const order = (d.order || []).filter((a) => areas[a] && areas[a].seasonal);
    if (!order.length) { errorBox('chStorage', 'Speicherdaten', (d.sources || []).map((s) => s.error).filter(Boolean).join(' · ')); return; }
    if (!areas[state.stArea]) state.stArea = order[0];
    const seg = $('stArea');
    seg.innerHTML = order.map((a) => `<button aria-pressed="${a === state.stArea}" data-v="${a}">${a}</button>`).join('');
    seg.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { state.stArea = b.dataset.v; store.set('stArea', state.stArea); renderStorage(); }));
    const s = areas[state.stArea];
    const S = s.seasonal;
    const X = Array.from({ length: 365 }, (_, i) => doyDate(i));
    const band = cssVar('--band-2'), muted = cssVar('--muted');
    const traces = [
      lineTrace(X, S.band.max, '5J-Max', 'rgba(0,0,0,0)', { line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }),
      lineTrace(X, S.band.min, `5J-Spanne ${S.band_years[0] || ''}–${S.band_years[S.band_years.length - 1] || ''}`, 'rgba(0,0,0,0)', { line: { width: 0 }, fill: 'tonexty', fillcolor: band, hoverinfo: 'skip' }),
      lineTrace(X, S.band.avg, '5J-Mittel', muted, { line: { color: muted, width: 1.5, dash: 'dot' }, hovertemplate: '%{y:.1f} %' }),
      lineTrace(X, S.last_year, String(S.year - 1), cssVar('--s2'), { hovertemplate: '%{y:.1f} %' }),
      lineTrace(X, S.current, String(S.year), cssVar('--s1'), { line: { color: cssVar('--s1'), width: 2.5 }, hovertemplate: '%{y:.2f} %' }),
    ];
    const L = baseLayout();
    plot('chStorage', traces, baseLayout({
      xaxis: ax(L.xaxis, Object.assign({ type: 'date', range: ['2001-01-01', '2001-12-31'], hoverformat: '%d.%m.' }, deMonthTicks())),
      yaxis: ax(L.yaxis, { range: [0, 102], ticksuffix: ' %', tickformat: ',.0f' }),
      shapes: [{ type: 'line', xref: 'x', yref: 'y', x0: '2001-10-01', x1: '2001-12-01', y0: s.target, y1: s.target, line: { color: cssVar('--text-2'), width: 2 } }],
      annotations: [{ x: '2001-11-01', y: s.target, yref: 'y', text: `Ziel ${NF0.format(s.target)} %`, showarrow: false, yshift: 10, font: { size: 10, color: cssVar('--text-2') } }],
    }));
    const tDec = (s.targets || []).find((t) => t.label === '1. Dez') || (s.targets || [])[0];
    $('mStorage').innerHTML = [
      metric(`Füllstand ${AREA_NAMES[state.stArea] || state.stArea}`, `${fmt(s.full)} %`, `${fmt(s.gis_twh, NF1)} von ${fmt(s.wgv_twh, NF1)} TWh · Gastag ${dmy(s.date)}${s.status === 'E' ? ' · geschätzt' : ''}`),
      metric('vs. 5-Jahres-Mittel', `${sgn(s.vs5y_pp, NF1)} pp`, `${sgn(s.vs5y_twh, NF1)} TWh · vs. Vorjahr ${sgn(s.vsly_pp, NF1)} pp`),
      metric('Netto-Einspeisung', `${sgn(s.net7, NF0)} GWh/d`, `Ø 7 T · D-1 ${sgn(s.net_d1, NF0)} · Ø 30 T ${sgn(s.net30, NF0)}`),
      tDec ? metric(`Bedarf bis ${tDec.label} (${NF0.format(s.target)} %)`, `${fmt(tDec.required_gwh_d, NF0)} GWh/d`, `Projektion ${fmt(tDec.projected_at_7d_pace, NF1)} % (7T-Tempo) · ${fmt(tDec.projected_at_5y_pace, NF1)} % (5J-Tempo)`) : metric('Ziel', '—', 'Einspeichersaison vorbei'),
    ].join('');
    const head = '<thead><tr><th>Gebiet</th><th>Gastag</th><th class="num">Füllstand</th><th class="num">TWh</th><th class="num">vs. 5J</th><th class="num">vs. VJ</th><th class="num">Netto D-1</th><th class="num">Netto Ø7T</th><th class="num">Bedarf → 1.11.</th><th class="num">Bedarf → 1.12.</th><th class="num">Proj. 1.12. (5J-Tempo)</th></tr></thead>';
    const body = order.map((a) => {
      const v = areas[a];
      const t1 = (v.targets || []).find((t) => t.label === '1. Nov'), t2 = (v.targets || []).find((t) => t.label === '1. Dez');
      return `<tr class="click${a === state.stArea ? ' sel' : ''}" data-a="${a}" tabindex="0"><td>${esc(AREA_NAMES[a] || a)}</td><td class="dim">${dm(v.date)}${v.status === 'E' ? ' <span class="src" title="GIE-Status E: Werte teils geschätzt, Revision möglich">E</span>' : ''}</td><td class="num"><b>${fmt(v.full)} %</b></td><td class="num">${fmt(v.gis_twh, NF1)}</td>
        <td class="num">${sgn(v.vs5y_pp, NF1)} pp</td><td class="num">${sgn(v.vsly_pp, NF1)} pp</td><td class="num">${sgn(v.net_d1, NF0)}</td><td class="num">${sgn(v.net7, NF0)}</td>
        <td class="num">${t1 ? fmt(t1.required_gwh_d, NF0) : '—'}</td><td class="num">${t2 ? fmt(t2.required_gwh_d, NF0) : '—'}</td><td class="num">${t2 ? fmt(t2.projected_at_5y_pace, NF1) + ' %' : '—'}</td></tr>`;
    }).join('');
    $('tStorage').innerHTML = head + `<tbody>${body}</tbody><tfoot><tr><td colspan="11" class="dim">Netto = Einspeisung − Ausspeisung in GWh/d. Bedarf = nötige Ø-Netto-Einspeisung bis Stichtag für ${NF0.format(s.target)} %.</td></tr></tfoot>`;
    $('tStorage').querySelectorAll('tr.click').forEach((tr) => tr.addEventListener('click', () => { state.stArea = tr.dataset.a; store.set('stArea', state.stArea); renderStorage(); }));
  }

  function renderNorway() {
    const d = state.data.norway;
    if (!d) return;
    sourceChips('srcNorway', d.sources);
    if (!d.days || !d.kpi || !d.kpi.day) { errorBox('chNorway', 'ENTSOG-Flüsse', (d.sources || []).map((s) => s.error).join(' ')); return; }
    const k = d.kpi;
    const idx = d.days.indexOf(d.complete_through);
    const x = d.days, tot = d.total;
    const xs = x.slice(0, idx + 1), fin = tot.slice(0, idx + 1);
    const L = baseLayout();
    plot('chNorway', [
      lineTrace(xs, fin, 'Gesamt', cssVar('--s1'), { hovertemplate: '%{y:,.0f} GWh/d' }),
    ], baseLayout({
      showlegend: false,
      xaxis: ax(L.xaxis, { type: 'date', tickformat: '%d.%m.', hoverformat: '%a %d.%m.' }),
      yaxis: ax(L.yaxis, { title: { text: 'GWh/d', font: { size: 10 } }, tickformat: ',.0f' }),
      shapes: isNum(k.avg30) ? [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: k.avg30, y1: k.avg30, line: { color: cssVar('--muted'), width: 1, dash: 'dot' } }] : [],
      annotations: isNum(k.avg30) ? [{ xref: 'paper', x: 0, y: k.avg30, text: 'Ø 30 T', showarrow: false, xanchor: 'left', yshift: 9, font: { size: 10, color: cssVar('--muted') } }] : [],
    }));
    $('mNorway').innerHTML = [
      metric(`Gastag ${dm(k.day)}`, `${fmt(k.d1, NF0)} GWh/d`, `≈ ${fmt(k.d1_mcm, NF0)} mcm/d`),
      metric('Ø 7 Tage', `${fmt(k.avg7, NF0)}`, `vs. Ø 30 T ${sgn(k.avg7_vs_30, NF0)}`),
      metric('Ø 30 Tage', `${fmt(k.avg30, NF0)}`, `≈ ${fmt(k.avg30_mcm, NF0)} mcm/d`),
      metric('Ø 30 T vs. Vorjahr', `${sgn(k.avg30_vs_ly_pct, NF1)} %`, isNum(k.avg30_ly) ? `VJ ${fmt(k.avg30_ly, NF0)} GWh/d` : 'keine VJ-Daten'),
    ].join('');
    const terms = Object.keys(d.terminals).sort((a, b) => (k.by_terminal_d1[b] || 0) - (k.by_terminal_d1[a] || 0));
    const rows = terms.map((t) => {
      const s = d.terminals[t].slice(0, idx + 1);
      const a7 = avg(s.slice(-7)), a30 = avg(s.slice(-30));
      const v = s[s.length - 1];
      return `<tr><td>${esc(t)}</td><td class="num"><b>${fmt(v, NF0)}</b></td><td class="num">${fmt(a7, NF0)}</td><td class="num">${fmt(a30, NF0)}</td><td class="num">${sgn(isNum(v) && isNum(a30) ? v - a30 : null, NF0)}</td></tr>`;
    }).join('');
    const pending = d.days.length - 1 - idx;
    $('tNorway').innerHTML = `<caption class="dim">${pending > 0 ? `${pending} jüngere${pending > 1 ? '' : 'r'} Gastag${pending > 1 ? 'e' : ''} noch unvollständig veröffentlicht – ausgeblendet.` : ''}</caption><thead><tr><th>Anlandepunkt</th><th class="num">D-1</th><th class="num">Ø 7 T</th><th class="num">Ø 30 T</th><th class="num">D-1 − Ø 30 T</th></tr></thead><tbody>${rows}</tbody><tfoot><tr><td colspan="5" class="dim">Norwegen je Anlandepunkt, GWh/d; mcm/d mit ENTSOG-Faktor 10,57275 GWh/mcm.</td></tr></tfoot>`;
    const routes = (d.routes || []).filter((r) => r.day);
    $('tRoutes').innerHTML = `<thead><tr><th>Weitere Importrouten</th><th class="num">D-1</th><th class="num">Ø 7 T</th><th class="num">Ø 30 T</th><th class="num">Ø 30 T VJ</th><th class="num">Δ VJ</th></tr></thead><tbody>${
      routes.map((r) => `<tr><td>${esc(r.name)}${r.day !== k.day ? ` <span class="dim">(${dm(r.day)})</span>` : ''}</td><td class="num"><b>${fmt(r.d1, NF0)}</b></td><td class="num">${fmt(r.avg7, NF0)}</td><td class="num">${fmt(r.avg30, NF0)}</td><td class="num">${fmt(r.avg30_ly, NF0)}</td><td class="num">${sgn(r.avg30_vs_ly, NF0)}</td></tr>`).join('')
    }</tbody><tfoot><tr><td colspan="6" class="dim">GWh/d am EU-Eintrittspunkt. UK → Kontinent: IUK + BBL Exporte minus Rückflüsse nach UK (negativ = UK importiert).</td></tr></tfoot>`;
    const umm = d.umm || [];
    const active = umm.filter((u) => u.active_now && isNum(u.unavailable) && u.unit === 'mcm/d');
    $('ummNote').textContent = umm.length ? `· letzte ${umm.length} Ereignisse aus dem Gassco-Feed, keine vollständige Liste${active.length ? ` · davon jetzt aktiv: −${NF1.format(active.reduce((a, u) => a + u.unavailable, 0))} mcm/d` : ''}` : '';
    const fdt = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(2, 4)} ${iso.slice(11, 16)}` : '—');
    $('tUmm').innerHTML = umm.length ? `<thead><tr><th>Anlage</th><th>Art</th><th>Beginn</th><th>Ende</th><th class="num">Nicht verfügbar</th><th class="num">Technisch</th><th>Grund</th><th>Veröffentlicht</th></tr></thead><tbody>${
      umm.map((u) => `<tr><td>${u.url ? `<a href="${esc(u.url)}" rel="noreferrer">${esc(u.asset || '—')}</a>` : esc(u.asset || '—')}${u.active_now ? ' <span class="src live">aktiv</span>' : ''}</td>
        <td>${esc(u.type === 'Unplanned' ? 'ungeplant' : u.type === 'Planned' ? 'geplant' : (u.type || '—'))}${u.status && u.status !== 'Active' ? ` <span class="dim">(${esc(u.status)})</span>` : ''}</td>
        <td class="dim">${fdt(u.start)}</td><td class="dim">${fdt(u.stop)}</td>
        <td class="num"><b>${fmt(u.unavailable, NF1)}</b> <span class="dim">${esc(u.unit || '')}</span></td><td class="num dim">${fmt(u.technical, NF1)}</td>
        <td class="dim">${esc(u.reason || '')}</td><td class="dim">${fdt(u.published)}</td></tr>`).join('')
    }</tbody>` : '<tbody><tr><td class="dim">Gassco-Feed nicht erreichbar.</td></tr></tbody>';
  }

  function renderGermany() {
    const d = state.data.germany;
    if (!d) return;
    sourceChips('srcGermany', d.sources);
    const pr = d.prices || [], c = d.consumption || {};
    const t = today(), tm = new Date(Date.parse(t + 'T12:00:00Z') + 86400000).toISOString().slice(0, 10);
    const pt = pr.find((p) => p.d === t), pn = pr.find((p) => p.d === tm);
    const lastFinal = [...pr].reverse().find((p) => p.status === 'endgültig');
    const theM1 = front('THE');
    const spotRef = pt || lastFinal;
    const k = c.kpi || {};
    $('mGermany').innerHTML = [
      metric(`THE-Gastagesindex ${pt ? 'heute' : ''}`, pt ? `${fmt(pt.index)}<span class="u">€/MWh</span>` : '—', pt ? `${esc(pt.status)} · Ausgleich ${fmt(pt.neg)}–${fmt(pt.pos)}` : ''),
      metric('Gastag morgen', pn ? `${fmt(pn.index)}<span class="u">€/MWh</span>` : '—', pn ? `${esc(pn.status)} (bisherige Day-Ahead-Trades)` : 'noch nicht veröffentlicht'),
      metric(`Spot − THE ${theM1 ? theM1.label : 'M1'}`, spotRef && theM1 ? `${sgn(spotRef.index - theM1.mark)}<span class="u">€/MWh</span>` : '—', spotRef ? `Spot ${dm(spotRef.d)} vs. Front-Monat (${theM1 ? (SRC[theM1.src] || [theM1.src])[0] : '—'})` : ''),
      metric('SLP heute (Prognose)', `${fmt(k.slp_today, NF0)}<span class="u">GWh</span>`, `Vorjahr ${fmt(k.slp_today_ly, NF0)} · morgen ${fmt(k.slp_tomorrow, NF0)}`),
      metric(`RLM ${k.rlm_day ? dm(k.rlm_day) : ''}`, `${fmt(k.rlm_d1, NF0)}<span class="u">GWh</span>`, `Ø 30 T vs. VJ ${sgn(k.rlm_30_vs_ly_pct, NF1)} %`),
      metric('Verbrauch lfd. Jahr', `${sgn(k.total_ytd_vs_ly_pct, NF1)} %`, isNum(k.total_ytd_twh) ? `${fmt(k.total_ytd_twh, NF0)} vs. ${fmt(k.total_ytd_ly_twh, NF0)} TWh (bis ${dm(k.rlm_day)})` : ''),
    ].join('');

    // spot chart: last 365 days, band = negative..positive balancing price
    const sp = pr.filter((p) => p.d >= new Date(Date.parse(t) - 365 * 86400000).toISOString().slice(0, 10));
    const xs = sp.map((p) => p.d);
    const L = baseLayout();
    plot('chSpot', [
      lineTrace(xs, sp.map((p) => p.pos), 'Ausgleich +', 'rgba(0,0,0,0)', { line: { width: 0 }, showlegend: false, hovertemplate: 'pos. %{y:,.2f}<extra></extra>' }),
      lineTrace(xs, sp.map((p) => p.neg), 'Ausgleichsenergie −/+', 'rgba(0,0,0,0)', { line: { width: 0 }, fill: 'tonexty', fillcolor: cssVar('--band-2'), hovertemplate: 'neg. %{y:,.2f}<extra></extra>' }),
      lineTrace(xs, sp.map((p) => p.index), 'THE-Gastagesindex', cssVar('--s1'), { hovertemplate: '%{y:,.2f} €/MWh<extra>Index</extra>' }),
    ], baseLayout({ xaxis: ax(L.xaxis, { type: 'date', tickformat: '%m/%y', hoverformat: '%a %d.%m.%y' }), yaxis: ax(L.yaxis, { title: { text: 'EUR/MWh', font: { size: 10 } }, tickformat: ',.0f' }) }));

    // consumption chart: 7-day moving average, current vs last year vs range of prior years
    const S = (c.series || {})[state.consKind];
    if (!S) { plot('chCons', [], baseLayout()); return; }
    const ma = (arr) => arr.map((_, i) => { const w = arr.slice(Math.max(0, i - 6), i + 1).filter(isNum); return w.length >= 5 ? w.reduce((a, b) => a + b, 0) / w.length : null; });
    const X = c.dates;
    plot('chCons', [
      lineTrace(X, ma(S.max), 'Max', 'rgba(0,0,0,0)', { line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }),
      lineTrace(X, ma(S.min), c.band_label || 'Spanne Vorjahre', 'rgba(0,0,0,0)', { line: { width: 0 }, fill: 'tonexty', fillcolor: cssVar('--band-2'), hoverinfo: 'skip' }),
      lineTrace(X, ma(S.last_year), 'Vorjahr', cssVar('--s2'), { hovertemplate: '%{y:,.0f} GWh/d' }),
      lineTrace(X, ma(S.current), 'Aktuell', cssVar('--s1'), { line: { color: cssVar('--s1'), width: 2.5 }, hovertemplate: '%{y:,.0f} GWh/d' }),
    ], baseLayout({ xaxis: ax(L.xaxis, { type: 'date', tickformat: '%m/%y', hoverformat: '%a %d.%m.%y' }), yaxis: ax(L.yaxis, { title: { text: 'GWh/d (Ø 7 T)', font: { size: 10 } }, tickformat: ',.0f', rangemode: 'tozero' }) }));
  }

  const ma7 = (pts) => pts.map((p, i) => [p[0], i >= 6 ? avg(pts.slice(i - 6, i + 1).map((q) => q[1])) : null]);
  function renderSendout() {
    const d = state.data.lng;
    if (!d) return;
    sourceChips('srcSendout', d.sources);
    const eu = (d.areas || {}).EU;
    if (!eu || !eu.series) { errorBox('chSendout', 'ALSI', (d.sources || []).map((s) => s.error).filter(Boolean).join(' · ')); return; }
    const cur = ma7(eu.series), ly = ma7(eu.ly_series || []);
    const L = baseLayout();
    plot('chSendout', [
      lineTrace(ly.map((p) => p[0]), ly.map((p) => p[1]), 'Vorjahr', cssVar('--s2'), { hovertemplate: '%{y:,.0f} GWh/d' }),
      lineTrace(cur.map((p) => p[0]), cur.map((p) => p[1]), 'Aktuell (Ø 7 T)', cssVar('--s1'), { hovertemplate: '%{y:,.0f} GWh/d' }),
    ], baseLayout({ xaxis: ax(L.xaxis, { type: 'date', tickformat: '%m/%y', hoverformat: '%d.%m.%Y' }), yaxis: ax(L.yaxis, { title: { text: 'GWh/d', font: { size: 10 } }, tickformat: ',.0f', rangemode: 'tozero' }) }));
    $('mSendout').innerHTML = [
      metric(`EU Gastag ${dm(eu.date)}`, `${fmt(eu.d1, NF0)} GWh/d`, `Ø 7 T ${fmt(eu.avg7, NF0)}`),
      metric('Ø 30 T vs. Vorjahr', `${sgn(eu.avg30_vs_ly_pct, NF1)} %`, `${fmt(eu.avg30, NF0)} vs. ${fmt(eu.avg30_ly, NF0)}`),
      metric('Tankinventar', `${fmt(eu.inv_pct, NF1)} %`, `${fmt(eu.inv_gwh, NF0)} GWh`),
      metric('Dekl. Send-out-Kapazität', `${fmt(eu.dtrs, NF0)}`, 'GWh/d (DTRS)'),
    ].join('');
    const rows = (d.order || []).filter((a) => a !== 'EU' && d.areas[a]).map((a) => {
      const v = d.areas[a];
      return `<tr><td>${esc(AREA_NAMES[a] || a)}</td><td class="num"><b>${fmt(v.d1, NF0)}</b></td><td class="num">${fmt(v.avg7, NF0)}</td><td class="num">${fmt(v.avg30, NF0)}</td><td class="num">${fmt(v.avg30_ly, NF0)}</td><td class="num">${sgn(v.avg30_vs_ly_pct, NF0)} %</td><td class="num">${fmt(v.inv_pct, NF0)} %</td></tr>`;
    }).join('');
    $('tSendout').innerHTML = `<thead><tr><th>Land</th><th class="num">D-1</th><th class="num">Ø 7 T</th><th class="num">Ø 30 T</th><th class="num">Ø 30 T VJ</th><th class="num">Δ VJ</th><th class="num">Tanks</th></tr></thead><tbody>${rows}</tbody>`;
  }

  function renderWeather() {
    const d = state.data.weather;
    if (!d) return;
    sourceChips('srcWeather', d.sources);
    const b = d[state.basket];
    if (!b) { errorBox('chWeather', 'Wetterdaten', (d.sources || []).map((s) => s.error).filter(Boolean).join(' · ')); return; }
    const n = state.normal, x = b.dates;
    const s1 = cssVar('--s1'), s2 = cssVar('--s2');
    const fc = x.map((dd) => dd >= b.today);
    const hddAnom = x.map((dd, i) => (fc[i] && isNum(b.best_hdd[i]) && isNum(b.normal_hdd[n][i]) ? b.best_hdd[i] - b.normal_hdd[n][i] : null));
    const nLabel = n === 'n10' ? 'Normal 2016–25' : 'Normal 1991–2020';
    const traces = [
      lineTrace(x, b.ens_gfs.p90, 'GEFS 90 %', 'rgba(0,0,0,0)', { line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }),
      lineTrace(x, b.ens_gfs.p10, 'GEFS 10–90 %', 'rgba(0,0,0,0)', { line: { width: 0 }, fill: 'tonexty', fillcolor: alpha(s2, 0.12), showlegend: false, hoverinfo: 'skip' }),
      lineTrace(x, b.ens_ecmwf.p90, 'ENS 90 %', 'rgba(0,0,0,0)', { line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }),
      lineTrace(x, b.ens_ecmwf.p10, 'ENS 10–90 %', 'rgba(0,0,0,0)', { line: { width: 0 }, fill: 'tonexty', fillcolor: alpha(s1, 0.16), showlegend: false, hoverinfo: 'skip' }),
      lineTrace(x, b.normal[n], nLabel, cssVar('--muted'), { line: { color: cssVar('--muted'), width: 1.5, dash: 'dot' }, hovertemplate: '%{y:.1f} °C' }),
      lineTrace(x, b.det_gfs, 'GFS det.', cssVar('--s4'), { line: { color: cssVar('--s4'), width: 1.25 }, hovertemplate: '%{y:.1f} °C' }),
      lineTrace(x, b.det_ecmwf, 'ECMWF det.', cssVar('--s3'), { line: { color: cssVar('--s3'), width: 1.25 }, hovertemplate: '%{y:.1f} °C' }),
      lineTrace(x, b.ens_gfs.mean, 'GEFS Mittel', s2, { hovertemplate: '%{y:.1f} °C' }),
      lineTrace(x, b.ens_ecmwf.mean, 'ECMWF-ENS Mittel', s1, { line: { color: s1, width: 2.5 }, hovertemplate: '%{y:.1f} °C' }),
      signedBars(x, hddAnom, 'HDD vs. Normal', 'HDD', 'y2', [cssVar('--bull'), cssVar('--bear')], ',.1f'),
    ];
    const L = baseLayout();
    plot('chWeather', traces, baseLayout({
      xaxis: ax(L.xaxis, { type: 'date', anchor: 'y2', tickformat: '%d.%m.', hoverformat: '%a %d.%m.' }),
      yaxis: ax(L.yaxis, { domain: [0.34, 1], title: { text: '°C', font: { size: 10 } }, tickformat: ',.0f' }),
      yaxis2: ax(L.yaxis, { domain: [0, 0.24], title: { text: 'Δ HDD', font: { size: 10 } }, tickformat: ',.1f' }),
      shapes: [{ type: 'line', xref: 'x', yref: 'paper', x0: b.today, x1: b.today, y0: 0, y1: 1, line: { color: cssVar('--line'), width: 1 } }],
      margin: { l: narrow() ? 44 : 56, r: 14, t: narrow() ? 96 : 34, b: 34 }, bargap: 0.25,
    }));
    const K = b.kpi || {};
    const w = (o, label) => metric(label, o && isNum(o[`anom_${n}`]) ? `${sgn(o[`anom_${n}`], NF1)} °C` : '—',
      o && isNum(o.t) ? `Ø ${fmt(o.t, NF1)} °C · HDD ${sgn(o[`hdd_anom_${n}`], NF0)} vs. Normal` : '');
    $('mWeather').innerHTML = [w(K.w0_4, 'T+0–4 vs. Normal'), w(K.w5_14, 'T+5–14 vs. Normal'), w(K.w15_34, 'T+15–34 (GEFS)'),
      metric('ECMWF − GFS det. T+5–14', `${sgn(K.ecmwf_minus_gfs_5_14, NF1)} °C`, 'negativ = ECMWF kälter')].join('');
    const wins = (b.countries[0] || { windows: [] }).windows.map((x2) => x2.label);
    const head = `<thead><tr><th>Land</th><th class="num">Gewicht</th>${wins.map((l) => `<th class="num">${esc(l)}</th>`).join('')}</tr></thead>`;
    const cell = (o) => (o && o.days ? `<td class="num"><b>${sgn(o[`anom_${n}`], NF1)} °C</b> <span class="dim">${sgn(o[`hdd_anom_${n}`], NF0)} HDD</span></td>` : '<td class="num dim">—</td>');
    const totW = b.countries.reduce((a, c) => a + c.weight, 0);
    const body = b.countries.map((c) => `<tr><td>${esc(c.name)}</td><td class="num dim">${NF0.format(c.weight / totW * 100)} %</td>${c.windows.map(cell).join('')}</tr>`).join('');
    $('tWeather').innerHTML = head + `<tbody>${body}</tbody><tfoot><tr><td colspan="${2 + wins.length}" class="dim">Anomalie vs. ${esc(nLabel)} (Ensemble-Mittel; T+0–14 ECMWF-ENS, danach GEFS). Positive HDD = kälter = mehr Heizbedarf.</td></tr></tfoot>`;
    const nm = d.normals || {};
    $('weatherMethod').innerHTML = `<p>Normale: ${esc(nm.source || '')}, Perioden ${esc(JSON.stringify(nm.periods || {}))}, gebaut ${esc(nm.built || '')}. Stationsnormale, geglättet (21-Tage-Mittel), HDD-Normal aus täglichen HDD gemittelt.</p>
      <p>Gewichte (Land ≈ Haushalte+GHD-Gasverbrauch, Stadt ≈ Bevölkerungsanteil): ${b.weights.map((q) => `${esc(q.city)} ${NF1.format(q.w * 100)} %`).join(' · ')}</p>
      <p>Kalibrierung: GEFS und GFS werden je Stadt um ihren systematischen Versatz zu ECMWF bereinigt (GEFS: Mittel T+0–4 gegen ECMWF-ENS; GFS det.: 14-Tage-Rückblick gegen ECMWF det.). Abgezogene Versätze GEFS/GFS in °C: ${
        (d.calibration || []).filter((q) => b.weights.some((w) => w.city === q.city)).map((q) => `${esc(q.city)} ${sgn(q.gefs, NF1)}/${sgn(q.gfs, NF1)}`).join(' · ') || '—'}</p>`;
  }

  function renderWind() {
    const w = state.data.weather && state.data.weather.wind;
    if (!w || !w.dates) { plot('chWind', [], baseLayout()); return; }
    const L = baseLayout();
    plot('chWind', [
      lineTrace(w.dates, w.ecmwf, 'ECMWF', cssVar('--s1'), { hovertemplate: '%{y:.1f} m/s' }),
      lineTrace(w.dates, w.gfs, 'GFS', cssVar('--s2'), { hovertemplate: '%{y:.1f} m/s' }),
    ], baseLayout({ xaxis: ax(L.xaxis, { type: 'date', tickformat: '%d.%m.', hoverformat: '%a %d.%m.' }), yaxis: ax(L.yaxis, { title: { text: 'm/s', font: { size: 10 } }, tickformat: ',.0f', rangemode: 'tozero' }) }));
    const part = (arr, a, b) => avg(arr.slice(a, b));
    const e1 = part(w.ecmwf, 0, 7), e2 = part(w.ecmwf, 7, 15), g1 = part(w.gfs, 0, 7), g2 = part(w.gfs, 7, 16);
    $('mWind').innerHTML = [
      metric('Ø T+0–6 ECMWF', `${fmt(e1, NF1)} m/s`, `GFS ${fmt(g1, NF1)} m/s`),
      metric('Ø T+7–14 ECMWF', `${fmt(e2, NF1)} m/s`, `GFS ${fmt(g2, NF1)} m/s`),
      metric('Schwächster Tag', (() => { const i = w.ecmwf.reduce((b, v, j) => (isNum(v) && (b < 0 || v < w.ecmwf[b]) ? j : b), -1); return i >= 0 ? `${fmt(w.ecmwf[i], NF1)} m/s` : '—'; })(),
        (() => { const i = w.ecmwf.reduce((b, v, j) => (isNum(v) && (b < 0 || v < w.ecmwf[b]) ? j : b), -1); return i >= 0 ? `ECMWF, ${dm(w.dates[i])}` : ''; })()),
    ].join('');
  }

  function renderUs() {
    const g = state.data.global;
    const u = g && g.us_storage;
    if (!u) { if (g) errorBox('chUs', 'EIA-Speicher', (g.sources || []).filter((s) => s.source.startsWith('EIA')).map((s) => s.error).join(' ')); return; }
    const wk = (dstr) => {
      const d = new Date(dstr + 'T00:00:00Z');
      const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
      const day = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - day);
      const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
      return Math.ceil(((t - y0) / 86400000 + 1) / 7);
    };
    const weeks = Object.keys(u.band).map(Number).sort((a, b) => a - b);
    const bmin = weeks.map((w) => u.band[w].min), bmax = weeks.map((w) => u.band[w].max), bavg = weeks.map((w) => u.band[w].avg);
    const L = baseLayout();
    plot('chUs', [
      lineTrace(weeks, bmax, '5J-Max', 'rgba(0,0,0,0)', { line: { width: 0 }, showlegend: false, hoverinfo: 'skip' }),
      lineTrace(weeks, bmin, '5J-Spanne', 'rgba(0,0,0,0)', { line: { width: 0 }, fill: 'tonexty', fillcolor: cssVar('--band-2'), hoverinfo: 'skip' }),
      lineTrace(weeks, bavg, '5J-Mittel', cssVar('--muted'), { line: { color: cssVar('--muted'), width: 1.5, dash: 'dot' }, hovertemplate: '%{y:,.0f} Bcf' }),
      lineTrace(u.last_year.map((p) => wk(p[0])), u.last_year.map((p) => p[1]), 'Vorjahr', cssVar('--s2'), { hovertemplate: '%{y:,.0f} Bcf' }),
      lineTrace(u.current.map((p) => wk(p[0])), u.current.map((p) => p[1]), 'Aktuell', cssVar('--s1'), { line: { color: cssVar('--s1'), width: 2.5 }, hovertemplate: '%{y:,.0f} Bcf' }),
    ], baseLayout({ xaxis: ax(L.xaxis, { title: { text: 'Kalenderwoche', font: { size: 10 } }, dtick: 4, range: [1, 53] }), yaxis: ax(L.yaxis, { title: { text: 'Bcf', font: { size: 10 } }, tickformat: ',.0f' }) }));
    const k = u.kpi;
    $('mUs').innerHTML = [
      metric(`Woche bis ${dmy(k.date)}`, `${fmt(k.bcf, NF0)} Bcf`, `Δ Woche ${sgn(k.chg, NF0)} Bcf`),
      metric('vs. 5-Jahres-Mittel', `${sgn(k.vs5y, NF0)} Bcf`, `${sgn(k.vs5y_pct, NF1)} %`),
      metric('vs. Vorjahr', `${sgn(k.vsly, NF0)} Bcf`, ''),
    ].join('');
  }

  function renderGlobal() {
    const g = state.data.global;
    if (g) {
      sourceChips('srcGlobal', [...(g.sources || []), ...((M() && M().sources) || []).filter((s) => s.state !== 'ok')]);
      const t = g.fronts && g.fronts.TTF, h = g.fronts && g.fronts.HH;
      const L = baseLayout();
      plot('chGlobal', [
        t && t.series_usd ? lineTrace(t.series_usd.map((p) => p[0]), t.series_usd.map((p) => p[1]), 'TTF Front (USD/MMBtu)', cssVar('--s1'), { hovertemplate: '%{y:,.2f} $' }) : { x: [] },
        h ? lineTrace(h.series.map((p) => p[0]), h.series.map((p) => p[1]), 'Henry Hub Front', cssVar('--s2'), { hovertemplate: '%{y:,.2f} $' }) : { x: [] },
      ], baseLayout({ xaxis: ax(L.xaxis, { type: 'date', tickformat: '%m/%y', hoverformat: '%d.%m.%Y' }), yaxis: ax(L.yaxis, { title: { text: 'USD/MMBtu', font: { size: 10 } }, tickformat: ',.0f', rangemode: 'tozero' }) }));
    }
    const m = M();
    if (!m) return;
    const euaDec = curve('EUA').find((r) => r.key === `M-${today().slice(0, 4)}-12` && isNum(r.mark)) || front('EUA');
    const brent = curve('BRENT').find((r) => isNum(r.mark));
    const items = [
      ['TTF', front('TTF'), 'EUR/MWh'], ['THE', front('THE'), 'EUR/MWh'], ['NBP', front('NBP'), 'p/th'], ['JKM', front('JKM'), 'USD/MMBtu'],
      ['HH', front('HH'), 'USD/MMBtu'], ['BRENT', brent, 'USD/bbl'], ['EUA', euaDec, 'EUR/t'], ['API2', front('API2'), 'USD/t'],
      ['DEPWR', front('DEPWR'), 'EUR/MWh'],
    ];
    let body = items.filter(([, r]) => r).map(([sym, r, u]) => {
      const gas = ['TTF', 'THE', 'NBP', 'JKM', 'HH'].includes(sym);
      const nf = u === 'USD/MMBtu' ? NF3 : NF2;
      return `<tr><td>${esc(M().names[sym] || sym)}</td><td>${esc(r.label)}</td><td class="num"><b>${fmt(r.mark, nf)}</b></td><td class="dim">${esc(u)}</td>
        <td class="num">${gas ? fmt(conv(sym, r.mark, 'eur')) : '—'}</td><td class="num">${gas ? fmt(conv(sym, r.mark, 'usd'), NF3) : '—'}</td>
        <td class="num">${sgn(r.chg_d, nf)}</td><td class="num">${sgn(r.chg_1w, nf)}</td><td>${srcTag(r.src, r.src === 'last' ? `Trade ${hhmm(r.last_time)}` : r.settle_date ? dmy(r.settle_date) : '')}</td></tr>`;
    }).join('');
    const hs = g && g.hh_spot;
    if (hs && hs.length) {
      const [d0, v0] = hs[hs.length - 1], prev = hs.length > 1 ? hs[hs.length - 2][1] : null;
      body += `<tr><td>Henry Hub Spot (EIA)</td><td>${dm(d0)}</td><td class="num"><b>${fmt(v0, NF3)}</b></td><td class="dim">USD/MMBtu</td><td class="num">${fmt(conv('HH', v0, 'eur'))}</td><td class="num">${fmt(v0, NF3)}</td><td class="num">${sgn(isNum(prev) ? v0 - prev : null, NF3)}</td><td class="num">—</td><td><span class="src">EIA</span></td></tr>`;
    }
    const gp = state.data.germany && state.data.germany.prices;
    const spot = gp && ([...gp].reverse().find((p) => p.d <= today()));
    if (spot) {
      const prevS = gp[gp.indexOf(spot) - 1];
      body += `<tr><td>THE Spot (Gastagesindex)</td><td>${dm(spot.d)}</td><td class="num"><b>${fmt(spot.index)}</b></td><td class="dim">EUR/MWh</td><td class="num">${fmt(spot.index)}</td><td class="num">${fmt(eurToUsd(spot.index), NF3)}</td><td class="num">${sgn(prevS ? spot.index - prevS.index : null)}</td><td class="num">—</td><td><span class="src">${esc(spot.status === 'vorläufig' ? 'THE vorl.' : 'THE')}</span></td></tr>`;
    }
    $('tGlobal').innerHTML = `<thead><tr><th>Markt</th><th>Kontrakt</th><th class="num">Preis</th><th>Einheit</th><th class="num">≈ EUR/MWh</th><th class="num">≈ USD/MMBtu</th><th class="num">Δ Tag</th><th class="num">Δ 1W</th><th>Quelle</th></tr></thead><tbody>${body}</tbody>`;
  }

  // ---------- signal strip ----------
  function renderSignals() {
    const m = M();
    if (m) {
      const f = front('TTF'), win = season('TTF', 'WIN'), cal = front('TTF', 'cal');
      const nf = priceNf(), u = state.unit === 'usd' ? '$' : '€';
      setSig('sigFront', f ? {
        value: `${fmt(conv('TTF', f.mark), nf)}<small>${u} ${esc(f.label)}</small>`,
        sub: `Δ Tag <b>${sgn(conv('TTF', f.chg_d), nf)}</b> · Δ 1W <b>${sgn(conv('TTF', f.chg_1w), nf)}</b><br>${win ? `${esc(win.label)} <b>${fmt(conv('TTF', win.mark), nf)}</b>` : ''}${cal ? ` · ${esc(cal.label)} <b>${fmt(conv('TTF', cal.mark), nf)}</b>` : ''}${(() => {
          const gp = state.data.germany && state.data.germany.prices; const sp = gp && [...gp].reverse().find((p) => p.d <= today());
          return sp ? `<br>THE Spot ${dm(sp.d)} <b>${fmt(conv('THE', sp.index), nf)}</b>` : ''; })()}`,
        foot: `<span>${f.src === 'last' ? `Trade ${hhmm(f.last_time)}` : `Settlement ${dmy(f.settle_date)}`}</span><span>ICE verzögert</span>`,
      } : { value: '—', sub: 'ICE nicht erreichbar' });
      const ws = spread('ttf_winsum'), st = spread('ttf_storage');
      if (ws && !ws.missing) {
        const [, fS, nfS] = spreadUnit(ws);
        setSig('sigSeason', {
          value: `${sgn(fS(ws.value), nfS)}<small>${state.unit === 'usd' ? '$' : '€'}</small>`,
          sub: `${esc(ws.legs_label)} · Δ 1W <b>${sgn(fS(ws.chg_1w), nfS)}</b><br>1J-Perzentil <b>${isNum(ws.pctile_1y) ? NF0.format(ws.pctile_1y) + ' %' : '—'}</b>`,
          foot: st && !st.missing ? `<span>Einspeicher-Spread ${esc(st.legs_label)}: ${sgn(fS(st.value), nfS)}</span>` : '',
        });
      }
      const jt = spread('jkm_ttf');
      if (jt && !jt.missing && state.params) {
        const hurdle = state.params.ship_asia - state.params.ship_nwe - state.params.regas_nwe;
        const edge = isNum(jt.value) ? jt.value - hurdle : null;
        setSig('sigJkm', {
          value: `${sgn(jt.value, NF2)}<small>$/MMBtu</small>`,
          sub: `${esc(jt.legs_label)}<br>Fracht-Hürde <b>${sgn(hurdle)}</b> ⇒ ${isNum(edge) ? (edge > 0 ? '<b>Asien zieht Cargos</b>' : '<b>Europa konkurrenzfähig</b>') : '—'}`,
          foot: `<span>Δ 1W ${sgn(jt.chg_1w)}</span><span>1J-Perz. ${isNum(jt.pctile_1y) ? NF0.format(jt.pctile_1y) + ' %' : '—'}</span>${jt.basis === 'settle_prev' ? '<span>Settle-Basis</span>' : ''}`,
          tag: dir(edge, 0.25, 'bull'),
        });
      }
    }
    const s = state.data.storage && state.data.storage.areas && state.data.storage.areas.EU;
    if (s && isNum(s.full)) {
      setSig('sigStorage', {
        value: `${fmt(s.full)}<small>%</small>`,
        sub: `vs. 5J-Ø <b>${sgn(s.vs5y_pp, NF1)} pp</b> (${sgn(s.vs5y_twh, NF0)} TWh)<br>vs. Vorjahr <b>${sgn(s.vsly_pp, NF1)} pp</b>`,
        foot: `<span>Netto Ø7T ${sgn(s.net7, NF0)} GWh/d</span><span>Gastag ${dm(s.date)}</span>`,
        tag: dir(s.vs5y_pp, 5, 'bear'),
      });
    }
    const w = state.data.weather && state.data.weather.eu && state.data.weather.eu.kpi;
    if (w && w.w0_14 && isNum(w.w0_14.t)) {
      const n = state.normal, a = w.w0_14[`anom_${n}`];
      setSig('sigWeather', {
        value: `${sgn(a, NF1)}<small>°C</small>`,
        sub: `HDD <b>${sgn(w.w0_14[`hdd_anom_${n}`], NF0)}</b> vs. ${n === 'n10' ? 'Normal 16–25' : 'Normal 91–20'}<br>T+15–34 <b>${sgn(w.w15_34 && w.w15_34[`anom_${n}`], NF1)} °C</b> (GEFS)`,
        foot: `<span>ECMWF-ENS Mittel</span><span>ECMWF−GFS ${sgn(w.ecmwf_minus_gfs_5_14, NF1)} °C</span>`,
        tag: dir(a, 1.0, 'bear'),
      });
    }
    const nk = state.data.norway && state.data.norway.kpi;
    if (nk && isNum(nk.d1)) {
      const rel = isNum(nk.avg30) ? (nk.d1 / nk.avg30 - 1) * 100 : null;
      setSig('sigNorway', {
        value: `${fmt(nk.d1, NF0)}<small>GWh/d</small>`,
        sub: `≈ <b>${fmt(nk.d1_mcm, NF0)} mcm/d</b><br>vs. Ø 30 T <b>${sgn(nk.d1_vs_30, NF0)}</b> (${sgn(rel, NF1)} %)`,
        foot: `<span>Gastag ${dm(nk.day)}</span><span>ENTSOG</span>`,
        tag: dir(rel, 5, 'bear'),
      });
    }
  }

  // ---------- status ----------
  function renderStatus() {
    const all = ENDPOINTS.flatMap((k) => (state.data[k] && state.data[k].sources) || []);
    const failed = ENDPOINTS.filter((k) => state.errors[k]);
    const bad = all.filter((s) => s.state === 'error').length + failed.length;
    const warn = all.filter((s) => s.state === 'stale' || s.state === 'not_configured').length;
    const dot = $('statusDot');
    dot.className = 'dot ' + (state.loading ? '' : bad ? (bad >= all.length ? 'bad' : 'warn') : warn ? 'warn' : 'ok');
    const t = new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' });
    $('statusText').textContent = state.loading ? 'lädt…' : `Stand ${t} · ${all.length - bad - warn}/${all.length} Quellen ok${bad ? ` · ${bad} Fehler` : ''}${warn ? ` · ${warn} Hinweis` : ''}`;
    $('statusText').title = [...failed.map((k) => `${k}: ${state.errors[k]}`), ...all.filter((s) => s.state !== 'ok').map((s) => `${s.source}: ${s.error || s.state}`)].join('\n');
  }

  const RENDER = {
    market: () => { sourceChips('srcMarket', M().sources); if (!state.params) initParams(M().defaults); renderCurve(); renderSpreads(); renderSwitch(); renderArb(); renderGlobal(); renderGermany(); },
    storage: renderStorage, norway: renderNorway, lng: renderSendout, germany: () => { renderGermany(); renderGlobal(); },
    weather: () => { renderWeather(); renderWind(); },
    global: () => { renderGlobal(); renderUs(); },
  };
  const PANELS = { market: ['pCurve', 'pSpreads', 'pSwitch', 'pArb'], germany: ['pGermany'], storage: ['pStorage'], norway: ['pNorway'], lng: ['pSendout'], weather: ['pWeather', 'pWind'], global: ['pGlobal', 'pUs'] };
  const FAIL_TARGET = { market: ['chCurve', 'Marktdaten (ICE)'], germany: ['chSpot', 'THE-Daten'], storage: ['chStorage', 'Speicherdaten'], norway: ['chNorway', 'ENTSOG'], lng: ['chSendout', 'ALSI'], weather: ['chWeather', 'Wetterdaten'], global: ['chGlobal', 'Globale Daten'] };

  function safeRender(k) {
    try { RENDER[k](); } catch (e) { console.error(k, e); errorBox(FAIL_TARGET[k][0], FAIL_TARGET[k][1], 'Darstellungsfehler: ' + e.message); }
    try { renderSignals(); } catch (e) { console.error('signals', e); }
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    renderStatus();
    Object.values(PANELS).flat().forEach((id) => $(id).classList.add('loading'));
    await Promise.allSettled(ENDPOINTS.map(async (k) => {
      try {
        const r = await fetch(`/api/${k}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
        state.data[k] = j;
        delete state.errors[k];
        safeRender(k);
      } catch (e) {
        state.errors[k] = e.message;
        if (!state.data[k]) errorBox(FAIL_TARGET[k][0], FAIL_TARGET[k][1], e.message);
      } finally {
        PANELS[k].forEach((id) => $(id).classList.remove('loading'));
      }
    }));
    state.loading = false;
    renderStatus();
  }

  function rerenderAll() { ENDPOINTS.forEach((k) => { if (state.data[k]) safeRender(k); }); }

  function bindSeg(id, key, after) {
    const el = $(id);
    el.querySelectorAll('button').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.v === state[key]));
      b.addEventListener('click', () => {
        state[key] = b.dataset.v;
        store.set(key, state[key]);
        el.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        after();
      });
    });
  }

  function schedule() {
    clearInterval(state.timer);
    if ($('auto').checked) state.timer = setInterval(load, REFRESH_S * 1000);
  }

  // ---------- init ----------
  bindSeg('unit', 'unit', rerenderAll);
  bindSeg('normal', 'normal', () => { renderWeather(); renderSignals(); });
  bindSeg('hub', 'hub', renderCurve);
  bindSeg('stripKind', 'stripKind', renderCurveTable);
  bindSeg('basket', 'basket', renderWeather);
  bindSeg('consKind', 'consKind', renderGermany);
  $('refresh').addEventListener('click', load);
  $('auto').addEventListener('change', schedule);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', rerenderAll);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && $('auto').checked) load(); });
  fetch('/api/meta').then((r) => r.json()).then((m) => {
    $('authChip').classList.toggle('hidden', !!m.auth);
    $('version').textContent = `· v${m.version}`;
    if (m.power_desk_url) $('powerLink').href = m.power_desk_url;
  }).catch(() => {});
  load();
  schedule();
})();
