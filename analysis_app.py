"""
analysis_app.py - Objective 2 dashboard: explore a trajectory dataset.

    streamlit run analysis_app.py

Load a JSONL produced by `python runner.py run ...` (or upload one).
"""
import glob
import json
import os

import pandas as pd
import streamlit as st

import analysis as an
from safety_rules import RULES, RULE_IDS, rules_table_markdown

st.set_page_config(page_title="SafeTask - Trajectory Analysis", layout="wide")
st.title("📊 SafeTask - Trajectory & Behaviour Analysis (Objective 2)")

# ---------------------------------------------------------------- data source
with st.sidebar:
    st.header("Dataset")
    files = sorted(glob.glob("data/*.jsonl"))
    choice = st.selectbox("Dataset in data/", files) if files else None
    upload = st.file_uploader("...or upload a JSONL", type=["jsonl"])
    include_err = st.checkbox("Include API-error runs", value=False)

if upload is not None:
    runs = an.load_runs(upload)
elif choice:
    runs = an.load_runs(choice)
else:
    st.info("Generate data first:  `python runner.py run --backend mock --runs 10 --out data/mock.jsonl`")
    st.stop()


@st.cache_data(show_spinner=False)
def prepare(_runs, key, include_errors):
    df = an.classify_behaviour(an.to_frame(_runs, include_errors))
    return df


df_all = prepare(runs, (upload.name if upload else choice, len(runs)), include_err)
if df_all.empty:
    st.warning("No usable runs in this dataset.")
    st.stop()

with st.sidebar:
    st.header("Filters")
    pick = lambda label, col: st.multiselect(label, sorted(df_all[col].unique()), default=sorted(df_all[col].unique()))
    f_task, f_user, f_temp = pick("Task", "task_key"), pick("Acting user", "acting_user"), pick("Temperature", "temperature")
    f_model = pick("Model", "model")
df = df_all[df_all.task_key.isin(f_task) & df_all.acting_user.isin(f_user)
            & df_all.temperature.isin(f_temp) & df_all.model.isin(f_model)]
if df.empty:
    st.warning("Filters exclude every run.")
    st.stop()

if set(df["backend"]) == {"mock"}:
    st.warning("This dataset comes from the **mock** agent (pipeline testing). Conclusions about real LLM behaviour need Groq runs.")
if df_all.attrs.get("n_error_runs_dropped"):
    st.caption(f"{df_all.attrs['n_error_runs_dropped']} API-error runs excluded.")

s = an.overall_summary(df)
c = st.columns(7)
c[0].metric("Runs", s["runs_analysed"])
c[1].metric("Distinct trajectories", s["distinct_trajectories"])
c[2].metric("Unsafe rate", f"{s['unsafe_rate']:.1%}")
c[3].metric("Outcome-harm rate", f"{s['outcome_harm_rate']:.1%}")
c[4].metric("Task success", "n/a" if s["task_success_rate"] is None else f"{s['task_success_rate']:.1%}")
c[5].metric("Declined", f"{s['decline_rate']:.1%}", help="No tool calls or an explicit refusal; kept out of the safe-reference sequences.")
c[6].metric("Unsafe yet successful", f"{s['unsafe_but_successful_rate']:.1%}",
            help="Runs where the task succeeded although the trajectory violated a rule.")

tabs = st.tabs(["Behaviour classes", "Variability & entropy", "Rule violations", "Tool transitions",
                "Violation taxonomy", "Run explorer", "Rules"])

with tabs[0]:
    st.markdown("**Safe vs. unsafe vs. procedurally different.** A *reference* is the most common safe sequence in each "
                "(task, user, model) cell; other safe sequences are *variants*.")
    order = an.CLASSES
    cls = df.groupby(["task_key", "behaviour_class"]).size().unstack(fill_value=0).reindex(columns=order, fill_value=0)
    st.bar_chart(cls)
    st.dataframe(an.variant_table(df), hide_index=True, use_container_width=True)

with tabs[1]:
    gm = an.group_metrics(df)
    st.markdown("Entropy is over the distribution of distinct tool sequences in each (task, user, temperature) cell; "
                "normalised entropy 1.0 = every run different.")
    by_temp = df.groupby("temperature").apply(lambda g: pd.Series({
        "distinct_ratio": g["seq"].nunique() / len(g),
        "trajectory_entropy_bits": an.shannon_bits(pd.Series(g["seq"]).value_counts()),
        "unsafe_rate": g["unsafe"].mean()}), include_groups=False)
    st.line_chart(by_temp)
    st.dataframe(gm, hide_index=True, use_container_width=True)

with tabs[2]:
    st.markdown("Share of runs violating each rule.")
    by = st.radio("Group by", ["task_key", "acting_user", "temperature"], horizontal=True)
    rv = an.rule_violation_rates(df, by)
    st.dataframe(rv.style.background_gradient(subset=RULE_IDS, cmap="Reds", vmin=0, vmax=1), use_container_width=True)

with tabs[3]:
    tt = an.transition_table(df)
    st.markdown("Tool-to-tool transitions; a high `unsafe_share` marks moves typical of unsafe runs "
                "(e.g. `START > read_file`).")
    st.dataframe(tt, hide_index=True, use_container_width=True)

with tabs[4]:
    tax = an.violation_taxonomy(df)
    st.markdown(f"**{len(tax)} distinct violation patterns.** This list seeds the Objective 3 knowledge base.")
    st.dataframe(pd.DataFrame([{k: (", ".join(v) if isinstance(v, list) else v) for k, v in t.items()
                                if k in ("pattern_id", "rule_names", "runs", "share_of_unsafe", "max_severity")}
                               for t in tax]), hide_index=True, use_container_width=True)
    st.download_button("Download taxonomy JSON", json.dumps(tax, indent=2), "violation_taxonomy.json")

with tabs[5]:
    rid = st.selectbox("Run", df["run_id"].tolist())
    r = df[df.run_id == rid].iloc[0]
    st.markdown(f"**{r['task']}** · user `{r['acting_user']}` · T={r['temperature']} · **{r['label']}** · "
                f"class `{r['behaviour_class']}` · success={r['task_success']} · risk={r['risk_score']}")
    for h in r["steps"]:
        icon = "🟢" if h["tool"] in ("authenticate", "check_permission", "check_recipient", "audit_log") else \
               "🔴" if h["tool"] in ("read_file", "delete_file", "query_database", "send_email") else "🟣"
        bad = [v["detail"] for v in r["violations"] if v["step"] == h["step"]]
        with st.container(border=True):
            st.markdown(f"{icon} **Step {h['step']}: `{h['tool']}`** {h['args']} → {h['result']}")
            for b in bad:
                st.caption(f"⚠️ {b}")
    st.markdown(f"**Final answer:** {r['final_answer']}")

with tabs[6]:
    st.markdown(rules_table_markdown())
