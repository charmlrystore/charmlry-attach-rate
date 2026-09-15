# Attach rate degli upsell · Charmlry

Dashboard statica che si aggiorna ogni notte con l'attach rate degli upsell (pietre, scratch card, gift box,
garanzia, mancia) dello store Shopify Charmlry, più il pannello dell'A/B test del configuratore.

```
collect.py                    raccolta (gira ogni notte su GitHub Actions; solo aggregati, mai un ordine singolo)
lines.json                    mappa product ID → linea, figli birthstone, add-on di carrello, costi, tetti pietre
data/index.json               manifest: mesi disponibili, ultimo giorno, data di generazione
data/YYYY-MM.json             un file per mese (schema del brief, §3)
index.html                    pagina (dal prototipo) — colori solo nel :root
app.js                        avvio, blocco CONFIG con tutte le soglie, stato del periodo
calc.js                       logica pura: niente DOM, niente stringhe (si porta via intatto dentro iCust)
render.js                     DOM e SVG, testi in italiano
tests/test_collect.py         regole di calcolo su ordini sintetici
.github/workflows/nightly.yml cron notturno: raccolta → commit dei JSON → pubblicazione su GitHub Pages
```

Il browser non chiama mai l'Admin API: legge solo i JSON statici. Il token vive nei repository secrets.

## Messa in funzione

1. **Custom app Shopify** (Impostazioni → App e canali di vendita → Sviluppa app) con gli scope
   `read_orders`, `read_all_orders` (serve per andare oltre 60 giorni), `read_products`. Copiare il token Admin API.
2. **Secrets del repository**: `SHOPIFY_SHOP` (es. `charmlry.myshopify.com`) e `SHOPIFY_ADMIN_TOKEN`.
3. **Backfill storico, una volta sola**. I file in `data/` sono il seed incorporato nel prototipo (fino al 7 settembre,
   senza distribuzione giornaliera delle pietre né dati A/B): il giro notturno si rifiuta di partire finché non
   viene sostituito dal backfill. Dal tab Actions lanciare *Attach rate · aggiornamento notturno* con
   `backfill = true`, oppure in locale:
   ```bash
   SHOPIFY_SHOP=… SHOPIFY_ADMIN_TOKEN=… python3 collect.py backfill --from 2026-01-01
   ```
   Circa 5 minuti per ~42.000 ordini (bulk operation, JSONL ~55 MB). Il JSONL scaricato resta in `/tmp`:
   per rielaborarlo senza rilanciare la bulk, `python3 collect.py backfill --jsonl /tmp/charmlry-orders-2026-01-01.jsonl`.
4. **GitHub Pages**: Settings → Pages → Source: *GitHub Actions*. Dal giro successivo la pagina è pubblicata.
   Nota: Pages su un repo **privato** richiede GitHub Pro/Team/Enterprise; su un piano Free il repo va reso pubblico
   (contiene solo aggregati) oppure si pubblica altrove (Cloudflare Pages, Netlify) la cartella `_site` che il workflow produce.
5. Da lì in poi il cron delle 06:30 UTC (02:30 a New York) esegue `collect.py daily`, che **ricalcola per intero gli
   ultimi 7 giorni** (gli ordini cambiano stato, vengono rimborsati e annullati dopo) e committa i JSON.
   Per rifare più giorni: Actions → Run workflow → `days = 30`.

### In locale

```bash
python3 -m unittest discover -s tests -v        # regole di calcolo
python3 -m http.server 8000                      # poi http://localhost:8000/  (fetch non funziona da file://)
python3 collect.py discover                      # catalogo con stato di mappatura (richiede il token)
python3 collect.py daily --days 7 --dry-run      # stampa gli aggregati senza scrivere
```

## Regole di calcolo (brief §4) — dove stanno nel codice

Cercare `REGOLA` in `collect.py`.

- **Perimetro** — `in_scope`: fuori gli ordini con `cancelledAt`, quelli con stato diverso da `PAID`/`PARTIALLY_REFUNDED`
  e gli ordini `test`.
- **Pietre** — `stones_per_unit`: si contano le line item properties del pezzo (`Extra charm 1..6`, `Birthstone 1..6`,
  accettata anche la chiave non numerata `Extra charm` se valorizzata), solo con valore non vuoto,
  **moltiplicate per la quantità della riga**. I figli birthstone (`Birthstone`, `Necklace Birthstone`,
  `Bracelet Birthstone`, `Cord Bracelet Birthstone`) contano solo per `stoneRev`.
- **Denominatori** — pietre sui **pezzi** della linea (`rows`); scratch card, gift box, garanzia e mancia sugli **ordini**
  che contengono la linea (`orders`). Una collana con pietre nel carrello non rende "con pietre" il bracciale accanto.
- **Raggruppamento per product ID** — `lines.json`, mai per titolo. Mens cord = handle `for-him-monogram-bracelet`.
- **Disponibilità** — `stonesFrom[linea]` = prima pietra venduta; la pagina lascia la cella **vuota** (non zero) se il
  periodo finisce prima di quella data, e non calcola la variazione se l'opzione non esisteva nel periodo di confronto.
