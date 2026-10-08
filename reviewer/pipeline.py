"""One business: legacy labels + verdicts -> gate -> label sets and per-transaction rows.

Shared by run.py and the threshold sweep so both apply exactly the same rules.
Every decision-impact number here is computed in code from the offer rule; the LLM never
estimates what a label does to the decision.
"""
from . import consistency, credit, gate
from . import groups as G


def sig(d, lab):
    return gate.feature_signature(d, lab["group"], lab["is_business"])


def offer_move(b, base, p, v, base_offer):
    """Offer before and after applying only this correction."""
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


def cumulative_check(b, base, base_offer, applied, cfg):
    """Each auto-applied correction passed the cap alone. Together they may not: eight small fixes
    can move the offer more than one large one, or flip the decision. That is the same weakness as
    a per-transaction limit that is beaten by splitting the amount. Returns a reason, or None."""
    if not applied:
        return None
    labels = dict(base)
    for p, v in applied:
        for tid in p["txn_ids"]:
            labels[tid] = {"group": v.group, "is_business": v.is_business}
    after = credit.features(b, labels)["offer"]
    if (base_offer == 0) != (after == 0):
        return f"together the {len(applied)} auto corrections flip the decision (${base_offer:,.0f} -> ${after:,.0f})"
    if abs(after - base_offer) > (cfg or gate.DEFAULT)["max_auto_offer_usd"]:
        return f"together the {len(applied)} auto corrections move the offer ${after - base_offer:+,.0f}"
    return None


# Alternatives a pattern could plausibly have, used only for the diagnostic below.
def _alternative(p, lab):
    view, d = p["view"], p["view"]["direction"]
    if d == "credit":
        rev = G.is_revenue(d, lab["group"], lab["is_business"])
        return ({"group": G.INTERNAL_TRANSFER, "is_business": True}, "if not revenue") if rev else \
            ({"group": G.NONE, "is_business": True}, "if revenue")
    if lab["group"] in (G.NSF, G.OVERDRAFT, G.ACTIVE_ADVANCE) or lab["group"] in G.HIGH_RISK:
        return {"group": G.NONE, "is_business": True}, f"if not {lab['group']}"
    if view["fixed_amount"] and view["cadence"] in ("every business day", "daily", "weekly"):
        return {"group": G.ACTIVE_ADVANCE, "is_business": True}, "if a hidden funder"
    return None, None


def decision_critical(b, final, pats, outcomes, cfg=None):
    """Diagnostic, not a queue: patterns the reviewer agreed with whose mislabel would flip the
    decision or move the offer more than the cap. This is the 'impact' half of expected loss; the
    'probability of error' half needs underwriter history to calibrate, which a PoC does not have."""
    cap = (cfg or gate.DEFAULT)["max_auto_offer_usd"]
    base_offer = credit.features(b, final)["offer"]
    out = []
    for p in pats:
        if outcomes[p["pid"]][0] != "agree":
            continue
        lab = final[p["txn_ids"][0]]
        alt, what = _alternative(p, lab)
        if alt is None:
            continue
        labels = dict(final)
        for tid in p["txn_ids"]:
            labels[tid] = alt
        after = credit.features(b, labels)["offer"]
        flip = (base_offer == 0) != (after == 0)
        if flip or abs(after - base_offer) > cap:
            out.append({"business_id": b["business"]["business_id"], "pid": p["pid"],
                        "pattern": p["view"]["untrusted_bank_description_examples"][0][:50],
                        "label": lab["group"], "scenario": what, "offer_now": round(base_offer),
                        "offer_if_wrong": round(after), "flips_decision": flip})
    return out


def process(b, legacy, pats, verdicts, status, gold=None, cfg=None, diagnose=True):
    """Returns (rows, labels, critical). labels has legacy / reviewed / assisted / underwriter sets.
    underwriter = a simulated underwriter who resolves every queued pattern correctly (needs gold).
    """
    base = {k: {"group": v["group"], "is_business": v["is_business"]} for k, v in legacy.items()}
    base_offer = credit.features(b, base)["offer"]

    # pass 1: one outcome per pattern
    outcomes = {}
    for p in pats:
        v = verdicts.get(p["pid"])
        move = offer_move(b, base, p, v, base_offer) if v is not None and v.verdict == "doubt" else None
        outcomes[p["pid"]] = (*outcome_for(p, v, move, cfg, status), move)

    # pass 2: the auto-applied set as a whole
    applied = [(p, verdicts[p["pid"]]) for p in pats if outcomes[p["pid"]][0] == "applied"]
    why_cum = cumulative_check(b, base, base_offer, applied, cfg)
    if why_cum:
        for p, _ in applied:
            outcomes[p["pid"]] = ("queued", why_cum, outcomes[p["pid"]][2])

    # pass 3: label sets and rows
    auto, assisted, oracle = dict(base), dict(base), dict(base)
    txn_by_id = {t["transaction_id"]: t for t in b["transactions"]}
    rows = []
    for p in pats:
        v = verdicts.get(p["pid"])
        outcome, why, move = outcomes[p["pid"]]
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
    critical = decision_critical(b, auto, pats, outcomes, cfg) if diagnose else []
    return rows, {"legacy": base, "reviewed": auto, "assisted": assisted, "underwriter": oracle}, critical
