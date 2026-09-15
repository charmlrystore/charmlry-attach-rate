#!/usr/bin/env python3
"""
collect.py — raccolta notturna dell'attach rate degli upsell (store Shopify Charmlry).

Comandi
  python3 collect.py daily     [--days 7] [--through YYYY-MM-DD]     giro notturno: ricalcola gli ultimi N giorni
  python3 collect.py backfill  [--from 2026-01-01] [--jsonl FILE]    storico completo via bulk operation (una tantum)
  python3 collect.py discover                                        elenca il catalogo e i configurabili non mappati

Variabili d'ambiente (mai nel client, mai nel repo):
  SHOPIFY_SHOP           es. charmlry.myshopify.com
  SHOPIFY_ADMIN_TOKEN    token Admin API (scope read_orders + read_all_orders, read_products)
  SHOPIFY_API_VERSION    default 2026-07

Output: data/YYYY-MM.json (un file per mese, schema del brief) + data/index.json (manifest letto dalla pagina).
Solo aggregati: nessun ordine singolo, nessun dato personale.

Le regole di calcolo (sezione 4 del brief) sono commentate accanto al codice che le applica, cercare "REGOLA".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = 1
ARMS = ("live", "classic", "chat")
CART_KEYS = ("scratch", "giftbox", "warranty", "tip")
MAX_DIST = 6  # la distribuzione va da 1 a 6 pietre

# REGOLA — chiavi valide delle line item properties: "Extra charm 1..6" e "Birthstone 1..6".
# Il naming cambia per prodotto (zodiac usa "Birthstone N", monogram usa "Extra charm N") e in alcuni
# prodotti compare anche la chiave non numerata "Extra charm" (di norma vuota): la si accetta se valorizzata.
STONE_KEY = re.compile(r"^(extra charm|birthstone)\s*\d*$", re.IGNORECASE)
MONTH_RE = re.compile(r"^(january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------- configurazione
class Cfg:
    """Mappa prodotto → linea letta da lines.json. Si ragiona SOLO per product ID (REGOLA: mai per titolo)."""

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self.raw = raw
        self.tz = ZoneInfo(raw.get("timezone", "America/New_York"))
        self.line_names: list[str] = list(raw["lines"].keys())
        self.caps = {n: int(v.get("cap", 0)) for n, v in raw["lines"].items()}
        self.line_by_pid: dict[int, str] = {}
        for name, v in raw["lines"].items():
            for p in v["products"]:
                self.line_by_pid[int(p["id"])] = name
        # REGOLA — i prodotti figlio (Birthstone, Necklace Birthstone, Bracelet Birthstone, Cord Bracelet Birthstone)
        # servono solo per stoneRev, mai per il conteggio delle pietre.
        self.child_ids = {int(p["id"]) for p in raw["stoneChildren"]}
        self.cart_by_pid: dict[int, str] = {}
        for key, v in raw["cart"].items():
            for pid in v.get("ids", []):
                self.cart_by_pid[int(pid)] = key
        self.excluded_ids = {int(p["id"]) for p in raw.get("excluded", [])}
        self.test_ids = {int(p) for p in raw["testProduct"]["ids"]}
        self.costs = raw.get("costs", {})
        self.known = set(self.line_by_pid) | self.child_ids | set(self.cart_by_pid) | self.excluded_ids


def load_cfg(path: str | None = None) -> Cfg:
    return Cfg(path or os.path.join(HERE, "lines.json"))


# ----------------------------------------------------------------------------- client Shopify
class Shopify:
    def __init__(self):
        self.shop = os.environ.get("SHOPIFY_SHOP", "").strip()
        self.token = os.environ.get("SHOPIFY_ADMIN_TOKEN", "").strip()
        self.version = os.environ.get("SHOPIFY_API_VERSION", "2026-07").strip()
        if not self.shop or not self.token:
            sys.exit("Servono SHOPIFY_SHOP e SHOPIFY_ADMIN_TOKEN nell'ambiente (repository secrets su GitHub).")
        self.shop = re.sub(r"^https?://", "", self.shop).strip("/")
        if not self.shop.endswith(".myshopify.com"):
            log(f"attenzione: SHOPIFY_SHOP='{self.shop}' non è un dominio .myshopify.com (es. nome-negozio.myshopify.com): "
                f"con il dominio personalizzato l'Admin API di solito risponde 404")
        self.url = f"https://{self.shop}/admin/api/{self.version}/graphql.json"

    def gql(self, query: str, variables: dict | None = None, attempts: int = 8) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        for attempt in range(attempts):
            req = urllib.request.Request(
                self.url, data=body, method="POST",
                headers={"Content-Type": "application/json", "X-Shopify-Access-Token": self.token},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    payload = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    sys.exit(f"HTTP {e.code} da Shopify: token non valido oppure custom app senza gli scope "
                             f"read_orders, read_all_orders, read_products (dopo averli aggiunti va cliccato 'Installa app').")
                if e.code == 404:
                    sys.exit(f"HTTP 404 da Shopify: SHOPIFY_SHOP='{self.shop}' non è raggiungibile. "
                             f"Serve il dominio nome-negozio.myshopify.com (Impostazioni → Domini).")
                if e.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                    wait = float(e.headers.get("Retry-After") or 2 ** attempt)
                    log(f"  HTTP {e.code}, riprovo tra {wait:.0f}s")
                    time.sleep(wait)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            errors = payload.get("errors") or []
            if errors:
                if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                    ts = ((payload.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
                    need = ((payload.get("extensions") or {}).get("cost") or {}).get("requestedQueryCost", 500)
                    avail = ts.get("currentlyAvailable", 0)
                    rate = ts.get("restoreRate", 50) or 50
                    wait = max(1.0, (need - avail) / rate + 0.5)
                    log(f"  throttled, attendo {wait:.1f}s")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"GraphQL: {errors}")
            # rispetto del budget: se il bucket si sta svuotando, rallento prima di essere throttlati
            ts = ((payload.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
            if ts and ts.get("currentlyAvailable", 1e9) < 300:
                time.sleep(2)
            return payload["data"]
        raise RuntimeError("troppi tentativi")


# Query del giro quotidiano (non bulk). customAttributes compare DUE volte, a due livelli: sull'ordine porta
# la variante dell'A/B test (ab_customizer, ab_source), sul line item la configurazione del pezzo. Servono entrambe.
ORDERS_QUERY = """
query($q: String!, $first: Int!, $after: String, $lines: Int!) {
  orders(first: $first, after: $after, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt cancelledAt displayFinancialStatus test
      totalTipReceivedSet { shopMoney { amount } }
      customAttributes { key value }
      lineItems(first: $lines) {
        pageInfo { hasNextPage endCursor }
        nodes {
          title quantity sku
          product { id }
          variant { title }
          originalUnitPriceSet { shopMoney { amount } }
          discountedUnitPriceSet { shopMoney { amount } }
          customAttributes { key value }
        }
      }
    }
  }
}
"""

MORE_LINES_QUERY = """
query($id: ID!, $after: String) {
  order(id: $id) {
    lineItems(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        title quantity sku
        product { id }
        variant { title }
        originalUnitPriceSet { shopMoney { amount } }
        discountedUnitPriceSet { shopMoney { amount } }
        customAttributes { key value }
      }
    }
  }
}
"""

# Bulk operation per il backfill. Note operative (brief §2):
#  - l'`id` sul nodo ordine è OBBLIGATORIO con connessioni annidate;
#  - il risultato è JSONL con una riga per oggetto, i line item hanno __parentId → vanno ricomposti;
#  - ~42.000 ordini ≈ 5 minuti, ~55 MB; l'URL scade dopo 7 giorni.
BULK_MUTATION = """
mutation RunBulk($q: String!) {
  bulkOperationRunQuery(query: $q) {
    bulkOperation { id status url }
    userErrors { field message }
  }
}
"""
BULK_QUERY_TEMPLATE = """
{ orders(query: "%s") { edges { node { id name createdAt cancelledAt displayFinancialStatus test
  totalTipReceivedSet { shopMoney { amount } }
  customAttributes { key value }
  lineItems { edges { node { id title quantity sku product { id } variant { title }
    originalUnitPriceSet { shopMoney { amount } }
    discountedUnitPriceSet { shopMoney { amount } }
    customAttributes { key value } } } } } } } }
