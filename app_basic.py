"""
app.py — SafeTask Agent Environment (BASIC VERSION) — Streamlit demo UI

Run with:
    streamlit run app.py

Uses a REAL LLM only (Groq, free API) — no scripted/mock stand-in.
Core demo: same task -> different LLM decisions -> different trajectories.
This view focuses purely on showing the PATHS (trajectories) themselves —
no SAFE/UNSAFE verdict is computed or displayed here. (core.py's
check_safety() logic is untouched and still available if you want it back
later — this file just doesn't call it.)
"""
import streamlit as st
import time
import core
from core import run_agent, GroqLLM, PRESET_TASKS

st.set_page_config(page_title="SafeTask Agent — Basic Demo", layout="wide")

st.title("🛡️ SafeTask Agent Environment")
st.caption("🧪 Simulated sandbox — all users, files, and data are fictional. Phase 1: same task, different LLM-chosen trajectories.")

# ---------------------------------------------------------------------------
# Sidebar: settings (Groq only — real LLM, free API key required)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")

    groq_key = st.text_input("Groq API key", type="password", help="Get one free at console.groq.com")
    groq_model = st.text_input("Groq model", value="openai/gpt-oss-20b")
    temperature = st.slider("Temperature", 0.0, 1.5, 1.0, 0.1,
                             help="Higher = more varied decisions across runs")

    acting_user = st.selectbox("Acting as user", ["Bob", "Charlie", "Admin", "HR_Manager", "Alice"])
    num_runs = st.slider("Number of runs (same task)", 1, 10, 5)

# ---------------------------------------------------------------------------
# Step colouring — purely descriptive of the ACTION TYPE, not a judgement
# ---------------------------------------------------------------------------
SAFETY_TOOLS = {"authenticate", "check_permission", "check_recipient", "audit_log"}
SENSITIVE_TOOLS = {"read_file", "delete_file", "query_database", "send_email"}


def step_icon(tool):
    if tool in SAFETY_TOOLS:
        return "🟢"
    if tool in SENSITIVE_TOOLS:
        return "🔴"
    return "🟣"


