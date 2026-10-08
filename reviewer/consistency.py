"""Does the model's reason agree with its own verdict? Deterministic, no second model call.

Seen in real runs: "Shopify Capital advance is funding, not revenue" with verdict=agree
on a REVENUE_VERIFICATION label; "CZR sportsbook debits are gambling expenses, correctly
labeled none". A reason that asserts a group different from the label it votes for means
the verdict cannot be trusted either way, so the pattern goes to a human.
"""
import re

from . import groups as G

TERMS = {
    G.HR_GAMBLING: ["gambling", "sportsbook", "casino", "betting"],
    G.ACTIVE_ADVANCE: ["merchant cash advance", "mca", "funder", "remittance", "advance"],
    G.NOT_REVENUE: ["loan proceeds", "capital injection", "owner capital", "tax refund"],
    G.NSF: ["nsf", "insufficient funds", "returned item"],
    G.OVERDRAFT: ["overdraft"],
    G.HR_GARNISHMENT: ["garnishment", "levy"],
    G.HR_BANKRUPTCY: ["bankruptcy"],
    G.HR_DEBT_SETTLEMENT: ["debt settlement", "debt relief"],
    G.HR_OTHER: ["crypto", "payday"],
}
NEGATORS = {"not", "no", "isn't", "never", "rather", "instead", "without", "nor", "refund"}


def asserted_groups(reason: str) -> set:
    """Groups the reason asserts, ignoring terms negated within the three words before them."""
    text = reason.lower()
    found = set()
    for group, terms in TERMS.items():
        for term in terms:
            # (?!'s): a possessive is a name ("Levy's Hardware"), not the thing
            for m in re.finditer(r"\b" + re.escape(term) + r"\b(?!'s)", text):
                before = re.findall(r"[a-z']+", text[:m.start()])[-3:]
                if not NEGATORS & set(before):
                    found.add(group)
    return found


def contradicts(reason: str, voted_group: str) -> bool:
    asserted = asserted_groups(reason)
    return bool(asserted) and voted_group not in asserted
