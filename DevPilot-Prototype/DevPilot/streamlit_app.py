#!/usr/bin/env python3
"""DevPilot -- Streamlit front end.

Full feature-parity port of webui.py's single-page app, rebuilt on
Streamlit so it can run on a host that keeps a real persistent Python
process (Streamlit Community Cloud, or any other Streamlit-capable host)
instead of a stateless serverless platform like Vercel, which can't run
DevPilot's architecture at all.

All the actual analysis logic is untouched -- this file only re-presents
the same `devpilot` package (orchestrator, subagents, blast radius,
pre-mortem, executive intelligence, history, dependency map, ask, fix
agent) through Streamlit's widgets instead of hand-written HTML/JS.

One honest behavioral difference from webui.py: Streamlit reruns the
script on each interaction rather than holding a live socket open, so the
agent log is shown in full once a run finishes (inside a spinner while it
runs) rather than streaming line-by-line in real time.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devpilot.orchestrator import run_all, _blast_radius_svg, _dependency_map_svg, _sparkline_svg
from devpilot.agents import fix_agent
from devpilot import ask, watsonx_client

DEMO_VIDEO_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_video.mp4"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Submission-Docs", "DevPilot_Demo_Video.mp4"),
]
BUNDLED_DEMO_PROJECT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "TaskFlow-API")

# ------------------------------------------------------------------ theme
st.set_page_config(page_title="DevPilot", page_icon=":rocket:", layout="wide")

PASTEL_CSS = """
<style>
:root {
  --pink:#FFD6EA; --pink-deep:#FF8FC7; --green:#CFF5DE; --green-deep:#57C98B;
  --purple:#E7DBFF; --purple-deep:#A78BFA; --yellow:#FFF3B0; --yellow-deep:#F2C94C;
  --bg:#FBF7FF; --card:#FFFFFF; --text:#3B3350; --muted:#8C82A6; --border:#F0E6FA;
}
.stApp { background: var(--bg); }
h1, h2, h3 { color: var(--text) !important; }
.dp-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 16px;
  padding: 18px 20px; margin-bottom: 14px;
}
.dp-badge {
  display: inline-block; padding: 4px 14px; border-radius: 999px; font-weight: 700;
  font-size: 0.85rem; color: #fff; margin-right: 6px;
}
.dp-muted { color: var(--muted); font-size: 0.9rem; }
.dp-qline { margin: 4px 0; }
</style>
"""
st.markdown(PASTEL_CSS, unsafe_allow_html=True)


def badge(text, hexcolor):
    return f'<span class="dp-badge" style="background:#{hexcolor}">{text}</span>'


def sev_color(sev):
    return {"high": "FF8FC7", "medium": "F2C94C", "low": "A78BFA"}.get(sev, "8C82A6")


def card(inner_html):
    st.markdown(f'<div class="dp-card">{inner_html}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------ session state
for key, default in [
    ("report", None), ("log", []), ("project_path", None), ("cloned", False),
    ("identifier", None), ("running", False), ("fix_result", None),
    ("ask_answers", []),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def _cleanup_clone():
    if st.session_state.cloned and st.session_state.project_path and os.path.isdir(st.session_state.project_path):
        shutil.rmtree(st.session_state.project_path, ignore_errors=True)
    st.session_state.cloned = False
    st.session_state.project_path = None


def run_devpilot(source_type, value, pr_diff):
    log = []

    def progress(msg):
        log.append(msg)

    if source_type == "github":
        workdir = tempfile.mkdtemp(prefix="devpilot_st_clone_")
        subprocess.run(["git", "clone", "--depth", "1", value, workdir], check=True, capture_output=True)
        project_path = workdir
        cloned = True
        identifier = value
    else:
        project_path = value
        cloned = False
        identifier = value

    report = run_all(
        project_path,
        pr_diff_path=(pr_diff or None),
        progress=progress,
        project_identifier=identifier,
    )
    return report, log, project_path, cloned, identifier


# ------------------------------------------------------------------ header
st.title("DevPilot")
st.caption("An AI developer-workflow copilot -- IBM Bob 2.0 hackathon prototype. Six subagents, run in parallel, reduced into one report.")

with st.form("run_form"):
    col1, col2 = st.columns([1, 2])
    with col1:
        source_type = st.radio("Source", ["GitHub URL", "Local folder path (within this deployment)"], horizontal=False)
    with col2:
        if source_type == "GitHub URL":
            value = st.text_input("Repository URL", placeholder="https://github.com/owner/repo.git")
        else:
            value = st.text_input(
                "Local path",
                value=os.path.relpath(BUNDLED_DEMO_PROJECT, os.path.dirname(os.path.abspath(__file__))),
                help="On a hosted deployment there's no access to your own machine's filesystem -- "
                     "this points at the TaskFlow-API demo project bundled in this same repo.",
            )
        pr_diff = st.text_input("Optional: path to a PR/diff file to review", placeholder="(leave blank to skip)")
    submitted = st.form_submit_button("Run DevPilot", type="primary")

if submitted and value:
    _cleanup_clone()
    st_type = "github" if source_type == "GitHub URL" else "local"
    resolved_value = value if st_type == "github" else os.path.join(os.path.dirname(os.path.abspath(__file__)), value)
    with st.spinner("Dispatching 6 subagents in parallel: onboarding, code review, testing, release readiness, modernization, auto-fix ..."):
        try:
            report, log, project_path, cloned, identifier = run_devpilot(st_type, resolved_value, pr_diff)
            st.session_state.report = report
            st.session_state.log = log
            st.session_state.project_path = project_path
            st.session_state.cloned = cloned
            st.session_state.identifier = identifier
            st.session_state.fix_result = None
        except Exception as e:
            st.error(f"Run failed: {e}")

if st.session_state.report:
    with st.expander("Agent log (full run, shown at once -- see the note at the top of this file for why)"):
        for line in st.session_state.log:
            st.text(line)
    if st.session_state.cloned:
        if st.button("Discard cloned repo"):
            _cleanup_clone()
            st.rerun()

if not watsonx_client.available():
    st.caption(":gray[watsonx.ai not configured -- Ask DevPilot and narrative text use deterministic templates.]")

st.divider()

# --------------------------------------------------------------- tabs
if not st.session_state.report:
    st.info("Paste a GitHub URL or use the bundled local demo path above, then click **Run DevPilot**.")
    st.stop()

report = st.session_state.report
r = report["results"]
health = report["health"]

tab_names = [
    "Full Report", "New Developer", "Reviewer", "Tester", "Release Manager",
    "Auto-Fix", "Blast Radius", "Pre-Mortem", "Executive Intelligence",
    "History", "Dependency Map", "Ask DevPilot", "Demo Video",
]
tabs = st.tabs(tab_names)

# ---------------------------------------------------------- Full Report
with tabs[0]:
    c1, c2 = st.columns([1, 2])
    with c1:
        st.markdown(
            f'<div class="dp-card" style="text-align:center;">'
            f'<div style="font-size:3rem;font-weight:800;color:#{health["color"]}">{health["score"]}</div>'
            f'<div>{badge(health["label"], health["color"])}</div>'
            f'</div>', unsafe_allow_html=True,
        )
        st.metric("Wall-clock (parallel)", f'{report["wall_clock_seconds"]}s')
        st.metric("Sequential-equivalent", f'{report["sequential_equivalent_seconds"]}s')
    with c2:
        st.subheader("Executive summary")
        st.write(report["executive_summary"]["summary"])
        st.caption(f'source: {report["executive_summary"]["source"]}')
        if health["deductions"]:
            st.write("**Score deductions**")
            for d in health["deductions"]:
                st.write(f'- {d["reason"]}: -{d["points"]}')
    st.write(f'**{len(report.get("confirmed_findings", []))}** finding(s) confirmed by two independent subagents (static flag + failing generated test).')
    st.write(f'Subagents dispatched in parallel: {", ".join(report["agents_run_in_parallel"])}')

# ------------------------------------------------------------- New Developer
with tabs[1]:
    ob = r["onboarding"]
    st.subheader(f'{ob["modules_scanned"]} modules mapped, {len(ob["starter_tasks"])} starter tasks ready')
    st.write(f'Total lines of code scanned: {ob["total_loc"]}')
    colA, colB = st.columns(2)
    with colA:
        st.write("**Setup steps**")
        for s in ob["setup_steps"]:
            st.write(f"- {s}")
        st.write("**Starter tasks**")
        for s in ob["starter_tasks"]:
            st.write(f"- {s}")
    with colB:
        st.write("**Architecture map** (first 25 modules)")
        st.dataframe(ob["architecture_map"], use_container_width=True, hide_index=True)
    if ob["undocumented_modules"]:
        st.warning(f'{len(ob["undocumented_modules"])} undocumented module(s): ' + ", ".join(ob["undocumented_modules"][:10]))

# ------------------------------------------------------------- Reviewer
with tabs[2]:
    rev = r["code_review"]
    st.subheader(rev["summary"])
    c1, c2, c3 = st.columns(3)
    c1.metric("High", rev["counts"]["high"])
    c2.metric("Medium", rev["counts"]["medium"])
    c3.metric("Low", rev["counts"]["low"])
    st.caption(f'{rev["rules_checked"]} rules checked this run.')
    confirmed_keys = {(c["file"], c.get("function"), c["rule"]) for c in report.get("confirmed_findings", [])}
    for f in rev["findings"]:
        is_confirmed = (f["file"], f.get("function"), f["rule"]) in confirmed_keys
        tag = badge("CONFIRMED", "57C98B") if is_confirmed else badge("flagged", "8C82A6")
        card(
            f'{badge(f["severity"].upper(), sev_color(f["severity"]))} {tag} '
            f'<b>{f["file"]}</b>{" :: " + f["function"] if f.get("function") else ""}<br>'
            f'<span class="dp-muted">{f["rule"]}</span><br>{f["message"]}'
        )
    if rev.get("pr_diff_reviewed"):
        st.write("**PR diff reviewed:**", rev["pr_diff_reviewed"])

# ------------------------------------------------------------- Tester
with tabs[3]:
    t = r["testing"]
    st.subheader(f'{t["headline_coverage_pct"]}% {t["headline_label"]}')
    if t.get("uncovered_endpoints"):
        st.write("**Uncovered endpoints**")
        for e in t["uncovered_endpoints"]:
            st.write(f"- {e}")
    else:
        st.write("No uncovered endpoints detected.")
    if t.get("function_coverage"):
        st.write(f'Function-level coverage: {t["function_coverage"].get("pct")}%')
    if t.get("generated_test_file"):
        st.write(f'Generated test stub written to: `{t["generated_test_file"]}`')
    gtr = report.get("generated_test_run")
    if gtr:
        if gtr.get("ran"):
            status = "✅ passed" if gtr.get("passed") else "❌ failed"
            st.write(f'Generated tests run against the live app: {status}')
            if gtr.get("output_tail"):
                st.code(gtr["output_tail"])
        else:
            st.caption(f'Generated tests not run: {gtr.get("reason")}')

# ------------------------------------------------------------- Release Manager
with tabs[4]:
    rel = r["release_readiness"]
    color = "57C98B" if rel["recommendation"] == "GO" else "FF6F91"
    st.markdown(badge(rel["recommendation"], color), unsafe_allow_html=True)
    if rel["dependency_flags"]:
        st.write("**Flagged dependencies**")
        st.table(rel["dependency_flags"])
    st.write("**Deployment checklist**")
    for item in rel["deployment_checklist"]:
        st.write(f"- {item}")
    with st.expander("Release notes"):
        st.write(rel["release_notes"])
    bv = report.get("business_value", {})
    if bv:
        st.info(f'Estimated **{bv.get("total_hours_saved")} hours** saved this run -- {bv.get("breakdown_summary")}')

# ------------------------------------------------------------- Auto-Fix
with tabs[5]:
    af = r["auto_fix"]
    st.write(f'{af["files_changed"]} file(s) have a candidate fix.')
    for c in af["candidate_fixes"]:
        st.write(f'**{c["file"]}** -- ' + "; ".join(c["fixes"]))
        st.code(c["diff"] or "(no diff)", language="diff")
    if af["candidate_fixes"]:
        st.warning("Applying writes real files to disk and commits locally on a new branch. It never pushes anywhere on its own.")
        ack = st.checkbox("I've reviewed the diff(s) above and want to apply them now.")
        if st.button("Yes, apply these fixes now", disabled=not ack):
            with st.spinner("Applying fixes and re-running tests ..."):
                st.session_state.fix_result = fix_agent.run(st.session_state.project_path, apply=True)
    if st.session_state.fix_result:
        fr = st.session_state.fix_result
        st.success(f'Applied to {fr["files_changed"]} file(s).')
        if fr.get("test_run", {}).get("ran"):
            st.write("Test run:", "✅ passed" if fr["test_run"]["passed"] else "❌ failed")
            st.code(fr["test_run"].get("output_tail", ""))
        if fr.get("git_committed"):
            st.write(f'Committed to local branch `{fr["git_branch"]}` (not pushed).')
        elif fr.get("git_error"):
            st.caption(f'Git commit step did not complete: {fr["git_error"]} (files were still written to disk and tests still ran.)')

# ------------------------------------------------------------- Blast Radius
with tabs[6]:
    br = report.get("blast_radius", [])
    if not br:
        st.write("No high/medium-severity or confirmed findings to compute a blast radius for this run.")
    for entry in br:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.components.v1.html(_blast_radius_svg(entry), height=260)
        with c2:
            conf = badge("CONFIRMED", "57C98B") if entry["confirmed"] else ""
            card(
                f'{badge(entry["severity"].upper(), sev_color(entry["severity"]))} {conf}<br>'
                f'<b>{entry["file"]}</b>{" :: " + entry["function"] if entry.get("function") else ""}<br>'
                f'<span class="dp-muted">{entry["rule"]}</span><br>{entry["message"]}<br><br>'
                f'<b>{entry["dependent_count"]}</b> dependent file(s): {", ".join(entry["dependents"]) or "none"}'
            )

# ------------------------------------------------------------- Pre-Mortem
with tabs[7]:
    pm = report.get("premortem", [])
    if not pm:
        st.write("No pre-mortem scenarios for this run.")
    for sc in pm:
        conf = badge("CONFIRMED", "57C98B") if sc["confirmed"] else ""
        st.markdown(f'### {sc["title"]} {badge(sc["severity"].upper(), sev_color(sc["severity"]))}', unsafe_allow_html=True)
        st.write(sc["narrative"])
        st.caption(f'source: {sc["source"]}')
        with st.expander("Step-by-step timeline"):
            for step in sc["timeline"]:
                st.write(step)
            st.write(f'**Impact:** {sc["impact"]}')
            st.write(f'**Prevention:** {sc["prevention"]}')

# ------------------------------------------------------------- Executive Intelligence
with tabs[8]:
    ei = report.get("executive_intelligence", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Health score", f'{ei.get("health_score")}/100', ei.get("health_label"))
    c2.metric("Release call", ei.get("release_recommendation"))
    c3.metric("Hours saved", ei.get("hours_saved"))
    c4.metric("Runs recorded", ei.get("runs_recorded"))
    if ei.get("trend"):
        delta = ei.get("score_delta")
        st.write(f'Trend: **{ei["trend"]}**' + (f' ({"+" if delta and delta > 0 else ""}{delta})' if delta is not None else ""))
    if ei.get("risk_portfolio"):
        st.write("**Risk portfolio**")
        st.table(ei["risk_portfolio"])
    st.warning(ei.get("caveat", ""))

# ------------------------------------------------------------- History
with tabs[9]:
    hist = report.get("history", {})
    st.write(f'Runs recorded for this project: **{hist.get("runs_recorded", 1)}**')
    st.write(f'Trend: **{hist.get("trend")}**')
    if hist.get("series") and len(hist["series"]) >= 2:
        st.components.v1.html(_sparkline_svg(hist["series"]), height=140)
    if hist.get("recurring_rules"):
        st.write("**Recurring across runs**")
        st.table(hist["recurring_rules"])
    else:
        st.caption("No rule has recurred across enough runs yet.")

# ------------------------------------------------------------- Dependency Map
with tabs[10]:
    dm = report.get("dependency_map", {})
    if dm.get("nodes"):
        st.components.v1.html(_dependency_map_svg(dm), height=560)
        cap = f'{len(dm["nodes"])} modules shown, {len(dm["edges"])} import edges'
        if dm.get("truncated"):
            cap += f' (truncated from {dm.get("total_files")} total files)'
        st.caption(cap)
    else:
        st.write("No modules found to map.")

# ------------------------------------------------------------- Ask DevPilot
with tabs[11]:
    st.write("**Suggested questions**")
    sqa = report.get("suggested_qa", [])
    cols = st.columns(len(sqa) or 1)
    for i, qa in enumerate(sqa):
        if cols[i % len(cols)].button(qa["question"], key=f"sqa_{i}"):
            st.session_state.ask_answers.append(qa)

    q = st.text_input("Ask anything about this run -- e.g. 'is this safe to ship?'", key="ask_input")
    if st.button("Ask") and q:
        result = ask.answer(q, report)
        st.session_state.ask_answers.append({"question": q, **result})

    for item in reversed(st.session_state.ask_answers[-10:]):
        st.markdown(f'<div class="dp-qline"><b>Q:</b> {item["question"]}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="dp-qline"><b>A:</b> {item["answer"]}</div>', unsafe_allow_html=True)
        st.caption(f'source: {item["source"]}')
        st.divider()

# ------------------------------------------------------------- Demo Video
with tabs[12]:
    video_path = next((p for p in DEMO_VIDEO_CANDIDATES if os.path.exists(p) and os.path.getsize(p) > 0), None)
    if video_path:
        st.video(video_path)
    else:
        st.info("No demo_video.mp4 found next to this app or in ../../Submission-Docs -- drop one in either location and reload.")