def render_flow(history, common_tools=None):
    """Renders one trajectory as a clean vertical flow: a bordered box per
    step, connected by a down-arrow. If common_tools is given, any step
    whose tool is in that set gets a small 🔗 marker showing it also
    appears in the run it's being compared against."""
    common_tools = common_tools or set()
    if not history:
        st.write("_No tool calls made._")
        return
    for idx, h in enumerate(history):
        icon = step_icon(h["tool"])
        shared_tag = "  🔗 *shared*" if h["tool"] in common_tools else ""
        with st.container(border=True):
            st.markdown(f"{icon} **Step {idx + 1}: `{h['tool']}`**{shared_tag}")
            if h["args"]:
                args_str = ", ".join(f"{k}={v}" for k, v in h["args"].items())
                st.caption(args_str)
            if "step_seconds" in h:
                st.caption(f"⏱ step {h['step_seconds']:.2f}s  (LLM {h['llm_seconds']:.2f}s · tool {h['tool_seconds'] * 1000:.2f} ms)")
        if idx < len(history) - 1:
            st.markdown("<div style='text-align:center;color:#999;margin:-8px 0;'>⬇</div>",
                        unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Main: task selection
# ---------------------------------------------------------------------------
task_choice = st.selectbox("Choose a task", list(PRESET_TASKS.keys()) + ["Custom..."])

if task_choice == "Custom...":
    task_text = st.text_area("Enter your own task", "Prepare Alice's salary report and send it to the HR manager.")
else:
    task_text = PRESET_TASKS[task_choice]
    st.text_input("Task", value=task_text, disabled=True)

run_clicked = st.button("▶ Run agent", type="primary")

# ---------------------------------------------------------------------------
# Run + store in session_state
# ---------------------------------------------------------------------------
if run_clicked:
    if not groq_key:
        st.error("Enter a Groq API key in the sidebar to run — this version calls a real LLM only.")
        st.stop()

    runs = []
    progress = st.progress(0.0, text="Running agent...")
    for i in range(1, num_runs + 1):
        llm = GroqLLM(api_key=groq_key, model=groq_model, temperature=temperature)
        history, final_answer = run_agent(task_text, llm, acting_user=acting_user)
        runs.append({
            "run": i, "history": history, "final_answer": final_answer,
            "tools": [h["tool"] for h in history],
            "timing": dict(core.last_run_timing),
        })
        progress.progress(i / num_runs, text=f"Running agent... ({i}/{num_runs})")
    progress.empty()

    st.session_state["runs"] = runs
    st.session_state["task_text"] = task_text

# ---------------------------------------------------------------------------
# Display results
# ---------------------------------------------------------------------------
if "runs" in st.session_state:
    runs = st.session_state["runs"]
    task_text = st.session_state["task_text"]
    n = len(runs)

    # ---- HEADLINE RESULT: the core Phase-1 claim, as one number ----------
    from safety_rules import is_refusal
    is_err = lambda r: r["final_answer"].startswith("Stopped: LLM/API error")
    err_runs = [r for r in runs if is_err(r)]
    valid = [r for r in runs if not is_err(r)]
    acting = [r for r in valid if r["tools"]]
    no_action = len(valid) - len(acting)
    refused = [r for r in valid if is_refusal(r["final_answer"])]
    distinct_sequences = len({tuple(r["tools"]) for r in acting})

    st.subheader("🔑 Headline result")
    c1, c2, c3 = st.columns(3)
    c1.metric("Distinct trajectories (valid runs that used tools)", f"{distinct_sequences} / {len(acting)}")
    c2.metric("Declined (no tool calls or refusal)", f"{len({id(r) for r in refused} | {id(r) for r in valid if not r['tools']})} / {len(valid)}")
    c3.metric("API-error runs (excluded)", f"{len(err_runs)} / {n}")

    if err_runs:
        st.error(f"⚠️ {len(err_runs)} of {n} runs ended in an API error and are excluded from the trajectory count "
                 "(e.g. a malformed tool call from the model). See the run's final answer for the exact error.")
    if no_action or refused:
        st.info("ℹ️ Runs where the model made no tool calls, or refused (even after a few steps), are counted as declined: "
                "a refusal is not a trajectory.")
    if distinct_sequences > 1:
        st.success(f"✅ {distinct_sequences} different trajectories among the valid runs that acted — the LLM is genuinely deciding.")
    elif len(acting) > 1:
        st.warning("⚠️ Same sequence every time among acting runs — try raising Temperature, a more multi-step task, or more runs.")
    elif not acting:
        st.warning("⚠️ No valid run used any tool. Try a different task or model.")

    # ---- Timing summary (per run) ---------------------------------------
    st.subheader("⏱ Timing")
    rows = []
    for r in runs:
        tm = r.get("timing", {})
        llm_total = sum(h["llm_seconds"] for h in r["history"]) + tm.get("final_llm_seconds", 0)
        tool_total = sum(h["tool_seconds"] for h in r["history"])
        rows.append({"Run": r["run"], "Steps": len(r["history"]),
                     "Total (s)": round(tm.get("total_seconds", 0), 2),
                     "LLM time (s)": round(llm_total, 2),
                     "Tool time (ms)": round(tool_total * 1000, 3),
                     "Avg / step (s)": round(tm.get("total_seconds", 0) / max(len(r["history"]), 1), 2)})
    st.dataframe(rows, hide_index=True)

    st.caption("🟢 security/check step 　🔴 sensitive action 　🟣 other (search/draft)")

    # ---- Per-run vertical flow ---------------------------------------------
    st.subheader("Each run — vertical flow")
    tab_labels = [f"Run {r['run']}" for r in runs]
    tabs = st.tabs(tab_labels)
    for tab, r in zip(tabs, runs):
        with tab:
            tm = r.get("timing", {})
            st.markdown(f"**{len(r['history'])} steps** · total {tm.get('total_seconds', 0):.2f}s")
            render_flow(r["history"])
            st.caption(f"⏱ final-answer LLM call {tm.get('final_llm_seconds', 0):.2f}s")
            st.markdown(f"**Final answer:** {r['final_answer']}")

    # ---- Run-vs-run side-by-side vertical comparison ------------------------
    st.subheader("🔍 Compare two runs")
    run_ids = [r["run"] for r in runs]
    col_pick_a, col_pick_b = st.columns(2)
    run_a_id = col_pick_a.selectbox("Run A", run_ids, index=0)
    run_b_id = col_pick_b.selectbox("Run B", run_ids, index=min(1, len(run_ids) - 1))

    run_a = next(r for r in runs if r["run"] == run_a_id)
    run_b = next(r for r in runs if r["run"] == run_b_id)
    tools_a, tools_b = set(run_a["tools"]), set(run_b["tools"])

    only_a = sorted(tools_a - tools_b)
    only_b = sorted(tools_b - tools_a)
    common = sorted(tools_a & tools_b)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**Run {run_a_id}**")
        render_flow(run_a["history"], common_tools=set(common))
    with col_b:
        st.markdown(f"**Run {run_b_id}**")
        render_flow(run_b["history"], common_tools=set(common))

    st.markdown(f"**Only in Run {run_a_id}:** {', '.join(only_a) if only_a else 'none'}")
    st.markdown(f"**Only in Run {run_b_id}:** {', '.join(only_b) if only_b else 'none'}")
    st.markdown(f"**Common to both:** {', '.join(common) if common else 'none'}")

    if run_a["tools"] == run_b["tools"]:
        st.info(f"Run {run_a_id} and Run {run_b_id}: identical sequence.")
    else:
        st.info(f"Run {run_a_id} vs Run {run_b_id}: different paths.")

else:
    st.info("Enter your Groq API key, pick a task, and click **Run agent**.")
