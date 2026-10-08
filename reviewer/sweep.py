"""Choose the gate thresholds: quality first, then cost under a budget. Plus the worst-case spend.

    python -m reviewer.sweep

Step 1, quality (hard constraint). A configuration is admissible only if, on the development set:
  a) it auto-applies zero materially wrong corrections and changes zero hard negatives, and
  b) zero observed errors is backed by enough evidence. With 0 errors in n auto-applied patterns,
     the 95% upper bound on the error rate is ~3/n (rule of three). Offer dollars at risk per
     application = that bound x the offer dollars the auto-applied corrections move. It must stay
     under RISK_TOLERANCE. This is what stops "auto-apply everything" when the sample is small.
Automatic changes skip the underwriter, so the bar is on them.
Step 2, cost. Among admissible configurations, pick the cheapest per application:
LLM cost (measured from the cache) + queued patterns x minutes per pattern x underwriter cost.
It must be under the budget per application, or nothing is chosen and we say so.
Step 3, confirm the chosen configuration on the held-out set, untouched by steps 1 and 2.

Underwriter cost and budget are assumptions, set below and printed with the result.
"""
import argparse
import itertools
import json
from pathlib import Path

from . import budget, gate, llm, patterns, pipeline
from . import keywords as K

GRID = {
    "max_auto_offer_usd": [0, 250, 500, 1000, 2500, 5000, 10**9],
    "tighten_bar": [0.70, 0.80, 0.90, 0.95],
    "loosen_bar": [0.80, 0.90, 0.95, 0.99, 1.01],   # 1.01 = never auto-apply a loosening change
}


def load_set(data_dir):
    d = Path(data_dir)
    gold = json.loads((d / "gold_labels.json").read_text(encoding="utf-8"))
    bizs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((d / "businesses").glob("*.json"))]
    prepared = []
    for b in bizs:
        legacy = {t["transaction_id"]: K.label(t) for t in b["transactions"]}
        prepared.append((b, legacy, patterns.build(b, legacy)))
    return prepared, gold


def runs_in_cache(cache):
    """{(model, prompt_version): {business_id: record}} for single-chunk records."""
    out = {}
    for r in cache.values():
        out.setdefault((r["model"], r["prompt_version"]), {})[r["business_id"]] = r
    return out


