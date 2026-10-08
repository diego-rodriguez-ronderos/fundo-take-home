"""Learn keyword rules from confirmed corrections on the development set, then measure them where
they were not learned: the same merchants 91 days later (renewal) and new merchants (held-out).

    python -m reviewer.learn

Confirmed = auto-applied by the gate, or resolved by the underwriter. Here the underwriter is
simulated (it resolves queued patterns to the gold label); in production these are real clicks.
No LLM call is made by this script.
"""
import json
from datetime import date
from pathlib import Path

from . import llm, patterns, pipeline, rules
from . import keywords as K

MODEL, PROMPT = "gpt-4.1", llm.PROMPT_VERSION
TODAY = date(2026, 10, 7)   # fixed so the rules file is reproducible
RULES_PATH = Path("rules/learned_rules.json")


def _load(d):
    d = Path(d)
    gold = json.loads((d / "gold_labels.json").read_text(encoding="utf-8"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((d / "businesses").glob("*.json"))], gold


def confirmed_corrections():
    cache = llm.load_cache()
    recs = {r["business_id"]: r for r in cache.values() if r["model"] == MODEL and r["prompt_version"] == PROMPT}
    bizs, gold = _load("data")
    out, skipped_injection = [], 0
    for b in bizs:
        legacy = {t["transaction_id"]: K.label(t) for t in b["transactions"]}
        pats = patterns.build(b, legacy)
        verdicts, _ = llm.parse_verdicts(recs[b["business"]["business_id"]]["response"], pats)
        rows, labs = pipeline.process(b, legacy, pats, verdicts, "ok", gold)
        txn = {t["transaction_id"]: t for t in b["transactions"]}
        seen = set()
        for r in rows:
            if r["pid"] in seen or r["gate"] not in ("applied", "queued"):
                continue
            seen.add(r["pid"])
            if r["injection_alert_code"]:
                skipped_injection += 1
                continue
            final = labs["underwriter"][r["transaction_id"]]
            key, from_cp = rules.counterparty_key(txn[r["transaction_id"]], with_source=True)
            out.append({"business_id": r["business_id"], "key": key, "key_from_counterparty": from_cp,
                        "direction": r["direction"], "group": final["group"], "is_business": final["is_business"]})
    return out, skipped_injection


def measure(data_dir, idx):
    bizs, gold = _load(data_dir)
    n = before = after = wrong_rule = rule_txns = 0
    pats_left = set()
    for b in bizs:
        bid = b["business"]["business_id"]
        for t in b["transactions"]:
            lg = K.label(t)
            lr = rules.apply(t, lg, bid, idx)
            g = gold[t["transaction_id"]]
            d = lg["direction"]
            wrong_before = pipeline.sig(d, lg) != pipeline.sig(d, g)
            wrong_after = pipeline.sig(d, lr) != pipeline.sig(d, g)
            n += 1
            before += wrong_before
            after += wrong_after
            if lr.get("source", "").startswith("rule"):
                rule_txns += 1
                wrong_rule += wrong_after
            if wrong_after:
                pats_left.add((bid, patterns.pattern_key(t["original_description"], d)))
    return {"transactions": n, "material_errors_legacy": before, "material_errors_legacy_plus_rules": after,
            "rule_labeled_txns": rule_txns, "rule_labels_materially_wrong": wrong_rule,
            "patterns_still_wrong": len(pats_left)}


def main():
    conf, skipped_inj = confirmed_corrections()
    learned, skipped = rules.learn(conf, TODAY)
    rules.save(learned, skipped, RULES_PATH)
    idx = rules.index(learned, TODAY)
    lines = ["# Learned rules", "",
             f"From {len(conf)} confirmed material corrections on the development set ({MODEL}, prompt {PROMPT}); "
             f"{skipped_inj} injection-flagged patterns excluded.",
             f"- rules: {len(learned)} ({sum(r['scope'] == 'global' for r in learned)} global, "
             f"{sum(r['scope'] != 'global' for r in learned)} merchant-scoped); keys not turned into rules: {len(skipped)} (below)",
             f"- size: {len(learned)} rows for {sum(1 for _ in Path('data/businesses').glob('*.json'))} merchants "
             "and 1,760 transactions; it grows with distinct corrected counterparties, not with volume",
             "", "| set | txns | material errors, legacy | legacy + rules | rule-labeled txns | rule labels wrong | patterns still wrong |",
             "|---|---|---|---|---|---|---|"]
    for name, d in [("renewal (same merchants, +91 days)", "data_renewal"), ("held-out (new merchants)", "data_holdout")]:
        m = measure(d, idx)
        lines.append(f"| {name} | {m['transactions']} | {m['material_errors_legacy']} | "
                     f"{m['material_errors_legacy_plus_rules']} | {m['rule_labeled_txns']} | "
                     f"{m['rule_labels_materially_wrong']} | {m['patterns_still_wrong']} |")
    naive, _ = rules.learn(conf, TODAY, k_global=1)
    m = measure("data_holdout", rules.index(naive, TODAY))
    lines += ["", f"Counterfactual, every rule made global (K=1): held-out material errors "
              f"{m['material_errors_legacy']} -> {m['material_errors_legacy_plus_rules']}, "
              f"rule labels wrong {m['rule_labels_materially_wrong']}."]
    lines += ["", "Rules (key, direction, scope -> label):", ""]
    lines += [f"- `{r['key']}` {r['direction']} [{r['scope']}] -> {r['group']}, business={r['is_business']}"
              for r in learned]
    if skipped:
        lines += ["", "Skipped:", ""] + [f"- `{s['key']}` {s['direction']}: {s['why']} {s['labels']}" for s in skipped]
    Path("out").mkdir(exist_ok=True)
    Path("out/rules_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
