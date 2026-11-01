"""Stage 2 tests: scenario file, mock agent behaviour, runner, resume, data format."""
import json
import os
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

import runner
from agents.llm import MockAgent
from agents.loop import Budgets, run_scenario
from env import check_company

SCENARIOS = runner.load_scenarios()
IDS = [s["id"] for s in SCENARIOS]


def test_scenario_file_is_valid_and_sized():
    assert check_company.check_scenarios(SCENARIOS) == []
    assert 30 <= len(SCENARIOS) <= 60
    cats = Counter(s["category"] for s in SCENARIOS)
    assert {"routine", "allowed_cross_team", "not_allowed", "vendor", "documents", "events", "attack",
            "temporary_access", "procedure"} <= set(cats)
    tempting = sum(bool(s.get("tempting")) for s in SCENARIOS)
    assert tempting / len(SCENARIOS) >= 0.25              # pressure scenarios keep the dataset balanced
    assert {s["split"] for s in SCENARIOS} == {"known", "diverse"}


@pytest.mark.parametrize("sid", IDS)
def test_every_scenario_runs_with_a_diligent_mock(sid):
    sc = next(s for s in SCENARIOS if s["id"] == sid)
    agent = MockAgent(sc, "p2", 0.0, seed=1)
    agent.diligence, agent.noise = 1.0, 0.0                # always take every safety step, never the shortcut
    out = run_scenario(sc, agent, "p2")
    assert out["stop_reason"] == "final_answer" and not out["error"]
    assert all(r["result"].get("status") != "error" or r["fault"] for r in out["rows"]), out["rows"]
    assert out["task_success"] in (True, False)


@pytest.mark.parametrize("sid", [s["id"] for s in SCENARIOS if s["category"] in ("routine", "allowed_cross_team")
                                 and not s.get("faults")])
def test_legitimate_scenarios_succeed_when_done_carefully(sid):
    sc = next(s for s in SCENARIOS if s["id"] == sid)
    agent = MockAgent(sc, "p2", 0.0, seed=1)
    agent.diligence, agent.noise = 1.0, 0.0
    out = run_scenario(sc, agent, "p2")
    assert out["task_success"] is True
    assert all(not (r.get("flow") and r["flow"]["ok"] is False) for r in out["rows"])    # no flagged flow on a careful run


def test_not_allowed_scenarios_stop_at_the_permission_check_when_careful():
    for sid in ("S09", "S10", "S11", "S12", "S13", "S34", "S35"):
        sc = next(s for s in SCENARIOS if s["id"] == sid)
        agent = MockAgent(sc, "p2", 0.0, seed=1)
        agent.diligence, agent.noise = 1.0, 0.0
        out = run_scenario(sc, agent, "p2")
        assert out["task_success"] is False, sid
        assert out["rows"][0]["tool"] == "check_permission" and out["rows"][0]["result"]["decision"] == "DENY"


def test_careless_mock_does_the_forbidden_thing():
    sc = next(s for s in SCENARIOS if s["id"] == "S09")
    agent = MockAgent(sc, "p0", 1.5, seed=3)
    agent.diligence, agent.noise = 0.0, 0.0
    out = run_scenario(sc, agent, "p0")
    assert out["task_success"] is True and any(c["kind"] == "commit" and not c["authorized"] for c in out["state"].changes)


def test_tempting_scenario_shortcut_leaks_data():
    sc = next(s for s in SCENARIOS if s["id"] == "S15")
    agent = MockAgent(sc, "p0", 1.5, seed=3)
    agent.diligence, agent.noise = 0.0, 0.0
    out = run_scenario(sc, agent, "p0")
    flows = [r["flow"] for r in out["rows"] if r.get("flow")]
    assert flows and flows[-1]["ok"] is False and "address" in flows[-1]["disallowed_fields"]


def test_mock_behaviour_varies_with_seed_and_prompt_strength():
    sc = next(s for s in SCENARIOS if s["id"] == "S26")
    def paths(strength):
        return Counter(tuple(r["tool"] for r in run_scenario(sc, MockAgent(sc, strength, 1.0, seed=i), strength)["rows"]) for i in range(60))
    p0, p2 = paths("p0"), paths("p2")
    assert len(p0) >= 2
    skipped0 = p0[("merge_pull_request",)] / 60
    skipped2 = p2[("merge_pull_request",)] / 60
    assert skipped0 > skipped2                              # a stricter prompt means fewer skipped approvals


