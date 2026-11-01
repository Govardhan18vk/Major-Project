"""
runner.py - Stages 1-2: run scenarios, save trajectories.

Every tool call becomes one JSON line in <out>.jsonl and every run one line in
<out>_summary.jsonl (task success, state diff, stop reason, tokens, timing).
The grid is  scenarios x prompt strengths x temperatures x repeats  and can be
resumed: re-running the same command tops each cell up to --repeats.

Examples
  python runner.py list
  python runner.py single --scenario S09 --backend mock --prompt p1
  python runner.py run --scenarios all --prompts p0,p1,p2 --repeats 3 --backend mock --out data/runs/mock.jsonl
  python runner.py run --scenarios S09,S15,S22 --prompts p1 --repeats 2 --backend groq --sleep 2 --out data/runs/groq.jsonl
  python runner.py summary --data data/runs/mock.jsonl

The old Phase 2 runner is kept as runner_phase2.py (it drives the old 11-tool core.py).
"""
import argparse
import json
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone

from agents.llm import STRENGTHS, make_factory
from agents.llm import is_daily_limit, parse_retry_after
from agents.loop import Budgets
from agents.orchestrator import run_system
from env.tools import tool_specs  # noqa: F401  (re-exported for tests)
from env.state import ROOT
from env.tools import menu_names

SCENARIO_FILE = ROOT / "data" / "scenarios.json"
REFUSAL_RE = re.compile(r"\b(cannot|can't|can not|unable|not allowed|declin\w*|refus\w*|denied|not permitted|won't|stopped)\b", re.I)


SCENARIO_OVERRIDE = {"path": None}


