# Gate thresholds: quality first, then cost

Assumptions: underwriter $1.0/min, 1.0 min per queued pattern, budget $3.0 per application, offer at risk from automation <= $500 per application.

## gpt-4.1, prompt v4
- development: 140 configurations, 120 pass the quality constraint, 120 also fit the budget
- chosen: {'max_auto_offer_usd': 5000, 'tighten_bar': 0.95, 'loosen_bar': 0.95} -> queue 1.8 patterns/app, cost $1.817/app, 13 auto-applied, offer at risk $158/app
- most permissive config {'max_auto_offer_usd': 1000000000, 'tighten_bar': 0.7, 'loosen_bar': 0.8}: wrong auto 0, offer at risk $1700/app, admissible=False
- current defaults {'tighten_bar': 0.95, 'loosen_bar': 0.95, 'max_auto_offer_usd': 5000}: admissible=True, cost $1.817/app, offer at risk $158/app
- held-out with chosen config: wrong auto-applied 0, hard negatives changed 0, queue 1.0/app, cost $1.016/app, offer at risk $400/app

## gpt-4.1-mini, prompt v3
- development: 140 configurations, 0 pass the quality constraint, 0 also fit the budget
- no configuration is both safe and within budget: do not auto-apply with this reviewer

## gpt-4.1-mini, prompt v4
- development: 140 configurations, 120 pass the quality constraint, 103 also fit the budget
- chosen: {'max_auto_offer_usd': 5000, 'tighten_bar': 0.8, 'loosen_bar': 0.8} -> queue 1.9 patterns/app, cost $1.903/app, 12 auto-applied, offer at risk $171/app
- most permissive config {'max_auto_offer_usd': 1000000000, 'tighten_bar': 0.7, 'loosen_bar': 0.8}: wrong auto 0, offer at risk $2502/app, admissible=False
- current defaults {'tighten_bar': 0.95, 'loosen_bar': 0.95, 'max_auto_offer_usd': 5000}: admissible=True, cost $2.903/app, offer at risk $380/app
- held-out with chosen config: wrong auto-applied 0, hard negatives changed 0, queue 1.33/app, cost $1.337/app, offer at risk $268/app

## Worst-case spend

- caps: max output 4000 tokens, 60 patterns per call, 4 attempts per call, input bounded at 2.0 chars/token (observed 3.12-3.19)
- this take-home: 42 calls; actual spend $0.3455; worst case for the same calls $4.06; hard budget $10.0 (a call whose worst case does not fit the remaining budget is never made)
- gpt-4.1, one application with 20 patterns: worst case $0.245
- gpt-4.1, one application with 60 patterns: worst case $0.437
- gpt-4.1, one application with 200 patterns: worst case $1.750
- gpt-4.1-mini, one application with 20 patterns: worst case $0.049
- gpt-4.1-mini, one application with 60 patterns: worst case $0.087
- gpt-4.1-mini, one application with 200 patterns: worst case $0.350

