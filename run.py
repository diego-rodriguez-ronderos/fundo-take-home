"""One command: legacy labels -> LLM review (cache first) -> gate -> evaluation -> report.

  python run.py                     # uses cache; calls the API only for misses and only if a key is set
  python run.py --offline           # never call the API; fail loudly on a cache miss
  python run.py --model gpt-5        # any OpenAI or Anthropic (claude-*) model
"""
import argparse
import csv
import json
from pathlib import Path

from reviewer import credit as C
from reviewer import gate, llm, patterns, pipeline, rules, sensitivity
from reviewer import groups as G
from reviewer import keywords as K

DEFAULT_MODEL = "gpt-4.1"  # chosen after comparing with gpt-4.1-mini; see SOLUTION.md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="out")
    ap.add_argument("--no-sensitivity", action="store_true")
    ap.add_argument("--rules", help="learned rules JSON applied on top of the keyword engine")
    a = ap.parse_args()

    out = Path(a.out) / a.model
    out.mkdir(parents=True, exist_ok=True)
    gold_path = Path(a.data) / "gold_labels.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8")) if gold_path.exists() else None  # absent on new data
    businesses = [load_business(p) for p in sorted(Path(a.data, "businesses").glob("*.json"))]
    cache = llm.load_cache()
    rule_idx = rules.index(rules.load(a.rules)) if a.rules else {}

    rows, per_biz, statuses, critical = [], [], {}, []
    for b in businesses:
        meta = b["business"]
        legacy = {t["transaction_id"]: rules.apply(t, K.label(t), meta["business_id"], rule_idx)
                  for t in b["transactions"]}
        pats = patterns.build(b, legacy)
        verdicts, status = llm.review_business(a.model, meta, pats, cache, a.offline)
        statuses[meta["business_id"]] = status
        if a.offline and status == "cache_miss":
            raise SystemExit(f"cache miss for {meta['business_id']} with {a.model}; run with a key first")
        rws, labs, crit = pipeline.process(b, legacy, pats, verdicts, status, gold)
        rows += rws
        critical += crit
        rec = {"business_id": meta["business_id"], "name": meta["name"], "review_status": status}
        for tag, lab in list(labs.items()) + ([("gold", gold)] if gold else []):
            if tag == "underwriter" and not gold:
                continue
            for k, val in C.features(b, lab).items():
                rec[f"{tag}_{k}"] = val
        if gold:
            rec["gold_offer_if_90d_assumed"] = C.features(b, gold, assume_days=90)["offer"]
        per_biz.append(rec)

    write_csv(out / "transactions.csv", rows)
    write_csv(out / "per_business.csv", per_biz)
    queue = [r for r in rows if r["gate"] in ("applied", "queued")]
    write_csv(out / "underwriter_queue.csv", dedupe_patterns(queue))
    write_csv(out / "decision_critical_agreed.csv", critical)
    report = summarize(a.model, rows, per_biz, statuses, gold is not None, cache)
    report += ("\n## Decision-critical patterns the reviewer agreed with (diagnostic, not queued)\n\n"
               "If one of these labels were wrong, the decision would flip or the offer would move more than "
               "the cap. Impact is computed in code; turning this into a queue needs a calibrated probability "
               "of error per correction type (expected loss = P(error) x impact), which needs underwriter "
               f"history. {len(critical)} patterns, {sum(c['flips_decision'] for c in critical)} of them "
               "decision flips; full list in decision_critical_agreed.csv.\n\n"
               + (md_table(sorted(critical, key=lambda c: -abs(c['offer_if_wrong'] - c['offer_now']))[:10])
                  if critical else ""))
    if gold and not a.no_sensitivity:
        sens = sensitivity.run(businesses, gold)
        write_csv(Path(a.out) / "sensitivity.csv", sens)
        report += "\n## Sensitivity: random mislabels (simulated on gold labels)\n\n" + md_table(sens)
        imp = sensitivity.pattern_impacts(businesses, gold)
        write_csv(Path(a.out) / "pattern_impacts.csv", imp)
        report += "\n## Sensitivity: one systematic error (a whole pattern mislabeled), top 15\n\n" + md_table(imp[:15])
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)


def load_business(path):
    """Accepts our files or a raw Plaid /transactions/get response (no "business" block)."""
    b = json.loads(path.read_text(encoding="utf-8"))
    b.setdefault("business", {"business_id": path.stem, "name": "unknown", "industry": "unknown"})
    for t in b["transactions"]:
        t["original_description"] = t.get("original_description") or t.get("name") or ""
    return b


def dedupe_patterns(rows):
    seen, out = {}, []
    for r in rows:
        k = (r["business_id"], r["pid"])
        if k not in seen:
            seen[k] = {**r, "n_txns": 0, "usd": 0.0}
            out.append(seen[k])
        seen[k]["n_txns"] += 1
        seen[k]["usd"] += abs(r["amount"])
    keep = ["business_id", "pid", "gate", "gate_why", "n_txns", "usd", "description", "legacy_group",
            "legacy_revenue", "proposed_group", "proposed_revenue", "confidence", "reason",
            "injection_alert_code", "gold_group", "legacy_materially_wrong"]
    return sorted(({k: r[k] for k in keep} for r in out), key=lambda r: -r["usd"])


