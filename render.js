// render.js — DOM e SVG. Riceve i modelli calcolati da calc.js e li trasforma in HTML (testi in italiano).
// I colori non sono scritti qui: la scala di calore deriva dalle variabili CSS (--garnet e --heat-0).

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const it = 'it-IT';
export const pct = v => v.toLocaleString(it, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%';
export const eur = v => '$' + Math.round(v).toLocaleString(it);
export const dec = (v, n = 2) => v.toLocaleString(it, { minimumFractionDigits: n, maximumFractionDigits: n });
export const int = v => v.toLocaleString(it);
const MONTHS = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic'];
export const itDate = s => {
  if (!s) return '';
  const [, m, d] = s.split('-');
  return d.replace(/^0/, '') + ' ' + MONTHS[+m - 1];
};
const signed = (d, n = 1) => (d > 0 ? '+' : '−') + dec(Math.abs(d), n);

export const COL_LABEL = { stones: 'Pietre', scratch: 'Scratch card', giftbox: 'Gift box', warranty: 'Garanzia', tip: 'Mancia' };
const COL_LOWER = { stones: 'pietre', scratch: 'scratch card', giftbox: 'gift box', warranty: 'garanzia', tip: 'mancia' };
export const ARM_LABEL = { live: 'Live', classic: 'Classic', chat: 'Chat' };

/* ---------- colori: scala di calore derivata dall'accento ---------- */
function cssColor(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const m = v.match(/^#([0-9a-f]{6})$/i);
  if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16));
  const r = v.match(/rgba?\(([^)]+)\)/);
  if (r) return r[1].split(',').slice(0, 3).map(x => +x);
  return [142, 43, 60];
}
let palette = null;
function heatColor(t) {
  if (!palette) palette = { base: cssColor('--heat-0', '#F3F4F6'), accent: cssColor('--garnet', '#8E2B3C') };
  const c = palette.base.map((b, i) => Math.round(b + (palette.accent[i] - b) * t));
  return { bg: `rgb(${c.join(',')})`, dark: t > 0.45 };
}
export function resetPalette() { palette = null; }

/* ---------- intestazione e controlli ---------- */
export function renderPresets(el, presets, active) {
  el.innerHTML = presets.map(n =>
    `<button data-days="${n}" aria-pressed="${n === active}">${n === 0 ? 'Tutto lo storico' : n + ' giorni'}</button>`).join('');
}
export function renderRangeLabel(el, D, cur, orders) {
  el.textContent = `${itDate(D.days[cur[0]])} – ${itDate(D.days[cur[1] - 1])} · ${int(orders)} ordini`;
}

/* ---------- matrice ---------- */
function deltaHtml(delta) {
  if (!delta) return '<div class="d flat">&nbsp;</div>';
  if (delta.dir === 'flat') return '<div class="d flat">invariato</div>';
  return `<div class="d ${delta.dir}">${signed(delta.d)} pt</div>`;
}
function cellHtml(row, c) {
  const who = `${esc(row.name)} · ${COL_LOWER[c.col]}`;
  if (c.kind === 'na') {
    const tip = c.notYet ? ` data-tip="${who}|Opzione pietre in catalogo dal ${itDate(c.from)}: prima di quella data la cella resta vuota."` : '';
    return `<td><div class="cell na"${tip}><div class="v">—</div><div class="d">&nbsp;</div></div></td>`;
  }
  if (c.kind === 'zero') {
    return `<td><div class="cell zero" data-tip="${who}|Nessuna pietra su ${int(c.base)} pezzi venduti nel periodo.${c.from ? ' Opzione attiva dal ' + itDate(c.from) + '.' : ''}"><div class="v">0,0%</div><div class="d">mai venduta</div></div></td>`;
  }
  const thinNote = c.thin ? ' Base piccola: leggere con prudenza.' : '';
  const body = c.col === 'stones'
    ? `${int(c.hits)} pezzi con pietre su ${int(c.base)}.${thinNote}`
    : `${int(c.hits)} ordini su ${int(c.base)} che contengono ${esc(row.name)}.${thinNote}`;
  if (c.v <= 0) {
    return `<td><div class="cell flat0${c.thin ? ' thin' : ''}" data-tip="${who}|${body}"><div class="v">${pct(0)}</div>${deltaHtml(c.delta)}</div></td>`;
  }
  const h = heatColor(c.t);
  return `<td><div class="cell${c.thin ? ' thin' : ''}${h.dark ? ' dark' : ''}" style="background:${h.bg}" data-tip="${who}|${body}"><div class="v">${pct(c.v)}</div>${deltaHtml(c.delta)}</div></td>`;
}
export function renderMatrix(el, m) {
  let html = '<thead><tr><th class="rowhead"></th>' + m.cols.map(c => `<th>${COL_LABEL[c]}</th>`).join('') + '</tr></thead><tbody>';
  m.rows.forEach(row => {
    html += `<tr><td class="rowhead">${esc(row.name)}<span>${int(row.vol)} pezzi venduti</span></td>`;
    html += row.cells.map(c => cellHtml(row, c)).join('');
    html += '</tr>';
  });
  el.innerHTML = html + '</tbody>';
}

