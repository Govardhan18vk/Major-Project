"""
runner.py - Objective 2: headless experiment runner + trajectory dataset.

Runs tasks x acting users x temperatures x N repeats and appends one JSON
line per run to a JSONL file. Each record holds the raw trajectory AND its
labels, so datasets can be re-labelled later when the rules change.

Examples
  # offline, no API key (stochastic mock agent) - pipeline testing only
  python runner.py run --backend mock --runs 10 --out data/mock.jsonl

  # real LLM via Groq (key from env or --api-key)
  set GROQ_API_KEY=...        (PowerShell: $env:GROQ_API_KEY="...")
  python runner.py run --backend groq --tasks salary_report,external_sharing ^
         --users Bob,Admin --temperatures 0.7,1.0 --runs 10 --sleep 1 --out data/groq.jsonl

  # recompute labels for an existing dataset after editing safety_rules.py
  python runner.py relabel --in data/groq.jsonl --out data/groq_relabelled.jsonl
"""
import argparse
import json
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

import core
from safety_rules import evaluate, is_refusal, LABELLER_VERSION
from scenarios import TASKS, task_success

ALL_USERS = ["Bob", "Charlie", "Admin", "HR_Manager", "Alice"]
STEP_KEYS = ("step", "call_id", "tool", "args", "result", "llm_seconds", "tool_seconds")


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def make_llm(backend, model, temperature, seed, api_key):
    if backend == "mock":
        return core.MockLLM(seed=seed, diligence=_clip(0.9 - 0.3 * temperature, 0.15, 0.95),
                            noise=_clip(0.1 + 0.1 * temperature, 0.05, 0.4))
    if backend == "groq":
        if not api_key:
            raise SystemExit("Groq backend needs --api-key or GROQ_API_KEY.")
        return core.GroqLLM(api_key=api_key, model=model, temperature=temperature)
    raise SystemExit(f"Unknown backend {backend}")


def label_record(task_key, history, state, user):
    ev = evaluate(history, user)
    ev["task_success"] = task_success(task_key, history, state) if state is not None else None
    return ev


def rate_limit_info(final_answer):
    """(is_daily, wait_seconds_or_None) if the run died on a rate limit, else None."""
    t = str(final_answer).lower()
    if "stopped: llm/api error" not in t or not ("429" in t or "rate limit" in t or "rate_limit" in t):
        return None
    return core.is_daily_limit(t), core.parse_retry_after(t)


def run_one(task_key, task_text, user, backend, model, temperature, seed, api_key, max_steps=12):
    llm = make_llm(backend, model, temperature, seed, api_key)
    out = core.run_agent_full(task_text, llm, acting_user=user, max_steps=max_steps)
    hist = out["history"]
    labels = label_record(task_key, hist, out["state"], user)
    spec = TASKS.get(task_key, {})
    return {
        "run_id": uuid.uuid4().hex[:12],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "labeller_version": LABELLER_VERSION,
        "task_key": task_key, "task": task_text, "category": spec.get("category", "custom"),
        "acting_user": user, "backend": backend, "model": model if backend == "groq" else "mock",
        "temperature": temperature, "seed": seed,
        "error": out["error"], "llm_retries": getattr(llm, "retry_count", 0), "tokens": getattr(llm, "tokens_used", 0),
        "refused": (not out["error"]) and is_refusal(out["final_answer"]), "n_steps": len(hist), "tools": [h["tool"] for h in hist],
        "steps": [{k: h[k] for k in STEP_KEYS if k in h} for h in hist],
        "final_answer": out["final_answer"], "timing": out["timing"],
        **labels,
    }


def _count_existing(path, model):
    """Successful runs already in `path`, per (task, user, temperature, backend, model) cell."""
    n = Counter()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    if not rec.get("error"):
                        n[(rec["task_key"], rec["acting_user"], rec["temperature"], rec["backend"], rec["model"])] += 1
    return n


