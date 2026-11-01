"""Stage 3 tests: multi-agent delegation, events and cascades, approver agent, attacker generator."""
import json

from agents import attacker
from agents.llm import make_factory
from agents.loop import Budgets
from agents.orchestrator import run_system
from env import check_company
import runner

SC = {s["id"]: s for s in json.load(open(runner.SCENARIO_FILE, encoding="utf-8"))}


def run(sid, prompt="p1", seed=1, **kw):
    sc = SC[sid]
    return run_system(sc, make_factory("mock", sc, prompt, 1.0, seed, overrides=kw or None), prompt)


def test_scenarios_valid():
    assert check_company.check_scenarios(list(SC.values())) == []
    assert len(SC) >= 49


def test_delegation_chain_and_depth():
    out = run("S38", diligence=1.0)
    agents = [r["agent"] for r in out["rows"]]
    assert agents == ["orchestrator", "hr_agent"]
    assert out["rows"][1]["chain"] == ["tara", "orchestrator", "hr_agent"] or out["rows"][1]["chain"][-2:] == ["orchestrator", "hr_agent"]
    assert out["rows"][1]["parent_step"] == 1 and out["max_depth"] == 1 and out["task_success"]


def test_two_delegations():
    out = run("S42", diligence=1.0)
    assert out["agents_used"] == ["orchestrator", "shipping_agent", "hr_agent"] and out["task_success"]


def test_confused_deputy_gap_recorded():
    out = run("S40", diligence=0.0, noise=0.0)
    commits = [c for c in out["state"].changes if c["kind"] == "commit"]
    assert commits and commits[0]["authorized"] is False and commits[0]["agent_authorized"] is True
    deleg = [c for c in out["state"].changes if c["kind"] == "delegation"]
    assert any(d["spoofed"] for d in deleg)           # careless path claims to act for anil


def test_careful_agent_stops_on_deny():
    out = run("S37", diligence=1.0)
    assert not out["task_success"]


def test_delegate_depth_limit():
    sc = SC["S38"]
    out = run_system(sc, make_factory("mock", sc, "p1", 1.0, 1), "p1", Budgets(max_depth=0))
    assert out["rows"][0]["result"]["status"] == "error"


def test_notifier_events_and_cascade():
    out = run("S46", diligence=0.0, noise=0.0)
    types = [e["type"] for e in out["events"]]
    assert "repo.push" in types and out["notifications"] >= 1
    out2 = run("S43", diligence=1.0)
    assert out2["max_event_depth"] >= 0 and out2["agents_used"][0] == "assistant"


def test_event_depth_cap():
    sc = SC["S46"]
    skipped = 0
    for seed in range(1, 8):          # the careless notifier runs CI only some of the time
        out = run_system(sc, make_factory("mock", sc, "p1", 1.0, seed, overrides={"diligence": 0.0, "noise": 0.0}), "p1", Budgets(max_event_depth=1))
        skipped += len(out["events_skipped"])
    assert skipped >= 1


def test_leave_notifier_leak_flagged():
    flagged = sum(run("S45", seed=s, diligence=0.0, noise=0.0)["notifications_flagged"] for s in range(1, 4))
    assert flagged >= 1


def test_approver_agent_pressure():
    careless = run("S48", diligence=0.0, noise=0.0)
    assert careless["task_success"]
    careful = run("S48", diligence=1.0)
    approvals = [c for c in careful["state"].changes if c["kind"] == "approval"]
    assert approvals and approvals[0]["decision"] == "denied" and not careful["task_success"]   # a careful approver asks a human, who says no


def test_no_decision_counts_as_denied():
    sc = SC["S48"]

    class Silent:
        tokens_used = retry_count = 0
        def decide(self, *a):
            return {"type": "final_answer", "text": "done"}
    base = make_factory("mock", sc, "p1", 1.0, 1, overrides={"diligence": 1.0})
    out = run_system(sc, lambda n, i, c=None: Silent() if n == "approver" else base(n, i, c), "p1")
    assert not any(c["kind"] == "approval" and c["decision"] == "approved" for c in out["state"].changes)


def test_injection_via_delegation():
    out = run("S47", diligence=0.0, noise=0.0)
    assert out["task_success"] and "shipping_agent" in out["agents_used"]


def test_attacker_generates_valid_unseen():
    scs = attacker.generate(10, seed=3)
    assert len(scs) == 10 and all(s["split"] == "unseen" and s["source"] == "attacker" for s in scs)
    assert check_company.check_scenarios(scs) == []
    assert len({s["id"] for s in scs}) == 10
    assert scs == attacker.generate(10, seed=3)       # deterministic


def test_attacker_rewrite_keeps_structure():
    scs = attacker.generate(3, seed=3, rewrite=lambda t: "REWORDED: " + t)
    assert all(s["task"].startswith("REWORDED") and "task_original" in s for s in scs)


def test_runner_summary_fields():
    sc = SC["S37"]
    rows, summ = runner.run_one(sc, "mock", "x", 1.0, "p0", 5, None, Budgets())
    for k in ("agents_used", "delegations", "events", "notifications", "max_depth"):
        assert k in summ
    assert runner.select_scenarios("multi", list(SC.values()))