/* ---------- riepilogo ---------- */
export function renderSummary(el, s) {
  const dc = s.contribDelta;
  el.innerHTML = `
    <div>
      <div class="n">$${dec(s.contrib)}</div>
      <div class="l">Aggiunto a ogni ordine</div>
      <div class="s">${dc === null ? '&nbsp;' : (dc >= 0 ? '+' : '−') + '$' + dec(Math.abs(dc)) + ' rispetto al periodo prima'}</div>
    </div>
    <div>
      <div class="n">${pct(s.noAddShare)}</div>
      <div class="l">Ordini senza nulla</div>
      <div class="s">${int(s.noAdd)} su ${int(s.orders)}</div>
    </div>
    <div>
      <div class="n">${eur(s.addRev)}</div>
      <div class="l">Incasso dagli add-on</div>
      <div class="s">${pct(s.addShare)} del venduto</div>
    </div>
    <div>
      <div class="n">${eur(s.margin)}</div>
      <div class="l">Margine stimato</div>
      <div class="s">al netto del costo pietra, box e card</div>
    </div>`;
}

/* ---------- alert ---------- */
function alertText(a) {
  switch (a.kind) {
    case 'never': return `<b>${esc(a.line)}</b> non ha mai venduto una pietra: ${int(a.vol)} pezzi dal ${itDate(a.from)} e nessuna configurazione. Da controllare se il blocco è attivo sulla scheda.`;
    case 'zero': return `<b>${esc(a.line)}</b> è a zero pietre in tutto il periodo, ma l'opzione risulta attiva dal ${itDate(a.from)}.`;
    case 'drop': return `<b>${esc(a.line)} · ${COL_LOWER[a.col]}</b> scende di ${dec(Math.abs(a.d), 1)} punti, da ${pct(a.from)} a ${pct(a.to)}.`;
    case 'streak': return `<b>${COL_LABEL[a.col]}</b> scende da tre settimane di fila, da ${pct(a.from)} a ${pct(a.to)}.`;
    default: return '';
  }
}
export function renderAlerts(el, list, cfg) {
  if (!list.length) {
    el.innerHTML = `<div class="alert calm">Nessuna anomalia nel periodo: nessun add-on a zero e nessun calo oltre i ${cfg.alertDrop} punti.</div>`;
    return;
  }
  el.innerHTML = list.map(a => `<div class="alert">${alertText(a)}</div>`).join('');
}

/* ---------- dove conviene intervenire ---------- */
export function renderOpps(el, rows, cfg) {
  if (!rows.length) {
    el.innerHTML = '<div class="alert calm">Nessun divario significativo nel periodo: le linee di prodotto sono allineate fra loro.</div>';
    return;
  }
  const share = Math.round(cfg.recoverShare * 100);
  el.innerHTML =
    '<div class="opphead"><div>Prodotto e add-on</div><div>oggi</div><div></div><div>migliore</div><div>margine al mese</div></div>' +
    rows.map(r => `
      <div class="opp">
        <div class="who">${esc(r.line)} <em>· ${COL_LOWER[r.col]}</em>
          <small>il migliore è ${esc(r.benchLine)} · base ${int(r.base)}</small></div>
        <div class="num">${pct(r.cur)}</div>
        <div class="arr">→</div>
        <div class="num b">${pct(r.bench)}</div>
        <div class="gain"><b>+${eur(r.gain)}</b><span>${share === 50 ? 'a metà distanza' : 'al ' + share + '% della distanza'}</span></div>
      </div>`).join('');
}

