# Fundo take-home: LLM reviewer for keyword transaction labels

A legacy keyword engine labels bank transactions (Plaid format) into 13 groups plus a
business/personal flag. An LLM reviewer reads those labels, flags the ones it doubts,
and code decides which corrections may change the credit features. Design, results and
trade-offs are in [SOLUTION.md](SOLUTION.md).

Python 3.10+. No framework, no database. Two runtime dependencies (`openai`, `pydantic`;
`anthropic` only if you pick a Claude model).

## Reproduce the committed output from cache (no API key)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py --offline
```

`--offline` never calls a provider and exits with an error on any cache miss, so a clean
run proves the output comes from `cache/llm_responses.jsonl`. Results land in
`out/<model>/` (`report.md`, `per_business.csv`, `transactions.csv`, `underwriter_queue.csv`)
and `out/sensitivity.csv`, `out/pattern_impacts.csv`.

The held-out set (6 businesses written after the prompt was frozen):

```bash
python run.py --offline --data data_holdout --out out_holdout --no-sensitivity
```

Compare models from cache: add `--model gpt-4.1-mini` to either command.

Threshold choice and worst-case spend, and learned rules (both offline, no API calls):

```bash
python -m reviewer.sweep             # out/threshold_sweep.md: quality first, then cost; worst case
python -m reviewer.learn             # rules/learned_rules.json + out/rules_report.md (renewal, held-out)
python run.py --rules rules/learned_rules.json   # engine + rules, then review (needs a key: new prompts)
```

## Run against the API

```bash
export OPENAI_API_KEY=sk-...         # PowerShell: $env:OPENAI_API_KEY="sk-..."
python run.py                        # default model gpt-4.1; only cache misses are sent
python run.py --model gpt-5          # any OpenAI model, or claude-* with ANTHROPIC_API_KEY
```

Every response is appended to `cache/llm_responses.jsonl`, keyed by
sha256(model, prompt version, system prompt, payload). Changing the prompt or the data
creates new keys; old entries stay for comparison.

## Other commands

```bash
python -m reviewer.generate          # rebuild data/, data_holdout/, data_renewal/ (deterministic)
REVIEWER_BUDGET_USD=5 python run.py  # hard spend cap; a call whose worst case does not fit is never made
python -m unittest discover -s tests # offline tests of the code side of the boundary
python run.py --data path/to/dir     # your own Plaid /transactions/get JSON files in dir/businesses/
```

A directory without `gold_labels.json` runs the full pipeline and skips evaluation.

## Layout

```
run.py                 pipeline + evaluation report
reviewer/groups.py     the 13 groups and the revenue rule (single source of truth)
reviewer/generate.py   synthetic Plaid data + gold labels
reviewer/keywords.py   legacy engine, deliberately imperfect
reviewer/patterns.py   transactions -> patterns, cadence, injection detector
reviewer/llm.py        prompt, structured output, validation, cache
reviewer/gate.py       what a model verdict is allowed to change (offer-impact cap, decision flips)
reviewer/pipeline.py   one business: verdicts -> gate -> label sets (shared by run and sweep)
reviewer/consistency.py reason vs verdict check
reviewer/budget.py     worst-case cost per call and per application; spend guard
reviewer/sweep.py      threshold choice: quality constraint, then cost under budget
reviewer/rules.py      learned counterparty rules: scope, conflicts, expiry, shadow audit
reviewer/learn.py      learn rules from confirmed corrections; measure on renewal and held-out
reviewer/credit.py     features and the offer rule
reviewer/sensitivity.py mislabel simulation
```
