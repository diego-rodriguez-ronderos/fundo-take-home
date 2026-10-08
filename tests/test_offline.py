"""Offline checks of the code side of the boundary: no API key, no network.

python -m unittest discover -s tests
"""
import json
import unittest
from pathlib import Path

from datetime import date

from reviewer import consistency, gate, keywords as K, llm, patterns, pipeline, rules
from reviewer import groups as G
from reviewer import credit as C

ROOT = Path(__file__).resolve().parents[1]


def load(bid):
    return json.loads((ROOT / "data" / "businesses" / f"{bid}.json").read_text(encoding="utf-8"))


class KeywordDefects(unittest.TestCase):
    def test_punctuation_keyword_never_matches(self):
        t = {"amount": 36.0, "original_description": "NON-SUFFICIENT FUNDS FEE 123"}
        self.assertEqual(K.label(t)["group"], G.NONE)

    def test_transfer_beats_processor(self):
        t = {"amount": -900.0, "original_description": "STRIPE DES:TRANSFER ID:ST-1"}
        lab = K.label(t)
        self.assertEqual(lab["group"], G.INTERNAL_TRANSFER)
        self.assertFalse(lab["is_revenue"])


class RevenueRule(unittest.TestCase):
    def test_rule(self):
        self.assertTrue(G.is_revenue("credit", G.NONE, True))
        self.assertFalse(G.is_revenue("credit", G.NONE, False))
        self.assertFalse(G.is_revenue("debit", G.NONE, True))
        self.assertFalse(G.is_revenue("credit", G.ACTIVE_ADVANCE, True))
        self.assertTrue(G.is_revenue("credit", G.AUTO_DEPOSIT, True))


class Gate(unittest.TestCase):
    def setUp(self):
        b = load("B09")
        legacy = {t["transaction_id"]: K.label(t) for t in b["transactions"]}
        self.all = patterns.build(b, legacy)
        self.pats = {p["view"]["untrusted_bank_description_examples"][0][:12]: p for p in self.all}

    def v(self, pid, group, conf, business=True):
        return llm.Verdict(pid=pid, verdict="doubt", group=group, is_business=business,
                           confidence=conf, reason="test")

    def test_injection_always_queued(self):
        p = next(p for p in self.all if "QUICKFUND" in p["view"]["untrusted_bank_description_examples"][0]
                 and p["injection_alert"])
        out, _ = gate.decide(p, self.v(p["pid"], G.ACTIVE_ADVANCE, 0.99))
        self.assertEqual(out, "queued")

    def test_injection_queued_even_when_model_agrees(self):
        """gpt-4.1 resisted the Humana injection and agreed; the fraud signal must still reach a human."""
        p = next(p for p in self.all if "HUMANA" in p["view"]["untrusted_bank_description_examples"][0])
        agree = llm.Verdict(pid=p["pid"], verdict="agree", confidence=0.99, reason="real claim payment")
        self.assertEqual(pipeline.outcome_for(p, agree, None, None, "ok")[0], "queued")
        self.assertEqual(pipeline.outcome_for(p, None, None, None, "api_error")[0], "queued")

    def test_tightening_applies_over_bar(self):
        p = self.pats["SBAD TREAS 3"]
        small = (64000.0, 63800.0)
        self.assertEqual(gate.decide(p, self.v(p["pid"], G.NOT_REVENUE, 0.97), small)[0], "applied")
        self.assertEqual(gate.decide(p, self.v(p["pid"], G.NOT_REVENUE, 0.90), small)[0], "queued")

    def test_big_offer_move_or_decision_flip_queued(self):
        p = self.pats["SBAD TREAS 3"]
        v = self.v(p["pid"], G.NOT_REVENUE, 0.99)
        self.assertEqual(gate.decide(p, v, (141268.0, 81268.0))[0], "queued")
        self.assertEqual(gate.decide(p, v, (38361.0, 0.0))[0], "queued")


class Validation(unittest.TestCase):
    def test_bad_rows_dropped_not_fatal(self):
        b = load("B10")
        legacy = {t["transaction_id"]: K.label(t) for t in b["transactions"]}
        pats = patterns.build(b, legacy)
        payload = {"business": b["business"], "patterns": [p["view"] for p in pats]}
        good = {"pid": pats[0]["pid"], "verdict": "agree", "group": None, "is_business": None,
                "confidence": 0.9, "reason": "ok", "injection_seen": False}
        bad = {"pid": pats[1]["pid"], "verdict": "doubt", "group": "MADE_UP", "is_business": True,
               "confidence": 0.9, "reason": "x", "injection_seen": False}
        ghost = dict(good, pid="P999")
        cache = {llm._key("fake", payload): {"model": "fake", "response": {"verdicts": [good, bad, ghost]},
                                             "usage": {"input_tokens": 0, "output_tokens": 0}}}
        out, status = llm.review_business("fake", b["business"], pats, cache, offline=True)
        self.assertEqual(list(out), [pats[0]["pid"]])
        self.assertTrue(status.startswith("partial"))