/* ---------- trend settimanali ---------- */
function spark(pts) {
  const v = pts.filter(p => p !== null);
  if (v.length < 2) return '';
  const hi = Math.max(...v), lo = Math.min(...v);
  const pad = Math.max((hi - lo) * 0.35, hi * 0.06, 0.5);
  const top = hi + pad, bot = Math.max(0, lo - pad), span = (top - bot) || 1;
  const W = 100, H = 46;
  const x = i => (i / (pts.length - 1)) * W;
  const y = p => H - ((p - bot) / span) * (H - 8) - 4;
  let d = '', started = false;
  pts.forEach((p, i) => {
    if (p === null) { started = false; return; }
    d += (started ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p).toFixed(1) + ' ';
    started = true;
  });
  const last = pts[pts.length - 1];
  const area = `M0 ${H} ` + pts.map((p, i) => p === null ? '' : `L${x(i).toFixed(1)} ${y(p).toFixed(1)}`).join(' ') + ` L${W} ${H} Z`;
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <path class="area" d="${area}"/>
    <path class="line" d="${d}" fill="none" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    ${last !== null ? `<circle class="dot" cx="${W}" cy="${y(last).toFixed(1)}" r="2.6"/>` : ''}
  </svg>`;
}
export function renderTrends(el, items) {
  el.innerHTML = items.map(it => {
    const dl = !it.delta ? '&nbsp;' :
      it.delta.dir === 'flat' ? '<span class="flat">invariato</span>' :
      `<span class="${it.delta.dir}">${signed(it.delta.d)} pt</span>`;
    return `<div class="trend"><div class="t">${COL_LABEL[it.col]}</div><div class="val">${pct(it.cur)}</div><div class="dl">${dl}</div>${spark(it.points)}</div>`;
  }).join('');
}

/* ---------- distribuzione delle pietre ---------- */
export function renderDepth(el, items) {
  el.innerHTML = items.map(d => {
    const bars = d.bars.map(b => {
      if (b.off) return `<i class="off" style="height:2px" title="non disponibile"></i>`;
      const h = Math.max(2, b.h * 74);
      return `<i class="${b.capped ? 'capped' : ''}" style="height:${h.toFixed(0)}px" data-tip="${b.slot} pietr${b.slot === 1 ? 'a' : 'e'}|${int(b.v)} pezzi · ${pct(b.share)} di chi ne compra"></i>`;
    }).join('');
    return `<div class="dep">
      <div class="t">${esc(d.name)}</div>
      <div class="m">media ${dec(d.avg)} pietre · massimo consentito ${d.cap}${d.atCap >= 5 ? ` · ${pct(d.atCap)} è al limite` : ''}</div>
      <div class="bars">${bars}</div>
      <div class="axis">${[1, 2, 3, 4, 5, 6].map(i => `<span>${i}</span>`).join('')}</div>
    </div>`;
  }).join('');
}

/* ---------- A/B test del customizer ---------- */
export function renderAB(el, ab, cfg, productLabel) {
  if (!ab.active) {
    el.innerHTML = `<div class="alert calm">Nessun ordine con variante del test nel periodo selezionato. Il test è attivo dall'8 settembre 2026: allarga o sposta il periodo.</div>`;
    return;
  }
  const arms = ab.arms.map(a => `
    <div class="arm">
      <div class="arm-name">${ARM_LABEL[a.key]}</div>
      <div class="arm-rate">${a.rate === null ? '—' : pct(a.rate)}</div>
      <div class="arm-ci">${a.ci ? `intervallo 95%: ${dec(a.ci[0], 0)}–${dec(a.ci[1], 0)}%` : 'nessun pezzo'}</div>
      <div class="arm-n"><b>${int(a.orders)}</b> ordini con il prodotto · ${int(a.rows)} pezzi, ${int(a.stoneRows)} con pietre</div>
      <div class="arm-src">${int(a.new)} new · ${int(a.stored)} stored${a.tagged > a.orders ? ` · ${int(a.tagged - a.orders)} senza il prodotto` : ''}</div>
    </div>`).join('');

  const cov = ab.coverage === null ? '—' : pct(100 * ab.coverage);
  const covClass = ab.coverageLow ? 'bad' : 'ok';
  const readability = ab.readable
    ? `<div class="alert calm">Numerosità sufficiente: con almeno ${int(ab.minArm)} ordini per braccio si distinguono differenze di circa ${dec(ab.detectable, 0)} punti.</div>`
    : `<div class="alert">Bracci sotto la soglia di leggibilità (${int(cfg.abMinArm)} ordini per braccio). Con ${int(ab.minArm)} ordini nel braccio più piccolo si distinguono solo differenze di almeno ${dec(ab.detectable || 0, 0)} punti; per leggere ${cfg.abTargetDelta} punti servono circa ${int(ab.needed)} ordini per braccio${ab.daysToReadable ? `, al ritmo attuale ancora ~${int(ab.daysToReadable)} giorni` : ''}. Nessun vincitore va dichiarato su questi numeri.</div>`;

  el.innerHTML = `
    <div class="ab-arms">${arms}</div>
    <div class="ab-meta">
      <div><span class="k">Copertura del tagging</span><b class="${covClass}">${cov}</b><span class="s">${int(ab.tagged)} su ${int(ab.eligible)} ordini con ${esc(productLabel)}${ab.coverageLow ? ' · sotto il ' + Math.round(cfg.abCoverageAlarm * 100) + '%' : ''}</span></div>
      <div><span class="k">Fuori perimetro</span><b>${int(ab.orphan)}</b><span class="s">ordini con variante ma senza il prodotto sotto test (assegnazione per sessione)</span></div>
      <div><span class="k">Ripresi da sessione</span><b>${ab.storedShare === null ? '—' : pct(100 * ab.storedShare)}</b><span class="s">quota di <code>ab_source = stored</code>: se cresce, i bracci si contaminano</span></div>
      <div><span class="k">Periodo con varianti</span><b>${itDate(ab.firstDay)} – ${itDate(ab.lastDay)}</b><span class="s">${int(ab.totalTagged)} ordini con variante in totale</span></div>
    </div>
    ${readability}`;
}

