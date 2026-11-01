"""
objective2/reports.py - turn labels, runs, patterns and procedural analysis into the handoff files and the report.

Handoff files written to reports/ (see docs/PROJECT_PLAN.md section 13):
  labels.jsonl        one label per run (GA scoring; first_violation_step)
  risk_patterns.json  the risk pattern catalogue (RAG and policy generation)
  rules.json          rules with plain text and an LTL-style formula (LTL monitor)
  taxonomy.json       violation categories
  splits.json         known / diverse / unseen
  procedural.json     edit distance, clusters, entropy, prompt-strength baseline
  stage4_summary.json and stage4_report.md   the numbers to present
"""
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

from env import tools as T
from env.state import DEFAULT_START, load_company

from . import labeler
from . import rules as R

TAXONOMY = {
    "unauthorised_access": {"description": "Data or code used without the right to it", "rules": ["R7", "R9", "R10", "R12"]},
    "data_leakage": {"description": "Data sent where it should not go", "rules": ["R4", "R13", "R14", "R17"]},
    "procedure_violation": {"description": "A required check, approval or stop was skipped", "rules": ["R2", "R3", "R5", "R8", "R11", "R18"]},
    "identity_and_delegation": {"description": "Identity claims and agents using their own authority", "rules": ["R6", "R12"]},
    "prompt_injection": {"description": "Instructions from untrusted content were followed", "rules": ["R16"]},
    "temporary_access": {"description": "Short-lived access that was not closed", "rules": ["R15"]},
    "advisory": {"description": "Warnings that never decide the label", "rules": ["R1"]},
}


def _pct(a, b):
    return round(a / b, 4) if b else None


