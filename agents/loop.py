"""
agents/loop.py - one agent invocation: decide -> run the tool -> record a row -> repeat.

`agent_loop` is used for EVERY agent in the system (the single assistant, the
orchestrator, team agents, the notifier, the approver). It writes rows into a
shared list so the whole multi-agent run is one trajectory, with each row
carrying its agent, delegation chain, depth and the step that spawned it.

A run is limited by budgets, not by a tiny step cap:
  token_budget   total LLM tokens across ALL agents in the run
  max_steps      total tool calls in the run (safety valve against endless loops)
  max_inv_steps  tool calls in a single agent invocation
  max_depth      delegation depth (orchestrator = 0, team agent = 1, ...)
  max_event_depth  how many event-triggered reactions may chain (cascades)
In open mode nothing is blocked; every call is recorded.
"""
import time
from dataclasses import dataclass

from env import faults
from env.tools import TOOLS


@dataclass
class Budgets:
    token_budget: int = 60000
    max_steps: int = 60
    max_inv_steps: int = 25
    max_depth: int = 3
    max_event_depth: int = 3


def _execute(state, name, args, allowed_names):
    if name not in TOOLS or name not in allowed_names:
        return {"status": "error", "error": f"unknown tool '{name}'; available: {', '.join(allowed_names)}"}
    if not isinstance(args, dict):
        return {"status": "error", "error": "arguments must be an object"}
    fault = faults.maybe_fault(state, name)
    if fault is not None:
        return fault
    try:
        result = TOOLS[name].fn(state, **args)
    except TypeError as exc:
        return {"status": "error", "error": f"bad arguments: {exc}"}
    except Exception as exc:                       # a tool bug must never crash a run
        return {"status": "error", "error": f"tool failed: {exc}"}
    return result if isinstance(result, dict) else {"status": "ok", "result": result}


def agent_loop(state, agent, system, task, names, specs, rows, budgets, ctx):
    """Run one invocation. ctx: shared (dict with 'abort'/'stop'), tokens (callable), inv, depth, parent_step, trigger_event."""
    from env.tools import tool_specs  # noqa: F401  (kept importable for callers)
    own, steps = [], 0
    final_answer, stop_reason, final_llm_s = None, None, 0.0
    shared = ctx["shared"]
    while True:
        if shared.get("abort"):
            stop_reason = "aborted"
            break
        if shared.get("stop"):
            stop_reason = shared["stop"]
            break
        if len(rows) >= budgets.max_steps:
            shared["stop"] = stop_reason = "max_steps"
            break
        if ctx["tokens"]() > budgets.token_budget:
            shared["stop"] = stop_reason = "token_budget"
            break
        if steps >= budgets.max_inv_steps:
            stop_reason = "inv_max_steps"
            break
        t0 = time.perf_counter()
        try:
            decision = agent.decide(system, task, own, specs)
        except Exception as exc:
            final_llm_s = time.perf_counter() - t0
            shared["abort"] = True
            shared["error"] = f"Stopped: LLM/API error - {exc}"
            final_answer, stop_reason = shared["error"], "error"
            break
        llm_s = time.perf_counter() - t0
        if decision["type"] == "final_answer":
            final_llm_s, final_answer, stop_reason = llm_s, decision["text"], "final_answer"
            break

        name, args = decision["name"], decision.get("args", {})
        state.reset_trace()
        row = {"call_id": decision.get("call_id", f"call_{len(rows) + 1}"), "step": len(rows) + 1,
               "invocation": ctx["inv"], "depth": ctx["depth"], "parent_step": ctx["parent_step"],
               "trigger_event": ctx.get("trigger_event"), "event_depth": state.event_depth,
               "agent": state.agent, "acting_user": state.acting_user, "on_behalf_of": state.requester,
               "chain": list(state.chain), "intent": decision.get("intent", ""), "tool": name, "args": args,
               "result": None, "sim_time": state.now(), "llm_seconds": llm_s}
        state.current_step = row["step"]
        rows.append(row)                    # appended BEFORE the tool runs, so a delegate call precedes its sub-agent's rows
        t1 = time.perf_counter()
        result = _execute(state, name, args, names)
        row["tool_seconds"] = time.perf_counter() - t1
        flow = state.last_flow
        row.update({"result": result, "tags_read": sorted({r["tag"] for r in state.last_read}),
                    "resources_read": [r["resource"] for r in state.last_read], "flow": flow,
                    "tags_sent": [f["tag"] for f in flow["fine_flows"]] if flow else [],
                    "untrusted": state.last_untrusted, "fault": state.last_fault, "event_id": state.last_event_id})
        own.append(row)
        state.advance()
        steps += 1
    return {"final_answer": final_answer if final_answer is not None else f"Stopped: {stop_reason}.",
            "stop_reason": stop_reason, "steps": steps, "final_llm_seconds": final_llm_s}


def run_scenario(scenario, agent, prompt_strength="p1", budgets=None):
    """Single-agent convenience wrapper: one agent object plays the entry role. Multi-agent scenarios
    need a factory; use agents.orchestrator.run_system for those."""
    from .orchestrator import run_system
    from .llm import MockAgent
    entry = scenario.get("agent", "assistant")
    if isinstance(agent, MockAgent):
        # a mock entry agent: give the other roles (team agents, notifier, approver) matching mock agents
        from .mock_roles import MockApprover, MockNotifier
        import zlib
        seed = zlib.crc32(scenario["id"].encode()) % 1000

        def factory(name, idx, context=None):
            if idx == 0 or name == entry and idx == 0:
                return agent
            kw = {"diligence": agent.diligence, "noise": agent.noise}
            if name == "notifier":
                return MockNotifier(scenario, prompt_strength, 1.0, seed + idx, context, **kw)
            if name == "approver":
                return MockApprover(scenario, prompt_strength, 1.0, seed + idx, context, **kw)
            return MockAgent(scenario, prompt_strength, 1.0, seed + idx, agent_name=name, **kw)
        if entry != "assistant":
            agent.agent_name = entry
        return run_system(scenario, factory, prompt_strength, budgets)
    return run_system(scenario, lambda name, idx, context=None: agent, prompt_strength, budgets)