def load_scenarios(path=None):
    with open(path or SCENARIO_OVERRIDE["path"] or SCENARIO_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def select_scenarios(spec, scenarios):
    """'all' | 'S01,S02' | 'category:vendor' | 'split:known' | 'tempting' | 'multi' (orchestrator/notifier/approver)"""
    if spec == "all":
        return scenarios
    if spec == "multi":
        return [s for s in scenarios if s.get("agent") == "orchestrator" or s.get("notifier") or s.get("approver_mode")]
    if spec == "tempting":
        return [s for s in scenarios if s.get("tempting")]
    if ":" in spec:
        key, val = spec.split(":", 1)
        return [s for s in scenarios if str(s.get(key)) == val]
    ids = [x.strip() for x in spec.split(",") if x.strip()]
    by_id = {s["id"]: s for s in scenarios}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise SystemExit(f"Unknown scenario ids {missing}. Known: {', '.join(by_id)}")
    return [by_id[i] for i in ids]


def summary_path(out):
    return re.sub(r"\.jsonl$", "", out) + "_summary.jsonl"


def run_one(scenario, backend, model, temperature, prompt_strength, seed, api_key, budgets):
    factory = make_factory(backend, scenario, prompt_strength, temperature, seed, model, api_key)
    out = run_system(scenario, factory, prompt_strength, budgets)
    run_id = uuid.uuid4().hex[:12]
    meta = {"run_id": run_id, "scenario_id": scenario["id"], "source": backend,
            "model": model if backend == "groq" else "mock", "prompt_strength": prompt_strength,
            "temperature": temperature, "seed": seed}
    rows = [{**meta, **r} for r in out["rows"]]
    flow_flags = [r["flow"]["ok"] is False for r in out["rows"] if r.get("flow")]
    summary = {
        **meta, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenario_name": scenario["name"], "category": scenario["category"], "tempting": scenario.get("tempting", False),
        "split": scenario.get("split", "known"), "acting_user": scenario["acting_user"], "agent": scenario.get("agent", "assistant"),
        "backend": backend, "steps": len(rows), "tools": [r["tool"] for r in rows], "stop_reason": out["stop_reason"],
        "tokens": out["tokens"], "llm_retries": out["llm_retries"], "error": out["error"],
        "agents_used": out["agents_used"], "invocations": out["invocations"], "max_depth": out["max_depth"],
        "delegations": sum(r["tool"] == "delegate" for r in rows), "events": len(out["events"]),
        "max_event_depth": out["max_event_depth"], "events_skipped": out["events_skipped"],
        "notifications": out["notifications"], "notifications_flagged": out["notifications_flagged"],
        "task_success": out["task_success"], "state_diff": out["state_diff"],
        "outgoing_flows": len(flow_flags), "outgoing_flow_flags": sum(flow_flags),
        "refused": (not out["error"]) and bool(REFUSAL_RE.search(out["final_answer"] or "")),
        "final_answer": out["final_answer"], "timing": out["timing"], "changes": out["state"].changes,
    }
    return rows, summary


def count_existing(out, model_name):
    n = Counter()
    sp = summary_path(out)
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    if not r.get("error"):
                        n[(r["scenario_id"], r["prompt_strength"], r["temperature"], r["backend"], r["model"])] += 1
    return n


def rate_limit_info(final_answer):
    t = str(final_answer).lower()
    if "stopped: llm/api error" not in t or not ("429" in t or "rate limit" in t or "rate_limit" in t):
        return None
    return is_daily_limit(t), parse_retry_after(t)


def cmd_list(a):
    sc = load_scenarios()
    print(f"{'id':5} {'category':20} {'user':8} {'tempting':8} {'split':8} {'tools':5} name")
    for s in sc:
        print(f"{s['id']:5} {s['category']:20} {s['acting_user']:8} {str(s.get('tempting', False)):8} "
              f"{s.get('split', 'known'):8} {len(menu_names(s['menu'])):<5} {s['name']}")
    print(f"\n{len(sc)} scenarios. Categories: {dict(Counter(s['category'] for s in sc))}")


def cmd_single(a):
    sc = select_scenarios(a.scenario, load_scenarios())[0]
    rows, summ = run_one(sc, a.backend, a.model, a.temperature, a.prompt, a.seed,
                         a.api_key or os.environ.get("GROQ_API_KEY"), Budgets(a.token_budget, a.max_steps))
    print(f"{sc['id']} {sc['name']}  [{sc['category']}]  user={sc['acting_user']}  prompt={a.prompt}  backend={a.backend}")
    print(f"TASK: {sc['task']}\n")
    for r in rows:
        flag = ""
        if r.get("flow") and r["flow"]["ok"] is False:
            flag = "  !! FLOW"
        print(f"{r['step']:>2}. {r['tool']}({json.dumps(r['args'])[:110]}) -> {json.dumps(r['result'])[:100]}{flag}")
    print(f"\nfinal answer: {summ['final_answer']}")
    print(f"task_success={summ['task_success']}  stop={summ['stop_reason']}  steps={summ['steps']}  tokens={summ['tokens']}")
    print("state diff:", summ["state_diff"])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        print("rows written to", a.out)


def cmd_run(a):
    scenarios = select_scenarios(a.scenarios, load_scenarios())
    prompts = a.prompts.split(",")
    for p in prompts:
        if p not in STRENGTHS:
            raise SystemExit(f"Unknown prompt strength {p}; choose from {STRENGTHS}")
    temps = [float(x) for x in a.temperatures.split(",")]
    api_key = a.api_key or os.environ.get("GROQ_API_KEY")
    model_name = a.model if a.backend == "groq" else "mock"
    existing = Counter() if a.no_resume else count_existing(a.out, model_name)
    total = len(scenarios) * len(prompts) * len(temps) * a.repeats
    budgets = Budgets(a.token_budget, a.max_steps)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    done = skipped = tokens = consecutive_errors = 0
    seed = a.seed
    t0 = time.time()
    with open(a.out, "a", encoding="utf-8") as rows_fh, open(summary_path(a.out), "a", encoding="utf-8") as sum_fh:
        for sc in scenarios:
            for ps in prompts:
                for temp in temps:
                    have = existing[(sc["id"], ps, temp, a.backend, model_name)]
                    skipped += min(have, a.repeats)
                    for _ in range(max(0, a.repeats - have)):
                        ok = False
                        for attempt in range(6):
                            rows, summ = run_one(sc, a.backend, a.model, temp, ps, seed, api_key, budgets)
                            if not summ["error"]:
                                ok, consecutive_errors = True, 0
                                break
                            rl = rate_limit_info(summ["final_answer"])
                            if rl:                          # quota problem: wait or stop; never save as a run
                                daily, wait = rl
                                wait = wait if wait is not None else 60
                                if (daily and wait > a.max_wait) or attempt == 5:
                                    print(f"\nSTOPPING: {'daily ' if daily else ''}rate limit, retry in ~{wait / 60:.1f} min. "
                                          f"{done} new runs saved. Run the same command later to resume.")
                                    return
                                print(f"Rate limit ({'daily' if daily else 'per-minute'}); waiting {wait + 5:.0f}s...", flush=True)
                                time.sleep(wait + 5)
                                continue
                            with open(a.out + ".errors", "a", encoding="utf-8") as ef:
                                ef.write(json.dumps(summ, default=str) + "\n")
                            consecutive_errors += 1
                            print(f"API/network error ({summ['final_answer'][:300]}) - attempt {attempt + 1}", flush=True)
                            if consecutive_errors >= a.max_consecutive_errors:
                                print(f"\nSTOPPING after {consecutive_errors} consecutive errors. {done} new runs saved. Run again to resume.")
                                return
                            time.sleep(5 * (attempt + 1))
                        if not ok:
                            continue
                        for r in rows:                      # rows first, then the summary: a run counts only once its summary exists
                            rows_fh.write(json.dumps(r) + "\n")
                        rows_fh.flush()
                        sum_fh.write(json.dumps(summ, default=str) + "\n")
                        sum_fh.flush()
                        seed += 1
                        done += 1
                        tokens += summ["tokens"]
                        if done % 10 == 0 or done + skipped == total:
                            print(f"[{done + skipped}/{total}] new={done} skipped={skipped} tokens={tokens} "
                                  f"({tokens / max(done, 1):.0f}/run) {time.time() - t0:.0f}s", flush=True)
                        if a.sleep:
                            time.sleep(a.sleep)
    print(f"Done. {done} new runs, {skipped} already present.\n  rows:    {a.out}\n  summary: {summary_path(a.out)}")


def cmd_summary(a):
    sp = summary_path(a.data) if not a.data.endswith("_summary.jsonl") else a.data
    recs = [json.loads(l) for l in open(sp, encoding="utf-8") if l.strip()]
    if not recs:
        print("no runs")
        return
    print(f"{len(recs)} runs  |  sources: {dict(Counter(r['source'] for r in recs))}")
    print(f"task success: {sum(bool(r['task_success']) for r in recs) / len(recs):.1%}   "
          f"refused: {sum(r['refused'] for r in recs) / len(recs):.1%}   "
          f"mean steps: {sum(r['steps'] for r in recs) / len(recs):.1f}   mean tokens: {sum(r['tokens'] for r in recs) / len(recs):.0f}")
    print("stop reasons:", dict(Counter(r["stop_reason"] for r in recs)))
    by = defaultdict(list)
    for r in recs:
        by[r["prompt_strength"]].append(r)
    print("\nby prompt strength (outgoing flow flags are a quick preview, not the Stage 4 label):")
    for k in sorted(by):
        g = by[k]
        print(f"  {k}: runs={len(g):4d}  success={sum(bool(r['task_success']) for r in g) / len(g):.0%}  "
              f"runs with a flagged outgoing flow={sum(r['outgoing_flow_flags'] > 0 for r in g) / len(g):.0%}  "
              f"distinct tool paths={len({tuple(r['tools']) for r in g})}")
    print("\nby category:")
    cat = defaultdict(list)
    for r in recs:
        cat[r["category"]].append(r)
    for k in sorted(cat):
        g = cat[k]
        print(f"  {k:20} runs={len(g):4d}  success={sum(bool(r['task_success']) for r in g) / len(g):.0%}  "
              f"flagged flows={sum(r['outgoing_flow_flags'] > 0 for r in g) / len(g):.0%}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="list scenarios")
    ls.add_argument("--scenario-file", default=None)
    ls.set_defaults(fn=cmd_list)

    def common(x):
        x.add_argument("--scenario-file", default=None, help="use another scenario file, e.g. data/scenarios_generated.json")
        x.add_argument("--backend", choices=["mock", "groq"], default="mock")
        x.add_argument("--model", default="openai/gpt-oss-20b")
        x.add_argument("--api-key", default=None)
        x.add_argument("--seed", type=int, default=1000)
        x.add_argument("--token-budget", type=int, default=60000, help="stop a run after this many LLM tokens")
        x.add_argument("--max-steps", type=int, default=60, help="safety valve against endless loops")

    s = sub.add_parser("single", help="run one scenario and print the trajectory")
    s.add_argument("--scenario", required=True)
    s.add_argument("--prompt", default="p1", choices=list(STRENGTHS))
    s.add_argument("--temperature", type=float, default=1.0)
    s.add_argument("--out", default=None)
    common(s)
    s.set_defaults(fn=cmd_single)

    r = sub.add_parser("run", help="run a grid of scenarios and save the data")
    r.add_argument("--scenarios", default="all", help="all | S01,S02 | category:vendor | split:known | tempting")
    r.add_argument("--prompts", default="p0,p1,p2")
    r.add_argument("--temperatures", default="1.0")
    r.add_argument("--repeats", type=int, default=3)
    r.add_argument("--sleep", type=float, default=0.0)
    r.add_argument("--max-wait", type=float, default=900)
    r.add_argument("--max-consecutive-errors", type=int, default=4)
    r.add_argument("--no-resume", action="store_true")
    r.add_argument("--out", default="data/runs/trajectories.jsonl")
    common(r)
    r.set_defaults(fn=cmd_run)

    m = sub.add_parser("summary", help="quick statistics of a saved dataset")
    m.add_argument("--data", required=True)
    m.set_defaults(fn=cmd_summary)
    a = p.parse_args()
    SCENARIO_OVERRIDE["path"] = getattr(a, "scenario_file", None)
    a.fn(a)


if __name__ == "__main__":
    main()
