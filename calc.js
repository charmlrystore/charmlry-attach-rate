// calc.js — logica pura della dashboard: aggregazioni, attach rate, opportunità, alert, A/B test.
//
// Vincolo esplicito: questo file non tocca il DOM e non contiene stringhe di interfaccia.
// Ogni funzione riceve (dati, finestra, config) e restituisce numeri e strutture; le parole le mette render.js.
// Quando la dashboard migrerà dentro iCust, questo file si porta via intatto.

export const CART_KEYS = ['scratch', 'giftbox', 'warranty', 'tip'];
export const ARMS = ['live', 'classic', 'chat'];
export const AB_SOURCES = ['new', 'stored'];
const AB_FIELDS = ['orders', 'tagged', 'stoneRows', 'rows', 'new', 'stored'];
const LINE_SERIES = ['rows', 'stoneRows', 'stones', 'orders', 'scratch', 'giftbox', 'warranty', 'tip'];

/* ------------------------------------------------------------------ dati: unione dei file mensili */

// docs: array di data/YYYY-MM.json in ordine cronologico. Le serie si concatenano; i campi cumulativi
// (dist, cap, from, stonesFrom, costs) si prendono dal mese più recente, che li rigenera ogni notte.
export function mergeMonths(docs) {
  const zeros = n => new Array(n).fill(0);
  const D = { generated: null, days: [], orders: [], revenue: [], noAddon: [], stoneRev: [],
    cart: {}, lines: {}, ab: { eligible: [], tagged: [], orphan: [], variants: {} }, stonesFrom: {}, costs: {} };
  CART_KEYS.forEach(k => { D.cart[k] = { orders: [], rev: [] }; });
  ARMS.forEach(v => { D.ab.variants[v] = {}; AB_FIELDS.forEach(f => { D.ab.variants[v][f] = []; }); });
  const lineNames = [];
  docs.forEach(doc => Object.keys(doc.lines || {}).forEach(n => { if (!lineNames.includes(n)) lineNames.push(n); }));
  lineNames.forEach(n => { D.lines[n] = { from: null, dist: zeros(6), cap: 0 }; LINE_SERIES.forEach(f => { D.lines[n][f] = []; }); });

  docs.forEach(doc => {
    const n = doc.days.length;
    D.generated = doc.generated || D.generated;
    D.days.push(...doc.days);
    D.orders.push(...doc.orders);
    D.revenue.push(...doc.revenue);
    D.noAddon.push(...doc.noAddon);
    D.stoneRev.push(...doc.stoneRev);
    CART_KEYS.forEach(k => {
      const c = doc.cart && doc.cart[k];
      D.cart[k].orders.push(...(c ? c.orders : zeros(n)));
      D.cart[k].rev.push(...(c ? c.rev : zeros(n)));
    });
    lineNames.forEach(name => {
      const L = doc.lines[name];
      LINE_SERIES.forEach(f => D.lines[name][f].push(...(L && L[f] ? L[f] : zeros(n))));
      if (L) {
        D.lines[name].dist = L.dist || D.lines[name].dist;
        D.lines[name].cap = L.cap == null ? D.lines[name].cap : L.cap;
        D.lines[name].from = L.from || D.lines[name].from;
      }
    });
    const ab = doc.ab || {};
    ['eligible', 'tagged', 'orphan'].forEach(f => D.ab[f].push(...(ab[f] || zeros(n))));
    ARMS.forEach(v => AB_FIELDS.forEach(f => {
      const V = ab.variants && ab.variants[v];
      D.ab.variants[v][f].push(...(V && V[f] ? V[f] : zeros(n)));
    }));
    if (doc.stonesFrom) D.stonesFrom = doc.stonesFrom;
    if (doc.costs) D.costs = doc.costs;
  });
  return D;
}

/* ------------------------------------------------------------------ finestre temporali */

export const sum = (a, i, j) => { let s = 0; for (let k = i; k < j; k++) s += a[k] || 0; return s; };

