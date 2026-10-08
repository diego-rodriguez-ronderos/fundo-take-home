"""One business: legacy labels + verdicts -> gate -> label sets and per-transaction rows.

Shared by run.py and the threshold sweep so both apply exactly the same rules.
"""
from . import consistency, credit, gate
from . import groups as G


def sig(d, lab):
    return gate.feature_signature(d, lab["group"], lab["is_business"])


def offer_move(b, base, p, v, base_offer):
    """Offer before and after applying only this correction. Pure code, no model."""
    fixed = dict(base)
    for tid in p["txn_ids"]:
        fixed[tid] = {"group": v.group, "is_business": v.is_business}
    return base_offer, credit.features(b, fixed)["offer"]


def outcome_for(p, v, move, cfg, status):
    if v is None:
        return "not_reviewed", status
    voted = p["view"]["legacy_label"]["group"] if v.verdict == "agree" else v.group
    if consistency.contradicts(v.reason, voted):
        return "queued", "reason contradicts verdict; human decides"
    if v.verdict == "agree":
        return "agree", ""
    return gate.decide(p, v, move, cfg)


def process(b, legacy, pats, verdicts, status, gold=None, cfg=None):
    """Returns (rows, labels) where labels has legacy / reviewed / assisted / underwriter sets.
    underwriter = a simulated underwriter who resolves every queued pattern correctly (needs gold).
    """
    base = {k: {"group": v["group"], "is_business": v["is_business"]} for k, v in legacy.items()}
    auto, assisted, oracle = dict(base), dict(base), dict(base)
    txn_by_id = {t["transaction_id"]: t for t in b["transactions"]}
    base_offer = credit.features(b, base)["offer"]
    rows = []
    for p in pats:
        v = verdicts.get(p["pid"])
        move = offer_move(b, base, p, v, base_offer) if v is not None and v.verdict == "doubt" else None
        outcome, why = outcome_for(p, v, move, cfg, status)
        doubt = v is not None and v.verdict == "doubt"
        prop = {"group": v.group, "is_business": v.is_business} if doubt else None
        for tid in p["txn_ids"]:
            t, lg = txn_by_id[tid], legacy[tid]
            d = lg["direction"]
            g = gold[tid] if gold else None
            if outcome.startswith("applied"):
                auto[tid] = oracle[tid] = prop
            if doubt:
                assisted[tid] = prop
            if outcome == "queued" and g:
                oracle[tid] = {"group": g["group"], "is_business": g["is_business"]}
            rows.append({
                "business_id": b["business"]["business_id"], "pid": p["pid"], "transaction_id": tid,
                "date": t["date"], "amount": t["amount"], "direction": d,
                "description": t["original_description"],
                "legacy_group": lg["group"], "legacy_business": lg["is_business"],
                "legacy_revenue": lg["is_revenue"], "legacy_source": lg.get("source", "keywords"),
                "verdict": v.verdict if v else "", "proposed_group": prop["group"] if prop else "",
                "proposed_business": prop["is_business"] if prop else "",
                "proposed_revenue": G.is_revenue(d, prop["group"], prop["is_business"]) if prop else "",
                "confidence": v.confidence if v else "", "reason": v.reason if v else "",
                "injection_alert_code": p["injection_alert"],
                "injection_seen_model": v.injection_seen if v else "",
                "gate": outcome, "gate_why": why,
                "offer_move_if_applied": round(move[1] - move[0], 0) if move else "",
                "final_group": auto[tid]["group"], "final_business": auto[tid]["is_business"],
                "final_revenue": G.is_revenue(d, auto[tid]["group"], auto[tid]["is_business"]),
                "gold_group": g["group"] if g else "", "gold_business": g["is_business"] if g else "",
                "gold_revenue": G.is_revenue(d, g["group"], g["is_business"]) if g else "",
                "case": g["case"] if g else "", "hard_negative": g["hard_negative"] if g else "",
                "legacy_materially_wrong": sig(d, lg) != sig(d, g) if g else "",
                "proposal_materially_right": sig(d, prop) == sig(d, g) if g and prop else "",
                "final_materially_wrong": sig(d, auto[tid]) != sig(d, g) if g else "",
            })
    return rows, {"legacy": base, "reviewed": auto, "assisted": assisted, "underwriter": oracle}
