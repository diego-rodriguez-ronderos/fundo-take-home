"""Offline checks of the code side of the boundary: no API key, no network.

python -m unittest discover -s tests
"""
import json
import unittest
from pathlib import Path

from reviewer import gate, keywords as K, llm, patterns
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

    def test_tightening_applies_over_bar(self):
        p = self.pats["SBAD TREAS 3"]
        self.assertEqual(gate.decide(p, self.v(p["pid"], G.NOT_REVENUE, 0.85))[0], "applied")
        self.assertEqual(gate.decide(p, self.v(p["pid"], G.NOT_REVENUE, 0.70))[0], "queued")


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