// state: { mode:'preset', days } oppure { mode:'custom', from, to }.
// Il periodo di confronto è sempre quello immediatamente precedente di pari durata (null se non c'è storico).
export function windows(D, state) {
  const N = D.days.length;
  const idx = d => D.days.indexOf(d);
  let cur;
  if (state.mode === 'custom') {
    const a = Math.max(0, idx(state.from)), b = Math.min(N - 1, idx(state.to));
    cur = [a, b + 1];
  } else {
    const len = state.days === 0 ? N : Math.min(state.days, N);
    cur = [N - len, N];
  }
  const len = cur[1] - cur[0];
  const prevStart = Math.max(0, cur[0] - len);
  const prev = prevStart < cur[0] ? [prevStart, cur[0]] : null;
  return { cur, prev, len };
}

/* ------------------------------------------------------------------ metriche di base */

// Pietre: opzione di prodotto → denominatore = pezzi venduti della linea (rows).
export function stoneRate(line, w) {
  const rows = sum(line.rows, w[0], w[1]);
  if (!rows) return null;
  const hits = sum(line.stoneRows, w[0], w[1]);
  return { v: 100 * hits / rows, base: rows, hits };
}
// Add-on di carrello: denominatore = ordini che contengono la linea (orders).
export function cartRate(line, k, w) {
  const o = sum(line.orders, w[0], w[1]);
  if (!o) return null;
  const hits = sum(line[k], w[0], w[1]);
  return { v: 100 * hits / o, base: o, hits };
}
// L'opzione pietre esiste su questa linea entro la fine della finestra?
export function stonesLive(D, name, w) {
  const from = D.stonesFrom[name];
  if (!from) return false;
  return D.days[w[1] - 1] >= from;
}
// L'opzione entra in catalogo dopo la fine della finestra: la cella va lasciata vuota, non a zero.
export function stonesNotYet(D, name, w) {
  const from = D.stonesFrom[name];
  return !!from && D.days[w[1] - 1] < from;
}

export function delta(cur, prev, eps) {
  if (prev === null || prev === undefined || cur === null || cur === undefined) return null;
  const d = cur - prev;
  return { d, dir: Math.abs(d) < eps ? 'flat' : d > 0 ? 'up' : 'down' };
}

/* ------------------------------------------------------------------ matrice prodotto × add-on */

export function matrix(D, state, cfg) {
  const { cur, prev } = windows(D, state);
  const names = Object.keys(D.lines);
  let max = 0;
  names.forEach(n => {
    const s = stoneRate(D.lines[n], cur); if (s && stonesLive(D, n, cur)) max = Math.max(max, s.v);
    CART_KEYS.forEach(k => { const r = cartRate(D.lines[n], k, cur); if (r) max = Math.max(max, r.v); });
  });
  max = Math.max(max, cfg.heatFloor);
  const heat = v => Math.min(1, Math.max(0, v / max));

  const rows = names.map(name => {
    const L = D.lines[name];
    const cells = [];
    const s = stoneRate(L, cur);
    const from = D.stonesFrom[name] || null;
    if (!s) {
      cells.push({ col: 'stones', kind: 'na' });
    } else if (stonesNotYet(D, name, cur)) {
      cells.push({ col: 'stones', kind: 'na', notYet: true, from, base: s.base });
    } else if (!from || s.hits === 0) {
      cells.push({ col: 'stones', kind: 'zero', base: s.base, from });
    } else {
      const sp = prev && stonesLive(D, name, prev) ? stoneRate(L, prev) : null;
      cells.push({ col: 'stones', kind: 'value', v: s.v, base: s.base, hits: s.hits, t: heat(s.v),
        thin: s.base < cfg.thinBase, delta: delta(s.v, sp ? sp.v : null, cfg.flatEps), from });
    }
    CART_KEYS.forEach(k => {
      const r = cartRate(L, k, cur);
      if (!r) { cells.push({ col: k, kind: 'na' }); return; }
      const p = prev ? cartRate(L, k, prev) : null;
      cells.push({ col: k, kind: 'value', v: r.v, base: r.base, hits: r.hits, t: heat(r.v),
        thin: r.base < cfg.thinBase, delta: delta(r.v, p ? p.v : null, cfg.flatEps) });
    });
    return { name, vol: sum(L.rows, cur[0], cur[1]), cells };
  });
  return { cols: ['stones', ...CART_KEYS], rows, max };
}

/* ------------------------------------------------------------------ riepilogo del periodo */