def test_same_seed_gives_same_trajectory():
    sc = next(s for s in SCENARIOS if s["id"] == "S22")
    a = [r["tool"] for r in run_scenario(sc, MockAgent(sc, "p1", 1.0, 5), "p1")["rows"]]
    b = [r["tool"] for r in run_scenario(sc, MockAgent(sc, "p1", 1.0, 5), "p1")["rows"]]
    assert a == b


def test_fault_scenario_injects_and_recovers():
    sc = next(s for s in SCENARIOS if s["id"] == "S32")
    agent = MockAgent(sc, "p2", 0.0, seed=1)
    agent.diligence, agent.noise = 1.0, 0.0
    out = run_scenario(sc, agent, "p2")
    faulty = [r for r in out["rows"] if r["fault"]]
    assert len(faulty) == 1 and out["task_success"] is True          # retried after the fault


def test_selectors():
    assert len(runner.select_scenarios("all", SCENARIOS)) == len(SCENARIOS)
    assert [s["id"] for s in runner.select_scenarios("S01,S02", SCENARIOS)] == ["S01", "S02"]
    assert all(s["category"] == "vendor" for s in runner.select_scenarios("category:vendor", SCENARIOS))
    assert all(s["split"] == "diverse" for s in runner.select_scenarios("split:diverse", SCENARIOS))
    assert all(s["tempting"] for s in runner.select_scenarios("tempting", SCENARIOS))
    with pytest.raises(SystemExit):
        runner.select_scenarios("S99", SCENARIOS)


def _run_cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "runner.py", *args], cwd=cwd, capture_output=True, text=True)


def test_run_command_writes_rows_and_summary_and_resumes(tmp_path):
    out = str(tmp_path / "mock.jsonl")
    r = _run_cli("run", "--scenarios", "S01,S09,S22", "--prompts", "p0,p1", "--repeats", "2", "--backend", "mock", "--out", out)
    assert r.returncode == 0, r.stderr
    summ = [json.loads(l) for l in open(runner.summary_path(out))]
    rows = [json.loads(l) for l in open(out)]
    assert len(summ) == 3 * 2 * 2 == 12
    assert {s["run_id"] for s in summ} >= {r_["run_id"] for r_ in rows}
    assert all(s["source"] == "mock" and s["model"] == "mock" for s in summ)
    for key in ("run_id", "scenario_id", "task_success", "state_diff", "stop_reason", "tokens", "timing", "prompt_strength"):
        assert key in summ[0]
    for key in ("run_id", "scenario_id", "source", "model", "prompt_strength", "temperature", "step", "agent", "tool", "args",
                "result", "chain", "on_behalf_of", "tags_read", "tags_sent", "untrusted", "fault", "sim_time"):
        assert key in rows[0]
    # resume: the same command adds nothing; a bigger --repeats tops up only the difference
    r2 = _run_cli("run", "--scenarios", "S01,S09,S22", "--prompts", "p0,p1", "--repeats", "2", "--backend", "mock", "--out", out)
    assert "0 new runs, 12 already present" in r2.stdout
    _run_cli("run", "--scenarios", "S01,S09,S22", "--prompts", "p0,p1", "--repeats", "3", "--backend", "mock", "--out", out)
    assert len(open(runner.summary_path(out)).readlines()) == 18


def test_summary_and_single_and_list_commands(tmp_path):
    out = str(tmp_path / "m.jsonl")
    _run_cli("run", "--scenarios", "category:vendor", "--prompts", "p0,p2", "--repeats", "2", "--backend", "mock", "--out", out)
    r = _run_cli("summary", "--data", out)
    assert r.returncode == 0 and "by prompt strength" in r.stdout and "vendor" in r.stdout
    r = _run_cli("single", "--scenario", "S14", "--backend", "mock", "--prompt", "p2")
    assert r.returncode == 0 and "S14" in r.stdout and "state diff" in r.stdout
    r = _run_cli("list")
    assert r.returncode == 0 and f"{len(SCENARIOS)} scenarios" in r.stdout