"""
BULK_STATUS_QUERY = """
{ currentBulkOperation { id status errorCode objectCount url createdAt completedAt } }
"""

PRODUCTS_QUERY = """
query($after: String) {
  products(first: 100, after: $after, sortKey: TITLE) {
    pageInfo { hasNextPage endCursor }
    nodes { id title handle status tags createdAt }
  }
}
"""


def gid_num(gid: str | None) -> int | None:
    if not gid:
        return None
    try:
        return int(str(gid).rsplit("/", 1)[-1])
    except ValueError:
        return None


def money(node) -> float:
    try:
        return float(((node or {}).get("shopMoney") or {}).get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def attrs_to_dict(items) -> dict[str, str]:
    out = {}
    for a in items or []:
        k = a.get("key")
        if k is None:
            continue
        out[str(k)] = "" if a.get("value") is None else str(a.get("value"))
    return out


def normalize_line(li: dict) -> dict:
    return {
        "pid": gid_num((li.get("product") or {}).get("id")),
        "title": li.get("title") or "",
        "qty": int(li.get("quantity") or 0),
        "orig": money(li.get("originalUnitPriceSet")),
        "disc": money(li.get("discountedUnitPriceSet")),
        "variant": ((li.get("variant") or {}).get("title") or ""),
        "props": attrs_to_dict(li.get("customAttributes")),
    }


def normalize_order(o: dict, lines: list[dict]) -> dict:
    return {
        "id": o.get("id"),
        "name": o.get("name"),
        "created": o.get("createdAt"),
        "cancelled": bool(o.get("cancelledAt")),
        "status": o.get("displayFinancialStatus"),
        "test": bool(o.get("test")),
        "tip": money(o.get("totalTipReceivedSet")),
        "attrs": attrs_to_dict(o.get("customAttributes")),
        "lines": lines,
    }


# ----------------------------------------------------------------------------- fetch quotidiano
def fetch_orders(api: Shopify, start_utc: datetime, end_utc: datetime, page: int = 20, lines: int = 40):
    """Ordini CREATI nell'intervallo [start, end). Si ricalcolano per intero i giorni della finestra (REGOLA: sempre
    7 giorni indietro, perché gli ordini cambiano stato). Si filtra su created_at e non su updated_at: un giorno si
    può ricalcolare correttamente solo avendo TUTTI i suoi ordini, non solo quelli toccati di recente."""
    q = f"created_at:>='{start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}' AND created_at:<'{end_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
    after = None
    n = 0
    while True:
        data = api.gql(ORDERS_QUERY, {"q": q, "first": page, "after": after, "lines": lines})
        conn = data["orders"]
        for o in conn["nodes"]:
            items = [normalize_line(li) for li in o["lineItems"]["nodes"]]
            pi = o["lineItems"]["pageInfo"]
            cursor = pi.get("endCursor")
            while pi.get("hasNextPage"):  # raro: ordini con più di `lines` righe
                more = api.gql(MORE_LINES_QUERY, {"id": o["id"], "after": cursor})["order"]["lineItems"]
                items += [normalize_line(li) for li in more["nodes"]]
                pi, cursor = more["pageInfo"], more["pageInfo"].get("endCursor")
            n += 1
            yield normalize_order(o, items)
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
        if n % 200 == 0:
            log(f"  {n} ordini…")


# ----------------------------------------------------------------------------- backfill (bulk)
def run_bulk(api: Shopify, start_utc: datetime, out_path: str) -> str:
    q = BULK_QUERY_TEMPLATE % f"created_at:>='{start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}'"
    res = api.gql(BULK_MUTATION, {"q": q})["bulkOperationRunQuery"]
    if res["userErrors"]:
        raise RuntimeError(f"bulkOperationRunQuery: {res['userErrors']}")
    op_id = res["bulkOperation"]["id"]
    log(f"bulk operation avviata: {op_id}")
    t0 = time.time()
    while True:
        time.sleep(10)
        st = api.gql(BULK_STATUS_QUERY)["currentBulkOperation"] or {}
        log(f"  {st.get('status')} · oggetti {st.get('objectCount')} · {time.time() - t0:.0f}s")
        if st.get("status") == "COMPLETED":
            url = st.get("url")
            break
        if st.get("status") in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"bulk operation {st.get('status')}: {st.get('errorCode')}")
    if not url:
        raise RuntimeError("bulk completata ma senza url (nessun ordine?)")
    log("scarico il JSONL…")
    with urllib.request.urlopen(url, timeout=600) as r, open(out_path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    log(f"  salvato in {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB)")
    return out_path


def iter_jsonl_orders(path: str):
    """Ricompone ordini e line item dal JSONL della bulk operation.

    Una riga per oggetto: gli ordini non hanno __parentId, i line item sì e puntano all'ordine. I figli seguono il
    padre, ma per non dipendere dalla contiguità si accumula per id e si emette alla fine (≈ decine di MB, va bene)."""
    orders: dict[str, dict] = {}
    lines: dict[str, list] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            parent = obj.get("__parentId")
            if parent is None:
                orders[obj["id"]] = obj
            else:
                lines[parent].append(normalize_line(obj))
    orphans = set(lines) - set(orders)
    if orphans:
        log(f"attenzione: {len(orphans)} gruppi di line item senza ordine nel JSONL")
    for oid, o in orders.items():
        yield normalize_order(o, lines.get(oid, []))


# ----------------------------------------------------------------------------- calcolo
def in_scope(o: dict) -> bool:
    """REGOLA (perimetro): fuori gli ordini annullati, quelli non pagati (status diverso da PAID/PARTIALLY_REFUNDED)
    e gli ordini di test."""
    if o["cancelled"] or o["test"]:
        return False
    return o["status"] in ("PAID", "PARTIALLY_REFUNDED")


def stones_per_unit(props: dict) -> int:
    """REGOLA: le pietre si contano dalle line item properties del pezzo, solo chiavi valide con valore non vuoto."""
    return sum(1 for k, v in props.items() if STONE_KEY.match(k.strip()) and str(v).strip())


def empty_day(cfg: Cfg) -> dict:
    return {
        "orders": 0, "revenue": 0.0, "noAddon": 0, "stoneRev": 0.0,
        "cart": {k: {"orders": 0, "rev": 0.0} for k in CART_KEYS},
        "lines": {n: {"rows": 0, "stoneRows": 0, "stones": 0, "orders": 0,
                      "scratch": 0, "giftbox": 0, "warranty": 0, "tip": 0,
                      "distDays": [0] * MAX_DIST} for n in cfg.line_names},
        "ab": {"eligible": 0, "tagged": 0, "orphan": 0,
               "variants": {v: {"orders": 0, "tagged": 0, "stoneRows": 0, "rows": 0, "new": 0, "stored": 0} for v in ARMS}},
    }


def local_day(created_iso: str, tz: ZoneInfo) -> str:
    dt = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
    return dt.astimezone(tz).date().isoformat()


class Diagnostics:
    def __init__(self):
        self.unmapped = Counter()        # pid → pezzi di prodotti non presenti in lines.json
        self.unmapped_title = {}
        self.variant_stone_no_props = 0  # righe con variante "Mese - Pietra" ma senza properties pietra
        self.over_cap = Counter()        # linea → righe con più di MAX_DIST pietre
        self.orders = 0
        self.skipped = 0

    def report(self) -> None:
        log(f"ordini letti {self.orders}, fuori perimetro {self.skipped}")
        if self.variant_stone_no_props:
            log(f"attenzione: {self.variant_stone_no_props} righe con variante birthstone (es. 'January - Garnet') senza "
                f"properties pietra: per la regola del brief NON sono contate come pietre")
        for line, n in self.over_cap.items():
            log(f"attenzione: {n} righe di {line} con più di {MAX_DIST} pietre, messe nell'ultimo bucket")
        if self.unmapped:
            log("prodotti fuori mappa (non in lines.json), per volume:")
            for pid, n in self.unmapped.most_common(15):
                log(f"  {pid:>15}  {n:>6} pz  {self.unmapped_title.get(pid, '')}")


def aggregate(orders, cfg: Cfg, diag: Diagnostics | None = None) -> dict[str, dict]:
    """Aggrega gli ordini in perimetro per giorno (fuso orario dello shop). Ritorna {giorno: record}."""
    diag = diag or Diagnostics()
    days: dict[str, dict] = {}
    for o in orders:
        diag.orders += 1
        if not in_scope(o):
            diag.skipped += 1
            continue
        day = local_day(o["created"], cfg.tz)
        d = days.get(day)
        if d is None:
            d = days[day] = empty_day(cfg)

        d["orders"] += 1
        revenue = 0.0
        stone_rev = 0.0
        child_lines = 0
        cart_seen: set[str] = set()
        line_seen: set[str] = set()
        line_stats: dict[str, dict] = {}
        eligible = False
        test_rows = 0
        test_stone_rows = 0
        property_stones = 0

        for li in o["lines"]:
            pid, qty = li["pid"], li["qty"]
            if qty <= 0:
                continue
            if pid is None:
                # righe senza prodotto: la mancia ("Tip") e prodotti cancellati
                if li["title"].strip().lower() == "tip":
                    cart_seen.add("tip")
                continue
            # REGOLA: revenue = valore merce, quantità × prezzo pieno, su tutte le righe prodotto
            revenue += qty * li["orig"]
            if pid in cfg.child_ids:
                # REGOLA: i figli birthstone contano solo per stoneRev (incasso effettivo, prezzo scontato)
                stone_rev += qty * li["disc"]
                child_lines += 1
                continue
            cart_key = cfg.cart_by_pid.get(pid)
            if cart_key:
                cart_seen.add(cart_key)
                d["cart"][cart_key]["rev"] += qty * li["disc"]
                continue
            line = cfg.line_by_pid.get(pid)
            if line is None:
                if pid not in cfg.excluded_ids:
                    diag.unmapped[pid] += qty
                    diag.unmapped_title.setdefault(pid, li["title"])
                continue
            # pezzo configurabile
            k = stones_per_unit(li["props"])
            if k == 0 and MONTH_RE.match(li["variant"] or ""):
                diag.variant_stone_no_props += 1
            if k > MAX_DIST:
                diag.over_cap[line] += 1
            ls = line_stats.setdefault(line, {"rows": 0, "stoneRows": 0, "stones": 0, "dist": [0] * MAX_DIST})
            ls["rows"] += qty
            if k > 0:
                # REGOLA: moltiplicare per la quantità della riga (q=2 con 3 charm = 6 pietre, 2 pezzi con pietre)
                ls["stoneRows"] += qty
                ls["stones"] += qty * k
                ls["dist"][min(k, MAX_DIST) - 1] += qty
                property_stones += qty * k
            line_seen.add(line)
            if pid in cfg.test_ids:
                eligible = True
                test_rows += qty
                if k > 0:
                    test_stone_rows += qty

        # mancia: preferisco il campo dell'ordine, la riga "Tip" è il fallback
        if o["tip"] > 0:
            cart_seen.add("tip")
            d["cart"]["tip"]["rev"] += o["tip"]
        elif "tip" in cart_seen:
            d["cart"]["tip"]["rev"] += sum(li["qty"] * li["disc"] for li in o["lines"]
                                           if li["pid"] is None and li["title"].strip().lower() == "tip")

        d["revenue"] += revenue
        d["stoneRev"] += stone_rev
        for ck in cart_seen:
            d["cart"][ck]["orders"] += 1
        for line, ls in line_stats.items():
            L = d["lines"][line]
            L["rows"] += ls["rows"]
            L["stoneRows"] += ls["stoneRows"]
            L["stones"] += ls["stones"]
            for i in range(MAX_DIST):
                L["distDays"][i] += ls["dist"][i]
            # REGOLA (denominatori): gli add-on di carrello si misurano sugli ORDINI che contengono la linea,
            # le pietre sui PEZZI della linea stessa (rows). Una collana con pietre nello stesso carrello
            # non rende "con pietre" il bracciale: le pietre restano attaccate al pezzo che le porta.
            L["orders"] += 1
            for ck in cart_seen:
                L[ck] += 1
        if not property_stones and not child_lines and not cart_seen:
            d["noAddon"] += 1

        # A/B test customizer: la variante è un note attribute a LIVELLO ORDINE
        arm = (o["attrs"].get("ab_customizer") or "").strip().lower()
        src = (o["attrs"].get("ab_source") or "").strip().lower()
        ab = d["ab"]
        if eligible:
            ab["eligible"] += 1
        if arm in ARMS:
            V = ab["variants"][arm]
            V["tagged"] += 1
            if src in ("new", "stored"):
                V[src] += 1
            if eligible:
                ab["tagged"] += 1
                V["orders"] += 1
                V["rows"] += test_rows
                V["stoneRows"] += test_stone_rows
            else:
                # assegnazione per sessione, non per prodotto: ordine con variante ma senza il prodotto sotto test
                ab["orphan"] += 1
        elif arm:
            log(f"  valore ab_customizer sconosciuto '{arm}' su {o['name']}: ignorato")
    return days


# ----------------------------------------------------------------------------- storage (data/YYYY-MM.json)
def month_of(day: str) -> str:
    return day[:7]


def daterange(a: str, b: str):
    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
    while d0 <= d1:
        yield d0.isoformat()
        d0 += timedelta(days=1)


def load_store(data_dir: str, cfg: Cfg) -> tuple[dict[str, dict], str | None]:
    """Legge i mensili esistenti e li riporta al formato per-giorno. Ritorna (store, source)."""
    store: dict[str, dict] = {}
    source = None
    if not os.path.isdir(data_dir):
        return store, source
    for fn in sorted(os.listdir(data_dir)):
        if not re.match(r"^\d{4}-\d{2}\.json$", fn):
            continue
        with open(os.path.join(data_dir, fn), encoding="utf-8") as f:
            doc = json.load(f)
        source = doc.get("source", source)
        for i, day in enumerate(doc["days"]):
            d = empty_day(cfg)
            d["orders"] = doc["orders"][i]
            d["revenue"] = doc["revenue"][i]
            d["noAddon"] = doc["noAddon"][i]
            d["stoneRev"] = doc["stoneRev"][i]
            for k in CART_KEYS:
                c = doc["cart"].get(k)
                if c:
                    d["cart"][k] = {"orders": c["orders"][i], "rev": c["rev"][i]}
            for name in cfg.line_names:
                L = doc["lines"].get(name)
                if not L:
                    continue
                dl = d["lines"][name]
                for k in ("rows", "stoneRows", "stones", "orders", "scratch", "giftbox", "warranty", "tip"):
                    dl[k] = L[k][i]
                if "distDays" in L:
                    dl["distDays"] = list(L["distDays"][i])
                else:
                    dl["distDays"] = None  # seed del prototipo: distribuzione giornaliera non disponibile
            ab = doc.get("ab")
            if ab:
                for k in ("eligible", "tagged", "orphan"):
                    d["ab"][k] = ab[k][i]
                for v in ARMS:
                    V = (ab.get("variants") or {}).get(v)
                    if V:
                        for k in ("orders", "tagged", "stoneRows", "rows", "new", "stored"):
                            if k in V:
                                d["ab"]["variants"][v][k] = V[k][i]
            store[day] = d
    return store, source


def series(store: dict, days: list[str], getter) -> list:
    return [getter(store[d]) for d in days]


def write_store(store: dict[str, dict], cfg: Cfg, data_dir: str, generated: str, source: str = "collect.py") -> None:
    os.makedirs(data_dir, exist_ok=True)
    if not store:
        raise RuntimeError("nessun dato da scrivere")
    first, last = min(store), max(store)
    # giorni contigui: i giorni senza ordini esistono comunque, a zero
    for day in daterange(first, last):
        store.setdefault(day, empty_day(cfg))
    all_days = sorted(store)

    # campi cumulativi sull'intero storico, rigenerati per intero a ogni giro (sono piccoli)
    dist = {n: [0] * MAX_DIST for n in cfg.line_names}
    stones_from = {n: None for n in cfg.line_names}
    line_from = {n: None for n in cfg.line_names}
    for day in all_days:
        for n in cfg.line_names:
            L = store[day]["lines"][n]
            if L["rows"] > 0 and line_from[n] is None:
                line_from[n] = day
            # REGOLA (disponibilità): stonesFrom = prima pietra venduta sulla linea; prima di quella data
            # la pagina mostra la cella vuota, non zero.
            if L["stoneRows"] > 0 and stones_from[n] is None:
                stones_from[n] = day
            if L["distDays"]:
                for i in range(MAX_DIST):
                    dist[n][i] += L["distDays"][i]

    months = sorted({month_of(d) for d in all_days})
    for m in months:
        days = [d for d in all_days if month_of(d) == m]
        S = store
        doc = {
            "schema": SCHEMA,
            "source": source,
            "generated": generated,
            "month": m,
            "days": days,
            "orders": series(S, days, lambda d: d["orders"]),
            "revenue": series(S, days, lambda d: round(d["revenue"], 2)),
            "noAddon": series(S, days, lambda d: d["noAddon"]),
            "stoneRev": series(S, days, lambda d: round(d["stoneRev"], 2)),
            "cart": {k: {"orders": series(S, days, lambda d, k=k: d["cart"][k]["orders"]),
                         "rev": series(S, days, lambda d, k=k: round(d["cart"][k]["rev"], 2))} for k in CART_KEYS},
            "lines": {},
            "ab": {
                "eligible": series(S, days, lambda d: d["ab"]["eligible"]),
                "tagged": series(S, days, lambda d: d["ab"]["tagged"]),
                "orphan": series(S, days, lambda d: d["ab"]["orphan"]),
                "variants": {v: {k: series(S, days, lambda d, v=v, k=k: d["ab"]["variants"][v][k])
                                 for k in ("orders", "tagged", "stoneRows", "rows", "new", "stored")} for v in ARMS},
            },
            "stonesFrom": stones_from,
            "costs": cfg.costs,
        }
        for n in cfg.line_names:
            doc["lines"][n] = {
                "from": line_from[n],
                "rows": series(S, days, lambda d, n=n: d["lines"][n]["rows"]),
                "stoneRows": series(S, days, lambda d, n=n: d["lines"][n]["stoneRows"]),
                "stones": series(S, days, lambda d, n=n: d["lines"][n]["stones"]),
                "orders": series(S, days, lambda d, n=n: d["lines"][n]["orders"]),
                "scratch": series(S, days, lambda d, n=n: d["lines"][n]["scratch"]),
                "giftbox": series(S, days, lambda d, n=n: d["lines"][n]["giftbox"]),
                "warranty": series(S, days, lambda d, n=n: d["lines"][n]["warranty"]),
                "tip": series(S, days, lambda d, n=n: d["lines"][n]["tip"]),
                "distDays": series(S, days, lambda d, n=n: d["lines"][n]["distDays"] or [0] * MAX_DIST),
                "dist": dist[n],
                "cap": cfg.caps[n],
            }
        path = os.path.join(data_dir, f"{m}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(data_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": SCHEMA, "generated": generated, "through": last, "months": months, "source": source}, f, indent=2)
    log(f"scritti {len(months)} mesi in {data_dir} · giorni {first} → {last}")


# ----------------------------------------------------------------------------- comandi
def local_midnight_utc(day: str, tz: ZoneInfo) -> datetime:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)


def cmd_daily(args, cfg: Cfg) -> None:
    today_local = datetime.now(cfg.tz).date()
    through = args.through or (today_local - timedelta(days=1)).isoformat()   # solo giorni completi
    start = (date.fromisoformat(through) - timedelta(days=args.days - 1)).isoformat()
    store, source = load_store(args.data_dir, cfg)
    if store and source == "prototype-seed":
        sys.exit("I file in data/ sono il seed del prototipo (senza distribuzione giornaliera delle pietre): "
                 "prima di usare `daily` va eseguito `python3 collect.py backfill`.")
    api = Shopify()
    log(f"giro quotidiano: ricalcolo {start} → {through} ({args.days} giorni, fuso {cfg.tz.key})")
    diag = Diagnostics()
    fresh = aggregate(fetch_orders(api, local_midnight_utc(start, cfg.tz),
                                   local_midnight_utc((date.fromisoformat(through) + timedelta(days=1)).isoformat(), cfg.tz),
                                   page=args.page_size), cfg, diag)
    diag.report()
    for day in daterange(start, through):
        store[day] = fresh.get(day, empty_day(cfg))
    if args.dry_run:
        print(json.dumps({d: store[d] for d in daterange(start, through)}, indent=1))
        return
    write_store(store, cfg, args.data_dir, generated=datetime.now(timezone.utc).date().isoformat())


def cmd_backfill(args, cfg: Cfg) -> None:
    start = args.from_date
    today_local = datetime.now(cfg.tz).date()
    through = args.through or (today_local - timedelta(days=1)).isoformat()
    if args.jsonl:
        path = args.jsonl
        log(f"backfill da file {path}")
    else:
        api = Shopify()
        path = os.path.join(tempfile.gettempdir(), f"charmlry-orders-{start}.jsonl")
        run_bulk(api, local_midnight_utc(start, cfg.tz), path)
    diag = Diagnostics()
    days = aggregate(iter_jsonl_orders(path), cfg, diag)
    diag.report()
    # il backfill rigenera tutto lo storico dalla data di partenza: i giorni dopo `through` (oggi, parziale) si scartano
    store = {d: v for d, v in days.items() if start <= d <= through}
    store.setdefault(start, empty_day(cfg))
    store.setdefault(through, empty_day(cfg))
    if args.dry_run:
        totals(store, cfg)
        return
    write_store(store, cfg, args.data_dir, generated=datetime.now(timezone.utc).date().isoformat())
    totals(store, cfg)


def totals(store: dict, cfg: Cfg) -> None:
    """Riepilogo di controllo (sezione 8 del brief: attach pietre annuo per linea)."""
    log("attach pietre sull'intero periodo:")
    for n in cfg.line_names:
        rows = sum(d["lines"][n]["rows"] for d in store.values())
        sr = sum(d["lines"][n]["stoneRows"] for d in store.values())
        log(f"  {n:15s} {sr:>7}/{rows:<7} = {100 * sr / rows if rows else 0:5.1f}%")
    ab_tagged = {v: sum(d["ab"]["variants"][v]["tagged"] for d in store.values()) for v in ARMS}
    log(f"  A/B ordini con variante: {sum(ab_tagged.values())} {ab_tagged}, orfani {sum(d['ab']['orphan'] for d in store.values())}")


def cmd_discover(args, cfg: Cfg) -> None:
    api = Shopify()
    after = None
    rows = []
    while True:
        data = api.gql(PRODUCTS_QUERY, {"after": after})["products"]
        for p in data["nodes"]:
            pid = gid_num(p["id"])
            where = cfg.line_by_pid.get(pid) or ("figlio pietra" if pid in cfg.child_ids else None) \
                or (f"carrello:{cfg.cart_by_pid[pid]}" if pid in cfg.cart_by_pid else None) \
                or ("escluso" if pid in cfg.excluded_ids else "— NON MAPPATO —")
            rows.append((where, pid, p["status"], p["title"], p["handle"], ",".join(p.get("tags") or [])))
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    rows.sort(key=lambda r: (r[0].startswith("—") is False, r[0], r[3]))
    for where, pid, status, title, handle, tags in rows:
        print(f"{where:18s} {pid:>15} {status:8s} {title[:60]:60s} {handle[:50]:50s} {tags}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "lines.json"))
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily", help="ricalcola gli ultimi N giorni (default 7)")
    d.add_argument("--days", type=int, default=7)
    d.add_argument("--through", help="ultimo giorno incluso (default: ieri nel fuso dello shop)")
    d.add_argument("--page-size", type=int, default=20)
    d.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("backfill", help="storico completo via bulk operation")
    b.add_argument("--from", dest="from_date", default="2026-01-01")
    b.add_argument("--through", help="ultimo giorno incluso (default: ieri nel fuso dello shop)")
    b.add_argument("--jsonl", help="usa un JSONL già scaricato invece di lanciare la bulk operation")
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("discover", help="elenca il catalogo con lo stato di mappatura")
    args = ap.parse_args(argv)
    cfg = load_cfg(args.config)
    {"daily": cmd_daily, "backfill": cmd_backfill, "discover": cmd_discover}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