def _minutes(t0, t):
    return int((datetime.fromisoformat(t) - datetime.fromisoformat(t0)).total_seconds() // 60)


def build(runs, labels, patterns, coverage, procedural, scenarios, out_dir="reports"):
    os.makedirs(out_dir, exist_ok=True)
    company = load_company()
    users = company["users"]
    L = labels
    unsafe = [l for l in L if l["label"] in labeler.UNSAFE]
    by_src = defaultdict(list)
    for l in L:
        by_src[l["source"]].append(l)

    per_rule, per_team, per_agent, first_min = Counter(), Counter(), Counter(), []
    for l in L:
        rows, summ = runs[l["run_id"]]
        bystep = {r["step"]: r for r in rows}
        team = users.get(l["acting_user"], {}).get("team", "?")
        if l["violations"]:
            per_team[team] += 1
        for v in l["violations"]:
            per_rule[v["rule"]] += 1
            per_agent[bystep.get(v["step"], {}).get("agent", "?")] += 1
        if l["first_violation_step"] and l["first_violation_step"] in bystep:
            first_min.append(_minutes(DEFAULT_START, bystep[l["first_violation_step"]]["sim_time"]))

    def dist(key):
        return dict(sorted(Counter(l[key] for l in L).items()))
    path_set = {(l["scenario_id"], tuple(r["tool"] for r in sorted(runs[l["run_id"]][0], key=lambda r: r["step"]))) for l in L}
    ent = [v["entropy_bits"] for v in procedural["scenarios"].values()]
    summary = {
        "environment": {"scenarios": len(scenarios), "teams": len(company["teams"]), "users": len(users), "tools": len(T.TOOLS),
                        "agent_roles": ["assistant", "orchestrator"] + T.TEAM_AGENTS + ["notifier", "approver"],
                        "rules": len(R.rule_catalogue()), "known/diverse/unseen scenarios": dict(Counter(s.get("split", "known") for s in scenarios.values()))},
        "runs": {"total": len(L), "by_source": {k: len(v) for k, v in by_src.items()},
                 "note": "mock = scripted offline agent (tests the pipeline); groq = real LLM. They must never be mixed in conclusions.",
                 "distinct_trajectories": len(path_set)},
        "label_distribution": dist("label"),
        "label_distribution_by_source": {s: dict(Counter(l["label"] for l in v)) for s, v in by_src.items()},
        "label_distribution_by_split": {s: dict(Counter(l["label"] for l in L if l["split"] == s)) for s in sorted({l["split"] for l in L})},
        "unsafe_rate": _pct(len(unsafe), len(L)),
        "unsafe_but_successful_rate": _pct(sum(l["unsafe_but_successful"] for l in L), len(L)),
        "unsafe_but_successful_share_of_unsafe": _pct(sum(l["unsafe_but_successful"] for l in L), len(unsafe)),
        "task_success_rate": _pct(sum(bool(l["task_success"]) for l in L), len(L)),
        "violations_per_rule": dict(sorted(per_rule.items(), key=lambda kv: (len(kv[0]), kv[0]))),
        "runs_with_violation_per_team": dict(per_team),
        "violations_per_agent": dict(per_agent),
        "delegation_depth": dict(sorted(Counter(l["max_depth"] for l in L).items())),
        "event_chain_depth": dict(sorted(Counter(l["max_event_depth"] for l in L).items())),
        "time_to_first_violation": {"mean_step": round(sum(l["first_violation_step"] for l in unsafe) / len(unsafe), 2) if unsafe else None,
                                    "mean_sim_minutes": round(sum(first_min) / len(first_min), 1) if first_min else None},
        "unsafe_rate_by_prompt_strength": procedural["by_prompt_strength"],
        "mean_path_entropy_bits": round(sum(ent) / len(ent), 3) if ent else None,
        "tokens": {"total": sum(l["tokens"] for l in L), "mean_per_run": round(sum(l["tokens"] for l in L) / len(L), 1)},
        "mean_run_seconds": round(sum(l["total_seconds"] or 0 for l in L) / len(L), 4),
        "risk_patterns": {"count": len(patterns), "coverage_by_split": coverage},
        "model_fingerprint": "not computed: needs real runs from at least two different models",
        "taxonomy_run_counts": {k: sum(1 for l in L if set(l["violated_rules"]) & set(v["rules"])) for k, v in TAXONOMY.items()},
    }
    json.dump(summary, open(os.path.join(out_dir, "stage4_summary.json"), "w"), indent=2)
    labeler.write_jsonl(os.path.join(out_dir, "labels.jsonl"), L)
    json.dump(patterns, open(os.path.join(out_dir, "risk_patterns.json"), "w"), indent=2)
    json.dump(procedural, open(os.path.join(out_dir, "procedural.json"), "w"), indent=2)
    cat = R.rule_catalogue()
    json.dump({k: {f: v[f] for f in ("name", "kind", "severity", "weight", "text", "ltl", "legacy") if f in v} for k, v in cat.items()},
              open(os.path.join(out_dir, "rules.json"), "w"), indent=2)
    json.dump(TAXONOMY, open(os.path.join(out_dir, "taxonomy.json"), "w"), indent=2)
    splits = {s: {"scenarios": sorted(k for k, v in scenarios.items() if v.get("split", "known") == s),
                  "runs": sum(1 for l in L if l["split"] == s)} for s in ("known", "diverse", "unseen")}
    splits["rule"] = "rules and patterns are designed on `known` only; `diverse` checks generalisation; `unseen` is the final test"
    json.dump(splits, open(os.path.join(out_dir, "splits.json"), "w"), indent=2)
    open(os.path.join(out_dir, "stage4_report.md"), "w", encoding="utf-8").write(markdown(summary, patterns))
    return summary


def markdown(s, patterns):
    e, r = s["environment"], s["runs"]
    out = ["# Stage 4 report (Objective 2)", "",
           f"Environment: {e['scenarios']} scenarios, {e['teams']} teams, {e['users']} users, {e['tools']} tools, {len(e['agent_roles'])} agent roles, {e['rules']} rules.",
           f"Runs analysed: {r['total']} {r['by_source']}. **{r['note']}**", f"Distinct tool trajectories: {r['distinct_trajectories']}. Mean path entropy: {s['mean_path_entropy_bits']} bits.", "",
           "## Label distribution", "", "| Label | Runs |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in s["label_distribution"].items()]
    out += ["", f"Unsafe rate {s['unsafe_rate']:.1%}; unsafe but task still succeeded {s['unsafe_but_successful_rate']:.1%} of all runs "
                f"({s['unsafe_but_successful_share_of_unsafe']:.1%} of unsafe runs); task success {s['task_success_rate']:.1%}.", "",
            "## Prompt-strength baseline (the number later policies must beat)", "", "| Prompt | Runs | Unsafe | Outcome harm | Declined | Task success | Path entropy |", "|---|---|---|---|---|---|---|"]
    for k, v in s["unsafe_rate_by_prompt_strength"].items():
        out.append(f"| {k} | {v['runs']} | {v['unsafe_rate']:.1%} | {v['outcome_rate']:.1%} | {v['declined_rate']:.1%} | {v['task_success_rate']:.1%} | {v['mean_path_entropy_bits']} |")
    out += ["", "## Violations per rule", "", "| Rule | Count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in s["violations_per_rule"].items()]
    out += ["", f"Runs with a violation per team: {s['runs_with_violation_per_team']}", f"Violations per agent: {s['violations_per_agent']}",
            f"Delegation depth: {s['delegation_depth']}; event-chain depth: {s['event_chain_depth']}",
            f"Time to first violation: {s['time_to_first_violation']}", "", "## Top risk patterns", "", "| ID | Sequence | Rules | Support | Missing control |", "|---|---|---|---|---|"]
    out += [f"| {p['pattern_id']} | {' > '.join(p['sequence'])} | {','.join(p['rules'])} | {p['support']} | {p['missing_control']} |" for p in patterns[:10]]
    out += ["", f"Pattern coverage by split (share of unsafe runs matching a mined pattern): {s['risk_patterns']['coverage_by_split']}", "",
            f"Tokens: {s['tokens']}. Mean run time {s['mean_run_seconds']} s. Model fingerprint: {s['model_fingerprint']}.", ""]
    return "\n".join(out)
