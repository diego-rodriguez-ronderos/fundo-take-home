"""The 13 keyword groups and the revenue rule.

This module is the single source of truth for what "revenue" means.
Neither the keyword engine nor the LLM decides revenue directly: both
produce (group, is_business), and `is_revenue` derives the rest.
"""

NONE = "NONE"  # no group matched (ordinary expense or plain deposit)

NOT_REVENUE = "NOT_AVG_MONTHLY_REVENUE"
REVENUE_VERIFICATION = "REVENUE_VERIFICATION"
NSF = "NSF"
OVERDRAFT = "OVERDRAFT"
INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
UCC = "UCC"
ACTIVE_ADVANCE = "ACTIVE_ADVANCE"
AUTO_DEPOSIT = "AUTO_DEPOSIT"
HR_GAMBLING = "HIGH_RISK_GAMBLING"
HR_BANKRUPTCY = "HIGH_RISK_BANKRUPTCY"
HR_DEBT_SETTLEMENT = "HIGH_RISK_DEBT_SETTLEMENT"
HR_GARNISHMENT = "HIGH_RISK_GARNISHMENT"
HR_OTHER = "HIGH_RISK_OTHER"

GROUPS = [
    NOT_REVENUE, REVENUE_VERIFICATION, NSF, OVERDRAFT, INTERNAL_TRANSFER,
    UCC, ACTIVE_ADVANCE, AUTO_DEPOSIT, HR_GAMBLING, HR_BANKRUPTCY,
    HR_DEBT_SETTLEMENT, HR_GARNISHMENT, HR_OTHER,
]
ALL_LABELS = GROUPS + [NONE]

HIGH_RISK = {HR_GAMBLING, HR_BANKRUPTCY, HR_DEBT_SETTLEMENT, HR_GARNISHMENT, HR_OTHER}

# Groups that stop a credit from counting as revenue.
# REVENUE_VERIFICATION and AUTO_DEPOSIT are positive evidence, not exclusions.
EXCLUDING = {NOT_REVENUE, NSF, OVERDRAFT, INTERNAL_TRANSFER, UCC, ACTIVE_ADVANCE} | HIGH_RISK

# Short definitions shown to the LLM. Our interpretation of the brief's group
# names; where the brief is ambiguous (UCC, Auto deposit) SOLUTION.md says so.
DEFINITIONS = {
    NOT_REVENUE: "Credit that is not earned from customers: loan proceeds (SBA, bank), tax refunds, owner capital injections, fee refunds, refunds/reversals of the business's own purchases.",
    REVENUE_VERIFICATION: "Credit from an identifiable payment processor or marketplace settling customer sales (Square, Stripe, Shopify Payments, Toast, PayPal, Amazon). Strong evidence of revenue.",
    NSF: "Non-sufficient-funds event: NSF fee, returned item fee, or an ACH/check item returned unpaid for insufficient funds (with or without a fee).",
    OVERDRAFT: "Overdraft fee or overdraft charge (not a refund of one).",
    INTERNAL_TRANSFER: "Money moving between accounts owned by the same business or its owner (savings, other checking).",
    UCC: "Payments to or proceeds from secured lenders that typically file a UCC-1 lien: equipment finance, secured term loans, lines of credit.",
    ACTIVE_ADVANCE: "Merchant cash advance / revenue-based funder: fixed daily or weekly remittance debits, or the funding deposit from such a funder (including processor-originated capital like Square Capital or Shopify Capital).",
    AUTO_DEPOSIT: "Recurring automated ACH credit from a customer or payer (insurance claim payments, B2B invoices, government vendor payments, freight brokers). Revenue.",
    HR_GAMBLING: "Gambling: sportsbooks, casinos, online betting, both deposits to and withdrawals from them.",
    HR_BANKRUPTCY: "Bankruptcy trustee payments or bankruptcy-related charges.",
    HR_DEBT_SETTLEMENT: "Payments to debt settlement / debt relief companies.",
    HR_GARNISHMENT: "Garnishment, levy, or legal-order debit taken by a court or tax authority.",
    HR_OTHER: "Other high-risk activity: crypto exchanges, payday lenders, pawn shops.",
    NONE: "None of the above: an ordinary operating expense, or an ordinary customer deposit (cash, check, card) with no special meaning.",
}


def is_revenue(direction: str, group: str, is_business: bool) -> bool:
    """A transaction is revenue when it is a business credit and no excluding group matched."""
    return direction == "credit" and is_business and group not in EXCLUDING
