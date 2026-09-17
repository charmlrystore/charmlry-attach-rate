"""Test delle regole di calcolo di collect.py su ordini sintetici (nessuna chiamata a Shopify).

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect  # noqa: E402

cfg = collect.load_cfg()

# product id reali (lines.json)
FOR_HER = 8677371707724          # Collane, prodotto sotto test A/B
FAMILY = 14686494523724          # Collane
CHARM_BR = 14694506758476        # Charm Bracelet (ex "Bespoke Monogram Bracelet")
MENS = 15645716218188            # Mens cord
KIDS = 15729645846860            # Kids cord
NECK_STONE = 15689729868108      # figlio: Necklace Birthstone
CORD_STONE = 15930898841932      # figlio: Cord Bracelet Birthstone
GIFTBOX = 9022872453452
SCRATCH = 14966466380108
WARRANTY = 8681019048268
CHARM_COMPONENT = 14694488736076  # escluso


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def gid(pid):
    return None if pid is None else f"gid://shopify/Product/{pid}"


def line(pid, qty=1, price="59.0", disc=None, props=None, title="x", variant="Default Title"):
    return {
        "title": title, "quantity": qty, "sku": None,
        "product": {"id": gid(pid)} if pid else None,
        "variant": {"title": variant} if pid else None,
        "originalUnitPriceSet": {"shopMoney": {"amount": price}},
        "discountedUnitPriceSet": {"shopMoney": {"amount": disc if disc is not None else price}},
        "customAttributes": [{"key": k, "value": v} for k, v in (props or {}).items()],
    }


def order(lines, created="2026-09-08T15:00:00Z", status="PAID", cancelled=None, test=False, tip="0.0", attrs=None, name="#1"):
    return collect.normalize_order({
        "id": "gid://shopify/Order/1", "name": name, "createdAt": created, "cancelledAt": cancelled,
        "displayFinancialStatus": status, "test": test,
        "totalTipReceivedSet": {"shopMoney": {"amount": tip}},
        "customAttributes": [{"key": k, "value": v} for k, v in (attrs or {}).items()],
    }, [collect.normalize_line(li) for li in lines])


def agg(*orders):
    return collect.aggregate(list(orders), cfg)


class Perimetro(unittest.TestCase):
    def test_esclusioni(self):
        plain = [line(FOR_HER)]
        days = agg(
            order(plain, cancelled="2026-09-08T16:00:00Z"),
            order(plain, status="PENDING"),
            order(plain, status="REFUNDED"),
            order(plain, test=True),
            order(plain, status="PARTIALLY_REFUNDED"),
            order(plain, status="PAID"),
        )
        self.assertEqual(days["2026-09-08"]["orders"], 2)


class Pietre(unittest.TestCase):
    def test_conteggio_pesato_per_quantita(self):
        # bracciale con quantity 2 e 3 Extra charm = 6 pietre e 2 pezzi con pietre
        d = agg(order([line(CHARM_BR, qty=2, props={"Extra charm 1": "May - Emerald", "Extra charm 2": "July - Ruby",
                                                    "Extra charm 3": "August - Peridot", "name": "AB"})]))["2026-09-08"]
        L = d["lines"]["Charm Bracelet"]
        self.assertEqual((L["rows"], L["stoneRows"], L["stones"]), (2, 2, 6))
        self.assertEqual(L["distDays"], [0, 0, 2, 0, 0, 0])

    def test_chiavi_valide(self):
        d = agg(order([line(FAMILY, props={"Birthstone 1": "June - Light Amethyst", "Extra charm": "", "Extra charm 2": " ",
                                           "favorite": "Vertical Blocks", "_design_id": "D-1"})]))["2026-09-08"]
        self.assertEqual(d["lines"]["Collane"]["stones"], 1)
        d = agg(order([line(FAMILY, props={"Extra charm": "May - Emerald"})]))["2026-09-08"]
        self.assertEqual(d["lines"]["Collane"]["stones"], 1, "chiave non numerata valorizzata: conta")
        d = agg(order([line(FAMILY, props={"Extra charm 1": "", "Extra charm 2": ""})]))["2026-09-08"]
        self.assertEqual((d["lines"]["Collane"]["stones"], d["lines"]["Collane"]["stoneRows"]), (0, 0))

    def test_figli_solo_per_stone_rev(self):
        d = agg(order([
            line(FOR_HER, props={"Extra charm 1": "May - Emerald"}),
            line(NECK_STONE, price="8.9", disc="7.9"),
            line(NECK_STONE, price="8.9", disc="7.9"),   # riga figlio in più (es. errore app): non altera le pietre
        ]))["2026-09-08"]
        self.assertEqual(d["lines"]["Collane"]["stones"], 1)
        self.assertAlmostEqual(d["stoneRev"], 15.8)
        self.assertAlmostEqual(d["revenue"], 59 + 8.9 * 2, msg="valore merce a prezzo pieno, figli inclusi")
        self.assertEqual(d["noAddon"], 0)


class Denominatori(unittest.TestCase):
    def test_pietre_sul_pezzo_addon_sull_ordine(self):
        # il caso del Mens cord al 4,6%: le pietre erano della collana nello stesso carrello
        d = agg(order([
            line(FOR_HER, props={"Extra charm 1": "May - Emerald", "Extra charm 2": "July - Ruby"}),
            line(NECK_STONE, price="8.9"), line(NECK_STONE, price="8.9"),
            line(MENS, qty=2, props={"name": "JP", "cord": "Brown"}),
            line(GIFTBOX, price="8.9"),
        ], tip="5.9"))["2026-09-08"]
        collane, mens = d["lines"]["Collane"], d["lines"]["Mens cord"]
        self.assertEqual((collane["rows"], collane["stoneRows"], collane["stones"]), (1, 1, 2))
        self.assertEqual((mens["rows"], mens["stoneRows"], mens["stones"]), (2, 0, 0))
        # add-on di carrello: contano gli ORDINI che contengono la linea
        self.assertEqual((collane["orders"], collane["giftbox"], collane["tip"], collane["scratch"]), (1, 1, 1, 0))
        self.assertEqual((mens["orders"], mens["giftbox"], mens["tip"]), (1, 1, 1))
        self.assertEqual(d["cart"]["giftbox"], {"orders": 1, "rev": 8.9})
        self.assertEqual(d["cart"]["tip"], {"orders": 1, "rev": 5.9})
        self.assertEqual(d["orders"], 1)


class Carrello(unittest.TestCase):
    def test_mancia_da_riga_tip_se_manca_il_campo(self):
        d = agg(order([line(MENS), line(None, price="4.0", title="Tip")], tip="0.0"))["2026-09-08"]
        self.assertEqual(d["cart"]["tip"], {"orders": 1, "rev": 4.0})
        self.assertAlmostEqual(d["revenue"], 59.0, msg="la mancia non è merce")

    def test_no_addon(self):
        days = agg(
            order([line(MENS)], created="2026-09-08T10:00:00Z"),
            order([line(MENS), line(WARRANTY, price="15.0")], created="2026-09-08T11:00:00Z"),
            order([line(MENS), line(SCRATCH, price="6.9")], created="2026-09-08T12:00:00Z"),
            order([line(FAMILY), line(NECK_STONE, price="8.9")], created="2026-09-08T13:00:00Z"),  # figlio senza properties
        )
        d = days["2026-09-08"]
        self.assertEqual((d["orders"], d["noAddon"]), (4, 1))
        self.assertEqual(d["cart"]["warranty"]["orders"], 1)
        self.assertEqual(d["cart"]["scratch"]["orders"], 1)

    def test_charm_componente_escluso(self):
        d = agg(order([line(CHARM_BR, props={"Extra charm 1": "May - Emerald"}), line(CHARM_COMPONENT, price="0.0")]))["2026-09-08"]
        self.assertEqual(d["noAddon"], 0)
        self.assertEqual(sum(v["orders"] for v in d["cart"].values()), 0)


class Raggruppamento(unittest.TestCase):
    def test_per_product_id_non_per_titolo(self):
        d = agg(
            order([line(CHARM_BR, title="Bespoke Monogram Bracelet")], created="2026-03-01T12:00:00Z"),
            order([line(CHARM_BR, title="Bespoke Charm Bracelet")], created="2026-06-01T12:00:00Z"),
            order([line(MENS, title="For Him | Bespoke Monogram Bracelet")], created="2026-06-01T12:00:00Z"),
        )
        self.assertEqual(d["2026-03-01"]["lines"]["Charm Bracelet"]["rows"], 1)
        self.assertEqual(d["2026-06-01"]["lines"]["Charm Bracelet"]["rows"], 1)
        self.assertEqual(d["2026-06-01"]["lines"]["Mens cord"]["rows"], 1)

    def test_prodotto_fuori_mappa_segnalato(self):
        diag = collect.Diagnostics()
        collect.aggregate([order([line(9299591528780, qty=3, title="Custom-Designed Bond to Wear"), line(CHARM_COMPONENT)])], cfg, diag)
        self.assertEqual(diag.unmapped, {9299591528780: 3})


class FusoOrario(unittest.TestCase):
    def test_giorno_dello_shop(self):
        days = agg(order([line(MENS)], created="2026-09-09T03:59:00Z"), order([line(MENS)], created="2026-09-09T04:00:00Z"))
        self.assertEqual(days["2026-09-08"]["orders"], 1)
        self.assertEqual(days["2026-09-09"]["orders"], 1)


class ABTest(unittest.TestCase):
    def test_eleggibili_taggati_orfani(self):
        days = agg(
            order([line(FOR_HER, props={"Extra charm 1": "May - Emerald"}), line(NECK_STONE, price="8.9")],
                  attrs={"ab_customizer": "live", "ab_source": "new"}),
            order([line(FOR_HER, qty=2)], attrs={"ab_customizer": "classic", "ab_source": "stored"}),
            order([line(FOR_HER)], attrs={"__ref_id": "x"}),                                    # eleggibile non taggato
            order([line(MENS, qty=2)], attrs={"ab_customizer": "live", "ab_source": "new"}),     # #180733LY: orfano
            order([line(FOR_HER)], attrs={"ab_customizer": "chat"}),                            # senza ab_source
        )
        ab = days["2026-09-08"]["ab"]
        self.assertEqual((ab["eligible"], ab["tagged"], ab["orphan"]), (4, 3, 1))
        V = ab["variants"]
        self.assertEqual(V["live"], {"orders": 1, "tagged": 2, "stoneRows": 1, "rows": 1, "new": 2, "stored": 0})
        self.assertEqual(V["classic"], {"orders": 1, "tagged": 1, "stoneRows": 0, "rows": 2, "new": 0, "stored": 1})
        self.assertEqual(V["chat"], {"orders": 1, "tagged": 1, "stoneRows": 0, "rows": 1, "new": 0, "stored": 0})


class Storage(unittest.TestCase):
    def test_scrittura_lettura_e_cumulativi(self):
        days = agg(
            order([line(MENS)], created="2026-07-30T12:00:00Z"),
            order([line(MENS, props={"Extra charm 1": "May - Emerald"}), line(CORD_STONE, price="8.9")], created="2026-08-02T12:00:00Z"),
            order([line(CHARM_BR, qty=2, props={"Extra charm 1": "x", "Extra charm 2": "y"})], created="2026-08-02T13:00:00Z"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            collect.write_store(days, cfg, tmp, generated="2026-09-12")
            self.assertEqual(sorted(os.listdir(tmp)), ["2026-07.json", "2026-08.json", "index.json"])
            aug = read_json(os.path.join(tmp, "2026-08.json"))
            self.assertEqual(aug["days"], ["2026-08-01", "2026-08-02"], "giorni contigui, lo 01 esiste a zero")
            self.assertEqual(aug["orders"], [0, 2])
            self.assertEqual(aug["lines"]["Mens cord"]["from"], "2026-07-30")
            self.assertEqual(aug["stonesFrom"]["Mens cord"], "2026-08-02")
            self.assertIsNone(aug["stonesFrom"]["Kids cord"])
            self.assertEqual(aug["lines"]["Charm Bracelet"]["dist"], [0, 2, 0, 0, 0, 0])
            self.assertEqual(aug["lines"]["Charm Bracelet"]["cap"], 4)
            self.assertEqual(aug["stoneRev"], [0, 8.9])
            self.assertEqual(aug["costs"]["stone"], 1.5)
            self.assertEqual(len(aug["ab"]["variants"]["live"]["orders"]), 2)
            jul = read_json(os.path.join(tmp, "2026-07.json"))
            self.assertEqual(jul["days"], ["2026-07-30", "2026-07-31"])
            self.assertEqual(jul["lines"]["Charm Bracelet"]["dist"], [0, 2, 0, 0, 0, 0], "cumulativo identico in ogni mese")
            idx = read_json(os.path.join(tmp, "index.json"))
            self.assertEqual((idx["months"], idx["through"]), (["2026-07", "2026-08"], "2026-08-02"))
            # rilettura e sostituzione di una finestra (giro quotidiano)
            store, source = collect.load_store(tmp, cfg)
            self.assertEqual(source, "collect.py")
            self.assertEqual(store["2026-08-02"]["lines"]["Charm Bracelet"]["distDays"], [0, 2, 0, 0, 0, 0])
            fresh = agg(order([line(MENS)], created="2026-08-02T12:00:00Z"))
            for day in collect.daterange("2026-08-01", "2026-08-03"):
                store[day] = fresh.get(day, collect.empty_day(cfg))
            collect.write_store(store, cfg, tmp, generated="2026-09-13")
            aug = read_json(os.path.join(tmp, "2026-08.json"))
            self.assertEqual(aug["days"], ["2026-08-01", "2026-08-02", "2026-08-03"])
            self.assertEqual(aug["lines"]["Charm Bracelet"]["dist"], [0, 0, 0, 0, 0, 0], "il cumulativo segue i giorni ricalcolati")
            self.assertIsNone(aug["stonesFrom"]["Mens cord"])

    def test_seed_del_prototipo_riconosciuto(self):
        # un mensile "prototype-seed" (senza distDays) in una cartella temporanea: non dipende dal contenuto di data/
        seed = {
            "schema": 1, "source": "prototype-seed", "generated": "2026-09-11", "month": "2026-09",
            "days": ["2026-09-01", "2026-09-02"], "orders": [3, 4], "revenue": [177.0, 236.0],
            "noAddon": [2, 3], "stoneRev": [8.9, 0.0],
            "cart": {k: {"orders": [0, 1], "rev": [0.0, 8.9]} for k in ("scratch", "giftbox", "warranty", "tip")},
            "lines": {"Collane": {"from": "2026-01-01", "rows": [3, 4], "stoneRows": [1, 0], "stones": [2, 0],
                                  "orders": [3, 4], "scratch": [0, 1], "giftbox": [0, 0], "warranty": [0, 0],
                                  "tip": [0, 0], "dist": [1, 1, 0, 0, 0, 0], "cap": 6}},
            "stonesFrom": {"Collane": "2026-01-01"}, "costs": {"stone": 1.5},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "2026-09.json"), "w", encoding="utf-8") as f:
                json.dump(seed, f)
            store, source = collect.load_store(tmp, cfg)
            self.assertEqual(source, "prototype-seed")
            self.assertEqual(sorted(store), ["2026-09-01", "2026-09-02"])
            self.assertEqual(store["2026-09-01"]["lines"]["Collane"]["stoneRows"], 1)
            self.assertIsNone(store["2026-09-01"]["lines"]["Collane"]["distDays"], "seed senza distribuzione giornaliera")
            self.assertEqual(store["2026-09-02"]["ab"]["eligible"], 0, "seed senza blocco ab: tutto a zero")


class JSONL(unittest.TestCase):
    def test_ricomposizione_parent_id(self):
        rows = [
            {"id": "gid://shopify/Order/1", "name": "#1", "createdAt": "2026-09-08T15:00:00Z", "cancelledAt": None,
             "displayFinancialStatus": "PAID", "test": False, "totalTipReceivedSet": {"shopMoney": {"amount": "0.0"}},
             "customAttributes": [{"key": "ab_customizer", "value": "chat"}]},
            {"id": "gid://shopify/LineItem/11", "title": "For Her", "quantity": 1, "product": {"id": gid(FOR_HER)},
             "variant": {"title": "No Birthstone"}, "originalUnitPriceSet": {"shopMoney": {"amount": "59.0"}},
             "discountedUnitPriceSet": {"shopMoney": {"amount": "59.0"}},
             "customAttributes": [{"key": "Extra charm 1", "value": "May - Emerald"}], "__parentId": "gid://shopify/Order/1"},
            {"id": "gid://shopify/LineItem/12", "title": "Necklace Birthstone", "quantity": 1, "product": {"id": gid(NECK_STONE)},
             "variant": {"title": "May - Emerald"}, "originalUnitPriceSet": {"shopMoney": {"amount": "8.9"}},
             "discountedUnitPriceSet": {"shopMoney": {"amount": "8.9"}}, "customAttributes": [], "__parentId": "gid://shopify/Order/1"},
            {"id": "gid://shopify/Order/2", "name": "#2", "createdAt": "2026-09-08T16:00:00Z", "cancelledAt": None,
             "displayFinancialStatus": "PAID", "test": False, "totalTipReceivedSet": {"shopMoney": {"amount": "0.0"}},
             "customAttributes": []},
            {"id": "gid://shopify/LineItem/21", "title": "Mens", "quantity": 1, "product": {"id": gid(MENS)}, "variant": None,
             "originalUnitPriceSet": {"shopMoney": {"amount": "59.0"}}, "discountedUnitPriceSet": {"shopMoney": {"amount": "59.0"}},
             "customAttributes": [], "__parentId": "gid://shopify/Order/2"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        orders = list(collect.iter_jsonl_orders(f.name))
        os.unlink(f.name)
        self.assertEqual([len(o["lines"]) for o in orders], [2, 1])
        d = collect.aggregate(orders, cfg)["2026-09-08"]
        self.assertEqual((d["orders"], d["lines"]["Collane"]["stones"], d["ab"]["tagged"]), (2, 1, 1))


if __name__ == "__main__":
    unittest.main()
