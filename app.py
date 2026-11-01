"""
app.py - SafeTask v2 (Stages 1-3): run a ShopSphere scenario and watch the trajectory.

    streamlit run app.py

Pick a scenario, a safety-prompt strength and a backend (offline mock or real
Groq LLM). The page shows each tool call as a vertical flow with its tags and
flow flags, the goal result, the state diff, and timing. No SAFE/UNSAFE verdict
is shown here: labelling is Objective 2 (Stage 4).
Stage 3 adds agent lanes (who made each call), the delegation chain, the event list and the notifier's notices.
Use the sidebar to switch to the attacker-generated scenarios (data/scenarios_generated.json, split "unseen").
The older single-agent demo is still available as app_basic.py.
"""
import json
import os

import streamlit as st

from agents.llm import STRENGTHS
from agents.loop import Budgets
from env.tools import menu_names
from runner import load_scenarios, run_one

st.set_page_config(page_title="SafeTask v2 - ShopSphere", layout="wide")
st.title("SafeTask: ShopSphere agentic environment")
st.caption("Simulated e-commerce company. All people, data and secrets are fictional. Open mode: tools never block, "
           "they only record what happens.")

from pathlib import Path
from env.state import ROOT

GEN = ROOT / "data" / "scenarios_generated.json"
with st.sidebar:
    use_gen = st.checkbox("Attacker-generated (unseen) scenarios", value=False, disabled=not GEN.exists(),
                          help="Create them with: python -m agents.attacker --n 15")
scenarios = load_scenarios(GEN if use_gen else None)
by_id = {s["id"]: s for s in scenarios}

with st.sidebar:
    st.header("Settings")
    backend = st.radio("Backend", ["mock", "groq"], help="mock = free offline scripted agent; groq = real LLM")
    api_key, model = None, "openai/gpt-oss-20b"
    if backend == "groq":
        api_key = st.text_input("Groq API key", type="password", value=os.environ.get("GROQ_API_KEY", ""))
        model = st.text_input("Groq model", value=model)
    category = st.selectbox("Category", ["all"] + sorted({s["category"] for s in scenarios}))
    pool = [s for s in scenarios if category == "all" or s["category"] == category]
    sid = st.selectbox("Scenario", [s["id"] for s in pool], format_func=lambda i: f"{i}  {by_id[i]['name']}")
    prompt = st.selectbox("Safety prompt strength", list(STRENGTHS),
                          format_func=lambda p: {"p0": "p0 - none", "p1": "p1 - mild hint", "p2": "p2 - strict rules"}[p])
    temperature = st.slider("Temperature", 0.0, 1.5, 1.0, 0.1)
    n_runs = st.slider("Runs of this scenario", 1, 8, 3)
    token_budget = st.number_input("Token budget per run", 5000, 200000, 60000, 5000)

sc = by_id[sid]
st.subheader(f"{sc['id']}: {sc['name']}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Acting user", sc["acting_user"])
c2.metric("Category", sc["category"])
c3.metric("Tempting", "yes" if sc.get("tempting") else "no")
c4.metric("Entry agent", sc.get("agent", "assistant") + (" + notifier" if sc.get("notifier") else "") +
          (" + approver agent" if sc.get("approver_mode") == "agent" else ""))
st.info(sc["task"])
with st.expander("Tools this agent can see"):
    st.write(", ".join(menu_names(sc["menu"])))
if sc.get("seed_messages"):
    with st.expander("Messages waiting in the inbox"):
        for m in sc["seed_messages"]:
            st.write(f"**From:** {m['from']}  ({m['origin']})  \n**Subject:** {m['subject']}  \n{m['body']}")


def render_flow(rows):
    if not rows:
        st.write("_No tool calls made._")
        return
    for i, r in enumerate(rows):
        flags = []
        if r.get("flow") and r["flow"]["ok"] is False:
            flags.append("flow flagged")
        if r.get("untrusted"):
            flags.append("untrusted content")
        if r.get("fault"):
            flags.append(f"fault: {r['fault']}")
        if r.get("tags_read"):
            flags.append("read: " + "/".join(r["tags_read"]))
        with st.container(border=True):
            lane = " > ".join(r.get("chain") or [r["agent"]])
            st.markdown(f"**{r['step']}. `{r['tool']}`**  :blue[{r['agent']}]  " + ("(spawned by step %s)  " % r["parent_step"] if r.get("parent_step") else "") + ("  \n" + " | ".join(flags) if flags else ""))
            if r.get("intent"):
                st.caption(f"chain: {lane}  |  depth {r['depth']}" + (f"  |  triggered by event {r['trigger_event']}" if r.get("trigger_event") else ""))
            st.caption(f"intent: {r['intent']}")
            st.code(json.dumps(r["args"], ensure_ascii=False), language="json")
            st.caption(f"-> {json.dumps(r['result'], ensure_ascii=False)[:260]}")
            st.caption(f"{r['llm_seconds']:.2f}s LLM | {r['tool_seconds'] * 1000:.2f} ms tool | sim time {r['sim_time']}")
            if r.get("flow") and r["flow"]["fine_flows"]:
                st.caption("data flowed: " + ", ".join(f"{f['resource']} [{f['tag']}] ok={f['ok']}" for f in r["flow"]["fine_flows"]))
        if i < len(rows) - 1:
            st.markdown("<div style='text-align:center'>&#8595;</div>", unsafe_allow_html=True)


if st.button("Run", type="primary"):
    if backend == "groq" and not api_key:
        st.error("Enter a Groq API key, or switch to the mock backend.")
        st.stop()
    results = []
    for i in range(n_runs):
        with st.spinner(f"Run {i + 1} of {n_runs}..."):
            rows, summ = run_one(sc, backend, model, temperature, prompt, 1000 + i, api_key, Budgets(int(token_budget), 60))
        results.append((rows, summ))
    st.session_state["results"] = results

results = st.session_state.get("results")
if results:
    st.divider()
    distinct = len({tuple(s["tools"]) for _, s in results})
    st.markdown(f"**{len(results)} runs, {distinct} distinct trajectories.**")
    tabs = st.tabs([f"Run {i + 1}" for i in range(len(results))])
    for tab, (rows, s) in zip(tabs, results):
        with tab:
            a, b, c, d = st.columns(4)
            a.metric("Task success", "yes" if s["task_success"] else "no")
            b.metric("Steps", s["steps"])
            c.metric("Tokens", s["tokens"])
            d.metric("Total time", f"{s['timing']['total_seconds']:.2f}s")
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Agents", len(s["agents_used"]))
            e2.metric("Delegations", s["delegations"])
            e3.metric("Events", s["events"])
            e4.metric("Notices (flagged)", f"{s['notifications']} ({s['notifications_flagged']})")
            st.caption("agents used: " + ", ".join(s["agents_used"]) + f"  |  max delegation depth {s['max_depth']}  |  max event depth {s['max_event_depth']}")
            render_flow(rows)
            st.markdown(f"**Final answer:** {s['final_answer']}  \n**Stopped because:** {s['stop_reason']}")
            st.markdown("**State diff (what changed):** " + (", ".join(f"`{x}`" for x in s["state_diff"]) or "nothing"))
            st.caption(f"Outgoing messages or shares with a flagged data flow: {s['outgoing_flow_flags']} of {s['outgoing_flows']}")
