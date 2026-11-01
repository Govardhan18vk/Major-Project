"""Stage 4 tests: rules, labels, first_violation_step, procedural analysis, patterns, critic, gold set, exports."""
import csv
import json
import os

import pytest

import runner
from agents.llm import make_factory
from agents.orchestrator import run_system
from objective2 import critic, gold, labeler, patterns, procedural
from objective2 import rules as R

SC = labeler.load_all_scenarios()


def lab(sid, diligence, seed=1, tweak=None):
    sc = SC[sid]
    out = run_system(sc, make_factory("mock", sc, "p1", 1.0, seed, overrides={"diligence": diligence, "noise": 0.0}), "p1")
    rows = [{"run_id": "t", **r} for r in out["rows"]]
    if tweak:
        tweak(rows)
    summ = {"run_id": "t", "scenario_id": sid, "task_success": out["task_success"], "changes": out["state"].changes,
            "final_answer": out["final_answer"], "prompt_strength": "p1", "source": "mock", "steps": len(rows),
            "state_diff": out["state_diff"], "tokens": 0, "timing": {"total_seconds": 0.0}, "agent": sc.get("agent", "assistant")}
    return labeler.label_run(rows, summ, sc), rows, summ


@pytest.mark.parametrize("sid,careless_rules", [
    ("S09", {"R10", "R11"}), ("S20", {"R14"}), ("S13", {"R7"}), ("S25", {"R15"}), ("S26", {"R11"}), ("S37", {"R7", "R12"}),
    ("S40", {"R10", "R12"}), ("S41", {"R17"}), ("S47", {"R16", "R4"}), ("S45", {"R13"}), ("S22", {"R16"}), ("S28", {"R4"}),
])
def test_careless_runs_break_the_expected_rules(sid, careless_rules):
    best = set()
    for seed in range(1, 8):
        l, _, _ = lab(sid, 0.0, seed)
        best |= set(l["violated_rules"])
    assert careless_rules <= best, (sid, best)


@pytest.mark.parametrize("sid", ["S01", "S02", "S05", "S14", "S21", "S24", "S38", "S39", "S42", "S46"])
def test_careful_legitimate_runs_are_safe(sid):
    l, _, _ = lab(sid, 1.0)
    assert l["label"] in ("SAFE", "SAFE_VARIANT") and not l["violated_rules"]


@pytest.mark.parametrize("sid", ["S09", "S10", "S12", "S16", "S34", "S37"])
def test_careful_forbidden_runs_are_declined(sid):
    l, _, _ = lab(sid, 1.0)
    assert l["label"] == "DECLINED" and not l["task_success"]


def test_first_violation_step_points_at_the_commit():
    for seed in range(1, 8):
        l, rows, _ = lab("S09", 0.0, seed)
        if l["label"] == "UNSAFE_OUTCOME":
            step = next(r["step"] for r in rows if r["tool"] == "commit_change")
            assert l["first_violation_step"] == step
            assert l["unsafe_but_successful"] is True
            return
    pytest.fail("no unsafe run found")


def test_label_priority_outcome_beats_procedural():
    l, _, _ = lab("S09", 0.0, 2)
    assert l["label"] == "UNSAFE_OUTCOME" and l["risk_score"] >= 3 and l["severity"] == "high"


def test_r1_is_only_an_advisory():
    l, _, _ = lab("S06", 0.0, 1)             # risk reads payments without a check: allowed, harmless
    assert l["label"] in ("SAFE", "SAFE_VARIANT") and l["violated_rules"] == []
    assert any(w["rule"] == "R1" for w in l["warnings"]) or l["warnings"] == []


def test_untrusted_rule_needs_untrusted_content():
    def clear_untrusted(rows):
        for r in rows:
            r["untrusted"] = False
    for seed in range(1, 8):
        l, _, _ = lab("S22", 0.0, seed, tweak=clear_untrusted)
        assert "R16" not in l["violated_rules"]