class PatternInvariant(unittest.TestCase):
    def test_mixed_legacy_labels_are_split(self):
        """Same key once digits are dropped, different legacy labels: must become two patterns."""
        txns = [{"transaction_id": f"t{i}", "amount": 30.0, "date": "2026-09-0" + str(i + 1),
                 "original_description": d, "counterparties": [], "personal_finance_category": None}
                for i, d in enumerate(["PAYMENT BET123 ACME", "PAYMENT XYZ999 ACME", "PAYMENT QRS777 ACME"])]
        self.assertEqual(len({patterns.pattern_key(t["original_description"], "debit") for t in txns}), 1)
        legacy = {t["transaction_id"]: K.label(t) for t in txns}
        pats = patterns.build({"transactions": txns}, legacy)
        self.assertEqual(len(pats), 2)
        for p in pats:
            self.assertEqual(len({legacy[i]["group"] for i in p["txn_ids"]}), 1)
            self.assertEqual(p["view"]["legacy_label"]["group"], legacy[p["txn_ids"][0]]["group"])

    def test_homogeneous_sets_unchanged(self):
        for bid in ("B01", "B04", "B09"):
            b = load(bid)
            legacy = {t["transaction_id"]: K.label(t) for t in b["transactions"]}
            for p in patterns.build(b, legacy):
                self.assertNotIn("legacy=", p["view"]["direction"])
                self.assertEqual(len({(legacy[i]["group"], legacy[i]["is_business"]) for i in p["txn_ids"]}), 1)


class Cumulative(unittest.TestCase):
    def test_small_fixes_that_add_up_are_queued(self):
        """B04: each customer-Zelle fix moves the offer < $5,000 alone; together they move it more."""
        b = load("B04")
        legacy = {t["transaction_id"]: K.label(t) for t in b["transactions"]}
        pats = patterns.build(b, legacy)
        zelle = [p for p in pats if p["view"]["untrusted_bank_description_examples"][0].startswith("ZELLE PAYMENT FROM")
                 and "MARIA" not in p["view"]["untrusted_bank_description_examples"][0]]
        verdicts = {p["pid"]: llm.Verdict(pid=p["pid"], verdict="doubt", group=G.NONE, is_business=True,
                                          confidence=0.99, reason="customer payment") for p in zelle}
        rows, labs, _ = pipeline.process(b, legacy, pats, verdicts, "ok", diagnose=False)
        gates = {r["pid"]: (r["gate"], r["gate_why"]) for r in rows if r["pid"] in verdicts}
        self.assertTrue(all(g == "queued" and "together" in why for g, why in gates.values()))
        self.assertEqual(labs["reviewed"], labs["legacy"])   # nothing auto-applied


class Rules(unittest.TestCase):
    def conf(self, bid, key, group, d="credit", cp=True):
        return {"business_id": bid, "key": key, "direction": d, "group": group, "is_business": True,
                "key_from_counterparty": cp}

    def test_exact_match_and_scope(self):
        rs, _ = rules.learn([self.conf("B01", "SQUARE CAPITAL", G.ACTIVE_ADVANCE)])
        idx = rules.index(rs)
        sq = {"amount": -900.0, "original_description": "SQUARE INC DES:SQ1", "counterparties": [{"name": "Square"}]}
        cap = {"amount": -9000.0, "original_description": "X", "counterparties": [{"name": "Square Capital"}]}
        self.assertEqual(rules.apply(sq, K.label(sq), "B01", idx)["group"], K.label(sq)["group"])
        self.assertEqual(rules.apply(cap, K.label(cap), "B01", idx)["group"], G.ACTIVE_ADVANCE)
        self.assertEqual(rules.apply(cap, K.label(cap), "B02", idx)["group"], K.label(cap)["group"])  # other merchant

    def test_conflict_means_no_rule(self):
        rs, skipped = rules.learn([self.conf("B01", "ACME", G.NONE), self.conf("B02", "ACME", G.ACTIVE_ADVANCE)])
        self.assertEqual(rs, [])
        self.assertEqual(skipped[0]["why"], "conflicting confirmed labels")

    def test_global_needs_k_merchants(self):
        rs, _ = rules.learn([self.conf("B01", "ACME", G.NONE), self.conf("B02", "ACME", G.NONE)])
        self.assertEqual([r["scope"] for r in rs], ["global"])

    def test_event_not_keyed_on_counterparty(self):
        rs, skipped = rules.learn([self.conf("B05", "FORWARD FINANCING", G.NSF)])
        self.assertEqual(rs, [])
        rs, _ = rules.learn([self.conf("B02", "NON SUFFICIENT FUNDS", G.NSF, "debit", cp=False)])
        self.assertEqual(len(rs), 1)

    def test_audit_suspends_and_expiry(self):
        rs, _ = rules.learn([self.conf("B01", "ACME", G.NONE)], today=date(2026, 1, 1))
        key = (rs[0]["scope"], "ACME", "credit")
        self.assertEqual(rules.audit(rs, {key: (10, 4)})[0]["status"], "suspended")
        self.assertEqual(rules.index(rs, today=date(2026, 12, 31)), {})   # past review_by


class Consistency(unittest.TestCase):
    def test_contradiction_and_negation(self):
        self.assertTrue(consistency.contradicts("CZR sportsbook debits are gambling, correctly labeled", G.NONE))
        self.assertFalse(consistency.contradicts("Supplier purchases, not gambling", G.NONE))
        self.assertFalse(consistency.contradicts("Levy's Hardware is a merchant", G.NONE))


class ShortHistory(unittest.TestCase):
    def test_61_days_hardcoded_to_90_understates(self):
        b = load("B10")
        legacy = {t["transaction_id"]: K.label(t) for t in b["transactions"]}
        real = C.features(b, legacy)
        forced = C.features(b, legacy, assume_days=90)
        self.assertEqual(real["days"], 61)
        self.assertLess(forced["avg_monthly_revenue"], real["avg_monthly_revenue"] * 0.7)


if __name__ == "__main__":
    unittest.main()