export function summary(D, state, cfg) {
  const { cur, prev } = windows(D, state);
  const names = Object.keys(D.lines);
  const ord = sum(D.orders, cur[0], cur[1]);
  const rev = sum(D.revenue, cur[0], cur[1]);
  const stoneRev = sum(D.stoneRev, cur[0], cur[1]);
  let addRev = stoneRev;
  CART_KEYS.forEach(k => { addRev += sum(D.cart[k].rev, cur[0], cur[1]); });
  const noAdd = sum(D.noAddon, cur[0], cur[1]);

  const stones = names.reduce((s, n) => s + sum(D.lines[n].stones, cur[0], cur[1]), 0);
  let margin = stoneRev - (D.costs.stone || 0) * stones;
  CART_KEYS.forEach(k => {
    margin += sum(D.cart[k].rev, cur[0], cur[1]) - (D.costs[k] || 0) * sum(D.cart[k].orders, cur[0], cur[1]);
  });

  let prevContrib = null;
  if (prev) {
    const po = sum(D.orders, prev[0], prev[1]);
    let pr = sum(D.stoneRev, prev[0], prev[1]);
    CART_KEYS.forEach(k => { pr += sum(D.cart[k].rev, prev[0], prev[1]); });
    prevContrib = po ? pr / po : null;
  }
  const contrib = ord ? addRev / ord : 0;
  return {
    orders: ord, revenue: rev, addRev, margin, noAdd,
    contrib, contribDelta: prevContrib === null ? null : contrib - prevContrib,
    noAddShare: ord ? 100 * noAdd / ord : 0,
    addShare: rev ? 100 * addRev / rev : 0,
  };
}

/* ------------------------------------------------------------------ alert automatici */

// Ritorna una lista ordinata di alert strutturati:
//   { kind:'never', line, vol, from }         linea che vende ma non ha mai venduto una pietra
//   { kind:'zero',  line, from }              zero pietre nel periodo con opzione attiva
//   { kind:'drop',  line, col, from, to }     calo oltre soglia rispetto al periodo precedente
//   { kind:'streak', col, from, to }          calo per tre settimane consecutive
export function alerts(D, state, cfg) {
  const { cur, prev } = windows(D, state);
  const names = Object.keys(D.lines);
  const out = [];
  names.forEach(name => {
    const L = D.lines[name];
    const vol = sum(L.rows, cur[0], cur[1]);
    if (vol < cfg.alertMinVolume) return;
    const from = D.stonesFrom[name];
    const hits = sum(L.stoneRows, cur[0], cur[1]);
    if (!from) {
      out.push({ kind: 'never', severity: 2, line: name, vol, from: L.from });
    } else if (hits === 0 && D.days[cur[1] - 1] >= from) {
      out.push({ kind: 'zero', severity: 2, line: name, from });
    }
    if (!prev) return;
    ['stones', ...CART_KEYS].forEach(col => {
      const a = col === 'stones' ? stoneRate(L, cur) : cartRate(L, col, cur);
      const b = col === 'stones' ? stoneRate(L, prev) : cartRate(L, col, prev);
      if (!a || !b || b.v < cfg.alertMinPrevRate || a.hits === 0) return;
      if (col === 'stones' && !stonesLive(D, name, prev)) return;
      const d = a.v - b.v;
      if (d <= -cfg.alertDrop) out.push({ kind: 'drop', severity: 1, line: name, col, from: b.v, to: a.v, d });
    });
  });
  // cali persistenti: tre settimane consecutive in discesa sul totale (quattro punti settimanali)
  if (cur[1] - cur[0] >= 7 * (cfg.alertWeeks + 1)) {
    ['stones', ...CART_KEYS].forEach(col => {
      const { num, den } = totalSeries(D, col);
      const w = weekly(num, den, cur).filter(v => v !== null);
      if (w.length < cfg.alertWeeks + 1) return;
      const last = w.slice(-(cfg.alertWeeks + 1));
      let falling = true;
      for (let i = 1; i < last.length; i++) if (last[i] >= last[i - 1]) falling = false;
      if (falling && last[0] - last[last.length - 1] >= cfg.streakMinDrop)
        out.push({ kind: 'streak', severity: 1, col, from: last[0], to: last[last.length - 1] });
    });
  }
  out.sort((a, b) => b.severity - a.severity);
  return out.slice(0, cfg.alertsMax);
}