def evaluate(prepared, gold, recs, cfg, usd_per_min, min_per_pattern, risk_tolerance):
    wrong_auto = hn_changed = queued = n_auto = 0
    llm_usd = impact_auto = 0.0
    n = 0
    for b, legacy, pats in prepared:
        rec = recs.get(b["business"]["business_id"])
        if rec is None:
            return None
        n += 1
        llm_usd += llm.spend({rec["key"]: rec})
        verdicts, _ = llm.parse_verdicts(rec["response"], pats)
        rows, _ = pipeline.process(b, legacy, pats, verdicts, "ok", gold, cfg)
        seen = set()
        for r in rows:
            k = r["pid"]
            if k in seen:
                continue
            seen.add(k)
            queued += r["gate"] == "queued"
            if r["gate"] == "applied":
                n_auto += 1
                impact_auto += abs(r["offer_move_if_applied"])
                wrong_auto += bool(r["final_materially_wrong"])
                hn_changed += bool(r["hard_negative"])
    queue_per_app = queued / n
    cost = llm_usd / n + queue_per_app * min_per_pattern * usd_per_min
    err_ub = min(1.0, 3 / n_auto) if n_auto else 0.0
    risk_per_app = err_ub * impact_auto / n
    return {"wrong_auto_patterns": wrong_auto, "hard_negatives_changed": hn_changed,
            "auto_patterns": n_auto, "error_rate_ub95": round(err_ub, 3),
            "offer_usd_at_risk_per_app": round(risk_per_app, 0),
            "queue_patterns_per_app": round(queue_per_app, 2), "llm_usd_per_app": round(llm_usd / n, 4),
            "cost_usd_per_app": round(cost, 3),
            "admissible": wrong_auto == 0 and hn_changed == 0 and risk_per_app <= risk_tolerance}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usd-per-min", type=float, default=1.0, help="underwriter cost, assumption")
    ap.add_argument("--min-per-pattern", type=float, default=1.0, help="review time, assumption")
    ap.add_argument("--budget-per-app", type=float, default=3.0, help="operating budget, assumption")
    ap.add_argument("--risk-tolerance", type=float, default=500.0,
                    help="offer USD at risk per application from automation (~1%% of a typical offer), assumption")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()

    cache = llm.load_cache()
    runs = runs_in_cache(cache)
    dev, dev_gold = load_set("data")
    hold, hold_gold = load_set("data_holdout")
    lines = ["# Gate thresholds: quality first, then cost", "",
             f"Assumptions: underwriter ${a.usd_per_min}/min, {a.min_per_pattern} min per queued pattern, "
             f"budget ${a.budget_per_app} per application, offer at risk from automation <= ${a.risk_tolerance:.0f} "
             "per application.", ""]
    table = []
    for (model, pv), recs in sorted(runs.items()):
        results = []
        for vals in itertools.product(*GRID.values()):
            cfg = dict(zip(GRID, vals))
            r = evaluate(dev, dev_gold, recs, cfg, a.usd_per_min, a.min_per_pattern, a.risk_tolerance)
            if r:
                results.append({**cfg, **r})
                table.append({"model": model, "prompt": pv, **cfg, **r})
        if not results:
            continue
        ok = [r for r in results if r["admissible"] and r["cost_usd_per_app"] <= a.budget_per_app]
        lines.append(f"## {model}, prompt {pv}")
        lines.append(f"- development: {len(results)} configurations, {sum(r['admissible'] for r in results)} pass "
                     f"the quality constraint, {len(ok)} also fit the budget")
        if not ok:
            lines.append("- no configuration is both safe and within budget: do not auto-apply with this reviewer")
            lines.append("")
            continue
        best = min(ok, key=lambda r: (r["cost_usd_per_app"], -r["max_auto_offer_usd"], -r["loosen_bar"], -r["tighten_bar"]))  # ties: stricter bars
        cfg = {k: best[k] for k in GRID}
        lines.append(f"- chosen: {cfg} -> queue {best['queue_patterns_per_app']} patterns/app, "
                     f"cost ${best['cost_usd_per_app']}/app, {best['auto_patterns']} auto-applied, "
                     f"offer at risk ${best['offer_usd_at_risk_per_app']:.0f}/app")
        loose = max(results, key=lambda r: (r["max_auto_offer_usd"], -r["loosen_bar"], -r["tighten_bar"]))
        lines.append(f"- most permissive config {({k: loose[k] for k in GRID})}: wrong auto {loose['wrong_auto_patterns']}, "
                     f"offer at risk ${loose['offer_usd_at_risk_per_app']:.0f}/app, admissible={loose['admissible']}")
        cur = evaluate(dev, dev_gold, recs, gate.DEFAULT, a.usd_per_min, a.min_per_pattern, a.risk_tolerance)
        lines.append(f"- current defaults {gate.DEFAULT}: admissible={cur['admissible']}, "
                     f"cost ${cur['cost_usd_per_app']}/app, offer at risk ${cur['offer_usd_at_risk_per_app']:.0f}/app")
        h = runs_in_cache_for(runs, model, pv)
        hr = evaluate(hold, hold_gold, h, cfg, a.usd_per_min, a.min_per_pattern, a.risk_tolerance) if h else None
        if hr:
            lines.append(f"- held-out with chosen config: wrong auto-applied {hr['wrong_auto_patterns']}, "
                         f"hard negatives changed {hr['hard_negatives_changed']}, "
                         f"queue {hr['queue_patterns_per_app']}/app, cost ${hr['cost_usd_per_app']}/app, "
                         f"offer at risk ${hr['offer_usd_at_risk_per_app']:.0f}/app")
        lines.append("")

    lines += worst_case_section(cache, dev + hold)
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    keys = list(table[0])
    (out / "threshold_sweep.csv").write_text(
        "\n".join([",".join(keys)] + [",".join(str(r[k]) for k in keys) for r in table]) + "\n", encoding="utf-8")
    (out / "threshold_sweep.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def runs_in_cache_for(runs, model, pv):
    return runs.get((model, pv))


def worst_case_section(cache, prepared):
    by_id = {b["business"]["business_id"]: (b, pats) for b, _, pats in prepared}
    worst_total = 0.0
    for r in cache.values():
        b, pats = by_id[r["business_id"]]
        payload = {"business": b["business"], "patterns": [p["view"] for p in pats]}
        worst_total += budget.worst_case_call_usd(llm.PRICES[r["model"]], llm.SYSTEM, payload)
    lines = ["## Worst-case spend", "",
             f"- caps: max output {budget.MAX_OUTPUT_TOKENS} tokens, {budget.MAX_PATTERNS_PER_CALL} patterns per call, "
             f"{budget.MAX_ATTEMPTS} attempts per call, input bounded at {budget.CHARS_PER_TOKEN_LOWER_BOUND} chars/token "
             f"(observed 3.12-3.19)",
             f"- this take-home: {len(cache)} calls; actual spend ${llm.spend(cache)}; "
             f"worst case for the same calls ${worst_total:.2f}; hard budget ${budget.BUDGET_USD} "
             "(a call whose worst case does not fit the remaining budget is never made)"]
    for model in ("gpt-4.1", "gpt-4.1-mini"):
        for n in (20, 60, 200):
            w = budget.worst_case_application_usd(llm.PRICES[model], llm.SYSTEM, n)
            lines.append(f"- {model}, one application with {n} patterns: worst case ${w:.3f}")
    return lines + [""]


if __name__ == "__main__":
    main()