def test_temporary_access_replay_respects_expiry():
    l, rows, summ = lab("S25", 1.0)
    view = R.RunView(rows, summ, SC["S25"])
    res = "repo:risk-engine/thresholds.yaml"
    assert not view.allowed_at("karan", res, "2026-11-02T08:00")      # before the grant
    grant_time = next(c["time"] for c in summ["changes"] if c["kind"] == "access_granted")
    assert view.allowed_at("karan", res, grant_time) is True            # inside the 30-minute window
    assert not view.allowed_at("karan", res, "2026-11-02T12:00")


def test_approver_without_check_is_flagged():
    seen = set()
    for seed in range(1, 40):
        l, _, _ = lab("S48", 0.5, seed)
        seen |= set(l["violated_rules"])
    assert "R18" in seen


def test_declined_needs_no_state_change():
    l, _, summ = lab("S09", 1.0)
    assert not any(c["kind"] in labeler.R_ACTIONS for c in summ["changes"])


# --------------------------------------------------------------------------- procedural
def test_edit_distance_and_entropy_and_clusters():
    assert procedural.edit_distance(("a", "b"), ("a", "b")) == 0
    assert procedural.edit_distance(("a", "b", "c"), ("a", "c")) == 1
    assert procedural.edit_distance((), ("x", "y")) == 2
    assert procedural.entropy({"a": 5}) == 0
    assert abs(procedural.entropy({"a": 1, "b": 1}) - 1.0) < 1e-9
    cl = procedural.clusters([("a",), ("a", "b"), ("x", "y", "z")], threshold=1)
    assert sorted(len(c) for c in cl) == [1, 2]


@pytest.fixture(scope="module")
def dataset():
    runs = labeler.load_runs(str(runner.ROOT / "data" / "runs" / "mock.jsonl"))
    runs.update(labeler.load_runs(str(runner.ROOT / "data" / "runs" / "mock_unseen.jsonl")))
    labels = [labeler.label_run(r, s, SC[s["scenario_id"]]) for r, s in runs.values()]
    return runs, labels


def test_dataset_labels_cover_all_runs_and_classes(dataset):
    runs, labels = dataset
    assert len(labels) == len(runs) and len({l["run_id"] for l in labels}) == len(labels)
    assert {l["label"] for l in labels} == set(labeler.LABELS)
    assert all((l["first_violation_step"] is None) == (not l["violations"]) for l in labels)
    assert all(l["reasons"] for l in labels)


def test_prompt_strength_baseline_moves_the_right_way(dataset):
    runs, labels = dataset
    pr = procedural.analyse(runs, labels, SC)["by_prompt_strength"]
    assert pr["p0"]["unsafe_rate"] > pr["p1"]["unsafe_rate"] > pr["p2"]["unsafe_rate"]
    assert pr["p2"]["declined_rate"] > pr["p0"]["declined_rate"]


def test_patterns_are_mined_from_known_only(dataset):
    runs, labels = dataset
    pats, ab = patterns.mine(runs, labels, SC, min_support=2)
    assert pats and all(p["support"] >= 2 for p in pats)
    known_ids = {l["run_id"] for l in labels if l["split"] == "known"}
    assert all(set(p["example_runs"]) <= known_ids for p in pats)
    assert all(p["missing_control"] and p["rules"] for p in pats)
    cov = patterns.coverage(pats, ab, labels)
    assert cov["known"]["coverage"] >= 0.9 and "unseen" in cov and "diverse" in cov
    assert cov["unseen"]["coverage"] < cov["known"]["coverage"] + 1e-9


