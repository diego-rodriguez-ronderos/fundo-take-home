"""How much does a small mislabel rate move features and the offer?

Start from gold labels, corrupt a share of the eligible transactions with one
error type at a time, recompute the offer. 300 trials per (type, rate).
Everything here is simulated on synthetic data: these are estimates, not measurements.
"""
import random
import statistics

from . import credit as C
from . import groups as G

TRIALS = 300
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
