"""
app_analysis.py - Objective 2 analysis UI (Stage 4).

    python stage4.py all          # first, to create the files in reports/
    streamlit run app_analysis.py

Tabs: Overview, Prompt strength, Violations, Risk patterns, Procedural variety, Run explorer, Gold validation, Critic.
"""
import json
import os

import pandas as pd
import streamlit as st

from env.state import ROOT

R = ROOT / "reports"
st.set_page_config(page_title="SafeTask analysis", layout="wide")
st.title("SafeTask: Objective 2 analysis")
if not (R / "stage4_summary.json").exists():
    st.error("No Stage 4 files yet. Run `python stage4.py all` in the project folder, then reload.")
    st.stop()

summ = json.load(open(R / "stage4_summary.json"))
labels = pd.read_json(R / "labels.jsonl", lines=True)
patterns = json.load(open(R / "risk_patterns.json"))
proc = json.load(open(R / "procedural.json"))
rules = json.load(open(R / "rules.json"))
st.caption(f"{summ['runs']['total']} runs {summ['runs']['by_source']}. " + summ["runs"]["note"])

with st.sidebar:
    srcs = st.multiselect("Source", sorted(labels["source"].unique()), default=list(sorted(labels["source"].unique())))
    splits = st.multiselect("Split", sorted(labels["split"].unique()), default=list(sorted(labels["split"].unique())))
df = labels[labels["source"].isin(srcs) & labels["split"].isin(splits)]
tabs = st.tabs(["Overview", "Prompt strength", "Violations", "Risk patterns", "Procedural variety", "Run explorer", "Gold validation", "Critic"])

with tabs[0]:
    a, b, c, d = st.columns(4)
    a.metric("Runs", len(df))
    b.metric("Unsafe rate", f"{df['label'].str.startswith('UNSAFE').mean():.1%}")
    c.metric("Unsafe but task succeeded", f"{df['unsafe_but_successful'].mean():.1%}")
    d.metric("Task success", f"{df['task_success'].mean():.1%}")
    st.bar_chart(df["label"].value_counts())
    st.markdown("**By category**")
    st.dataframe(pd.crosstab(df["category"], df["label"]), use_container_width=True)
    st.markdown("**By split**")
    st.dataframe(pd.crosstab(df["split"], df["label"]), use_container_width=True)

with tabs[1]:
    t = df.groupby("prompt_strength").agg(runs=("run_id", "count"), unsafe=("label", lambda s: s.str.startswith("UNSAFE").mean()),
                                         outcome=("label", lambda s: (s == "UNSAFE_OUTCOME").mean()),
                                         declined=("label", lambda s: (s == "DECLINED").mean()), task_success=("task_success", "mean"))
    st.dataframe(t.style.format({c: "{:.1%}" for c in ["unsafe", "outcome", "declined", "task_success"]}), use_container_width=True)
    st.bar_chart(t[["unsafe", "outcome", "declined"]])
    st.caption("This is the prompt-only baseline: later policies must lower the unsafe rate more than a stricter prompt does, without blocking safe work.")

with tabs[2]:
    rc = pd.Series([r for rs in df["violated_rules"] for r in rs]).value_counts()
    st.bar_chart(rc)
    st.dataframe(pd.DataFrame([{"rule": k, "name": v["name"], "type": v["kind"], "severity": v["severity"], "meaning": v["text"], "LTL form": v["ltl"]}
                               for k, v in rules.items()]), use_container_width=True)
    st.markdown("Violations per agent: " + json.dumps(summ["violations_per_agent"]))
    st.markdown("Runs with a violation per team: " + json.dumps(summ["runs_with_violation_per_team"]))

with tabs[3]:
    st.write("Coverage = share of unsafe runs whose abstract steps match a pattern mined from `known` only.")
    st.json(summ["risk_patterns"]["coverage_by_split"])
    st.dataframe(pd.DataFrame([{**p, "sequence": " > ".join(p["sequence"]), "rules": ",".join(p["rules"])} for p in patterns]), use_container_width=True)

with tabs[4]:
    sc = pd.DataFrame(proc["scenarios"]).T
    st.dataframe(sc[["runs", "distinct_paths", "clusters", "entropy_bits", "mean_edit_distance_to_reference"]], use_container_width=True)
    pick = st.selectbox("Scenario", list(proc["scenarios"]))
    st.json(proc["scenarios"][pick])

with tabs[5]:
    sid = st.selectbox("Scenario ", sorted(df["scenario_id"].unique()))
    sub = df[df["scenario_id"] == sid]
    rid = st.selectbox("Run", sub["run_id"], format_func=lambda r: f"{r[:8]}  {sub[sub.run_id == r].label.iloc[0]}  {sub[sub.run_id == r].prompt_strength.iloc[0]}")
    lab = sub[sub.run_id == rid].iloc[0]
    st.markdown(f"### {lab['label']}  (risk {lab['risk_score']}, severity {lab['severity']}, first violation step {lab['first_violation_step']})")
    for x in lab["reasons"]:
        st.write("- " + x)
    for x in lab.get("warnings") or []:
        st.caption(f"advisory {x['rule']}: {x['detail']}")
    rows_files = [p for p in (ROOT / "data" / "runs").glob("*.jsonl") if not p.name.endswith("_summary.jsonl")]
    shown = False
    for p in rows_files:
        for line in open(p, encoding="utf-8"):
            r = json.loads(line)
            if r["run_id"] == rid:
                shown = True
                mark = "  :red[first violation]" if r["step"] == lab["first_violation_step"] else ""
                st.markdown(f"**{r['step']}. `{r['tool']}`** :blue[{r['agent']}]{mark}")
                st.caption(f"chain {' > '.join(r['chain'])} | args {json.dumps(r['args'])[:160]} | result {json.dumps(r['result'])[:160]}")
        if shown:
            break

with tabs[6]:
    g = R / "gold_validation.json"
    if g.exists():
        st.json(json.load(open(g)))
    else:
        st.info("Not validated yet. Hand-label reports/gold_sample.csv (see reports/gold_guide.md), then run "
                "`python stage4.py gold-validate --a reports/gold_sample.csv [--b second_person.csv]`.")

with tabs[7]:
    cp = R / "critic_proposals.json"
    if cp.exists():
        data = json.load(open(cp))
        st.write(f"{data['candidates']} runs looked suspicious but passed every rule.")
        st.json(data["proposals"])
    st.caption("A human accepts a proposal with `python stage4.py critic-accept X01`, then reruns `python stage4.py all`.")
