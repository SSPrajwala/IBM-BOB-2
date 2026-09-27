"""DevPilot orchestrator.

Mirrors IBM Bob 2.0's Agent mode: a lead agent decomposes a workflow request
into independent subagent tasks, fans them out in parallel, and reduces the
results into one consolidated, decision-ready report -- instead of a human
running five separate tools and stitching the output together by hand.

After the parallel pass, the orchestrator does two things no single
subagent can do on its own: it cross-checks subagents against each other
(corroboration.py) to separate confirmed bugs from single-source flags, and
it rolls everything into a business-value estimate and per-audience views.
"""
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from devpilot.agents import onboarding_agent, review_agent, testing_agent, release_agent, modernization_agent, fix_agent
from devpilot import corroboration, business_value, audience_views, ask, health, blast_radius, history, premortem, executive


def _timed(name, fn, *args, **kwargs):
    start = time.time()
    result = fn(*args, **kwargs)
    result["_elapsed_seconds"] = round(time.time() - start, 3)
    return name, result


def run_all(project_path, pr_diff_path=None, run_generated_tests=True, progress=None, project_identifier=None):
    """progress, if given, is called with a single human-readable string at
    each meaningful step -- this is the hook the web UI polls to show a live
    agent-by-agent console instead of a silent wait."""
    def emit(msg):
        if progress:
            progress(msg)

    project_path = os.path.abspath(project_path)
    tasks = {
        "onboarding": (onboarding_agent.run, (project_path,), {}),
        "code_review": (review_agent.run, (project_path,), {"pr_diff_path": pr_diff_path}),
        "testing": (testing_agent.run, (project_path,), {}),
        "release_readiness": (release_agent.run, (project_path,), {}),
        "modernization": (modernization_agent.run, (project_path,), {}),
        "auto_fix": (fix_agent.run, (project_path,), {"apply": False}),
    }

    emit(f"Dispatching {len(tasks)} subagents in parallel: {', '.join(tasks)}")
    results = {}
    wall_start = time.time()
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {
            pool.submit(_timed, name, fn, *args, **kwargs): name
            for name, (fn, args, kwargs) in tasks.items()
        }
        for future in as_completed(futures):
            name, result = future.result()
            results[name] = result
            emit(f"✓ {name} finished ({result['_elapsed_seconds']}s)")
    wall_elapsed = round(time.time() - wall_start, 3)
    sequential_elapsed = round(sum(r["_elapsed_seconds"] for r in results.values()), 3)

    report = {
        "project": os.path.basename(project_path.rstrip("/")),
        "generated_by": "DevPilot (IBM Bob 2.0 prototype)",
        "agents_run_in_parallel": list(tasks.keys()),
        "wall_clock_seconds": wall_elapsed,
        "sequential_equivalent_seconds": sequential_elapsed,
        "results": results,
    }

    # Write the generated test stubs into the target project so they can
    # actually be executed (not just displayed) -- this is what lets a
    # static-analysis finding get corroborated by a real, running failure.
    testing = results["testing"]
    generated_test_run = {"ran": False, "reason": "no uncovered endpoints"}
    failed_tests = []
    if run_generated_tests and testing.get("generated_test_source"):
        emit("Running the auto-generated tests against the live app ...")
        gen_rel = testing["generated_test_file"]
        gen_full = os.path.join(project_path, gen_rel)
        os.makedirs(os.path.dirname(gen_full), exist_ok=True)
        with open(gen_full, "w", encoding="utf-8") as f:
            f.write(testing["generated_test_source"])
        generated_test_run = corroboration.run_generated_tests(project_path, gen_rel)
        failed_tests = generated_test_run.get("failed_tests", [])
        if generated_test_run.get("ran"):
            emit(f"  {len(failed_tests)} generated test(s) failed against the running app")
        else:
            emit(f"  skipped: {generated_test_run.get('reason')}")

    emit("Cross-checking findings across subagents (confirmed vs. flagged) ...")
    confirmed, annotated_findings = corroboration.correlate(report, failed_tests)
    report["results"]["code_review"]["findings"] = annotated_findings
    report["confirmed_findings"] = confirmed
    report["generated_test_run"] = generated_test_run
    emit(f"  {len(confirmed)} finding(s) confirmed by two independent subagents")

    emit("Estimating business value and building role-scoped views ...")
    report["business_value"] = business_value.estimate(report)
    report["audience_views"] = audience_views.build(report)
    report["suggested_qa"] = ask.suggested_qa(report)

    emit("Scoring project health and writing the executive summary ...")
    report["health"] = health.compute_score(report)
    report["executive_summary"] = health.executive_summary(report, report["health"])

    emit("Mapping blast radius (reverse-dependency search) for the top findings ...")
    report["blast_radius"] = blast_radius.compute(project_path, report)

    emit("Mapping the full module dependency graph ...")
    report["dependency_map"] = blast_radius.full_graph(project_path)

    emit("Recording this run in the project's history log ...")
    identifier = project_identifier or project_path
    report["history"] = history.record_and_analyze(identifier, report)

    emit("Writing pre-mortem scenarios for the top findings ...")
    report["premortem"] = premortem.compute(report)

    emit("Assembling the executive intelligence view ...")
    report["executive_intelligence"] = executive.compute(report)
    emit("Done.")

    return report


