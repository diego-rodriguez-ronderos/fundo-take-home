"""The reviewer teaches the keyword engine. Confirmed corrections become small, reviewable rules.

Why this does not "memorize answers":
- A rule keys on the counterparty only (Plaid counterparty name, or the ACH originator name),
  extracted in code and matched exactly. Never on memo text: the counterparty writes the memo
  and could use it to poison what we learn. Exact match means SQUARE CAPITAL never catches SQUARE.
- Two scopes. A merchant rule applies to that merchant only (the same lottery sweep is a
  pass-through at a store and gambling at a bar). A global rule needs the same confirmed label
  at K_GLOBAL or more merchants and no confirmed evidence the other way.
- Conflicting evidence for a key means no rule: that counterparty stays with the reviewer.
- Nothing is learned from injection-flagged patterns or unconfirmed verdicts.
- NSF and overdraft are events, not identities. "Credits from Forward Financing were returned
  items" says nothing about the next credit from Forward Financing, which could be a new advance.
  So those groups are learned only from bank-written fee text, never keyed on a counterparty.
- A rule is a proposal in a versioned JSON file, with its evidence and a review-by date. It is
  promoted like code (reviewed, tested, backtested), not written straight into production.
- Meaning changes over time (a processor launches a lending arm, a funder renames). So rules
  expire, and the reviewer keeps re-checking a sample of rule-labeled patterns in shadow;
  a rule the reviewer disagrees with is suspended (see `audit`).

Scale: one row per corrected counterparty and scope, never one per transaction. The file grows
with the number of distinct counterparties (thousands), not with volume (millions). Lookup is a
dict access per transaction.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

from . import groups as G
from .keywords import normalize

K_GLOBAL = 2            # demo value with 10 merchants; in production 3+ and from different ISOs/banks
REVIEW_AFTER_DAYS = 180
STOP = {"DES", "ID", "INDN", "CO", "CCD", "PPD", "WEB", "SEC", "TRACE", "ENTRY", "DESCR", "CONF",
        "REF", "PMT", "PAYMENT", "DEBIT", "CREDIT", "ACH", "ORIG", "NAME", "FROM", "TO"}


def counterparty_key(txn, with_source=False):
    """Plaid counterparty name, else ACH originator name, else the leading bank-text tokens."""
    def out(k, from_cp):
        return (k, from_cp) if with_source else k
    cps = txn.get("counterparties") or []
    if cps and cps[0].get("name"):
        return out(normalize(cps[0]["name"]), True)
    desc = txn.get("original_description") or ""
    m = re.search(r"ORIG CO NAME:(.+?)\s+(?:CO\s+)?ENTRY", desc)
    if m:
        return out(normalize(m.group(1)), True)
    tokens = []
    for t in normalize(desc).split():
        if sum(c.isdigit() for c in t) > len(t) / 2 or t in STOP:   # IDs out, "CH13" stays
            if tokens:
                break
            continue
        tokens.append(t)
        if len(tokens) == 3:
            break
    return out(" ".join(tokens) or None, False)


EVENT_GROUPS = {G.NSF, G.OVERDRAFT}


def learn(confirmed, today=None, k_global=None):
    """confirmed: dicts with business_id, key, direction, group, is_business.
    Returns (rules, skipped) where skipped explains every key that did not become a rule."""
    today = today or date.today()
    k_global = k_global or K_GLOBAL
    by_key, skipped = {}, []
    for c in confirmed:
        if not c["key"]:
            continue
        if c["group"] in EVENT_GROUPS and c.get("key_from_counterparty"):
            skipped.append({"key": c["key"], "direction": c["direction"],
                            "why": "event group keyed on a counterparty", "labels": [c["group"]]})
            continue
        e = by_key.setdefault((c["key"], c["direction"]), {})
        e.setdefault((c["group"], c["is_business"]), set()).add(c["business_id"])
    rules = []
    for (key, direction), labels in sorted(by_key.items()):
        if len(labels) > 1:
            skipped.append({"key": key, "direction": direction, "why": "conflicting confirmed labels",
                            "labels": sorted(f"{g}/{b}" for g, b in labels)})
            continue
        (group, is_business), merchants = next(iter(labels.items()))
        base = {"key": key, "direction": direction, "group": group, "is_business": is_business,
                "evidence_merchants": sorted(merchants), "status": "proposed",
                "created": today.isoformat(),
                "review_by": (today + timedelta(days=REVIEW_AFTER_DAYS)).isoformat()}
        if len(merchants) >= k_global:
            rules.append({**base, "scope": "global"})
        else:
            rules += [{**base, "scope": m} for m in sorted(merchants)]
    return rules, skipped


def index(rules, today=None):
    """{(scope, key, direction): rule} for active, unexpired rules."""
    today = (today or date.today()).isoformat()
    return {(r["scope"], r["key"], r["direction"]): r for r in rules
            if r["status"] in ("proposed", "active") and r["review_by"] >= today}


def apply(txn, legacy_label, business_id, idx):
    """Rules override the keyword label: merchant scope first, then global. Pure lookup."""
    key = counterparty_key(txn)
    d = legacy_label["direction"]
    r = idx.get((business_id, key, d)) or idx.get(("global", key, d))
    if r is None:
        return legacy_label
    return {**legacy_label, "group": r["group"], "is_business": r["is_business"],
            "is_revenue": G.is_revenue(d, r["group"], r["is_business"]),
            "source": f"rule:{'global' if r['scope'] == 'global' else 'merchant'}"}


def audit(rules, disagreements, max_disagreement=0.2):
    """Shadow check. disagreements: {(scope, key, direction): (n_checked, n_reviewer_disagreed)}.
    A rule the reviewer disagrees with more than max_disagreement of the time is suspended."""
    out = []
    for r in rules:
        n, bad = disagreements.get((r["scope"], r["key"], r["direction"]), (0, 0))
        if n and bad / n > max_disagreement:
            r = {**r, "status": "suspended", "suspended_reason": f"reviewer disagreed {bad}/{n}"}
        out.append(r)
    return out


def save(rules, skipped, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"rules": rules, "skipped": skipped}, indent=1), encoding="utf-8")


def load(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))["rules"] if p.exists() else []
