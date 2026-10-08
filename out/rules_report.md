# Learned rules

From 29 confirmed material corrections on the development set (gpt-4.1, prompt v4); 2 injection-flagged patterns excluded.
- rules: 28 (0 global, 28 merchant-scoped); keys not turned into rules: 1 (below)
- size: 28 rows for 10 merchants and 1,760 transactions; it grows with distinct corrected counterparties, not with volume

| set | txns | material errors, legacy | legacy + rules | rule-labeled txns | rule labels wrong | patterns still wrong |
|---|---|---|---|---|---|---|
| renewal (same merchants, +91 days) | 1768 | 403 | 5 | 400 | 0 | 2 |
| held-out (new merchants) | 1090 | 314 | 314 | 0 | 0 | 20 |

Counterfactual, every rule made global (K=1): held-out material errors 314 -> 313, rule labels wrong 0.

Rules (key, direction, scope -> label):

- `ASHLEY KIM` credit [B04] -> NONE, business=True
- `BETTER BEVERAGE DISTRIBUTORS` debit [B06] -> NONE, business=True
- `BRENDA OKAFOR` credit [B04] -> NONE, business=True
- `CH13 TRUSTEE` debit [B07] -> HIGH_RISK_BANKRUPTCY, business=True
- `DK SPORTSBK` debit [B06] -> HIGH_RISK_GAMBLING, business=False
- `JESSICA WU` credit [B04] -> NONE, business=True
- `LAURA BIANCHI` credit [B04] -> NONE, business=True
- `LEGAL ORDER` debit [B07] -> HIGH_RISK_GARNISHMENT, business=True
- `LEVY S HARDWARE` debit [B07] -> NONE, business=True
- `MARIA LOPEZ` credit [B04] -> NOT_AVG_MONTHLY_REVENUE, business=True
- `NATIONAL DEBT RELIEF` debit [B07] -> HIGH_RISK_DEBT_SETTLEMENT, business=True
- `NICOLE GRANT` credit [B04] -> NONE, business=True
- `NON SUFFICIENT FUNDS` debit [B02] -> NSF, business=True
- `O D ITEM` debit [B02] -> OVERDRAFT, business=True
- `OD FEE REFUND` credit [B10] -> NOT_AVG_MONTHLY_REVENUE, business=True
- `PAYPAL` credit [B03] -> REVENUE_VERIFICATION, business=True
- `QUICKFUND LLC` debit [B09] -> ACTIVE_ADVANCE, business=True
- `RACHEL STONE` credit [B04] -> NONE, business=True
- `SHOPIFY` credit [B03] -> REVENUE_VERIFICATION, business=True
- `SHOPIFY CAPITAL` credit [B03] -> ACTIVE_ADVANCE, business=True
- `SOFIA DIAZ` credit [B04] -> NONE, business=True
- `SQUARE CAPITAL` credit [B01] -> ACTIVE_ADVANCE, business=True
- `STRIPE` credit [B07] -> REVENUE_VERIFICATION, business=True
- `TANYA REYES` credit [B04] -> NONE, business=True
- `TEXAS LOTTERY COMMISSION` credit [B08] -> NONE, business=True
- `TEXAS LOTTERY COMMISSION` debit [B08] -> NONE, business=True
- `U S SMALL BUSINESS ADMINISTRATION` credit [B09] -> NOT_AVG_MONTHLY_REVENUE, business=True
- `YSTONE CAPITAL` debit [B02] -> ACTIVE_ADVANCE, business=True

Skipped:

- `FORWARD FINANCING` credit: event group keyed on a counterparty ['NSF']