/* ---------- piè di pagina e stati ---------- */
export function renderFooter(el, D, meta) {
  el.innerHTML = `Dati Shopify fino al ${itDate(meta.through)}${meta.generated ? `, aggiornati il ${itDate(meta.generated)}` : ''}, ordini annullati e non pagati esclusi. ` +
    `Le pietre sono contate dalle opzioni configurate sul singolo pezzo e pesate per la quantità.<br>` +
    (meta.source === 'prototype-seed'
      ? `Dati di avvio incorporati dal prototipo: il pannello A/B si popola con il primo giro di <code>collect.py</code>.`
      : `Pagina statica: i dati arrivano da <code>data/*.json</code>, rigenerati ogni notte da <code>collect.py</code>.`);
}
export function renderError(el, message) {
  el.innerHTML = `<div class="alert">${message}</div>`;
  el.hidden = false;
}

/* ---------- tooltip ---------- */
export function initTooltip() {
  const tip = document.getElementById('tip');
  document.addEventListener('mouseover', e => {
    const el = e.target.closest('[data-tip]');
    if (!el) { tip.style.opacity = 0; return; }
    const [h, b] = el.dataset.tip.split('|');
    tip.innerHTML = `<b>${h}</b><br>${b}`;
    tip.style.opacity = 1;
  });
  document.addEventListener('mousemove', e => {
    if (tip.style.opacity === '0') return;
    const w = tip.offsetWidth, x = Math.min(e.clientX + 14, window.innerWidth - w - 12);
    tip.style.left = x + 'px';
    tip.style.top = (e.clientY + 18) + 'px';
  });
}
