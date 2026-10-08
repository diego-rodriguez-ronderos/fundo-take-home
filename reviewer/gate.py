"""Code decides what a model verdict is allowed to do. The model proposes; this applies.

Outcomes per doubted pattern:
  applied_silent  - correction changes no credit feature (cosmetic). Applied, nobody is paged.
  applied         - material correction, confidence over the bar. Applied and shown.
  queued          - material but under the bar, or blocked. Legacy label stays; underwriter decides.
"""
from . import groups as G

TIGHTEN_BAR = 0.80   # change makes the file look riskier (less revenue, more NSF/funders/high-risk)
LOOSEN_BAR = 0.95    # change makes the file look safer; wrong ones inflate offers, so the bar is higher

RISK_GROUPS = G.HIGH_RISK | {G.NSF, G.OVERDRAFT, G.ACTIVE_ADVANCE}

# Self-reported confidence turned out to be weakly calibrated (v3 run: 0.9 meant ~70% right).
# So the main brake is money: a correction touching more than this share of the file's
# credits (or debits) waits for an underwriter, however confident the model is.
MAX_AUTO_SHARE = 0.05


def feature_signature(direction, group, is_business):
    return (G.is_revenue(direction, group, is_business), group in (G.NSF, G.OVERDRAFT) and group,
            group in G.HIGH_RISK, group == G.ACTIVE_ADVANCE)


def decide(pattern, verdict, side_total_usd=None):
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
    if side_total_usd and v["total_usd"] > MAX_AUTO_SHARE * side_total_usd:
        share = v["total_usd"] / side_total_usd
        return "queued", f"moves {share:.0%} of the file's {d}s; underwriter confirms"
    bar = LOOSEN_BAR if loosening else TIGHTEN_BAR
    if verdict.confidence >= bar:
        return "applied", f"{'loosening' if loosening else 'tightening'} >= {bar}"
    return "queued", f"confidence {verdict.confidence:.2f} < {bar}"