// Serie giornaliere (numeratore, denominatore) per una colonna sul totale delle linee.
export function totalSeries(D, col) {
  const names = Object.keys(D.lines);
  if (col === 'stones') {
    return {
      num: D.days.map((_, i) => names.reduce((s, n) => s + (D.lines[n].stoneRows[i] || 0), 0)),
      den: D.days.map((_, i) => names.reduce((s, n) => s + (D.lines[n].rows[i] || 0), 0)),
    };
  }
  return { num: D.cart[col].orders, den: D.orders };
}

/* ------------------------------------------------------------------ dove conviene intervenire */

// Ogni add-on è confrontato con la linea che lo vende meglio nello stesso periodo (fra quelle con base solida).
// La stima è il margine mensile recuperabile chiudendo una quota `recoverShare` della distanza, normalizzata a 30 giorni.
export function opportunities(D, state, cfg) {
  const { cur, len } = windows(D, state);
  const names = Object.keys(D.lines);
  const scale = 30 / len;
  const stones = names.reduce((s, n) => s + sum(D.lines[n].stones, cur[0], cur[1]), 0);
  const stoneRev = sum(D.stoneRev, cur[0], cur[1]);
  const stonePrice = stones ? stoneRev / stones : cfg.stonePriceFallback;
  const stoneMargin = Math.max(0, stonePrice - (D.costs.stone || 0));
  const rows = [];

  ['stones', ...CART_KEYS].forEach(col => {
    const meas = names.map(n => {
      const L = D.lines[n];
      if (col === 'stones') {
        if (!stonesLive(D, n, cur)) return null;
        const r = stoneRate(L, cur);
        if (!r) return null;
        const sr = sum(L.stoneRows, cur[0], cur[1]);
        const perRow = sr ? sum(L.stones, cur[0], cur[1]) / sr : cfg.stonesPerRowFallback;
        return { n, v: r.v, base: r.base, unit: perRow * stoneMargin };
      }
      const r = cartRate(L, col, cur);
      if (!r) return null;
      const o = sum(D.cart[col].orders, cur[0], cur[1]);
      const val = o ? sum(D.cart[col].rev, cur[0], cur[1]) / o : 0;
      return { n, v: r.v, base: r.base, unit: Math.max(0, val - (D.costs[col] || 0)) };
    }).filter(Boolean);

    const solid = meas.filter(m => m.base >= cfg.minBase);
    if (solid.length < 2) return;
    const best = solid.reduce((a, b) => (b.v > a.v ? b : a));
    meas.forEach(m => {
      if (m.n === best.n || m.base < cfg.minBase) return;
      const gap = best.v - m.v;
      if (gap < cfg.oppMinGap) return;
      const gain = (gap * cfg.recoverShare / 100) * m.base * m.unit * scale;
      if (gain < cfg.oppMinGain) return;
      rows.push({ line: m.n, col, cur: m.v, bench: best.v, benchLine: best.n, base: m.base, gain });
    });
  });
  rows.sort((a, b) => b.gain - a.gain);
  return rows.slice(0, cfg.oppMax);
}

/* ------------------------------------------------------------------ trend settimanali */

// Un punto per settimana, allineato alla fine della finestra; null dove il denominatore è zero.
export function weekly(num, den, w) {
  const pts = [];
  for (let end = w[1]; end > w[0]; end -= 7) {
    const start = Math.max(w[0], end - 7);
    const d = sum(den, start, end);
    pts.unshift(d ? 100 * sum(num, start, end) / d : null);
  }
  return pts;
}

export function trends(D, state, cfg) {
  const { cur, prev } = windows(D, state);
  return ['stones', ...CART_KEYS].map(col => {
    const { num, den } = totalSeries(D, col);
    const c = 100 * sum(num, cur[0], cur[1]) / (sum(den, cur[0], cur[1]) || 1);
    const p = prev ? 100 * sum(num, prev[0], prev[1]) / (sum(den, prev[0], prev[1]) || 1) : null;
    return { col, cur: c, prev: p, delta: delta(c, p, cfg.flatEps), points: weekly(num, den, cur) };
  });
}

/* ------------------------------------------------------------------ distribuzione delle pietre */

