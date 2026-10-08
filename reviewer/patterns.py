"""Collapse transactions into description patterns, computed in code.

The reviewer sees one row per pattern, not per transaction: 1,760 transactions
become ~250 patterns. That cuts cost ~7x and, more importantly, gives the model
facts it cannot see in a single row (cadence, fixed amounts, count), which is
how you tell a daily MCA remittance from a supplier invoice.
"""
import re
import statistics
from datetime import date

from .keywords import normalize

# Instruction-like text in a counterparty-written field. Deterministic, runs before the model.
INJECTION_RE = re.compile(
    r"\b(IGNORE|INSTRUCTION|REVIEWER|SYSTEM OVERRIDE|RECLASSIFY|CLASSIFY AS|KEEP LABEL|"
    r"MARK AS|LABEL AS|AI|PROMPT|ASSISTANT)\b")


def pattern_key(desc: str, direction: str) -> str:
    tokens = [t for t in normalize(desc).split() if not any(c.isdigit() for c in t)]
    return direction + "|" + " ".join(tokens)


def cadence(dates: list[date]) -> str:
    ds = sorted(set(dates))
    if len(ds) == 1:
        return "once"
    gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
    g = statistics.median(gaps)
    weekdays_only = all(d.weekday() < 5 for d in ds)
    if g <= 1 or (weekdays_only and g <= 3 and len(ds) > 10):
        return "every business day" if weekdays_only else "daily"
    if 6 <= g <= 8:
        return "weekly"
    if 27 <= g <= 32:
        return "monthly"
    return "irregular"


def build(biz: dict, legacy: dict) -> list[dict]:
    groups: dict[str, list] = {}
    for t in biz["transactions"]:
        d = "debit" if t["amount"] > 0 else "credit"
        groups.setdefault(pattern_key(t["original_description"], d), []).append(t)
    out = []
    for i, (key, ts) in enumerate(sorted(groups.items())):
        amts = [abs(t["amount"]) for t in ts]
        first = ts[0]
        lab = legacy[first["transaction_id"]]
        examples = list(dict.fromkeys(t["original_description"] for t in ts))[:2]
        cps = first.get("counterparties") or []
        cp = cps[0] if cps else None
        out.append({
            "pid": f"P{i:03d}",
            "txn_ids": [t["transaction_id"] for t in ts],
            "injection_alert": any(INJECTION_RE.search(normalize(e)) for e in
                                   (t["original_description"] for t in ts)),
            "view": {  # exactly what the model sees
                "pid": f"P{i:03d}",
                "direction": "credit" if key.startswith("credit") else "debit",
                "count": len(ts),
                "total_usd": round(sum(amts), 2),
                "median_usd": round(statistics.median(amts), 2),
                "fixed_amount": max(amts) - min(amts) < 0.01 * max(amts),
                "cadence": cadence([date.fromisoformat(t["date"]) for t in ts]),
                "first_date": min(t["date"] for t in ts),
                "last_date": max(t["date"] for t in ts),
                "untrusted_bank_description_examples": examples,
                "plaid_merchant_name": first.get("merchant_name"),
                "plaid_counterparty": cp and {"name": cp["name"], "type": cp["type"]},
                "plaid_category": (first.get("personal_finance_category") or {}).get("detailed"),
                "legacy_label": {"group": lab["group"], "is_business": lab["is_business"],
                                 "is_revenue": lab["is_revenue"]},
            },
        })
    return out
