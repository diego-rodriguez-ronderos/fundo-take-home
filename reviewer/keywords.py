"""Legacy keyword engine. Deliberately imperfect: it stands in for the production engine.

Known defects, kept on purpose (SOLUTION.md lists which cases they produce):
  1. The description is normalized (punctuation -> space) but keywords are not,
     so any keyword containing punctuation ("NON-SUFFICIENT", "O/D") never matches.
  2. Plain substring match: "BET" fires on "BETTER BEVERAGE", "LEVY" on "LEVY'S HARDWARE".
  3. Precedence puts INTERNAL_TRANSFER above REVENUE_VERIFICATION, so a processor
     payout whose description says TRANSFER (Shopify, Stripe, PayPal) is excluded.
  4. "SQUARE" and "SHOPIFY" are revenue keywords; their capital arms are not listed.
  5. Personal flag is a merchant list. Every Zelle/Venmo is personal, even customer payments.
  6. Funder and high-risk lists are exact names; truncated or abbreviated names are missed.
"""
import re

from . import groups as G

KEYWORDS = {
    G.HR_GAMBLING: ["DRAFTKINGS", "FANDUEL", "CASINO", "LOTTERY", "BET", "POKER"],
    G.HR_BANKRUPTCY: ["BANKRUPTCY", "CHAPTER 13 TRUSTEE", "CHAPTER 7"],
    G.HR_DEBT_SETTLEMENT: ["DEBT SETTLEMENT", "DEBT RELIEF", "FREEDOM DEBT"],
    G.HR_GARNISHMENT: ["GARNISHMENT", "WAGE GARN", "LEVY"],
    G.HR_OTHER: ["COINBASE", "PAYDAY", "PAWN"],
    G.NSF: ["NSF FEE", "INSUFFICIENT FUNDS", "NON-SUFFICIENT", "RETURNED ITEM FEE"],
    G.OVERDRAFT: ["OVERDRAFT", "O/D", "OD FEE"],
    G.ACTIVE_ADVANCE: ["PEARL DELTA", "LIBERTAS", "YELLOWSTONE", "FORWARD FINANCING",
                       "ONDECK", "KAPITUS", "RAPID FINANCE"],
    G.UCC: ["CATERPILLAR FINANCIAL", "DEERE FINANCIAL", "EQUIPMENT FIN", "BALBOA"],
    G.INTERNAL_TRANSFER: ["TRANSFER"],
    G.NOT_REVENUE: ["SBA LOAN", "LOAN PROCEEDS", "TAX REF", "REVERSAL", "REFUND"],
    G.REVENUE_VERIFICATION: ["SQUARE", "SHOPIFY", "STRIPE", "TOAST", "PAYPAL", "AMAZON"],
    G.AUTO_DEPOSIT: ["EDI PYMNTS", "HCCLAIMPMT", "VENDOR PMT", "DIRECT DEP"],
}

# First match in this order wins.
PRECEDENCE = [
    G.HR_GAMBLING, G.HR_BANKRUPTCY, G.HR_DEBT_SETTLEMENT, G.HR_GARNISHMENT, G.HR_OTHER,
    G.NSF, G.OVERDRAFT, G.ACTIVE_ADVANCE, G.UCC, G.INTERNAL_TRANSFER, G.NOT_REVENUE,
    G.REVENUE_VERIFICATION, G.AUTO_DEPOSIT,
]

PERSONAL = ["NETFLIX", "SPOTIFY", "ZELLE", "VENMO", "CASH APP", "DRAFTKINGS", "FANDUEL",
            "COINBASE", "UBER EATS", "DOORDASH"]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", text.upper())).strip()


def direction(txn) -> str:
    # Plaid: positive amount = money out of the account
    return "debit" if txn["amount"] > 0 else "credit"


def label(txn) -> dict:
    text = normalize(txn.get("original_description") or txn.get("name") or "")
    matched = [g for g in PRECEDENCE if any(k in text for k in KEYWORDS[g])]
    group = matched[0] if matched else G.NONE
    business = not any(k in text for k in PERSONAL)
    d = direction(txn)
    return {"group": group, "is_business": business, "direction": d,
            "is_revenue": G.is_revenue(d, group, business), "all_matches": matched}