// Distribuzione cumulativa sull'intero storico (dist) con il tetto della scheda prodotto (cap).
export function depth(D, cfg) {
  return Object.keys(D.lines)
    .filter(n => D.lines[n].dist.reduce((a, b) => a + b, 0) >= cfg.depthMinTotal)
    .map(n => {
      const L = D.lines[n];
      const total = L.dist.reduce((a, b) => a + b, 0);
      const avg = L.dist.reduce((s, v, i) => s + v * (i + 1), 0) / total;
      const max = Math.max(...L.dist);
      const atCap = L.cap ? 100 * (L.dist[L.cap - 1] || 0) / total : 0;
      return { name: n, dist: L.dist, cap: L.cap, total, avg, max, atCap,
        bars: L.dist.map((v, i) => ({ slot: i + 1, v, share: 100 * v / total, off: i + 1 > L.cap, capped: i + 1 === L.cap, h: max ? v / max : 0 })) };
    });
}

/* ------------------------------------------------------------------ A/B test del customizer */

// Intervallo di Wilson al 95%: l'affidabilità va mostrata accanto al risultato.
export function wilson(k, n, z = 1.96) {
  if (!n) return null;
  const p = k / n, z2 = z * z;
  const c = (p + z2 / (2 * n)) / (1 + z2 / n);
  const h = (z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n);
  return [100 * Math.max(0, c - h), 100 * Math.min(1, c + h)];
}

// Differenza minima rilevabile (punti percentuali) fra due bracci di numerosità n con tasso di base p,
// approssimazione classica a due proporzioni (alfa 5%, potenza 80%).
export function detectableDelta(p, n) {
  if (!n) return null;
  return 100 * 2.8 * Math.sqrt(2 * p * (1 - p) / n);
}
// Numerosità per braccio necessaria per distinguere una differenza di `deltaPts` punti attorno a p.
export function neededPerArm(p, deltaPts) {
  const d = deltaPts / 100;
  return Math.ceil(2 * 2.8 * 2.8 * p * (1 - p) / (d * d));
}

export function abTest(D, state, cfg) {
  const { cur } = windows(D, state);
  const S = f => sum(f, cur[0], cur[1]);
  const eligible = S(D.ab.eligible), tagged = S(D.ab.tagged), orphan = S(D.ab.orphan);
  const arms = ARMS.map(v => {
    const V = D.ab.variants[v];
    const rows = S(V.rows), stoneRows = S(V.stoneRows);
    return { key: v, orders: S(V.orders), tagged: S(V.tagged), rows, stoneRows,
      new: S(V.new), stored: S(V.stored),
      rate: rows ? 100 * stoneRows / rows : null, ci: wilson(stoneRows, rows) };
  });
  const totalTagged = arms.reduce((s, a) => s + a.tagged, 0);
  let firstDay = null, lastDay = null;
  for (let i = cur[0]; i < cur[1]; i++) {
    const anyTag = ARMS.some(v => D.ab.variants[v].tagged[i] > 0);
    if (anyTag) { if (!firstDay) firstDay = D.days[i]; lastDay = D.days[i]; }
  }
  const pooledRows = arms.reduce((s, a) => s + a.rows, 0);
  const pooledStone = arms.reduce((s, a) => s + a.stoneRows, 0);
  const p = pooledRows ? pooledStone / pooledRows : cfg.abAssumedRate;
  const minArm = Math.min(...arms.map(a => a.orders));
  const storedShare = totalTagged ? arms.reduce((s, a) => s + a.stored, 0) / totalTagged : null;
  return {
    active: totalTagged > 0, eligible, tagged, orphan, totalTagged, firstDay, lastDay, arms,
    coverage: eligible ? tagged / eligible : null,
    coverageLow: eligible ? tagged / eligible < cfg.abCoverageAlarm : false,
    storedShare,
    minArm, readable: minArm >= cfg.abMinArm,
    detectable: detectableDelta(p, minArm),
    needed: neededPerArm(p, cfg.abTargetDelta),
    // giorni di calendario necessari al ritmo del periodo per arrivare alla soglia col braccio più piccolo
    daysToReadable: (() => {
      const rate = firstDay ? minArm / Math.max(1, daysBetween(firstDay, D.days[cur[1] - 1]) + 1) : 0;
      return rate > 0 ? Math.ceil(Math.max(0, cfg.abMinArm - minArm) / rate) : null;
    })(),
  };
}

export function daysBetween(a, b) {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
}