def _li(items):
    return "".join(f"<li>{i}</li>" for i in items) or "<li><em>none</em></li>"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_BR_COLORS = {"high": "FF8FC7", "medium": "F2C94C", "low": "A78BFA"}


def _blast_radius_svg(entry):
    """A small radiating diagram: the buggy file in the center, its
    real reverse-dependencies (files that import it) arranged around it.
    Built from DevPilot's own import-graph scan, not a simulation."""
    deps = entry["dependents"]
    color = _BR_COLORS.get(entry["severity"], "A78BFA")
    size = 240
    cx = cy = size / 2
    ring_r = 84
    node_r = 15
    center_r = 30

    nodes = []
    lines = []
    n = len(deps)
    for i, dep in enumerate(deps):
        angle = (2 * math.pi * i / n) - math.pi / 2 if n else 0
        x = cx + ring_r * math.cos(angle)
        y = cy + ring_r * math.sin(angle)
        lines.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#{color}" stroke-width="1.5" stroke-opacity="0.55"/>')
        short = dep.split("/")[-1]
        nodes.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{node_r}" fill="#FFFFFF" stroke="#{color}" stroke-width="2"/>'
            f'<text x="{x:.1f}" y="{y + node_r + 13:.1f}" text-anchor="middle" font-size="9.5" fill="#6b6483">{_esc(short)}</text>'
        )

    center_label = entry["file"].split("/")[-1]
    svg = (
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(lines)
        + "".join(nodes)
        + f'<circle cx="{cx}" cy="{cy}" r="{center_r}" fill="#{color}" fill-opacity="0.18" stroke="#{color}" stroke-width="2.5"/>'
        + f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="11" font-weight="700" fill="#3B3350">{_esc(center_label)}</text>'
        + f'<text x="{cx}" y="{cy + 13}" text-anchor="middle" font-size="9" fill="#6b6483">{entry["dependent_count"]} dependent(s)</text>'
        + "</svg>"
    )
    return svg


def _dependency_map_svg(graph):
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    n = len(nodes)
    if n == 0:
        return "<p><em>No Python modules found to map.</em></p>"
    size = 460
    cx = cy = size / 2
    r = size / 2 - 60
    positions = {}
    for i, node in enumerate(nodes):
        angle = 2 * math.pi * i / n - math.pi / 2
        positions[node] = (cx + r * math.cos(angle), cy + r * math.sin(angle))

    lines = [
        f'<line x1="{positions[e["from"]][0]:.1f}" y1="{positions[e["from"]][1]:.1f}" '
        f'x2="{positions[e["to"]][0]:.1f}" y2="{positions[e["to"]][1]:.1f}" '
        f'stroke="#A78BFA" stroke-width="1" stroke-opacity="0.32"/>'
        for e in edges if e["from"] in positions and e["to"] in positions
    ]
    dots = []
    for node, (x, y) in positions.items():
        short = node.split("/")[-1]
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="#FF8FC7" stroke="#fff" stroke-width="1.5"/>')
        dots.append(f'<text x="{x:.1f}" y="{y - 10:.1f}" font-size="8" fill="#6b6483" text-anchor="middle">{_esc(short)}</text>')

    return (
        f'<svg viewBox="0 0 {size} {size}" width="100%" height="{size}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(lines) + "".join(dots) + "</svg>"
    )