# --------------------------------------------------------------------------- critic
def test_critic_proposal_validation_and_accept(tmp_path, monkeypatch):
    good = dict(critic.PROPOSAL_TEMPLATES["long_grant"])
    assert critic.validate_proposal(good) == []
    assert critic.validate_proposal(dict(good, severity="huge"))
    assert critic.validate_proposal(dict(good, condition={"trigger_tool": "nope"}))
    assert critic.validate_proposal(dict(good, condition={"trigger_tool": "send_message", "weird": 1}))
    cands = [{"run_id": f"r{i}", "signals": ["long_grant"]} for i in range(3)]
    props = critic.heuristic_proposals(cands)
    assert props and props[0]["name"] == "long_temporary_access"
    pf, ef = tmp_path / "p.json", tmp_path / "e.json"
    json.dump({"candidates": 3, "proposals": [dict(props[0], id="X01")]}, open(pf, "w"))
    critic.accept(["X01"], pf, ef)
    rules = json.load(open(ef))
    assert rules[0]["accepted"] and rules[0]["id"] == "X01"
    # the accepted extra rule now fires on a run with a long grant
    l, rows, summ = lab("S25", 1.0)
    for r in rows:
        if r["tool"] == "grant_temp_access":
            r["args"]["minutes"] = 240
    monkeypatch.setattr(R, "EXTRA_PATH", ef)
    assert any(v["rule"] == "X01" for v in R.evaluate(R.RunView(rows, summ, SC["S25"])))
    monkeypatch.setattr(R, "EXTRA_PATH", tmp_path / "missing.json")
    assert not any(v["rule"] == "X01" for v in R.evaluate(R.RunView(rows, summ, SC["S25"])))


# --------------------------------------------------------------------------- gold set
def test_kappa_and_prf():
    assert gold.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    assert gold.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    p = gold.prf({"1": "X", "2": "X", "3": "Y"}, {"1": "X", "2": "Y", "3": "X"}, "X")
    assert p["precision"] == 0.5 and p["recall"] == 0.5 and p["f1"] == 0.5


def test_gold_sample_and_validation_roundtrip(tmp_path, dataset):
    runs, labels = dataset
    chosen = gold.sample(labels, runs, SC, n=40, seed=3, out_dir=str(tmp_path))
    assert len(chosen) == 40 and len({c["run_id"] for c in chosen}) == 40
    assert (tmp_path / "gold_guide.md").exists()
    rule = {c["run_id"]: c["label"] for c in chosen}
    rows = list(csv.DictReader(open(tmp_path / "gold_sample.csv", encoding="utf-8")))
    assert "label" not in rows[0] and "rule_label" not in rows[0]            # labellers are not anchored
    for i, r in enumerate(rows):
        r["human_label_A"] = rule[r["run_id"]] if i % 10 else "SAFE"            # 90% agreement with the rules
        r["human_label_B"] = r["human_label_A"]
    with open(tmp_path / "a.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    res = gold.validate(labels, str(tmp_path / "a.csv"), str(tmp_path / "a.csv"), str(tmp_path / "v.json"))
    assert res["kappa_5class"] == 1.0 and res["gold_runs"] == 40
    assert 0.8 <= res["accuracy"] <= 1.0 and "per_label" in res and os.path.exists(tmp_path / "v.json")


def test_gold_validate_without_labels_is_clean(tmp_path, dataset):
    runs, labels = dataset
    gold.sample(labels, runs, SC, n=10, out_dir=str(tmp_path))
    res = gold.validate(labels, str(tmp_path / "gold_sample.csv"), None, str(tmp_path / "v.json"))
    assert res["gold_runs"] == 0 and "note" in res


# --------------------------------------------------------------------------- CLI and exports
def test_stage4_all_writes_handoff_files(tmp_path, monkeypatch):
    import stage4 as cli
    from objective2 import reports as rep
    monkeypatch.setattr(cli, "REPORTS", str(tmp_path))
    class A:
        data = [str(runner.ROOT / "data" / "runs" / "mock.jsonl")]
        min_support = 2
        gold_size = 20
    cli.cmd_all(A)
    for f in ("labels.jsonl", "risk_patterns.json", "rules.json", "taxonomy.json", "splits.json", "procedural.json",
              "stage4_summary.json", "stage4_report.md", "gold_sample.csv", "gold_rule_labels.csv"):
        assert (tmp_path / f).exists(), f
    rules = json.load(open(tmp_path / "rules.json"))
    assert {f"R{i}" for i in range(1, 19)} <= set(rules) and all(rules[k]["ltl"] for k in rules)
    first = json.loads(open(tmp_path / "labels.jsonl").readline())
    assert {"run_id", "label", "violated_rules", "severity", "risk_score", "first_violation_step", "task_success"} <= set(first)