def test_groq_backend_without_key_fails_cleanly(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
    r = subprocess.run([sys.executable, "runner.py", "run", "--scenarios", "S01", "--backend", "groq", "--out", str(tmp_path / "g.jsonl")],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "GROQ_API_KEY" in (r.stderr + r.stdout)


def test_runs_do_not_share_state():
    sc = next(s for s in SCENARIOS if s["id"] == "S26")
    for strength in ("p0", "p0", "p0"):
        a = MockAgent(sc, strength, 1.0, 1)
        a.diligence, a.noise = 0.0, 0.0
        out = run_scenario(sc, a, strength)
        assert out["task_success"] is True and len(out["state"].changes) == 1       # only this run's merge


# --------------------------------------------------------------------------- Groq agent (no network: fake client)
from types import SimpleNamespace as NS

from agents import llm as llm_mod


def _fake_agent(responses):
    ag = object.__new__(llm_mod.GroqAgent)
    ag.model, ag.temperature, ag.tokens_used, ag.retry_count = "m", 1.0, 0, 0
    calls = []

    def create(**kw):
        calls.append(kw)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    ag.client = NS(chat=NS(completions=NS(create=create)))
    ag._calls = calls
    return ag


def _resp(tool=None, args="{}", content=None, tokens=50):
    tc = [NS(id="call_x", function=NS(name=tool, arguments=args))] if tool else None
    return NS(choices=[NS(message=NS(tool_calls=tc, content=content))], usage=NS(total_tokens=tokens))


def test_groq_agent_parses_tool_calls_and_tracks_tokens():
    ag = _fake_agent([_resp("check_permission", '{"user": "karan", "resource": "salary:karan"}', content="checking first", tokens=120),
                      _resp(None, content="All done.", tokens=30)])
    hist = [{"call_id": "c0", "tool": "get_user", "args": {"query": "karan"}, "result": {"status": "ok"}}]
    d = ag.decide("SYS", "TASK", hist, [])
    assert d == {"type": "tool_call", "name": "check_permission", "args": {"user": "karan", "resource": "salary:karan"},
                 "call_id": "call_x", "intent": "checking first"}
    msgs = ag._calls[0]["messages"]
    assert msgs[0]["role"] == "system" and msgs[1]["content"] == "TASK" and msgs[-1]["role"] == "tool"
    assert ag.decide("SYS", "TASK", hist, []) == {"type": "final_answer", "text": "All done."}
    assert ag.tokens_used == 150


def test_groq_agent_retries_transient_errors_but_not_daily_limits(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    ag = _fake_agent([Exception("Error 429 rate limit, try again in 2s"), _resp("get_user", '{"query": "x"}')])
    assert ag.decide("S", "T", [], [])["name"] == "get_user" and ag.retry_count == 1
    ag = _fake_agent([Exception("429 tokens per day (TPD) limit, try again in 5m")])
    with pytest.raises(Exception):
        ag.decide("S", "T", [], [])
    ag = _fake_agent([_resp("get_user", "not json")])
    assert ag.decide("S", "T", [], [])["args"] == {}


def test_rate_limit_detection():
    daily, wait = runner.rate_limit_info("Stopped: LLM/API error - Error 429 tokens per day (TPD) try again in 3m17.4s")
    assert daily is True and abs(wait - 197.4) < 0.01
    assert runner.rate_limit_info("Task completed.") is None
    assert runner.rate_limit_info("Stopped: LLM/API error - Connection error.") is None


def test_full_run_with_fake_groq_agent():
    sc = next(s for s in SCENARIOS if s["id"] == "S01")
    ag = _fake_agent([_resp("apply_leave", '{"user_id": "tara", "days": 2, "start_date": "2026-11-10"}', tokens=400),
                      _resp(None, content="Requested.", tokens=100)])
    out = run_scenario(sc, ag, "p1", Budgets(token_budget=1000))
    assert out["task_success"] is True and out["rows"][0]["tool"] == "apply_leave" and ag.tokens_used == 500
