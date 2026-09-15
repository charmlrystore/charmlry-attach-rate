// app.js — avvio della pagina: carica i JSON mensili, tiene lo stato del periodo e collega calc.js a render.js.
import * as calc from './calc.js';
import * as ui from './render.js';

// Tutte le soglie in un posto solo. I valori sono quelli del prototipo; i commenti dicono dove agiscono.
export const CONFIG = {
  dataDir: 'data/',        // cartella con index.json e YYYY-MM.json (relativa alla pagina)
  presets: [30, 90, 0],    // pulsanti del periodo; 0 = tutto lo storico

  // matrice
  minBase: 150,            // base minima (pezzi o ordini) per entrare nelle opportunità
  thinBase: 40,            // sotto questa base la cella è marcata "base piccola"
  heatFloor: 25,           // il massimo della scala di calore non scende sotto questo valore (in punti)
  flatEps: 0.35,           // variazioni più piccole di così sono "invariato"

  // alert
  alertDrop: 5,            // punti di calo che generano un alert
  alertMinVolume: 10,      // volume minimo (pezzi nel periodo) perché una linea sia sorvegliata
  alertMinPrevRate: 5,     // sotto questo attach nel periodo precedente un calo non è significativo
  alertWeeks: 3,           // settimane consecutive in calo per l'alert persistente
  streakMinDrop: 2,        // punti persi complessivi nelle settimane consecutive
  alertsMax: 5,

  // opportunità
  recoverShare: 0.5,       // quota di distanza dal benchmark usata nella stima
  oppMinGap: 3,            // punti minimi di distanza dal migliore
  oppMinGain: 40,          // $/mese minimi perché la riga compaia
  oppMax: 7,
  stonePriceFallback: 8.9, // prezzo pietra se nel periodo non ne è stata venduta nessuna
  stonesPerRowFallback: 2, // pietre per pezzo se nel periodo non ci sono pezzi con pietre

  // distribuzione delle pietre
  depthMinTotal: 30,       // pezzi con pietre minimi perché la linea compaia

  // A/B test del customizer
  abMinArm: 500,           // ordini per braccio sotto cui il risultato non è leggibile (~7 punti attorno al 20-27%)
  abTargetDelta: 7,        // differenza in punti che si vorrebbe saper distinguere
  abCoverageAlarm: 0.9,    // copertura del tagging (tagged/eligible) sotto cui scatta l'avviso
  abAssumedRate: 0.23,     // tasso di base usato per la soglia quando non ci sono ancora pezzi
  abProduct: 'For Her | Bespoke Monogram Necklace',
};

const $ = id => document.getElementById(id);
let D = null;
let meta = { generated: null, through: null, source: null };
let state = { mode: 'preset', days: CONFIG.presets[0] };

async function fetchJson(url) {
  const r = await fetch(url, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

async function loadData() {
  const index = await fetchJson(CONFIG.dataDir + 'index.json');
  const docs = await Promise.all(index.months.map(m => fetchJson(`${CONFIG.dataDir}${m}.json`)));
  meta = { generated: index.generated, through: index.through, source: index.source };
  return calc.mergeMonths(docs);
}

function render() {
  const { cur } = calc.windows(D, state);
  ui.renderRangeLabel($('rangeLabel'), D, cur, calc.sum(D.orders, cur[0], cur[1]));
  ui.renderMatrix($('matrix'), calc.matrix(D, state, CONFIG));
  ui.renderSummary($('summary'), calc.summary(D, state, CONFIG));
  ui.renderAlerts($('alerts'), calc.alerts(D, state, CONFIG), CONFIG);
  ui.renderOpps($('opps'), calc.opportunities(D, state, CONFIG), CONFIG);
  ui.renderTrends($('trends'), calc.trends(D, state, CONFIG));
  ui.renderDepth($('depth'), calc.depth(D, CONFIG));
  ui.renderAB($('ab'), calc.abTest(D, state, CONFIG), CONFIG, CONFIG.abProduct);
  ui.renderFooter($('footer'), D, meta);
  syncInputs();
}

function syncInputs() {
  const { cur } = calc.windows(D, state);
  $('from').value = D.days[cur[0]];
  $('to').value = D.days[cur[1] - 1];
}

function wireControls() {
  const box = $('presets');
  ui.renderPresets(box, CONFIG.presets, state.days);
  box.addEventListener('click', e => {
    const b = e.target.closest('button[data-days]');
    if (!b) return;
    state = { mode: 'preset', days: +b.dataset.days };
    box.querySelectorAll('button').forEach(o => o.setAttribute('aria-pressed', o === b));
    render();
  });
  const fromEl = $('from'), toEl = $('to');
  const N = D.days.length;
  [fromEl, toEl].forEach(el => { el.min = D.days[0]; el.max = D.days[N - 1]; });
  const idx = d => D.days.indexOf(d);
  [fromEl, toEl].forEach(el => el.addEventListener('change', () => {
    let a = fromEl.value, b = toEl.value;
    if (idx(a) < 0) a = D.days[0];
    if (idx(b) < 0) b = D.days[N - 1];
    if (idx(a) > idx(b)) { const t = a; a = b; b = t; }
    fromEl.value = a; toEl.value = b;
    state = { mode: 'custom', from: a, to: b };
    box.querySelectorAll('button').forEach(o => o.setAttribute('aria-pressed', 'false'));
    render();
  }));
}

async function main() {
  ui.initTooltip();
  try {
    D = await loadData();
  } catch (err) {
    const local = location.protocol === 'file:';
    ui.renderError($('status'), local
      ? `La pagina legge i dati con <code>fetch</code>, che non funziona aprendo il file direttamente. Avvia un server locale nella cartella del progetto (<code>python3 -m http.server 8000</code>) e apri <code>http://localhost:8000/</code>.`
      : `Impossibile caricare i dati: ${err.message}`);
    return;
  }
  if (!D.days.length) { ui.renderError($('status'), 'Nessun giorno nei dati.'); return; }
  $('status').hidden = true;
  document.querySelectorAll('.needs-data').forEach(el => { el.hidden = false; });
  wireControls();
  render();
  // se cambia il tema (variabili CSS), la scala di calore si ricalcola
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { ui.resetPalette(); render(); });
}

main();
