"""How much does a small mislabel rate move features and the offer?

Start from gold labels, corrupt a share of the eligible transactions with one
error type at a time, recompute the offer. 300 trials per (type, rate).
Everything here is simulated on synthetic data: these are estimates, not measurements.
"""
import random
import statistics

from . import credit as C
from . import groups as G

TRIALS = 100
RATES = [0.02, 0.05, 0.10]


def _d(t):
    return "debit" if t["amount"] > 0 else "credit"


# error type -> (eligible(txn, gold_label), corrupted label)
ERRORS = {
    "false_revenue (non-revenue credit counted)": (
        lambda t, g: _d(t) == "credit" and not G.is_revenue("credit", g["group"], g["is_business"]),
        lambda g: {"group": G.NONE, "is_business": True}),
    "missed_revenue (revenue credit excluded)": (
        lambda t, g: _d(t) == "credit" and G.is_revenue("credit", g["group"], g["is_business"]),
        lambda g: {"group": G.INTERNAL_TRANSFER, "is_business": True}),
    "missed_active_advance (funder debit unlabeled)": (
        lambda t, g: _d(t) == "debit" and g["group"] == G.ACTIVE_ADVANCE,
        lambda g: {"group": G.NONE, "is_business": True}),
    "false_active_advance (ordinary debit as funder)": (
        lambda t, g: _d(t) == "debit" and g["group"] == G.NONE,
        lambda g: {"group": G.ACTIVE_ADVANCE, "is_business": True}),
    "missed_nsf": (
        lambda t, g: g["group"] == G.NSF,
        lambda g: {"group": G.NONE, "is_business": True}),
    "false_nsf (ordinary debit as NSF)": (
        lambda t, g: _d(t) == "debit" and g["group"] == G.NONE,
        lambda g: {"group": G.NSF, "is_business": True}),
    "uniform (any txn, random group)": (
        lambda t, g: True,
        None),  # random group chosen per txn
}


def run(businesses, gold, seed=7):
    rng = random.Random(seed)
    rows = []
    sub = {b["business"]["business_id"]: {t["transaction_id"]: gold[t["transaction_id"]]
                                          for t in b["transactions"]} for b in businesses}
    base_of = {bid: C.features(b, sub[bid]) for b, bid in
               ((b, b["business"]["business_id"]) for b in businesses)}
    for name, (eligible, corrupt) in ERRORS.items():
        for rate in RATES:
            deltas, rel, flips = [], [], 0
            for _ in range(TRIALS):
                for b in businesses:
                    bid = b["business"]["business_id"]
                    base = base_of[bid]
                    pool = [t for t in b["transactions"] if eligible(t, gold[t["transaction_id"]])]
                    if not pool:
                        continue
                    k = max(0, round(rate * len(pool)))
                    if k == 0 and rng.random() < rate * len(pool):  # tiny pools: expected count
                        k = 1
                    labels = dict(sub[bid])
                    for t in rng.sample(pool, k):
                        g = gold[t["transaction_id"]]
                        labels[t["transaction_id"]] = corrupt(g) if corrupt else \
                            {"group": rng.choice(G.ALL_LABELS), "is_business": rng.random() < 0.9}
                    f = C.features(b, labels)
                    deltas.append(f["offer"] - base["offer"])
                    if base["offer"]:
                        rel.append((f["offer"] - base["offer"]) / base["offer"])
                    flips += (base["offer"] == 0) != (f["offer"] == 0)
            n = len(deltas)
            rows.append({
                "error": name, "rate": rate,
                "mean_offer_delta_usd": round(statistics.mean(deltas), 0),
                "p95_abs_offer_delta_usd": round(sorted(map(abs, deltas))[int(0.95 * (n - 1))], 0),
                "mean_offer_delta_pct": round(100 * statistics.mean(rel), 1) if rel else 0.0,
                "decision_flip_rate_pct": round(100 * flips / n, 1),
            })
    return rows


def pattern_impacts(businesses, gold):
    """Mislabel one whole pattern (what a keyword engine actually does) and measure the offer move.
    Credits flip revenue <-> excluded; funder debits lose ACTIVE_ADVANCE; NSF/OD lose their group;
    ordinary debits become ACTIVE_ADVANCE (a false funder)."""
    from .keywords import label as legacy_label
    from .patterns import build
    rows = []
    for b in businesses:
        sub = {t["transaction_id"]: gold[t["transaction_id"]] for t in b["transactions"]}
        base = C.features(b, sub)
        legacy = {t["transaction_id"]: legacy_label(t) for t in b["transactions"]}
        for p in build(b, legacy):
            g = sub[p["txn_ids"][0]]
            d = p["view"]["direction"]
            if d == "credit":
                rev = G.is_revenue(d, g["group"], g["is_business"])
                new, kind = ({"group": G.INTERNAL_TRANSFER, "is_business": True}, "revenue missed") if rev \
                    else ({"group": G.NONE, "is_business": True}, "false revenue")
            elif g["group"] == G.ACTIVE_ADVANCE:
                new, kind = {"group": G.NONE, "is_business": True}, "funder missed"
            elif g["group"] in (G.NSF, G.OVERDRAFT):
                new, kind = {"group": G.NONE, "is_business": True}, f"{g['group']} missed"
            else:
                new, kind = {"group": G.ACTIVE_ADVANCE, "is_business": True}, "false funder"
            labels = dict(sub)
            for tid in p["txn_ids"]:
                labels[tid] = new
            f = C.features(b, labels)
            rows.append({"business_id": b["business"]["business_id"], "error": kind,
                         "pattern": p["view"]["untrusted_bank_description_examples"][0][:48],
                         "n_txns": len(p["txn_ids"]), "pattern_usd": p["view"]["total_usd"],
                         "offer_gold": base["offer"], "offer_delta": round(f["offer"] - base["offer"], 0),
                         "usd_offer_per_usd_mislabeled": round(abs(f["offer"] - base["offer"]) / p["view"]["total_usd"], 2)})
    return sorted(rows, key=lambda r: -abs(r["offer_delta"]))
