"""Code decides what a model verdict is allowed to do. The model proposes; this applies.

Outcomes per doubted pattern:
  applied_silent  - correction changes no credit feature (cosmetic). Applied, nobody is paged.
  applied         - material correction, confidence over the bar. Applied and shown.
  queued          - injection text, reason contradicts verdict, moves the offer more than the cap,
                    flips the decision, or under the confidence bar. Legacy label stays; a human decides.
"""
from . import groups as G

# Values chosen by `python -m reviewer.sweep` for gpt-4.1 (quality constraint first, then cost),
# confirmed on the held-out set. With gpt-4.1 the bars barely matter: it reports 0.95-0.99 on
# everything, so the offer cap does the work. They stay as a floor against a less sure model.
TIGHTEN_BAR = 0.95   # change makes the file look riskier (less revenue, more NSF/funders/high-risk)
LOOSEN_BAR = 0.95    # change makes the file look safer

RISK_GROUPS = G.HIGH_RISK | {G.NSF, G.OVERDRAFT, G.ACTIVE_ADVANCE}

# Self-reported confidence turned out to be weakly calibrated (v3 run: 0.9 meant ~70% right).
# So the main brake is money, measured where it matters: how many offer dollars this one
# correction moves, computed in code from the offer rule. First version capped the share of
# the file's cash flow; the threshold sweep showed why that is the wrong unit: a $72 NSF fee
# moves 0.01% of the cash flow and $38,361 of offer, because it crosses the NSF > 5 decline.
MAX_AUTO_OFFER_USD = 5000

DEFAULT = {"tighten_bar": TIGHTEN_BAR, "loosen_bar": LOOSEN_BAR, "max_auto_offer_usd": MAX_AUTO_OFFER_USD}


def feature_signature(direction, group, is_business):
    return (G.is_revenue(direction, group, is_business), group in (G.NSF, G.OVERDRAFT) and group,
            group in G.HIGH_RISK, group == G.ACTIVE_ADVANCE)


def decide(pattern, verdict, offer_move=None, cfg=None):
    """offer_move: (offer before, offer after) applying only this correction; None = unknown."""
    cfg = cfg or DEFAULT
    v = pattern["view"]
    old = v["legacy_label"]
    d = v["direction"]
    new_rev = G.is_revenue(d, verdict.group, verdict.is_business)
    material = feature_signature(d, old["group"], old["is_business"]) != \
        feature_signature(d, verdict.group, verdict.is_business)
    if pattern["injection_alert"]:
        # Instruction-like text in a counterparty memo is itself a fraud signal. A human decides,
        # whichever direction the change goes; the model's verdict is shown, not applied.
        return "queued", "counterparty text contains instructions; human decides"
    if not material:
        return "applied_silent", "no feature changes"
    loosening = (new_rev and not old["is_revenue"]) or \
        (old["group"] in RISK_GROUPS and verdict.group not in RISK_GROUPS)
    if offer_move is not None:
        before, after = offer_move
        if (before == 0) != (after == 0):
            return "queued", f"flips the decision (offer ${before:,.0f} -> ${after:,.0f}); underwriter confirms"
        if abs(after - before) > cfg["max_auto_offer_usd"]:
            return "queued", f"moves the offer ${after - before:+,.0f}; underwriter confirms"
    bar = cfg["loosen_bar"] if loosening else cfg["tighten_bar"]
    if verdict.confidence >= bar:
        return "applied", f"{'loosening' if loosening else 'tightening'} >= {bar}"
    return "queued", f"confidence {verdict.confidence:.2f} < {bar}"