def _sparkline_svg(series):
    if len(series) < 2:
        return "<p><em>Not enough runs yet for a trend line.</em></p>"
    w, h, pad = 420, 90, 12
    scores = [pt["score"] for pt in series]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1
    step = (w - 2 * pad) / (len(scores) - 1)
    pts = [(pad + i * step, h - pad - ((s - lo) / span) * (h - 2 * pad)) for i, s in enumerate(scores)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#A78BFA"/>' for x, y in pts)
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{path}" fill="none" stroke="#FF8FC7" stroke-width="2.5"/>{dots}</svg>'
    )


def to_html(report, video_src=None):
    r = report["results"]
    onboarding, review, testing, release, modernization = (
        r["onboarding"], r["code_review"], r["testing"], r["release_readiness"], r["modernization"]
    )
    bv = report["business_value"]
    confirmed = report["confirmed_findings"]
    auto_fix = r.get("auto_fix", {})
    health = report.get("health", {"score": 0, "label": "n/a", "color": "5B6B8C", "deductions": []})
    exec_summary = report.get("executive_summary", {}).get("summary", "")

    def _doc_badge(m):
        return "yes" if m["documented"] else '<span class="bad">no</span>'

    arch_rows = "".join(
        f"<tr><td>{m['file']}</td><td>{m['functions']}</td><td>{m['classes']}</td>"
        f"<td>{_doc_badge(m)}</td></tr>"
        for m in onboarding["architecture_map"]
    ) or "<tr><td colspan='4'><em>none</em></td></tr>"

    def finding_row(f):
        badge = '<span class="tag confirmed">CONFIRMED</span>' if f.get("confirmed") else '<span class="tag flagged">flagged</span>'
        loc = f["file"] + (f":" + str(f["line"]) if "line" in f else (" :: " + f["function"] if f.get("function") else ""))
        return f"<tr class='sev-{f['severity']}'><td>{f['severity'].upper()}</td><td>{loc}</td><td>{f['message']}</td><td>{badge}</td></tr>"

    findings_rows = "".join(finding_row(f) for f in review["findings"]) or "<tr><td colspan='4'><em>No findings</em></td></tr>"

    uncovered_rows = "".join(
        f"<tr><td>{e['method'].upper()}</td><td>{e['path']}</td><td>{e['source_file']}</td></tr>"
        for e in testing.get("uncovered_endpoints", [])
    ) or "<tr><td colspan='3'><em>Full coverage</em></td></tr>"

    checklist_rows = "".join(
        f"<tr><td>{c['item']}</td><td class='status-{c['status'].split()[0]}'>{c['status']}</td></tr>"
        for c in release["deployment_checklist"]
    )
    dep_flags = "".join(
        f"<li><strong>{d['package']}=={d['pinned_version']}</strong>: {d['note']}</li>" for d in release["dependency_flags"]
    ) or "<li><em>No flagged dependencies</em></li>"

    mod_rows = "".join(
        f"<tr><td>{m['type']}</td><td>{m['file']}{' :: ' + m['function'] if m.get('function') else ''}</td><td>{m['suggestion']}</td></tr>"
        for m in modernization["findings"]
    ) or "<tr><td colspan='3'><em>No modernization findings</em></td></tr>"

    confirmed_html = "".join(
        f"<li><strong>{c['file']}</strong> ({c.get('function','')}) -- {c['message']} <em>{c['corroborated_by']}</em></li>"
        for c in confirmed
    ) or "<li><em>No cross-agent confirmations this run</em></li>"

    qa_html = "".join(
        f"<div class='qa'><div class='q'>Q: {qa['question']}</div><div class='a'>A: {qa['answer']} <span class='src'>[{qa['source']}]</span></div></div>"
        for qa in report["suggested_qa"]
    )

    test_run = report.get("generated_test_run", {})
    test_run_html = (
        f"Ran generated tests: {len(test_run.get('failed_tests', []))} failed of the newly generated stubs."
        if test_run.get("ran") else f"Generated tests not executed ({test_run.get('reason', 'n/a')})."
    )

    deductions_html = "".join(
        f"<li>{_esc(d['reason'])} <strong>-{d['points']}</strong></li>" for d in health["deductions"]
    ) or "<li><em>No deductions -- nothing dragged the score down this run.</em></li>"

    fix_html = "".join(
        f"<div class='fixcard'><div class='fixfile'>{_esc(c['file'])}</div>"
        f"<div class='fixlist'>{'; '.join(_esc(x) for x in c['fixes'])}</div>"
        f"<pre class='diff'>{_esc(c['diff'])}</pre></div>"
        for c in auto_fix.get("candidate_fixes", [])
    ) or "<p><em>No safe, mechanical fixes available for this run -- everything DevPilot found either needs human judgment or is already clean.</em></p>"

    br_html = "".join(
        f"<div class='brcard'><div class='brsvg'>{_blast_radius_svg(e)}</div>"
        f"<div class='brinfo'><span class='tag {'confirmed' if e['confirmed'] else 'flagged'}'>{'CONFIRMED' if e['confirmed'] else 'flagged'}</span> "
        f"<span class='sevpill sev-{e['severity']}'>{e['severity'].upper()}</span>"
        f"<div class='brfile'>{_esc(e['file'])}{' :: ' + _esc(e['function']) if e.get('function') else ''}</div>"
        f"<div class='brmsg'>{_esc(e['message'])}</div>"
        f"<div class='brdeps'>{'If this breaks, it directly affects <strong>' + str(e['dependent_count']) + '</strong> other file(s): ' + ', '.join(_esc(d) for d in e['dependents']) if e['dependents'] else 'No other file in this repo imports it directly -- limited blast radius.'}</div>"
        f"</div></div>"
        for e in report.get("blast_radius", [])
    ) or "<p><em>No confirmed or high/medium findings to map this run.</em></p>"

    pm_html = "".join(
        f"<div class='pmcard'><div class='pmtitle'>{_esc(sc['title'])} "
        f"<span class='tag {'confirmed' if sc['confirmed'] else 'flagged'}'>{'CONFIRMED' if sc['confirmed'] else 'flagged'}</span></div>"
        f"<p class='pmnarrative'>{_esc(sc['narrative'])}</p>"
        f"<ol class='pmtimeline'>{''.join('<li>' + _esc(s.split('. ', 1)[-1]) + '</li>' for s in sc['timeline'])}</ol>"
        f"<p class='pmimpact'>{_esc(sc['impact'])}</p>"
        f"<p class='pmprevent'><strong>Prevention:</strong> {_esc(sc['prevention'])}</p>"
        f"<span class='src-tag'>Generated by {_esc(sc['source'])}</span>"
        f"</div>"
        for sc in report.get("premortem", [])
    ) or "<p><em>No confirmed or high/medium findings to write a pre-mortem for this run.</em></p>"

    exec_intel = report.get("executive_intelligence", {})
    portfolio_rows = "".join(
        f"<tr><td><span class='tag {'confirmed' if p['confirmed'] else 'flagged'}'>{'CONFIRMED' if p['confirmed'] else 'flagged'}</span></td>"
        f"<td class='sevpill sev-{p['severity']}'>{p['severity'].upper()}</td>"
        f"<td>{_esc(p['file'])}{' :: ' + _esc(p['function']) if p.get('function') else ''}</td>"
        f"<td>{p['dependent_count']}</td><td>{_esc(p['message'])}</td></tr>"
        for p in exec_intel.get("risk_portfolio", [])
    ) or "<tr><td colspan='5'><em>No standout risks this run</em></td></tr>"

    hist = report.get("history", {})
    trend_word = hist.get("trend", "n/a")
    delta = hist.get("score_delta")
    delta_html = (
        f"{'+' if delta and delta > 0 else ''}{delta} vs. the previous run" if delta is not None else "no previous run to compare yet"
    )
    recurring_html = "".join(
        f"<li><strong>{_esc(rr['rule'])}</strong> -- seen in {rr['seen_in_runs']} of the last {hist.get('runs_recorded')} runs</li>"
        for rr in hist.get("recurring_rules", [])
    ) or "<li><em>No rule has recurred across multiple runs yet.</em></li>"

    dep_map_html = _dependency_map_svg(report.get("dependency_map", {"nodes": [], "edges": []}))
    dep_map_note = report.get("dependency_map", {})
    dep_map_caption = (
        f"Showing {len(dep_map_note.get('nodes', []))} of {dep_map_note.get('total_files', 0)} Python modules"
        + (" (truncated for readability)" if dep_map_note.get("truncated") else "")
    )

    video_available = bool(video_src)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>DevPilot Report -- {report['project']}</title>
