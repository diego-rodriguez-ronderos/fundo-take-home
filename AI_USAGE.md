# AI usage

Assistant: Claude (Anthropic), agentic coding session. Reviewer models: OpenAI gpt-4.1 and
gpt-4.1-mini through the API (these are part of the product, not development help).

| File | First draft | How it was checked |
|---|---|---|
| `reviewer/groups.py` | Claude | Revenue rule unit-tested (`tests/test_offline.py`); group definitions read against the brief |
| `reviewer/generate.py` | Claude | Each case traced from generator to legacy label to gold label in a per-case diff (legacy vs gold) before any LLM run |
| `reviewer/keywords.py` | Claude | Each intended defect has a case that triggers it; two defects unit-tested |
| `reviewer/patterns.py` | Claude | Asserted that every transaction in a pattern shares one legacy label; injection regex checked on both injection cases |
| `reviewer/llm.py` | Claude | Per-verdict validation unit-tested with a fake cached response (bad group, ghost pid); real runs with both models |
| `reviewer/gate.py` | Claude, rules changed after reading results | Unit tests for injection and confidence bars; rule 3 added after the v3 cash/check failure |
| `reviewer/credit.py` | Claude | Offer recomputed by hand for B02 (NSF decline) and B10 (61-day window) |
| `reviewer/sensitivity.py` | Claude | Per-dollar sensitivities checked against the formula (0.40 revenue, 0.32 funder) |
| `reviewer/budget.py`, `sweep.py` | Claude, from my spec (quality first, then cost; hard worst-case cap under $10) | Token bound checked against all 42 cached calls (3.12-3.19 chars/token vs bound 2.0); sweep result re-checked on held-out |
| `reviewer/rules.py`, `learn.py` | Claude, from my constraints (no memorized answers, meaning changes over time, no store that grows with volume) | Unit tests for exact match, scope, conflicts, events, expiry, audit; measured on renewal and held-out sets |
| `reviewer/consistency.py` | Claude | Run over every cached verdict; two false positives found (Levy's possessive, "refund of overdraft") and fixed |
| `run.py` | Claude | `--offline` reproduction from cache on Linux and Windows |
| `SOLUTION.md`, `README.md` | Claude, edited by me | Every number traced to `out/` or `out_holdout/` reports |

What was wrong in AI output and got caught:
- A claim that a mislabeled funder costs ~50x a mislabeled revenue dollar. Measured: 0.32 vs 0.40.
- First gate trusted model confidence. Measured calibration (v3): 0.9 meant 71% right.
- Hard-negative set initially missed cash and check deposits, the exact cases the model then got wrong.
- Prompt v4 was tuned on the development set; development scores are optimistic. Held-out set added.
- Windows run of the sensitivity simulation was too slow at 300 trials; reduced to 100.
- First money brake capped cash-flow share; the sweep showed a $72 NSF fee moving $38,361 of offer. Replaced by an offer-impact cap.
- First threshold sweep picked "auto-apply everything" because 0 errors were observed on 29 patterns; added the rule-of-three evidence bound.
- Gate checked each correction alone; Diego asked whether decision impact could be checked before routing to a human. Checking the data showed eight sub-cap fixes in B04 adding up to +$8,200. Added the cumulative check and the decision-critical diagnostic.
- Diego asked during code review whether a pattern could hold transactions with different legacy labels (the code showed the model the first one). It could: the key drops digit tokens, the engine reads them ("BET" inside "BET123"). It did not happen on any of the three sets; mixed groups are now split by label, with a test.
- First rules learner turned "Forward Financing credits were NSF returns" into a rule; NSF/OD are now learned only from bank fee text.

Prompt history: `docs/runs/v3_gpt-4.1-mini_report.md` (before) and `out/*/report.md` (v4).
