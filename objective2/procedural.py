"""
objective2/procedural.py - how differently does the agent solve the same task?

For every scenario: the tool sequence of each run (entry and team agents; the event notifier and the approver
agent are left out, they are not "the agent solving the task"), the edit distance to the nearest reference path,
clusters of similar paths, and the Shannon entropy of the path distribution.
"""
import math
from collections import Counter, defaultdict

from . import labeler

LEFT_OUT = {"notifier", "approver"}


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def entropy(counter):
    n = sum(counter.values())
    return -sum(c / n * math.log2(c / n) for c in counter.values()) if n else 0.0


def clusters(paths, threshold=1):
    """Single-link clustering: two paths are in one cluster if they are within `threshold` edits (via any chain)."""
    paths = list(paths)
    parent = list(range(len(paths)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            if edit_distance(paths[i], paths[j]) <= threshold:
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i, p in enumerate(paths):
        groups[find(i)].append(p)
    return list(groups.values())


def run_path(rows):
    return tuple(r["tool"] for r in sorted(rows, key=lambda r: r["step"]) if r["agent"] not in LEFT_OUT)


def analyse(runs, labels, scenarios, threshold=1):
    """runs: {run_id: (rows, summary)}; labels: list of label dicts. Returns a JSON-ready dict."""
    lab = {l["run_id"]: l for l in labels}
    by_sc = defaultdict(list)
    for rid, (rows, summ) in runs.items():
        if rid in lab:
            by_sc[summ["scenario_id"]].append((rid, run_path(rows), summ["prompt_strength"]))
    out = {"threshold": threshold, "scenarios": {}, "by_prompt_strength": {}}
    for sid, items in sorted(by_sc.items()):
        refs = [tuple(p) for p in scenarios[sid].get("reference_paths", [])]
        paths = Counter(p for _, p, _ in items)
        dist = [min((edit_distance(p, r) for r in refs), default=0) for _, p, _ in items]
        cl = clusters(paths, threshold)
        out["scenarios"][sid] = {
            "runs": len(items), "distinct_paths": len(paths), "clusters": len(cl), "entropy_bits": round(entropy(paths), 3),
            "mean_edit_distance_to_reference": round(sum(dist) / len(dist), 3),
            "labels": dict(Counter(lab[r]["label"] for r, _, _ in items)),
            "top_paths": [{"path": list(p), "count": c} for p, c in paths.most_common(5)],
        }
    for ps in ("p0", "p1", "p2"):
        ls = [l for l in labels if l["prompt_strength"] == ps]
        if not ls:
            continue
        sc_ent = []
        for sid, items in by_sc.items():
            c = Counter(p for _, p, s in items if s == ps)
            if c:
                sc_ent.append(entropy(c))
        out["by_prompt_strength"][ps] = {
            "runs": len(ls), "unsafe_rate": round(sum(l["label"] in labeler.UNSAFE for l in ls) / len(ls), 4),
            "outcome_rate": round(sum(l["label"] == "UNSAFE_OUTCOME" for l in ls) / len(ls), 4),
            "declined_rate": round(sum(l["label"] == "DECLINED" for l in ls) / len(ls), 4),
            "task_success_rate": round(sum(bool(l["task_success"]) for l in ls) / len(ls), 4),
            "mean_path_entropy_bits": round(sum(sc_ent) / len(sc_ent), 3) if sc_ent else 0.0}
    return out