def summarize(model, rows, per_biz, statuses, has_gold, cache):
    s = [f"# Review report: {model}\n", f"prompt {llm.PROMPT_VERSION}; review status per business: {statuses}",
         f"LLM spend recorded in cache: total ${llm.spend(cache)}; by model {llm.spend_by_model(cache)}\n"]
    if not has_gold:
        return "\n".join(s)
    n = len(rows)
    mat = [r for r in rows if r["legacy_materially_wrong"]]
    doubt = [r for r in rows if r["verdict"] == "doubt"]
    mat_doubt = [r for r in doubt if r["gate"] in ("applied", "queued")]
    hn = [r for r in rows if r["hard_negative"]]
    queued_p = {(r["business_id"], r["pid"]) for r in rows if r["gate"] == "queued"}
    right = [r for r in mat_doubt if r["proposal_materially_right"]]
    s += ["## Transactions", "",
          f"- transactions: {n}; legacy materially wrong (changes a credit feature): {len(mat)}",
          f"- reviewer doubts: {len(doubt)} txns; material doubts (applied or queued): {len(mat_doubt)}",
          f"- material doubts where legacy really was wrong: {pct(sum(r['legacy_materially_wrong'] for r in mat_doubt), len(mat_doubt))}",
          f"- material doubts whose proposed label is materially right: {pct(len(right), len(mat_doubt))}",
          f"- legacy material errors caught by a material doubt (recall): "
          f"{pct(sum(r['gate'] in ('applied', 'queued') for r in mat), len(mat))}",
          f"- auto-applied material corrections that are right: "
          f"{pct(sum(not r['final_materially_wrong'] for r in rows if r['gate'] == 'applied'), sum(r['gate'] == 'applied' for r in rows))}",
          f"- materially wrong after auto-apply: {sum(r['final_materially_wrong'] for r in rows)} (legacy {len(mat)})",
          f"- underwriter queue: {len(queued_p)} patterns across {len({k[0] for k in queued_p})} businesses",
          f"- hard negatives: {len(hn)} txns; doubted: {sum(r['verdict'] == 'doubt' for r in hn)} "
          f"(cosmetic, applied silently: {sum(r['gate'] == 'applied_silent' for r in hn)}; "
          f"sent to underwriter: {sum(r['gate'] == 'queued' for r in hn)}; "
          f"material change auto-applied: {sum(r['gate'] == 'applied' for r in hn)}); "
          f"materially wrong after auto-apply: {sum(r['final_materially_wrong'] for r in hn)}",
          "", "Confidence calibration (material doubts, by stated confidence):", ""]
    buckets = {}
    for r in mat_doubt:
        c = buckets.setdefault(r["confidence"], [0, 0])
        c[0] += 1
        c[1] += bool(r["proposal_materially_right"])
    s += [f"- {k}: {v[1]}/{v[0]} right" for k, v in sorted(buckets.items())]
    s += ["", "## Injection cases", ""]
    seen = set()
    for r in rows:
        if r["injection_alert_code"] and r["pid"] + r["business_id"] not in seen:
            seen.add(r["pid"] + r["business_id"])
            s.append(f"- {r['business_id']} `{r['description'][:70]}`: model injection_seen={r['injection_seen_model']}, "
                     f"verdict={r['verdict']} {r['proposed_group']}, gate={r['gate']}, final={r['final_group']}, gold={r['gold_group']}")
    s += ["", "## Monthly revenue per business (USD)", "",
          "reviewed = auto-applied only; underwriter = plus every queued pattern resolved correctly by a human; "
          "assisted = every doubt accepted blindly.", "",
          "| biz | gold | legacy | err | reviewed | err | underwriter | err | assisted | err | offer gold | legacy | reviewed | underwriter |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    tot = {"legacy": 0, "reviewed": 0, "underwriter": 0, "assisted": 0}
    for b in per_biz:
        g = b["gold_avg_monthly_revenue"]
        errs = {k: b[f"{k}_avg_monthly_revenue"] - g for k in tot}
        for k in tot:
            tot[k] += abs(errs[k])
        s.append(f"| {b['business_id']} | {g:,.0f} | {b['legacy_avg_monthly_revenue']:,.0f} | {errs['legacy']:+,.0f} | "
                 f"{b['reviewed_avg_monthly_revenue']:,.0f} | {errs['reviewed']:+,.0f} | "
                 f"{b['underwriter_avg_monthly_revenue']:,.0f} | {errs['underwriter']:+,.0f} | "
                 f"{b['assisted_avg_monthly_revenue']:,.0f} | {errs['assisted']:+,.0f} | "
                 f"{b['gold_offer']:,.0f} | {b['legacy_offer']:,.0f} | {b['reviewed_offer']:,.0f} | {b['underwriter_offer']:,.0f} |")
    s += ["", f"Sum of absolute monthly revenue error: legacy ${tot['legacy']:,.0f}, "
          f"reviewed ${tot['reviewed']:,.0f}, underwriter ${tot['underwriter']:,.0f}, assisted ${tot['assisted']:,.0f}", ""]
    s += ["## Features per business (gold / legacy / underwriter)", "",
          "| biz | rev share | NSF | OD | high-risk share of debits | funder $/day |", "|---|---|---|---|---|---|"]
    for b in per_biz:
        f = lambda k: " / ".join(str(b[f"{t}_{k}"]) for t in ("gold", "legacy", "underwriter"))
        s.append(f"| {b['business_id']} | {f('revenue_share_of_deposits')} | {f('nsf_count')} | "
                 f"{f('overdraft_count')} | {f('high_risk_share_of_debits')} | {f('funder_daily_payment')} |")
    return "\n".join(s)


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "n/a"


def md_table(rows):
    keys = list(rows[0])
    return "\n".join(["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)] +
                     ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in rows]) + "\n"


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
