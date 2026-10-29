"""
analysis.py - Objective 2: trajectory & behaviour analysis.

Answers: which trajectories are safe / unsafe / procedurally different, how
variable is the agent, and which violation patterns occur. Outputs feed
Objective 3 (violation taxonomy -> RAG knowledge base) and Objective 4
(per-rule violation statistics -> GA fitness design).

  python analysis.py --data data/sample_mock_trajectories.jsonl --out reports
"""
import argparse
import json
import math
import os
import random
from collections import Counter, defaultdict
from itertools import combinations

import pandas as pd

from safety_rules import RULES, RULE_IDS

GROUP = ["task_key", "acting_user", "backend", "model"]          # one behavioural "cell"
CLASSES = ["DECLINED", "SAFE_REFERENCE", "SAFE_VARIANT", "UNSAFE_PROCEDURAL", "UNSAFE_OUTCOME"]


# ------------------------------------------------------------------ loading
def load_runs(path_or_file):
    """Load a JSONL path or file-like object into a list of run dicts."""
    if hasattr(path_or_file, "read"):
        data = path_or_file.read()
        lines = (data.decode("utf-8") if isinstance(data, bytes) else data).splitlines()
    else:
        with open(path_or_file, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def to_frame(runs, include_errors=False):
    """DataFrame with one row per run plus derived columns. API/LLM error runs
    are excluded by default - they are not trajectories."""
    df = pd.DataFrame(runs)
    if df.empty:
        return df
    n_err = int(df["error"].sum())
    if not include_errors:
        df = df[~df["error"]].copy()
    df.attrs["n_error_runs_dropped"] = 0 if include_errors else n_err
    df["seq"] = df["tools"].apply(lambda t: " > ".join(t) if t else "(no tool calls)")
    df["pattern"] = df["violated_rules"].apply(lambda r: "+".join(r) if r else "SAFE")
    df["unsafe"] = df["label"] == "UNSAFE"
    df["no_action"] = df["n_steps"] == 0          # no tool call at all (refused, asked a question, or just replied)
    if "refused" not in df:
        df["refused"] = False
    df["refused"] = df["refused"].fillna(False).astype(bool)
    # declined = made no attempt, or explicitly refused (possibly after a few harmless steps)
    df["declined"] = df["no_action"] | df["refused"]
    df["task_success"] = df["task_success"].astype("object")
    df["unsafe_success"] = df["unsafe"] & (df["task_success"] == True)  # noqa: E712
    return df.reset_index(drop=True)


# ------------------------------------------------------------------ metrics
def _counts(counts):
    return list(counts.values()) if isinstance(counts, dict) else list(counts)


def shannon_bits(counts):
    """Shannon entropy (bits) of a Counter/iterable of counts."""
    vals = [c for c in _counts(counts) if c > 0]
    n = sum(vals)
    return -sum((c / n) * math.log2(c / n) for c in vals) if n else 0.0


def normalised_entropy(counts):
    k = len([c for c in _counts(counts) if c > 0])
    return shannon_bits(counts) / math.log2(k) if k > 1 else 0.0


def edit_distance(a, b):
    """Levenshtein distance between two tool sequences."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def mean_pairwise_edit(seqs, max_pairs=300, seed=0):
    seqs = [tuple(s) for s in seqs]
    pairs = list(combinations(range(len(seqs)), 2))
    if not pairs:
        return 0.0
    if len(pairs) > max_pairs:
        pairs = random.Random(seed).sample(pairs, max_pairs)
    return sum(edit_distance(seqs[i], seqs[j]) for i, j in pairs) / len(pairs)


def group_metrics(df, by=("task_key", "acting_user", "temperature")):
    """Variability + safety metrics per (task, user, temperature)."""
    rows = []
    for key, g in df.groupby(list(by)):
        n = len(g)
        traj = Counter(g["seq"])
        pat = Counter(g["pattern"])
        succ = g["task_success"].dropna()
        rows.append({
            **dict(zip(by, key)), "runs": n,
            "distinct_trajectories": len(traj), "distinct_ratio": round(len(traj) / n, 3),
            "trajectory_entropy_bits": round(shannon_bits(traj), 3),
            "trajectory_entropy_norm": round(normalised_entropy(traj), 3),
            "pattern_entropy_bits": round(shannon_bits(pat), 3),
            "mean_pairwise_edit": round(mean_pairwise_edit(g["tools"].tolist()), 2),
            "decline_rate": round(g["declined"].mean(), 3),
            "unsafe_rate": round(g["unsafe"].mean(), 3),
            "unsafe_rate_among_acting": round(g.loc[~g["declined"], "unsafe"].mean(), 3) if (~g["declined"]).any() else None,
            "outcome_harm_rate": round(g["outcome_harm"].mean(), 3),
            "task_success_rate": round(succ.astype(bool).mean(), 3) if len(succ) else None,
            "unsafe_success_rate": round(g["unsafe_success"].mean(), 3),
            "mean_risk": round(g["risk_score"].mean(), 2),
        })
    return pd.DataFrame(rows)


def rule_violation_rates(df, by="task_key"):
    """Share of runs violating each rule, per group (heatmap-ready)."""
    rows = []
    for key, g in df.groupby(by):
        row = {by: key, "runs": len(g)}
        for rid in RULE_IDS:
            row[rid] = round(g["violated_rules"].apply(lambda r, rid=rid: rid in r).mean(), 3)
        rows.append(row)
    return pd.DataFrame(rows).set_index(by)


# ------------------------------------------------------------ behaviour classes
def classify_behaviour(df):
    """Adds `behaviour_class`:
      DECLINED           no tool calls, or an explicit refusal, and no rule broken; "safe" only
                         because the task was not attempted, so it is kept apart
      SAFE_REFERENCE     safe, and the most common safe sequence of its cell
      SAFE_VARIANT       safe, but procedurally different from the reference
      UNSAFE_PROCEDURAL  violates only procedural rules (skipped/misordered checks)
      UNSAFE_OUTCOME     caused unauthorised access or external disclosure
    A cell = (task, acting user, backend, model).
    """
    df = df.copy()
    ref = {}
    for key, g in df[(~df["unsafe"]) & (~df["declined"])].groupby(GROUP):
        ref[key] = g["seq"].value_counts().index[0]

    def cls(r):
        if r["unsafe"]:      # a run that broke a rule is unsafe even if it later refused
            return "UNSAFE_OUTCOME" if r["outcome_harm"] else "UNSAFE_PROCEDURAL"
        if r["declined"]:
            return "DECLINED"
        return "SAFE_REFERENCE" if ref.get(tuple(r[c] for c in GROUP)) == r["seq"] else "SAFE_VARIANT"

    df["behaviour_class"] = df.apply(cls, axis=1)
    return df


def variant_table(df):
    """Every distinct trajectory with its count, label and violations."""
    rows = []
    for (task, seq), g in df.groupby(["task_key", "seq"]):
        pats = Counter(g["pattern"])
        rows.append({"task_key": task, "trajectory": seq, "runs": len(g),
                     "unsafe_share": round(g["unsafe"].mean(), 3),
                     "dominant_pattern": pats.most_common(1)[0][0],
                     "users": ",".join(sorted(set(g["acting_user"]))), "length": g["n_steps"].iloc[0]})
    return pd.DataFrame(rows).sort_values(["task_key", "runs"], ascending=[True, False]).reset_index(drop=True)


def transition_table(df):
    """Tool->tool bigram counts split by SAFE/UNSAFE runs (START/END markers)."""
    c = {"SAFE": Counter(), "UNSAFE": Counter()}
    for tools, label in zip(df["tools"], df["label"]):
        seq = ["START"] + list(tools) + ["END"]
        for a, b in zip(seq, seq[1:]):
            c[label][(a, b)] += 1
    keys = sorted(set(c["SAFE"]) | set(c["UNSAFE"]))
    t = pd.DataFrame([{"from": a, "to": b, "safe_runs": c["SAFE"][(a, b)], "unsafe_runs": c["UNSAFE"][(a, b)]}
                      for a, b in keys])
    t["unsafe_share"] = (t["unsafe_runs"] / (t["safe_runs"] + t["unsafe_runs"])).round(3)
    return t.sort_values("unsafe_runs", ascending=False).reset_index(drop=True)


def violation_taxonomy(df):
    """Violation patterns (distinct combinations of violated rules), with
    counts, where they occur, an example trajectory and the mitigation text.
    This is the seed of the Objective 3 attack/defence knowledge base."""
    unsafe = df[df["unsafe"]]
    out = []
    for i, (pat, g) in enumerate(sorted(unsafe.groupby("pattern"), key=lambda kv: -len(kv[1])), 1):
        rules = pat.split("+")
        ex = g.sort_values("n_steps").iloc[0]
        out.append({
            "pattern_id": f"P{i:02d}", "rules": rules,
            "rule_names": [RULES[r]["name"] for r in rules],
            "kinds": sorted({RULES[r]["kind"] for r in rules}),
            "runs": int(len(g)), "share_of_unsafe": round(len(g) / len(unsafe), 3),
            "max_severity": max(RULES[r]["severity"] for r in rules),
            "tasks": dict(Counter(g["task_key"]).most_common()),
            "users": dict(Counter(g["acting_user"]).most_common()),
            "example_run_id": ex["run_id"], "example_trajectory": ex["tools"],
            "mitigations": [RULES[r]["mitigation"] for r in rules],
            "ltl": [RULES[r]["ltl"] for r in rules],
        })
    return out


def overall_summary(df):
    traj, pat = Counter(df["seq"]), Counter(df["pattern"])
    succ = df["task_success"].dropna()
    return {
        "runs_analysed": int(len(df)),
        "error_runs_dropped": int(df.attrs.get("n_error_runs_dropped", 0)),
        "backends": sorted(set(df["backend"])), "models": sorted(set(df["model"])),
        "distinct_trajectories": len(traj),
        "trajectory_entropy_bits": round(shannon_bits(traj), 3),
        "violation_pattern_entropy_bits": round(shannon_bits(pat), 3),
        "distinct_violation_patterns": len(pat) - (1 if "SAFE" in pat else 0),
        "decline_rate": round(float(df["declined"].mean()), 3),
        "unsafe_rate": round(float(df["unsafe"].mean()), 3),
        "unsafe_rate_among_acting": round(float(df.loc[~df["declined"], "unsafe"].mean()), 3) if (~df["declined"]).any() else None,
        "outcome_harm_rate": round(float(df["outcome_harm"].mean()), 3),
        "task_success_rate": round(float(succ.astype(bool).mean()), 3) if len(succ) else None,
        "unsafe_but_successful_rate": round(float(df["unsafe_success"].mean()), 3),
        "behaviour_classes": dict(Counter(df["behaviour_class"])) if "behaviour_class" in df else None,
        "labeller_versions": sorted(set(df["labeller_version"])),
    }


# ------------------------------------------------------------------ reporting
def write_reports(df, outdir):
    os.makedirs(outdir, exist_ok=True)
    df = classify_behaviour(df)
    gm = group_metrics(df)
    gm.to_csv(f"{outdir}/group_metrics.csv", index=False)
    rule_violation_rates(df, "task_key").to_csv(f"{outdir}/rule_violation_by_task.csv")
    rule_violation_rates(df, "acting_user").to_csv(f"{outdir}/rule_violation_by_user.csv")
    variant_table(df).to_csv(f"{outdir}/trajectory_variants.csv", index=False)
    transition_table(df).to_csv(f"{outdir}/tool_transitions.csv", index=False)
    tax = violation_taxonomy(df)
    json.dump(tax, open(f"{outdir}/violation_taxonomy.json", "w"), indent=2)
    summ = overall_summary(df)
    json.dump(summ, open(f"{outdir}/summary.json", "w"), indent=2)

    by_task = df.groupby("task_key").agg(runs=("run_id", "size"), declined=("declined", "mean"), unsafe_rate=("unsafe", "mean"),
                                         harm=("outcome_harm", "mean"), success=("task_success", lambda s: s.dropna().astype(bool).mean()),
                                         distinct=("seq", "nunique")).round(3)
    md = ["# Objective 2 - behaviour analysis report", "",
          f"Backends: {', '.join(summ['backends'])} | models: {', '.join(summ['models'])} | labeller {', '.join(summ['labeller_versions'])}", "",
          "## Overall", "", "```", json.dumps(summ, indent=2), "```", "",
          "## Per task", "", by_task.to_markdown(), "",
          "## Rule violation rate by task", "", rule_violation_rates(df, "task_key").to_markdown(), "",
          "## Violation taxonomy (top 10)", ""]
    for t in tax[:10]:
        md.append(f"- **{t['pattern_id']}** {' + '.join(t['rule_names'])}: {t['runs']} runs "
                  f"({t['share_of_unsafe']:.1%} of unsafe), max severity {t['max_severity']}")
    open(f"{outdir}/report.md", "w", encoding="utf-8").write("\n".join(md))
    return summ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="reports")
    ap.add_argument("--include-errors", action="store_true")
    a = ap.parse_args()
    df = to_frame(load_runs(a.data), include_errors=a.include_errors)
    summ = write_reports(df, a.out)
    print(json.dumps(summ, indent=2))
    print(f"\nReports written to {a.out}/")


if __name__ == "__main__":
    main()
