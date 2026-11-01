"""
stage4.py - Objective 2 command line: label runs, analyse, mine patterns, validate, export the handoff files.

  python stage4.py all --data data/runs/mock.jsonl --data data/runs/mock_unseen.jsonl     everything below in one go
  python stage4.py label     --data data/runs/mock.jsonl        -> reports/labels.jsonl
  python stage4.py explain   --data data/runs/mock.jsonl --run <run_id>   why one run got its label
  python stage4.py critic    [--backend heuristic|groq]         -> reports/critic_proposals.json
  python stage4.py critic-accept X01,X02                        (human decision) -> reports/rules_extra.json
  python stage4.py gold-sample                                  -> reports/gold_sample.csv (hand-label it)
  python stage4.py gold-validate --a reports/gold_sample.csv [--b second_person.csv]
Run `all` again after accepting critic rules: labels and reports are rebuilt with the extra rules.
"""
import argparse
import json
import os

from objective2 import critic, gold, labeler, patterns, procedural, reports

REPORTS = "reports"


def load(paths):
    runs, labels = {}, []
    sc = labeler.load_all_scenarios()
    for p in paths:
        r = labeler.load_runs(p)
        runs.update(r)
    from env.state import load_company
    company = load_company()
    for rid, (rows, summ) in runs.items():
        s = sc.get(summ["scenario_id"])
        if s and rows:
            labels.append(labeler.label_run(rows, summ, s, company))
    return runs, labels, sc


def cmd_all(a):
    runs, labels, sc = load(a.data)
    pats, ab = patterns.mine(runs, labels, sc, min_support=a.min_support)
    cov = patterns.coverage(pats, ab, labels)
    proc = procedural.analyse(runs, labels, sc)
    cr = critic.run_critic(runs, labels, "heuristic", out=os.path.join(REPORTS, "critic_proposals.json"))
    gold.sample(labels, runs, sc, n=a.gold_size, out_dir=REPORTS)
    summ = reports.build(runs, labels, pats, cov, proc, sc, REPORTS)
    print(f"{len(labels)} runs labelled: {summ['label_distribution']}")
    print(f"unsafe rate {summ['unsafe_rate']:.1%}, unsafe-but-successful {summ['unsafe_but_successful_rate']:.1%}")
    print("by prompt strength:", {k: f"{v['unsafe_rate']:.0%}" for k, v in summ["unsafe_rate_by_prompt_strength"].items()})
    print(f"{len(pats)} risk patterns; coverage by split: " + str({k: v['coverage'] for k, v in cov.items()}))
    print(f"critic: {cr['candidates']} suspicious-but-safe runs, {len(cr['proposals'])} proposals")
    print(f"files written to {REPORTS}/: labels.jsonl risk_patterns.json rules.json taxonomy.json splits.json procedural.json "
          "stage4_summary.json stage4_report.md gold_sample.csv gold_rule_labels.csv gold_guide.md")


def cmd_label(a):
    runs, labels, _ = load(a.data)
    labeler.write_jsonl(os.path.join(REPORTS, "labels.jsonl"), labels)
    from collections import Counter
    print(len(labels), dict(Counter(l["label"] for l in labels)))


def cmd_explain(a):
    runs, labels, _ = load(a.data)
    l = next((x for x in labels if x["run_id"].startswith(a.run)), None)
    if l is None:
        raise SystemExit(f"No run starting with '{a.run}' in {', '.join(a.data)}. Use a run id from your own data "
                         "(see reports/labels.jsonl) and the rows file it came from.")
    rows, summ = runs[l["run_id"]]
    print(f"{l['run_id']}  {l['scenario_id']}  prompt={l['prompt_strength']}  -> {l['label']}  (risk {l['risk_score']}, severity {l['severity']})")
    for r in sorted(rows, key=lambda r: r["step"]):
        mark = "  <-- first violation" if r["step"] == l["first_violation_step"] else ""
        print(f"  {r['step']:>2}. [{r['agent']}] {r['tool']}({json.dumps(r['args'])[:90]}){mark}")
    for x in l["reasons"]:
        print("  -", x)
    for w in l["warnings"]:
        print("  (advisory)", w["rule"], w["detail"])


def cmd_critic(a):
    runs, labels, _ = load(a.data)
    out = critic.run_critic(runs, labels, a.backend, a.api_key or os.environ.get("GROQ_API_KEY"), a.model,
                            out=os.path.join(REPORTS, "critic_proposals.json"))
    print(f"{out['candidates']} suspicious runs that passed all rules; {len(out['proposals'])} proposals")
    for p in out["proposals"]:
        print(f"  {p['id']} [{p['severity']}] {p['name']}: {p['rationale']}  (support {p.get('support')}, would flag {p['would_flag']} safe runs)")


def cmd_accept(a):
    critic.accept([x.strip() for x in a.ids.split(",")], os.path.join(REPORTS, "critic_proposals.json"), os.path.join(REPORTS, "rules_extra.json"))
    print("accepted; run `python stage4.py all ...` again to relabel with the extra rules")


def cmd_gold_sample(a):
    runs, labels, sc = load(a.data)
    ch = gold.sample(labels, runs, sc, n=a.n, out_dir=REPORTS)
    print(f"{len(ch)} runs written to {REPORTS}/gold_sample.csv; fill human_label_A (and B for a second person)")


def cmd_gold_validate(a):
    runs, labels, _ = load(a.data)
    res = gold.validate(labels, a.a, a.b, os.path.join(REPORTS, "gold_validation.json"))
    print(json.dumps({k: v for k, v in res.items() if k not in ("disagreements", "humans_disagree")}, indent=2))
    print(f"{len(res.get('disagreements', []))} rule-vs-human disagreements listed in reports/gold_validation.json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    default = ["data/runs/mock.jsonl", "data/runs/mock_unseen.jsonl"]

    def data(x):
        x.add_argument("--data", action="append", default=None, help="rows file (repeatable); its _summary.jsonl must sit next to it")

    x = sub.add_parser("all"); data(x); x.add_argument("--min-support", type=int, default=2); x.add_argument("--gold-size", type=int, default=80); x.set_defaults(fn=cmd_all)
    x = sub.add_parser("label"); data(x); x.set_defaults(fn=cmd_label)
    x = sub.add_parser("explain"); data(x); x.add_argument("--run", required=True); x.set_defaults(fn=cmd_explain)
    x = sub.add_parser("critic"); data(x); x.add_argument("--backend", choices=["heuristic", "groq"], default="heuristic")
    x.add_argument("--api-key", default=None); x.add_argument("--model", default="llama-3.3-70b-versatile"); x.set_defaults(fn=cmd_critic)
    x = sub.add_parser("critic-accept"); x.add_argument("ids"); x.set_defaults(fn=cmd_accept)
    x = sub.add_parser("gold-sample"); data(x); x.add_argument("--n", type=int, default=80); x.set_defaults(fn=cmd_gold_sample)
    x = sub.add_parser("gold-validate"); data(x); x.add_argument("--a", required=True); x.add_argument("--b", default=None); x.set_defaults(fn=cmd_gold_validate)
    a = p.parse_args()
    if getattr(a, "data", "x") is None:
        a.data = default
    a.fn(a)


if __name__ == "__main__":
    main()