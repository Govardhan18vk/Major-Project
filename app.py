"""SafeTask unified Streamlit dashboard.

Run with:
    streamlit run app.py
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agents.llm import STRENGTHS
from agents.loop import Budgets
from env.state import ROOT
from env.tools import menu_names
from objective2.labeler import label_run
from runner import load_scenarios, run_one

load_dotenv()

st.set_page_config(page_title="SafeTask", layout="wide")

RUN_FILE = ROOT / "data" / "runs" / "streamlit_groq.jsonl"
SUMMARY_FILE = ROOT / "data" / "runs" / "streamlit_groq_summary.jsonl"
REPORTS = ROOT / "reports"


def save_run(rows, summary):
    """Append one completed UI run to the durable Groq dataset."""
    RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RUN_FILE.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
    with SUMMARY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, default=str) + "\n")


def rebuild_reports():
    """Refresh Objective 2 reports from runs collected in this dashboard."""
    import stage4

    stage4.cmd_all(SimpleNamespace(data=[str(RUN_FILE)], min_support=2))


def render_flow(rows):
    if not rows:
        st.write("_No tool calls made._")
        return
    for index, row in enumerate(rows):
        flags = []
        if row.get("flow") and row["flow"]["ok"] is False:
            flags.append("flow flagged")
        if row.get("untrusted"):
            flags.append("untrusted content")
        if row.get("fault"):
            flags.append(f"fault: {row['fault']}")
        if row.get("tags_read"):
            flags.append("read: " + "/".join(row["tags_read"]))
        with st.container(border=True):
            lane = " > ".join(row.get("chain") or [row["agent"]])
            st.markdown(
                f"**{row['step']}. `{row['tool']}`** :blue[{row['agent']}]"
                + (f" (spawned by step {row['parent_step']})" if row.get("parent_step") else "")
                + (f"  \n{' | '.join(flags)}" if flags else "")
            )
            st.caption(f"chain: {lane} | intent: {row.get('intent', '')}")
            st.code(json.dumps(row["args"], ensure_ascii=False), language="json")
            st.caption(f"-> {json.dumps(row['result'], ensure_ascii=False)[:260]}")
            st.caption(
                f"{row['llm_seconds']:.2f}s LLM | "
                f"{row['tool_seconds'] * 1000:.2f} ms tool | sim time {row['sim_time']}"
            )
            if row.get("flow") and row["flow"]["fine_flows"]:
                st.caption(
                    "data flowed: "
                    + ", ".join(
                        f"{flow['resource']} [{flow['tag']}] ok={flow['ok']}"
                        for flow in row["flow"]["fine_flows"]
                    )
                )
        if index < len(rows) - 1:
            st.markdown("<div style='text-align:center'>&#8595;</div>", unsafe_allow_html=True)


def render_run_agents():
    st.title("SafeTask: Run agents")
    st.caption(
        "Run a fictional ShopSphere scenario with the mock backend or the real Groq backend. "
        "Groq runs are saved automatically."
    )
    generated = ROOT / "data" / "scenarios_generated.json"
    with st.sidebar:
        use_generated = st.checkbox(
            "Attacker-generated scenarios",
            value=False,
            disabled=not generated.exists(),
        )
    scenarios = load_scenarios(generated if use_generated else None)
    by_id = {scenario["id"]: scenario for scenario in scenarios}

    with st.sidebar:
        st.header("Run settings")
        backend = st.radio(
            "Backend",
            ["mock", "groq"],
            index=1 if os.environ.get("GROQ_API_KEY") else 0,
            help="mock is offline; groq uses the real API.",
        )
        api_key = None
        model = "openai/gpt-oss-20b"
        if backend == "groq":
            api_key = st.text_input(
                "Groq API key",
                type="password",
                value=os.environ.get("GROQ_API_KEY", ""),
            )
            model = st.text_input("Groq model", value=model)
        category = st.selectbox("Category", ["all"] + sorted({s["category"] for s in scenarios}))
        pool = [s for s in scenarios if category == "all" or s["category"] == category]
        sid = st.selectbox(
            "Scenario",
            [s["id"] for s in pool],
            format_func=lambda value: f"{value}  {by_id[value]['name']}",
        )
        prompt = st.selectbox(
            "Safety prompt",
            list(STRENGTHS),
            format_func=lambda value: {
                "p0": "p0 - none",
                "p1": "p1 - mild hint",
                "p2": "p2 - strict rules",
            }[value],
        )
        temperature = st.slider("Temperature", 0.0, 1.5, 1.0, 0.1)
        n_runs = st.slider("Runs", 1, 8, 3)
        token_budget = st.number_input("Token budget per run", 5000, 200000, 60000, 5000)

    scenario = by_id[sid]
    st.subheader(f"{scenario['id']}: {scenario['name']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Acting user", scenario["acting_user"])
    c2.metric("Category", scenario["category"])
    c3.metric("Tempting", "yes" if scenario.get("tempting") else "no")
    c4.metric("Entry agent", scenario.get("agent", "assistant"))
    st.info(scenario["task"])
    with st.expander("Tools this agent can see"):
        st.write(", ".join(menu_names(scenario["menu"])))

    if st.button("Run", type="primary"):
        if backend == "groq" and not api_key:
            st.error("Enter a Groq API key.")
            st.stop()
        results = []
        for index in range(n_runs):
            with st.spinner(f"Run {index + 1} of {n_runs}..."):
                rows, summary = run_one(
                    scenario,
                    backend,
                    model,
                    temperature,
                    prompt,
                    1000 + index,
                    api_key,
                    Budgets(int(token_budget), 60),
                )
            results.append((rows, summary))
            if backend == "groq" and not summary["error"]:
                save_run(rows, summary)
        if backend == "groq" and any(not summary["error"] for _, summary in results):
            rebuild_reports()
            st.success(f"Saved completed Groq runs to {RUN_FILE}")
        st.session_state["results"] = results
        st.session_state["last_scenario_id"] = scenario["id"]

    results = st.session_state.get("results")
    if not results:
        return
    st.divider()
    st.markdown(f"**{len(results)} runs, {len({tuple(s['tools']) for _, s in results})} distinct trajectories.**")
    tabs = st.tabs([f"Run {index + 1}" for index in range(len(results))])
    for tab, (rows, summary) in zip(tabs, results):
        with tab:
            label = label_run(rows, summary, scenario)
            if label["label"].startswith("UNSAFE"):
                st.error(f"Safety result: **{label['label']}**")
            elif label["label"] in ("SAFE", "SAFE_VARIANT"):
                st.success(f"Safety result: **{label['label']}**")
            else:
                st.warning(f"Safety result: **{label['label']}**")
            st.caption(
                f"Risk score: {label['risk_score']} | Severity: {label['severity']} | "
                f"First violation step: {label['first_violation_step'] or 'none'}"
            )
            if label["reasons"]:
                with st.expander("Why this result?"):
                    for reason in label["reasons"]:
                        st.write(f"- {reason}")
            a, b, c, d = st.columns(4)
            a.metric("Task success", "yes" if summary["task_success"] else "no")
            b.metric("Steps", summary["steps"])
            c.metric("Tokens", summary["tokens"])
            d.metric("Total time", f"{summary['timing']['total_seconds']:.2f}s")
            render_flow(rows)
            st.markdown(f"**Final answer:** {summary['final_answer']}")
            st.markdown(
                "**State diff:** "
                + (", ".join(f"`{item}`" for item in summary["state_diff"]) or "nothing")
            )


def render_objective2():
    st.title("Objective 2: Judge")
    summary_path = REPORTS / "stage4_summary.json"
    if not summary_path.exists():
        st.info("Run a Groq scenario from the Run agents page first.")
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    labels = pd.read_json(REPORTS / "labels.jsonl", lines=True)
    patterns = json.loads((REPORTS / "risk_patterns.json").read_text(encoding="utf-8"))
    procedural = json.loads((REPORTS / "procedural.json").read_text(encoding="utf-8"))
    rules = json.loads((REPORTS / "rules.json").read_text(encoding="utf-8"))
    st.caption(f"{summary['runs']['total']} saved runs: {summary['runs']['by_source']}")

    page = st.sidebar.radio(
        "Objective 2 section",
        ["Overview", "Prompt strength", "Violations", "Risk patterns", "Procedural", "Run explorer"],
    )
    if page == "Overview":
        a, b, c, d = st.columns(4)
        a.metric("Runs", len(labels))
        b.metric("Unsafe rate", f"{labels['label'].str.startswith('UNSAFE').mean():.1%}")
        c.metric("Unsafe but successful", f"{labels['unsafe_but_successful'].mean():.1%}")
        d.metric("Task success", f"{labels['task_success'].mean():.1%}")
        st.bar_chart(labels["label"].value_counts())
        st.dataframe(pd.crosstab(labels["category"], labels["label"]), use_container_width=True)
    elif page == "Prompt strength":
        table = labels.groupby("prompt_strength").agg(
            runs=("run_id", "count"),
            unsafe=("label", lambda values: values.str.startswith("UNSAFE").mean()),
            outcome=("label", lambda values: (values == "UNSAFE_OUTCOME").mean()),
            declined=("label", lambda values: (values == "DECLINED").mean()),
            task_success=("task_success", "mean"),
        )
        st.dataframe(table.style.format("{:.1%}"), use_container_width=True)
        st.bar_chart(table[["unsafe", "outcome", "declined"]])
    elif page == "Violations":
        violations = pd.Series([rule for ruleset in labels["violated_rules"] for rule in ruleset]).value_counts()
        st.bar_chart(violations)
        st.dataframe(
            pd.DataFrame(
                [
                    {"rule": key, "name": value["name"], "type": value["kind"], "severity": value["severity"], "meaning": value["text"]}
                    for key, value in rules.items()
                ]
            ),
            use_container_width=True,
        )
    elif page == "Risk patterns":
        st.json(summary["risk_patterns"]["coverage_by_split"])
        st.dataframe(
            pd.DataFrame(
                [{**pattern, "sequence": " > ".join(pattern["sequence"]), "rules": ",".join(pattern["rules"])} for pattern in patterns]
            ),
            use_container_width=True,
        )
    elif page == "Procedural":
        table = pd.DataFrame(procedural["scenarios"]).T
        st.dataframe(
            table[["runs", "distinct_paths", "clusters", "entropy_bits", "mean_edit_distance_to_reference"]],
            use_container_width=True,
        )
        scenario_id = st.selectbox("Scenario", list(procedural["scenarios"]))
        st.json(procedural["scenarios"][scenario_id])
    else:
        scenario_id = st.selectbox("Scenario", sorted(labels["scenario_id"].unique()))
        subset = labels[labels["scenario_id"] == scenario_id]
        run_id = st.selectbox("Run", subset["run_id"])
        selected = subset[subset["run_id"] == run_id].iloc[0]
        st.subheader(
            f"{selected['label']} | risk {selected['risk_score']} | "
            f"first violation {selected['first_violation_step'] or 'none'}"
        )
        st.write(selected["reasons"])


def main():
    st.sidebar.title("SafeTask")
    page = st.sidebar.radio(
        "Pages",
        ["Home", "1. Run agents", "2. Objective 2: judge", "3. Objective 3: policies"],
    )
    if page == "Home":
        st.title("SafeTask")
        st.write("Run Groq agents, automatically save trajectories, and inspect Objective 2 results.")
        st.info("Start with **1. Run agents** in the sidebar.")
    elif page == "1. Run agents":
        render_run_agents()
    elif page == "2. Objective 2: judge":
        render_objective2()
    else:
        st.title("Objective 3: Policies")
        st.info("Policy generation is not implemented yet.")


main()