- **Charm** (product 14694488736076) non è un add-on: componente incluso, escluso da tutto.
- **Mancia**: `totalTipReceivedSet` dell'ordine; se è zero, la riga `Tip` senza prodotto.
- **Giorno**: fuso orario dello shop (`America/New_York`, in `lines.json`), non UTC.
- `revenue` = valore merce a prezzo pieno (q × `originalUnitPrice`) su tutte le righe prodotto, mancia esclusa;
  `stoneRev` e `cart.*.rev` = incasso effettivo (prezzo scontato).

Le linee **Collane, Charm Bracelet, Keychain, Mens cord, Womens cord, Kids cord** sono mappate in `lines.json` con
gli id reali del catalogo (verificati il 12 settembre 2026). Il raggruppamento segue alla lettera il brief
("titolo contiene Necklace, esclusi i birthstone", ecc.); in `_unmapped_candidates` sono elencati i configurabili
che quella regola lascia fuori (es. *Custom-Designed Bond to Wear*, *Custom Family Initials Charm*): da decidere con il team.
Ogni giro segnala nei log i prodotti fuori mappa con il loro volume, così la mappa non invecchia in silenzio.

## Schema dati

È quello del brief (§3), con queste aggiunte, tutte retrocompatibili:

- `lines[L].distDays` — distribuzione 1..6 pietre **per giorno**: serve per rigenerare `dist` (cumulativo) a ogni giro
  senza rileggere gli ordini. Senza questo campo il giro notturno non può ricalcolare il cumulativo, per questo il seed
  del prototipo va sostituito dal backfill.
- `ab.variants[v].tagged` — tutti gli ordini con `ab_customizer = v`, compresi quelli senza il prodotto sotto test;
  `orders` sono solo quelli eleggibili (usati per l'attach). `new`/`stored` contano su `tagged`.
- `source` (`collect.py` | `prototype-seed`), `month`, `schema` in ogni mensile; `data/index.json` come manifest.
- I campi cumulativi (`dist`, `cap`, `from`, `stonesFrom`, `costs`) sono scritti identici in **tutti** i mensili a ogni giro;
  la pagina legge quelli del mese più recente.

## A/B test del customizer

Variante e origine sono **note attribute a livello ordine** (`ab_customizer` ∈ live/classic/chat, `ab_source` ∈ new/stored).
Per giorno il collettore salva: `eligible` (ordini con *For Her | Bespoke Monogram Necklace*, id 8677371707724),
`tagged` (eleggibili con variante), `orphan` (variante senza il prodotto: assegnazione per sessione), e per braccio
ordini, pezzi del prodotto sotto test e pezzi con pietre, `new`/`stored`.

La pagina mostra per ogni braccio attach rate + intervallo di Wilson al 95% + numerosità, la copertura del tagging con
allarme sotto il 90% (`CONFIG.abCoverageAlarm`), la quota di `stored`, e un avviso esplicito finché il braccio più piccolo
è sotto `CONFIG.abMinArm` (500 ordini: per distinguere 20% da 27% ne servono ~570 per braccio). I bracci restano in ordine
fisso e non viene mai indicato un vincitore.

### Verifica sui dati reali (12 settembre 2026)

Le regole sono state provate sugli ordini veri prima di scrivere il codice:

- 8 settembre, primi 50 ordini della giornata: **20 eleggibili, 15 con variante** — esattamente i numeri del brief.
  La giornata intera (fuso dello shop) fa però **25 eleggibili, 19 con variante (76%)**: il 20/15 del brief coincide
  con la prima pagina da 50 ordini della query. Il collettore pagina sempre fino in fondo.
- #180733LY (due Mens Braided Cord, variante `live`) è davvero un orfano.
- 8–12 settembre (fino alle 9:36 di New York): ordini con variante **76 = live 27 · classic 27 · chat 22**, di cui
  3 orfani; con l'ordine isolato del 4 settembre si arriva ai 77 / 28-27-22 del brief. Copertura per giorno:
  76% · 77% · 60% · 88% (l'11 settembre 23 su 26).
- Fra gli eleggibili senza variante ce ne sono alcuni con **nessun attributo** (nemmeno `__ref_id`, es. #180746LY con
  `_created_at` del 29 agosto, #180864–866LY del 10 settembre): carrelli nati prima del test o un percorso che non
  esegue lo script. È un indizio utile per il team che deve chiudere la copertura.

## Configurazione

Tutte le soglie sono nel blocco `CONFIG` in testa a `app.js` (base minima 150, base piccola 40, calo 5 punti, volume
minimo 10, quota di recupero 0,5, preset 30/90/tutto, più le soglie A/B). I colori sono solo nel `:root` di `index.html`:
la scala di calore della matrice è interpolata a runtime fra `--heat-0` e `--garnet`.

Costi unitari (`costs`) e tetti pietre per linea (`cap`) stanno in `lines.json` e finiscono nei JSON: la pagina non li conosce.

## Decisioni aperte

- Quali prodotti fuori dalla regola del brief entrano nelle linee (`_unmapped_candidates` in `lines.json`).
- Alcune schede vendono ancora la pietra come **variante** (`January - Garnet` a 66,90 $) invece che come proprietà +
  prodotto figlio: per la regola del brief non sono pietre. Il collettore conta queste righe e le segnala nei log.
- I due upsell nuovi in bozza (*Jewelry Care Kit*, *Charmlry Jewelry Case*) non sono tracciati: basta aggiungerli in
  `cart` di `lines.json`, in `CART_KEYS` (collect.py e calc.js) e nelle etichette di `render.js`: la colonna compare da sola.