<style>
:root {{
  --pink:#FFD6EA; --pink-deep:#FF8FC7; --green:#CFF5DE; --green-deep:#57C98B;
  --purple:#E7DBFF; --purple-deep:#A78BFA; --yellow:#FFF3B0; --yellow-deep:#F2C94C;
  --bg:#FBF7FF; --card:#FFFFFF; --text:#3B3350; --muted:#8C82A6; --border:#F0E6FA;
}}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 0 0 60px; background: var(--bg); color: var(--text); }}
a {{ color: var(--purple-deep); }}
.topbar {{ background: linear-gradient(120deg, var(--purple) 0%, var(--pink) 55%, var(--yellow) 100%); padding: 36px 40px 30px; }}
.topbar .eyebrow {{ color: #6b5a94; font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 800; opacity: 0.9; }}
.topbar h1 {{ font-size: 28px; margin: 6px 0 4px; color: #3B3350; }}
.topbar .sub {{ color: #5A4E78; font-size: 13.5px; opacity: 0.95; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 0 40px; }}
.hero {{ display: grid; grid-template-columns: 160px 1fr; gap: 28px; align-items: center; background: var(--card); border: 1px solid var(--border); border-radius: 22px; padding: 24px 28px; margin: -20px 0 24px; box-shadow: 0 14px 34px rgba(167,139,250,0.18); }}
.gauge {{ width: 140px; height: 140px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-direction: column; background: conic-gradient(#{health['color']} {health['score']*3.6}deg, #F0EAFB 0deg); }}
.gauge-inner {{ width: 112px; height: 112px; border-radius: 50%; background: var(--card); display: flex; flex-direction: column; align-items: center; justify-content: center; }}
.gauge-inner .score {{ font-size: 32px; font-weight: 800; color: #{health['color']}; }}
.gauge-inner .label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-top: 2px; }}
.hero .summary h2 {{ margin: 0 0 8px; font-size: 15px; color: var(--purple-deep); text-transform: uppercase; letter-spacing: 0.06em; border: none; padding: 0; }}
.hero .summary p {{ margin: 0; font-size: 14.5px; line-height: 1.6; color: var(--text); }}
.hero .src-tag {{ display: inline-block; margin-top: 10px; font-size: 10.5px; color: var(--muted); }}
h2 {{ font-size: 16px; margin-top: 34px; border-bottom: 2px solid var(--border); padding-bottom: 8px; color: var(--text); }}
p {{ line-height: 1.55; }}
.grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 4px 0 20px; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px; box-shadow: 0 4px 14px rgba(167,139,250,0.08); }}
.card .n {{ font-size: 21px; font-weight: 700; }}
.card .l {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
td, th {{ padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; text-align: left; }}
th {{ color: var(--muted); font-weight: 600; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.03em; }}
.sev-high td:first-child, .sevpill.sev-high {{ color: #E0558F; font-weight: 700; }}
.sev-medium td:first-child, .sevpill.sev-medium {{ color: #C99A1E; font-weight: 700; }}
.sev-low td:first-child, .sevpill.sev-low {{ color: var(--muted); font-weight: 700; }}
.sevpill {{ font-size: 10px; letter-spacing: 0.03em; }}
.status-fail {{ color: #E0558F; font-weight: 700; }}
.status-pass {{ color: #3AAE73; font-weight: 700; }}
.status-attention {{ color: #C99A1E; font-weight: 700; }}
.bad {{ color: #E0558F; }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 10px; font-weight: 700; }}
.tag.confirmed {{ background: var(--green); color: #1F8C57; }}
.tag.flagged {{ background: var(--yellow); color: #97701A; }}
.badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
.badge.go {{ background: var(--green); color: #1F8C57; }}
.badge.nogo {{ background: var(--pink); color: #C23A6E; }}
ul {{ margin: 8px 0; padding-left: 20px; font-size: 13px; line-height: 1.6; }}
.tabs {{ display: flex; gap: 6px; margin: 30px 0 0; flex-wrap: wrap; }}
.tab {{ padding: 9px 16px; border-radius: 999px; cursor: pointer; font-size: 13px; border: 1px solid var(--border); background: var(--card); color: var(--muted); font-weight: 600; }}
.tab.active {{ background: linear-gradient(90deg, var(--purple-deep), var(--pink-deep)); color: #fff; border-color: transparent; box-shadow: 0 6px 16px rgba(167,139,250,0.35); }}
.panel {{ display: none; border: 1px solid var(--border); border-radius: 18px; padding: 22px 24px; background: var(--card); margin-top: 12px; }}
.panel.active {{ display: block; }}
.qa {{ background: #FBF7FF; border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; margin-bottom: 8px; font-size: 13px; }}
.qa .q {{ font-weight: 700; color: #6b5a94; }}
.qa .a {{ margin-top: 4px; }}
.qa .src {{ color: var(--muted); font-size: 11px; }}
.fixcard {{ background: #FBF7FF; border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; margin-bottom: 14px; }}
.fixfile {{ font-weight: 700; color: var(--purple-deep); font-size: 13.5px; }}
.fixlist {{ color: var(--muted); font-size: 12.5px; margin: 4px 0 10px; }}
.diff {{ background: #2B2640; border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; font-size: 12px; overflow-x: auto; white-space: pre; color: #E7DBFF; }}
.fixnote {{ font-size: 12.5px; color: var(--muted); margin-top: -8px; margin-bottom: 16px; }}
.brcard {{ display: grid; grid-template-columns: 240px 1fr; gap: 18px; align-items: center; background: #FBF7FF; border: 1px solid var(--border); border-radius: 16px; padding: 16px; margin-bottom: 16px; }}
.brfile {{ font-weight: 700; color: var(--text); font-size: 13.5px; margin: 6px 0 4px; }}
.brmsg {{ font-size: 13px; color: var(--text); margin-bottom: 8px; }}
.brdeps {{ font-size: 12.5px; color: var(--muted); }}
.demo-box {{ background: #FBF7FF; border: 2px dashed var(--purple-deep); border-radius: 16px; padding: 40px 24px; text-align: center; color: var(--muted); font-size: 13.5px; }}
video {{ width: 100%; border-radius: 16px; border: 1px solid var(--border); }}
.pmcard {{ background: #FBF7FF; border: 1px solid var(--border); border-radius: 16px; padding: 16px 18px; margin-bottom: 16px; }}
.pmtitle {{ font-weight: 700; font-size: 14px; color: var(--text); }}
.pmnarrative {{ font-size: 13.5px; color: var(--text); margin: 8px 0; line-height: 1.6; }}
.pmtimeline {{ font-size: 12.5px; color: var(--muted); margin: 6px 0; padding-left: 20px; }}
.pmimpact, .pmprevent {{ font-size: 12.5px; margin: 6px 0; }}
.exec-card {{ background: linear-gradient(120deg, var(--purple) 0%, var(--pink) 100%); border-radius: 18px; padding: 20px; margin-bottom: 16px; }}
.exec-card .n {{ font-size: 26px; font-weight: 800; color: var(--text); }}
.exec-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
.exec-caveat {{ font-size: 12px; color: var(--muted); margin-top: 12px; font-style: italic; }}
.hist-trend {{ display: inline-block; padding: 4px 12px; border-radius: 999px; font-weight: 700; font-size: 12.5px; }}
.hist-trend.improving {{ background: var(--green); color: #1F8C57; }}
.hist-trend.declining {{ background: var(--pink); color: #C23A6E; }}
.hist-trend.unchanged, .hist-trend.na {{ background: var(--yellow); color: #97701A; }}
</style></head>
<body>
<div class="topbar">
  <div class="eyebrow">DevPilot &middot; IBM Bob 2.0 prototype</div>
  <h1>{report['project']}</h1>
  <div class="sub">{len(report['agents_run_in_parallel'])} subagents run in parallel &middot;
  wall-clock {report['wall_clock_seconds']}s vs. {report['sequential_equivalent_seconds']}s sequential &middot; {test_run_html}</div>
</div>
<main>

<div class="hero">
  <div class="gauge"><div class="gauge-inner"><div class="score">{health['score']}</div><div class="label">{_esc(health['label'])}</div></div></div>
  <div class="summary">
    <h2>Executive summary</h2>
    <p>{_esc(exec_summary)}</p>
    <span class="src-tag">Generated by {_esc(report.get('executive_summary', {}).get('source', 'template'))}</span>
  </div>
</div>

<div class="grid">
  <div class="card"><div class="n">{onboarding['modules_scanned']}</div><div class="l">modules mapped</div></div>
  <div class="card"><div class="n">{review['counts']['high']}H / {review['counts']['medium']}M</div><div class="l">review findings ({len(confirmed)} confirmed)</div></div>
  <div class="card"><div class="n">{testing['headline_coverage_pct']}%</div><div class="l">{testing['headline_label']}</div></div>
  <div class="card"><div class="n"><span class="badge {'go' if release['recommendation']=='GO' else 'nogo'}">{release['recommendation']}</span></div><div class="l">release recommendation</div></div>
  <div class="card"><div class="n">{bv['total_hours_saved']}h</div><div class="l">estimated hours saved</div></div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('all')">Full Report</div>
  <div class="tab" onclick="showTab('dev')">New Developer</div>
  <div class="tab" onclick="showTab('rev')">Reviewer</div>
  <div class="tab" onclick="showTab('test')">Tester</div>
  <div class="tab" onclick="showTab('rel')">Release Manager</div>
  <div class="tab" onclick="showTab('fix')">Auto-Fix Preview</div>
  <div class="tab" onclick="showTab('br')">Blast Radius</div>
  <div class="tab" onclick="showTab('pm')">Pre-Mortem</div>
  <div class="tab" onclick="showTab('exec')">Executive Intelligence</div>
  <div class="tab" onclick="showTab('hist')">History</div>
  <div class="tab" onclick="showTab('depmap')">Dependency Map</div>
  <div class="tab" onclick="showTab('ask')">Ask DevPilot</div>
  <div class="tab" onclick="showTab('demo')">Demo Video</div>
</div>

<div id="panel-all" class="panel active">

<h2>1. Onboarding -- architecture map &amp; starter tasks</h2>
<p>{onboarding['modules_scanned']} modules scanned, {onboarding['total_loc']} lines. README: {'yes' if onboarding['has_readme'] else 'no'} &middot; Dockerfile: {'yes' if onboarding['has_dockerfile'] else 'no'}</p>
<table><tr><th>File</th><th>Functions</th><th>Classes</th><th>Documented</th></tr>{arch_rows}</table>
<strong>Suggested starter tasks</strong><ul>{_li(onboarding['starter_tasks'])}</ul>

<h2>2. Code review -- {review['summary']}</h2>
<table><tr><th>Severity</th><th>Location</th><th>Finding</th><th>Status</th></tr>{findings_rows}</table>
{f"<p><strong>PR diff reviewed:</strong> {review['pr_diff_reviewed']['file']} ({review['pr_diff_reviewed']['risky_lines_added']} of {review['pr_diff_reviewed']['lines_added']} added lines flagged)</p>" if review.get('pr_diff_reviewed') else ""}
<strong>Cross-agent confirmed findings</strong><ul>{confirmed_html}</ul>

<h2>3. Testing -- {testing['headline_coverage_pct']}% {testing['headline_label']}</h2>
<table><tr><th>Method</th><th>Path</th><th>File</th></tr>{uncovered_rows}</table>
<p>Function-level reference coverage (fallback metric, always available): {testing['function_coverage']['referenced_functions']}/{testing['function_coverage']['total_functions']} functions ({testing['function_coverage']['pct']}%) referenced somewhere in the test suite.</p>

<h2>4. Release readiness -- recommendation: {release['recommendation']}</h2>
<strong>Dependency advisories</strong><ul>{dep_flags}</ul>
<table><tr><th>Check</th><th>Status</th></tr>{checklist_rows}</table>

<h2>5. Modernization</h2>
<p>{modernization['summary']}</p>
<table><tr><th>Type</th><th>Location</th><th>Suggestion</th></tr>{mod_rows}</table>

<h2>6. Business value</h2>
<p><strong>{bv['total_hours_saved']} hours</strong> estimated saved this run: {bv['breakdown_summary']}.</p>
<p><em>{bv['caveat']}</em></p>

<h2>7. Health score breakdown</h2>
<p>Score starts at 100 and is docked for each issue category below; nothing here is a guess -- every deduction traces back to a specific finding elsewhere in this report.</p>
<ul>{deductions_html}</ul>

</div>

<div id="panel-dev" class="panel"><h2>For a new developer</h2>
<strong>Setup</strong><ul>{_li(onboarding['setup_steps'])}</ul>
<strong>Architecture</strong><table><tr><th>File</th><th>Functions</th><th>Classes</th><th>Documented</th></tr>{arch_rows}</table>
<strong>Start here</strong><ul>{_li(onboarding['starter_tasks'])}</ul>
</div>

<div id="panel-rev" class="panel"><h2>For a code reviewer</h2>
<p>{review['summary']}</p>
<table><tr><th>Severity</th><th>Location</th><th>Finding</th><th>Status</th></tr>{findings_rows}</table>
<strong>Confirmed by execution</strong><ul>{confirmed_html}</ul>
</div>

<div id="panel-test" class="panel"><h2>For a tester</h2>
<p>{testing['headline_coverage_pct']}% {testing['headline_label']} &middot; {test_run_html}</p>
<table><tr><th>Method</th><th>Path</th><th>File</th></tr>{uncovered_rows}</table>
</div>

<div id="panel-rel" class="panel"><h2>For a release manager</h2>
<p><span class="badge {'go' if release['recommendation']=='GO' else 'nogo'}">{release['recommendation']}</span></p>
<strong>Dependency advisories</strong><ul>{dep_flags}</ul>
<table><tr><th>Check</th><th>Status</th></tr>{checklist_rows}</table>
<p><strong>{bv['total_hours_saved']}h</strong> estimated saved: {bv['breakdown_summary']}.</p>
</div>

<div id="panel-fix" class="panel"><h2>Auto-Fix Preview</h2>
<p class="fixnote">Preview only -- nothing on this page has been written to disk. Run with <code>--apply-fixes</code> (CLI) or use the Fix action in the web UI to actually apply and locally commit these changes; pushing to GitHub is always a separate, explicitly-confirmed step using your own token.</p>
{fix_html}
</div>

<div id="panel-br" class="panel"><h2>Blast Radius -- if this breaks, what else does?</h2>
<p>For each top finding, DevPilot searches the real import graph of this repo for files that depend on it -- a concrete, computed signal, not a simulation.</p>
{br_html}
</div>

<div id="panel-pm" class="panel"><h2>Pre-Mortem Intelligence -- how this could fail</h2>
<p>Plain-English failure scenarios for the top findings, built from this run's own severity, message, and blast-radius data -- not a simulation trained on incident history.</p>
{pm_html}
</div>

<div id="panel-exec" class="panel"><h2>Executive Intelligence</h2>
<div class="exec-card">
  <div class="exec-grid">
    <div><div class="n">{exec_intel.get('health_score','?')}/100</div><div class="l">health score ({_esc(exec_intel.get('health_label','n/a'))})</div></div>
    <div><div class="n">{exec_intel.get('release_recommendation','?')}</div><div class="l">release recommendation</div></div>
    <div><div class="n">{exec_intel.get('hours_saved','?')}h</div><div class="l">estimated hours saved</div></div>
    <div><div class="n">{len(exec_intel.get('risk_portfolio', []))}</div><div class="l">standout risks this run</div></div>
  </div>
</div>
<h2 style="margin-top:16px">Risk portfolio</h2>
<table><tr><th>Status</th><th>Severity</th><th>Location</th><th>Blast radius</th><th>Finding</th></tr>{portfolio_rows}</table>
<p class="exec-caveat">{_esc(exec_intel.get('caveat',''))}</p>
</div>

<div id="panel-hist" class="panel"><h2>Engineering Historian</h2>
<p>{hist.get('runs_recorded',1)} run(s) recorded for this project &middot; trend: <span class="hist-trend {trend_word.split()[0] if ' ' not in trend_word else 'na'}">{_esc(trend_word)}</span> ({_esc(delta_html)})</p>
{_sparkline_svg(hist.get('series', []))}
<h2 style="margin-top:16px">Recurring patterns</h2>
<p>Rules that keep showing up run after run -- these are architectural weaknesses, not one-off mistakes.</p>
<ul>{recurring_html}</ul>
</div>

<div id="panel-depmap" class="panel"><h2>Dependency Map</h2>
<p>Every module in this repo and who imports whom -- the same real import-graph scan behind Blast Radius, applied to the whole codebase instead of just the top findings. {_esc(dep_map_caption)}.</p>
{dep_map_html}
</div>

<div id="panel-ask" class="panel"><h2>Ask DevPilot</h2>{qa_html}</div>

<div id="panel-demo" class="panel"><h2>Demo Video</h2>
{f"<video controls src='{_esc(video_src)}'></video>" if video_available else "<div class='demo-box'>No demo_video.mp4 found next to this report.<br>Drop one alongside devpilot_report.html (or serve it via the web UI) and it will play right here.</div>"}
</div>

</main>
<script>
function showTab(id) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  event.target.classList.add('active');
}}
</script>
</body></html>"""
    return html