def cmd_run(a):
    tasks = list(TASKS) if a.tasks == "all" else a.tasks.split(",")
    users = ALL_USERS if a.users == "all" else a.users.split(",")
    temps = [float(x) for x in a.temperatures.split(",")]
    for t in tasks:
        if t not in TASKS:
            raise SystemExit(f"Unknown task '{t}'. Known: {', '.join(TASKS)}")
    api_key = a.api_key or os.environ.get("GROQ_API_KEY")
    model_name = a.model if a.backend == "groq" else "mock"
    existing = Counter() if a.no_resume else _count_existing(a.out, model_name)
    total = len(tasks) * len(users) * len(temps) * a.runs
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    done, skipped, tokens, seed, consecutive_errors = 0, 0, 0, a.seed, 0
    t0 = time.time()
    with open(a.out, "a", encoding="utf-8") as fh:
        for task_key in tasks:
            for user in users:
                for temp in temps:
                    have = existing[(task_key, user, temp, a.backend, model_name)]
                    skipped += min(have, a.runs)
                    for _ in range(max(0, a.runs - have)):
                        ok = False
                        for attempt in range(6):
                            rec = run_one(task_key, TASKS[task_key]["task"], user, a.backend, a.model, temp,
                                          seed, api_key, a.max_steps)
                            if not rec["error"]:
                                ok, consecutive_errors = True, 0
                                break
                            rl = rate_limit_info(rec["final_answer"])
                            if rl:                      # quota problem: wait or stop, never save as a run
                                daily, wait = rl
                                wait = wait if wait is not None else 60
                                if (daily and wait > a.max_wait) or attempt == 5:
                                    print(f"\nSTOPPING: {'daily ' if daily else ''}rate limit, Groq says retry in ~{wait/60:.1f} min. "
                                          f"{done} new runs saved to {a.out}. Run the same command later to resume.")
                                    return
                                print(f"Rate limit hit ({'daily token' if daily else 'per-minute'}); waiting {wait + 5:.0f}s then retrying...", flush=True)
                                time.sleep(wait + 5)
                                continue
                            # network / API failure: keep a note in <out>.errors, back off, try a fresh run
                            with open(a.out + ".errors", "a", encoding="utf-8") as ef:
                                ef.write(json.dumps(rec) + "\n")
                            consecutive_errors += 1
                            print(f"API/network error ({rec['final_answer'][:400]}) - attempt {attempt + 1}", flush=True)
                            if consecutive_errors >= a.max_consecutive_errors:
                                print(f"\nSTOPPING after {consecutive_errors} consecutive errors (check your internet/VPN/proxy and the Groq status). "
                                      f"{done} new runs saved to {a.out}. Run the same command to resume.")
                                return
                            time.sleep(5 * (attempt + 1))
                        if not ok:
                            continue
                        fh.write(json.dumps(rec) + "\n")
                        fh.flush()
                        seed += 1
                        done += 1
                        tokens += rec.get("tokens", 0)
                        if done % 5 == 0 or done + skipped == total:
                            print(f"[{done + skipped}/{total}] new={done} resumed-skip={skipped} tokens_used={tokens} "
                                  f"({tokens / max(done, 1):.0f}/run) {time.time() - t0:.0f}s", flush=True)
                        if a.sleep:
                            time.sleep(a.sleep)
    print(f"Done. {done} new runs, {skipped} already present. Wrote to {a.out}")


def cmd_relabel(a):
    n = 0
    with open(a.inp, encoding="utf-8") as fin, open(a.out, "w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            rec = json.loads(line)
            hist = rec["steps"]
            ev = evaluate(hist, rec["acting_user"])
            ev["task_success"] = rec.get("task_success")   # needs final state; keep stored value
            rec.update(ev)
            rec["labeller_version"] = LABELLER_VERSION
            fout.write(json.dumps(rec) + "\n")
            n += 1
    print(f"Relabelled {n} runs -> {a.out} (task_success kept from original run)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="generate trajectories")
    r.add_argument("--backend", choices=["mock", "groq"], default="mock")
    r.add_argument("--model", default="openai/gpt-oss-20b")
    r.add_argument("--api-key", default=None)
    r.add_argument("--tasks", default="all", help="comma list of task keys, or 'all'")
    r.add_argument("--users", default="all", help="comma list of users, or 'all'")
    r.add_argument("--temperatures", default="1.0")
    r.add_argument("--runs", type=int, default=10, help="repeats per (task,user,temperature)")
    r.add_argument("--seed", type=int, default=1000)
    r.add_argument("--max-steps", type=int, default=20, help="step cap per run (core default is 12)")
    r.add_argument("--sleep", type=float, default=0.0, help="seconds between runs (rate limits)")
    r.add_argument("--max-wait", type=float, default=900, help="on a rate limit, wait up to this many seconds, else stop (resume later)")
    r.add_argument("--max-consecutive-errors", type=int, default=4, help="stop after this many API/network failures in a row")
    r.add_argument("--no-resume", action="store_true", help="ignore runs already in --out (default: top up to --runs per cell)")
    r.add_argument("--out", default="data/trajectories.jsonl")
    r.set_defaults(fn=cmd_run)
    l = sub.add_parser("relabel", help="recompute labels from stored trajectories")
    l.add_argument("--in", dest="inp", required=True)
    l.add_argument("--out", required=True)
    l.set_defaults(fn=cmd_relabel)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
