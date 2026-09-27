#!/usr/bin/env python3
"""DevPilot web UI -- a small, dependency-free local server.

A real single-page app: paste a GitHub URL or a local folder path, watch
the six subagents report in live, then explore the same role-scoped report
run_devpilot.py produces -- rendered natively on the page (not a static
iframe), with two features that only make sense once the page is live:

  * Ask DevPilot -- type any question, get an answer grounded in this run's
    actual findings (POST /api/ask/<job_id>).
  * Make it flawless -- apply DevPilot's safe, mechanical fixes right from
    the UI and see the diff + test result (POST /api/fix/<job_id>). This
    always commits locally on a new branch and NEVER pushes anywhere --
    pushing/opening a PR is a separate, explicitly confirmed step
    (push_and_pr.py, which needs --confirm and your own GITHUB_TOKEN).

Stdlib only (http.server + threading): nothing extra to pip install.

Usage:
    python3 webui.py            # then open http://localhost:8765
    python3 webui.py --port 9000
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from devpilot.orchestrator import run_all
from devpilot.agents import fix_agent
from devpilot import watsonx_client, ask

JOBS = {}
JOBS_LOCK = threading.Lock()
CLONE_TTL_SECONDS = 60 * 60  # abandoned clones get swept after an hour
DEMO_VIDEO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_video.mp4")


def _clone(repo_url):
    workdir = tempfile.mkdtemp(prefix="devpilot_web_clone_")
    subprocess.run(["git", "clone", "--depth", "1", repo_url, workdir], check=True, capture_output=True)
    return workdir


def _run_job(job_id, source_type, value, pr_diff):
    def log(msg):
        with JOBS_LOCK:
            JOBS[job_id]["log"].append({"t": round(time.time() - JOBS[job_id]["started"], 2), "msg": msg})

    try:
        if source_type == "github":
            log(f"Cloning {value} ...")
            cloned_path = _clone(value)
            project_path = cloned_path
            with JOBS_LOCK:
                JOBS[job_id]["project_path"] = project_path
                JOBS[job_id]["cloned"] = True
            log("Cloned into a temporary folder (kept alive for this session so Ask/Fix can use it).")
        else:
            project_path = value
            if not os.path.isdir(project_path):
                raise FileNotFoundError(f"'{project_path}' is not a folder DevPilot can see. "
                                         f"If this app is running in a different sandbox than your files, "
                                         f"use a path inside that sandbox, or use a GitHub URL instead.")
            with JOBS_LOCK:
                JOBS[job_id]["project_path"] = project_path
                JOBS[job_id]["cloned"] = False
            log(f"Assessing local folder: {project_path}")

        identifier = value if source_type == "github" else project_path
        report = run_all(project_path, pr_diff_path=(pr_diff or None), progress=log, project_identifier=identifier)

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "done"
            JOBS[job_id]["report"] = report

    except Exception as e:
        log(f"ERROR: {e}")
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
            proj = JOBS[job_id].get("project_path")
            if JOBS[job_id].get("cloned") and proj:
                shutil.rmtree(proj, ignore_errors=True)
                JOBS[job_id]["project_path"] = None
                JOBS[job_id]["cloned"] = False


def _sweep_loop():
    """Background thread: deletes cloned repos nobody has touched in a
    while, so a long-running server doesn't quietly fill up disk with
    abandoned clones from closed browser tabs."""
    while True:
        time.sleep(300)
        now = time.time()
        with JOBS_LOCK:
            for job_id, job in list(JOBS.items()):
                if job.get("cloned") and job.get("project_path") and (now - job["started"]) > CLONE_TTL_SECONDS:
                    shutil.rmtree(job["project_path"], ignore_errors=True)
                    job["project_path"] = None
                    job["cloned"] = False
                    job["log"].append({"t": round(now - job["started"], 2), "msg": "Clone auto-deleted after being idle for over an hour."})


INDEX_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>DevPilot</title>
<style>
:root {
  --pink:#FFD6EA; --pink-deep:#FF8FC7; --green:#CFF5DE; --green-deep:#57C98B;
  --purple:#E7DBFF; --purple-deep:#A78BFA; --yellow:#FFF3B0; --yellow-deep:#F2C94C;
  --bg:#FBF7FF; --card:#FFFFFF; --text:#3B3350; --muted:#8C82A6; --border:#F0E6FA;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
a { color: var(--purple-deep); }
code { background: #F0E6FA; padding: 1px 5px; border-radius: 4px; color: #6b5a94; }
.topbar { background: linear-gradient(120deg, var(--purple) 0%, var(--pink) 55%, var(--yellow) 100%); padding: 30px 40px 26px; }
.topbar .eyebrow { color: #6b5a94; font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 800; opacity: 0.9; }
.topbar h1 { font-size: 26px; margin: 6px 0 4px; color: #3B3350; }
.topbar p { margin: 0; color: #5A4E78; font-size: 13.5px; }
main { max-width: 1080px; margin: 0 auto; padding: 26px 40px 70px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 18px; padding: 20px 24px; margin-bottom: 20px; box-shadow: 0 4px 14px rgba(167,139,250,0.08); }
label { display: block; font-size: 12.5px; color: var(--muted); margin: 14px 0 6px; text-transform: uppercase; letter-spacing: 0.03em; }
input[type=text], textarea { width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border); background: #FBF7FF; color: var(--text); font-size: 14px; font-family: inherit; }
.radios { display: flex; gap: 18px; }
.radios label { display: flex; align-items: center; gap: 6px; color: var(--text); font-size: 14px; margin: 0; text-transform: none; letter-spacing: 0; }
button { margin-top: 16px; background: linear-gradient(90deg, var(--purple-deep), var(--pink-deep)); color: #fff; border: none; padding: 11px 22px; border-radius: 999px; font-weight: 700; font-size: 13.5px; cursor: pointer; box-shadow: 0 6px 16px rgba(167,139,250,0.35); }
button.secondary { background: var(--card); color: var(--text); border: 1px solid var(--border); box-shadow: none; }
button.danger { background: var(--pink); color: #C23A6E; border: 1px solid #F5B8D2; box-shadow: none; }
button:disabled { opacity: 0.5; cursor: default; }
#log { background: #2B2640; border-radius: 12px; padding: 14px; font-family: "SF Mono", Consolas, monospace; font-size: 12.5px; height: 200px; overflow-y: auto; white-space: pre-wrap; color: #B9F5CE; }
#log .line { margin-bottom: 4px; }
.hint { color: var(--muted); font-size: 12px; margin-top: 8px; }
.error-box { color: #C23A6E; background: var(--pink); border: 1px solid #F5B8D2; border-radius: 10px; padding: 12px 14px; font-size: 13px; }

.hero { display: grid; grid-template-columns: 140px 1fr; gap: 26px; align-items: center; }
.gauge { width: 120px; height: 120px; border-radius: 50%; display: flex; align-items: center; justify-content: center; }
.gauge-inner { width: 96px; height: 96px; border-radius: 50%; background: var(--card); display: flex; flex-direction: column; align-items: center; justify-content: center; }
.gauge-inner .score { font-size: 28px; font-weight: 800; }
.gauge-inner .label { font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-top: 2px; }
.hero h2 { margin: 0 0 8px; font-size: 14px; color: var(--purple-deep); text-transform: uppercase; letter-spacing: 0.06em; }
.hero p { margin: 0; font-size: 14.5px; line-height: 1.6; color: var(--text); }
.src-tag { display: block; margin-top: 8px; font-size: 10.5px; color: var(--muted); }

.grid5 { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 18px 0 0; }
.stat { background: #FBF7FF; border: 1px solid var(--border); border-radius: 14px; padding: 12px 14px; }
.stat .n { font-size: 20px; font-weight: 700; }
.stat .l { font-size: 11px; color: var(--muted); margin-top: 2px; }

.tabs { display: flex; gap: 6px; margin: 20px 0 0; flex-wrap: wrap; }
.tab { padding: 9px 16px; border-radius: 999px; cursor: pointer; font-size: 13px; border: 1px solid var(--border); background: var(--card); color: var(--muted); font-weight: 600; }
.tab.active { background: linear-gradient(90deg, var(--purple-deep), var(--pink-deep)); color: #fff; border-color: transparent; box-shadow: 0 6px 16px rgba(167,139,250,0.35); }
.panel { display: none; border: 1px solid var(--border); border-radius: 18px; padding: 22px 24px; background: var(--card); margin-top: 12px; }
.panel.active { display: block; }
h2.section { font-size: 15px; margin: 24px 0 10px; border-bottom: 2px solid var(--border); padding-bottom: 8px; }
h2.section:first-child { margin-top: 0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
td, th { padding: 7px 9px; border-bottom: 1px solid var(--border); vertical-align: top; text-align: left; }
th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; }
.sev-high td:first-child { color: #E0558F; font-weight: 700; }
.sev-medium td:first-child { color: #C99A1E; font-weight: 700; }
.sev-low td:first-child { color: var(--muted); font-weight: 700; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 10px; font-weight: 700; }
.tag.confirmed { background: var(--green); color: #1F8C57; }
.tag.flagged { background: var(--yellow); color: #97701A; }
.badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }
.badge.go { background: var(--green); color: #1F8C57; }
.badge.nogo { background: var(--pink); color: #C23A6E; }
ul.plain { margin: 6px 0; padding-left: 20px; font-size: 13px; line-height: 1.6; }

.qa { background: #FBF7FF; border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; margin-bottom: 8px; font-size: 13px; }
.qa .q { font-weight: 700; color: #6b5a94; }
.qa .a { margin-top: 4px; }
.qa .src { color: var(--muted); font-size: 11px; }
#askForm { display: flex; gap: 8px; margin-top: 14px; }
#askForm input { flex: 1; }
#askForm button { margin-top: 0; }

.fixcard { background: #FBF7FF; border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; margin-bottom: 14px; }
.fixfile { font-weight: 700; color: var(--purple-deep); font-size: 13.5px; }
.fixlist { color: var(--muted); font-size: 12.5px; margin: 4px 0 10px; }
.diff { background: #2B2640; border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; font-size: 12px; overflow-x: auto; white-space: pre; color: #E7DBFF; }
.fixresult { background: var(--green); border: 1px solid #B7E8C9; border-radius: 10px; padding: 12px 14px; font-size: 13px; margin-top: 14px; color: #1F8C57; }
.fixresult.bad { background: var(--pink); border-color: #F5B8D2; color: #C23A6E; }
.fixconfirm { background: var(--yellow); border: 1px solid #F0DE85; border-radius: 14px; padding: 14px 16px; margin-top: 14px; }
.fixconfirm p { font-size: 13px; margin: 0 0 8px; color: #6b5a1e; }
.fixconfirm ul.plain { margin: 4px 0 10px; }
.checkline { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #6b5a1e; text-transform: none; letter-spacing: 0; margin: 10px 0; }
.checkline input { width: auto; margin: 0; }
.fixconfirm-actions { display: flex; gap: 10px; margin-top: 6px; }
.fixconfirm-actions button { margin-top: 0; }
#fixConfirmBtn:disabled { opacity: 0.4; cursor: not-allowed; }

.brcard { display: grid; grid-template-columns: 240px 1fr; gap: 18px; align-items: center; background: #FBF7FF; border: 1px solid var(--border); border-radius: 16px; padding: 16px; margin-bottom: 16px; }
.brfile { font-weight: 700; color: var(--text); font-size: 13.5px; margin: 6px 0 4px; }
.brmsg { font-size: 13px; color: var(--text); margin-bottom: 8px; }
.brdeps { font-size: 12.5px; color: var(--muted); }
.demo-box { background: #FBF7FF; border: 2px dashed var(--purple-deep); border-radius: 16px; padding: 40px 24px; text-align: center; color: var(--muted); font-size: 13.5px; }
video { width: 100%; border-radius: 16px; border: 1px solid var(--border); }

.pmcard { background: #FBF7FF; border: 1px solid var(--border); border-radius: 16px; padding: 16px 18px; margin-bottom: 16px; }
.pmtitle { font-weight: 700; font-size: 14px; color: var(--text); }
.pmnarrative { font-size: 13.5px; color: var(--text); margin: 8px 0; line-height: 1.6; }
.pmtimeline { font-size: 12.5px; color: var(--muted); margin: 6px 0; padding-left: 20px; }
.pmimpact, .pmprevent { font-size: 12.5px; margin: 6px 0; }
.exec-card { background: linear-gradient(120deg, var(--purple) 0%, var(--pink) 100%); border-radius: 18px; padding: 20px; margin-bottom: 16px; }
.exec-card .n { font-size: 26px; font-weight: 800; color: var(--text); }
.exec-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.exec-caveat { font-size: 12px; color: var(--muted); margin-top: 12px; font-style: italic; }
.hist-trend { display: inline-block; padding: 4px 12px; border-radius: 999px; font-weight: 700; font-size: 12.5px; }
.hist-trend.improving { background: var(--green); color: #1F8C57; }
.hist-trend.declining { background: var(--pink); color: #C23A6E; }
.hist-trend.unchanged, .hist-trend.na { background: var(--yellow); color: #97701A; }

.panel.active { animation: dpFadeIn 0.35s ease; }
@keyframes dpFadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
.live-badge { display: inline-flex; align-items: center; gap: 6px; background: var(--pink); color: #C23A6E; font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 999px; margin-left: 10px; }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: #C23A6E; animation: dpPulse 1.1s ease-in-out infinite; }
@keyframes dpPulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.7); } }
</style></head>
<body>
<div class="topbar">
  <div class="eyebrow">DevPilot &middot; IBM Bob 2.0 prototype</div>
  <h1>DevPilot</h1>
  <p>Paste a GitHub repo URL or a local folder path. Watch six subagents assess it live, then ask questions and apply safe fixes right from here.</p>
</div>
<main>
  <div class="card" id="runCard">
    <div class="radios">
      <label><input type="radio" name="source_type" value="github" checked> GitHub URL</label>
      <label><input type="radio" name="source_type" value="local"> Local folder path</label>
    </div>
    <label id="valueLabel">Repository URL</label>
    <input type="text" id="value" placeholder="https://github.com/owner/repo.git">
    <label>Optional: path to a PR/diff file to review</label>
    <input type="text" id="prDiff" placeholder="(leave blank to skip)">
    <button id="runBtn" onclick="runDevPilot()">Run DevPilot</button>
    <div class="hint">Cloned GitHub repos are kept alive for this session (so Ask/Fix can use the files) and auto-deleted after an hour of inactivity, or on demand with Discard below.</div>
  </div>

  <div class="card" id="logCard" style="display:none">
    <div style="margin-bottom:8px"><strong>Live agent log</strong><span class="live-badge" id="liveBadge"><span class="live-dot"></span>LIVE</span></div>
    <div id="log"></div>
  </div>

  <div id="reportRoot"></div>
</main>

<script>
let jobId = null, currentReport = null, poller = null;

document.querySelectorAll('input[name=source_type]').forEach(r => r.addEventListener('change', () => {
  const isGh = document.querySelector('input[name=source_type]:checked').value === 'github';
  document.getElementById('valueLabel').textContent = isGh ? 'Repository URL' : 'Local folder path';
  document.getElementById('value').placeholder = isGh ? 'https://github.com/owner/repo.git' : '/path/to/your/project';
}));

function esc(s) { const d = document.createElement('div'); d.textContent = (s === undefined || s === null) ? '' : String(s); return d.innerHTML; }
function li(items) { return (items && items.length) ? items.map(i => `<li>${esc(i)}</li>`).join('') : '<li><em>none</em></li>'; }

async function runDevPilot() {
  const sourceType = document.querySelector('input[name=source_type]:checked').value;
  const value = document.getElementById('value').value.trim();
  const prDiff = document.getElementById('prDiff').value.trim();
  if (!value) { alert('Enter a URL or path first.'); return; }

  document.getElementById('runBtn').disabled = true;
  document.getElementById('logCard').style.display = 'block';
  document.getElementById('liveBadge').style.display = 'inline-flex';
  document.getElementById('log').innerHTML = '';
  document.getElementById('reportRoot').innerHTML = '';

  const resp = await fetch('/api/run', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({source_type: sourceType, value, pr_diff: prDiff}),
  });
  const data = await resp.json();
  if (!resp.ok) { appendLine('ERROR: ' + data.error); document.getElementById('runBtn').disabled = false; return; }

  jobId = data.job_id;
  let shown = 0;
  poller = setInterval(async () => {
    const r = await fetch('/api/status/' + jobId);
    const s = await r.json();
    for (; shown < s.log.length; shown++) appendLine('[' + s.log[shown].t + 's] ' + s.log[shown].msg);
    if (s.status === 'done') {
      clearInterval(poller);
      document.getElementById('runBtn').disabled = false;
      document.getElementById('liveBadge').style.display = 'none';
      const rep = await fetch('/api/report_json/' + jobId);
      currentReport = await rep.json();
      renderReport(currentReport);
    } else if (s.status === 'error') {
      clearInterval(poller);
      document.getElementById('runBtn').disabled = false;
      document.getElementById('liveBadge').style.display = 'none';
      document.getElementById('reportRoot').innerHTML = `<div class="card"><div class="error-box">ERROR: ${esc(s.error)}</div></div>`;
    }
  }, 600);
}

function appendLine(text) {
  const div = document.createElement('div');
  div.className = 'line';
  div.textContent = text;
  document.getElementById('log').appendChild(div);
  document.getElementById('log').scrollTop = document.getElementById('log').scrollHeight;
}

function findingRow(f) {
  const badge = f.confirmed ? '<span class="tag confirmed">CONFIRMED</span>' : '<span class="tag flagged">flagged</span>';
  const loc = f.file + (f.line !== undefined ? (':' + f.line) : (f.function ? (' :: ' + f.function) : ''));
  return `<tr class="sev-${f.severity}"><td>${esc(f.severity.toUpperCase())}</td><td>${esc(loc)}</td><td>${esc(f.message)}</td><td>${badge}</td></tr>`;
}

const BR_COLORS = {high: 'FF8FC7', medium: 'F2C94C', low: 'A78BFA'};

function blastRadiusSvg(entry) {
  const deps = entry.dependents || [];
  const color = BR_COLORS[entry.severity] || 'A78BFA';
  const size = 240, cx = size / 2, cy = size / 2, ringR = 84, nodeR = 15, centerR = 30;
  let lines = '', nodes = '';
  const n = deps.length;
  deps.forEach((dep, i) => {
    const angle = n ? (2 * Math.PI * i / n) - Math.PI / 2 : 0;
    const x = cx + ringR * Math.cos(angle), y = cy + ringR * Math.sin(angle);
    lines += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#${color}" stroke-width="1.5" stroke-opacity="0.55"/>`;
    const short = dep.split('/').pop();
    nodes += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${nodeR}" fill="#FFFFFF" stroke="#${color}" stroke-width="2"/>`
      + `<text x="${x.toFixed(1)}" y="${(y + nodeR + 13).toFixed(1)}" text-anchor="middle" font-size="9.5" fill="#6b6483">${esc(short)}</text>`;
  });
  const centerLabel = entry.file.split('/').pop();
  return `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" xmlns="http://www.w3.org/2000/svg">`
    + lines + nodes
    + `<circle cx="${cx}" cy="${cy}" r="${centerR}" fill="#${color}" fill-opacity="0.18" stroke="#${color}" stroke-width="2.5"/>`
    + `<text x="${cx}" y="${cy - 2}" text-anchor="middle" font-size="11" font-weight="700" fill="#3B3350">${esc(centerLabel)}</text>`
    + `<text x="${cx}" y="${cy + 13}" text-anchor="middle" font-size="9" fill="#6b6483">${entry.dependent_count} dependent(s)</text>`
    + `</svg>`;
}

function dependencyMapSvg(graph) {
  const nodes = graph.nodes || [], edges = graph.edges || [];
  const n = nodes.length;
  if (n === 0) return '<p><em>No Python modules found to map.</em></p>';
  const size = 460, cx = size / 2, cy = size / 2, r = size / 2 - 60;
  const pos = {};
  nodes.forEach((node, i) => {
    const angle = 2 * Math.PI * i / n - Math.PI / 2;
    pos[node] = [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
  });
  let lines = '', dots = '';
  edges.forEach(e => {
    if (pos[e.from] && pos[e.to]) {
      const [x1, y1] = pos[e.from], [x2, y2] = pos[e.to];
      lines += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="#A78BFA" stroke-width="1" stroke-opacity="0.32"/>`;
    }
  });
  nodes.forEach(node => {
    const [x, y] = pos[node];
    const short = node.split('/').pop();
    dots += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="6" fill="#FF8FC7" stroke="#fff" stroke-width="1.5"/>`;
    dots += `<text x="${x.toFixed(1)}" y="${(y - 10).toFixed(1)}" font-size="8" fill="#6b6483" text-anchor="middle">${esc(short)}</text>`;
  });
  return `<svg viewBox="0 0 ${size} ${size}" width="100%" height="${size}" xmlns="http://www.w3.org/2000/svg">${lines}${dots}</svg>`;
}

function sparklineSvg(series) {
  if (!series || series.length < 2) return '<p><em>Not enough runs yet for a trend line.</em></p>';
  const w = 420, h = 90, pad = 12;
  const scores = series.map(p => p.score);
  const lo = Math.min(...scores), hi = Math.max(...scores);
  const span = (hi - lo) || 1;
  const step = (w - 2 * pad) / (scores.length - 1);
  const pts = scores.map((s, i) => [pad + i * step, h - pad - ((s - lo) / span) * (h - 2 * pad)]);
  const path = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const dots = pts.map(([x, y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" fill="#A78BFA"/>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" xmlns="http://www.w3.org/2000/svg"><path d="${path}" fill="none" stroke="#FF8FC7" stroke-width="2.5"/>${dots}</svg>`;
}

function animateScore(el, target) {
  let current = 0;
  const step = Math.max(1, Math.round(target / 20));
  const timer = setInterval(() => {
    current = Math.min(target, current + step);
    el.textContent = current;
    if (current >= target) clearInterval(timer);
  }, 20);
}

function renderReport(report) {
  const r = report.results;
  const onboarding = r.onboarding, review = r.code_review, testing = r.testing, release = r.release_readiness, modernization = r.modernization;
  const bv = report.business_value, confirmed = report.confirmed_findings || [], health = report.health || {score:0,label:'n/a',color:'5B6B8C',deductions:[]};
  const execSum = (report.executive_summary || {}).summary || '';
  const execSrc = (report.executive_summary || {}).source || 'template';

  const archRows = (onboarding.architecture_map || []).map(m =>
    `<tr><td>${esc(m.file)}</td><td>${m.functions}</td><td>${m.classes}</td><td>${m.documented ? 'yes' : '<span style="color:#ff6b6b">no</span>'}</td></tr>`
  ).join('') || `<tr><td colspan="4"><em>none</em></td></tr>`;

  const findingsRows = (review.findings || []).map(findingRow).join('') || `<tr><td colspan="4"><em>No findings</em></td></tr>`;

  const uncoveredRows = (testing.uncovered_endpoints || []).map(e =>
    `<tr><td>${esc(e.method.toUpperCase())}</td><td>${esc(e.path)}</td><td>${esc(e.source_file)}</td></tr>`
  ).join('') || `<tr><td colspan="3"><em>Full coverage</em></td></tr>`;

  const checklistRows = (release.deployment_checklist || []).map(c =>
    `<tr><td>${esc(c.item)}</td><td>${esc(c.status)}</td></tr>`
  ).join('');

  const depFlags = (release.dependency_flags || []).map(d =>
    `<li><strong>${esc(d.package)}==${esc(d.pinned_version)}</strong>: ${esc(d.note)}</li>`
  ).join('') || '<li><em>No flagged dependencies</em></li>';

  const modRows = (modernization.findings || []).map(m =>
    `<tr><td>${esc(m.type)}</td><td>${esc(m.file)}${m.function ? ' :: ' + esc(m.function) : ''}</td><td>${esc(m.suggestion)}</td></tr>`
  ).join('') || `<tr><td colspan="3"><em>No modernization findings</em></td></tr>`;

  const confirmedHtml = confirmed.map(c =>
    `<li><strong>${esc(c.file)}</strong> (${esc(c.function||'')}) -- ${esc(c.message)} <em>${esc(c.corroborated_by)}</em></li>`
  ).join('') || '<li><em>No cross-agent confirmations this run</em></li>';

  const deductionsHtml = (health.deductions || []).map(d =>
    `<li>${esc(d.reason)} <strong>-${d.points}</strong></li>`
  ).join('') || '<li><em>No deductions -- nothing dragged the score down this run.</em></li>';

  const testRun = report.generated_test_run || {};
  const testRunHtml = testRun.ran
    ? `Ran generated tests: ${(testRun.failed_tests||[]).length} failed of the newly generated stubs.`
    : `Generated tests not executed (${esc(testRun.reason||'n/a')}).`;

  const qaHtml = (report.suggested_qa || []).map(qa =>
    `<div class="qa"><div class="q">Q: ${esc(qa.question)}</div><div class="a">A: ${esc(qa.answer)} <span class="src">[${esc(qa.source)}]</span></div></div>`
  ).join('');

  const auto_fix = r.auto_fix || {};
  const fixHtml = (auto_fix.candidate_fixes || []).map(c =>
    `<div class="fixcard"><div class="fixfile">${esc(c.file)}</div><div class="fixlist">${c.fixes.map(esc).join('; ')}</div><pre class="diff">${esc(c.diff)}</pre></div>`
  ).join('') || `<p><em>No safe, mechanical fixes available for this run -- everything DevPilot found either needs human judgment or is already clean.</em></p>`;

  const goBadge = release.recommendation === 'GO' ? 'go' : 'nogo';
  const canFix = (auto_fix.candidate_fixes || []).length > 0;

  const brHtml = (report.blast_radius || []).map(e => `
    <div class="brcard">
      <div>${blastRadiusSvg(e)}</div>
      <div>
        <span class="tag ${e.confirmed ? 'confirmed' : 'flagged'}">${e.confirmed ? 'CONFIRMED' : 'flagged'}</span>
        <span class="tag" style="background:transparent;color:${e.severity==='high'?'#E0558F':e.severity==='medium'?'#C99A1E':'#8C82A6'}">${esc(e.severity.toUpperCase())}</span>
        <div class="brfile">${esc(e.file)}${e.function ? ' :: ' + esc(e.function) : ''}</div>
        <div class="brmsg">${esc(e.message)}</div>
        <div class="brdeps">${e.dependents.length ? `If this breaks, it directly affects <strong>${e.dependent_count}</strong> other file(s): ${e.dependents.map(esc).join(', ')}` : 'No other file in this repo imports it directly -- limited blast radius.'}</div>
      </div>
    </div>`
  ).join('') || '<p><em>No confirmed or high/medium findings to map this run.</em></p>';

  const videoHtml = report._has_demo_video
    ? `<video controls src="/demo_video.mp4"></video>`
    : `<div class="demo-box">No demo_video.mp4 found next to webui.py.<br>Drop one in the DevPilot folder (same place as webui.py) and reload this page to have it play right here.</div>`;

  const pmHtml = (report.premortem || []).map(sc => `
    <div class="pmcard">
      <div class="pmtitle">${esc(sc.title)} <span class="tag ${sc.confirmed ? 'confirmed' : 'flagged'}">${sc.confirmed ? 'CONFIRMED' : 'flagged'}</span></div>
      <p class="pmnarrative">${esc(sc.narrative)}</p>
      <ol class="pmtimeline">${sc.timeline.map(s => `<li>${esc(s.split('. ').slice(1).join('. '))}</li>`).join('')}</ol>
      <p class="pmimpact">${esc(sc.impact)}</p>
      <p class="pmprevent"><strong>Prevention:</strong> ${esc(sc.prevention)}</p>
      <span class="src-tag">Generated by ${esc(sc.source)}</span>
    </div>`
  ).join('') || '<p><em>No confirmed or high/medium findings to write a pre-mortem for this run.</em></p>';

  const execIntel = report.executive_intelligence || {};
  const portfolioRows = (execIntel.risk_portfolio || []).map(p => `
    <tr><td><span class="tag ${p.confirmed ? 'confirmed' : 'flagged'}">${p.confirmed ? 'CONFIRMED' : 'flagged'}</span></td>
    <td class="sev-${p.severity}">${esc(p.severity.toUpperCase())}</td>
    <td>${esc(p.file)}${p.function ? ' :: ' + esc(p.function) : ''}</td>
    <td>${p.dependent_count}</td><td>${esc(p.message)}</td></tr>`
  ).join('') || `<tr><td colspan="5"><em>No standout risks this run</em></td></tr>`;

  const hist = report.history || {};
  const trendWord = hist.trend || 'n/a';
  const trendClass = trendWord.includes(' ') ? 'na' : trendWord;
  const deltaHtml = hist.score_delta !== null && hist.score_delta !== undefined
    ? `${hist.score_delta > 0 ? '+' : ''}${hist.score_delta} vs. the previous run` : 'no previous run to compare yet';
  const recurringHtml = (hist.recurring_rules || []).map(rr =>
    `<li><strong>${esc(rr.rule)}</strong> -- seen in ${rr.seen_in_runs} of the last ${hist.runs_recorded} runs</li>`
  ).join('') || '<li><em>No rule has recurred across multiple runs yet.</em></li>';

  const depMap = report.dependency_map || {nodes: [], edges: []};
  const depMapCaption = `Showing ${(depMap.nodes||[]).length} of ${depMap.total_files||0} Python modules` + (depMap.truncated ? ' (truncated for readability)' : '');

  document.getElementById('reportRoot').innerHTML = `
  <div class="card hero">
    <div class="gauge" style="background: conic-gradient(#${health.color} ${health.score*3.6}deg, #F0EAFB 0deg)">
      <div class="gauge-inner"><div class="score" id="gaugeScore" style="color:#${health.color}">0</div><div class="label">${esc(health.label)}</div></div>
    </div>
    <div>
      <h2>Executive summary</h2>
      <p>${esc(execSum)}</p>
      <span class="src-tag">Generated by ${esc(execSrc)} &middot; ${report.agents_run_in_parallel.length} subagents in parallel, ${report.wall_clock_seconds}s wall-clock vs ${report.sequential_equivalent_seconds}s sequential &middot; ${testRunHtml}</span>
    </div>
  </div>

  <div class="grid5">
    <div class="stat"><div class="n">${onboarding.modules_scanned}</div><div class="l">modules mapped</div></div>
    <div class="stat"><div class="n">${review.counts.high}H / ${review.counts.medium}M</div><div class="l">review findings (${confirmed.length} confirmed)</div></div>
    <div class="stat"><div class="n">${testing.headline_coverage_pct}%</div><div class="l">${esc(testing.headline_label)}</div></div>
    <div class="stat"><div class="n"><span class="badge ${goBadge}">${release.recommendation}</span></div><div class="l">release recommendation</div></div>
    <div class="stat"><div class="n">${bv.total_hours_saved}h</div><div class="l">estimated hours saved</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" data-tab="all">Full Report</div>
    <div class="tab" data-tab="dev">New Developer</div>
    <div class="tab" data-tab="rev">Reviewer</div>
    <div class="tab" data-tab="test">Tester</div>
    <div class="tab" data-tab="rel">Release Manager</div>
    <div class="tab" data-tab="fix">Auto-Fix</div>
    <div class="tab" data-tab="br">Blast Radius</div>
    <div class="tab" data-tab="pm">Pre-Mortem</div>
    <div class="tab" data-tab="exec">Executive Intelligence</div>
    <div class="tab" data-tab="hist">History</div>
    <div class="tab" data-tab="depmap">Dependency Map</div>
    <div class="tab" data-tab="ask">Ask DevPilot</div>
    <div class="tab" data-tab="demo">Demo Video</div>
  </div>

  <div id="panel-all" class="panel active">
    <h2 class="section">1. Onboarding</h2>
    <p>${onboarding.modules_scanned} modules scanned, ${onboarding.total_loc} lines. README: ${onboarding.has_readme ? 'yes' : 'no'} &middot; Dockerfile: ${onboarding.has_dockerfile ? 'yes' : 'no'}</p>
    <table><tr><th>File</th><th>Functions</th><th>Classes</th><th>Documented</th></tr>${archRows}</table>
    <strong>Suggested starter tasks</strong><ul class="plain">${li(onboarding.starter_tasks)}</ul>

    <h2 class="section">2. Code review -- ${esc(review.summary)}</h2>
    <table><tr><th>Severity</th><th>Location</th><th>Finding</th><th>Status</th></tr>${findingsRows}</table>
    <strong>Cross-agent confirmed findings</strong><ul class="plain">${confirmedHtml}</ul>

    <h2 class="section">3. Testing -- ${testing.headline_coverage_pct}% ${esc(testing.headline_label)}</h2>
    <table><tr><th>Method</th><th>Path</th><th>File</th></tr>${uncoveredRows}</table>

    <h2 class="section">4. Release readiness -- ${release.recommendation}</h2>
    <strong>Dependency advisories</strong><ul class="plain">${depFlags}</ul>
    <table><tr><th>Check</th><th>Status</th></tr>${checklistRows}</table>

    <h2 class="section">5. Modernization</h2>
    <p>${esc(modernization.summary)}</p>
    <table><tr><th>Type</th><th>Location</th><th>Suggestion</th></tr>${modRows}</table>

    <h2 class="section">6. Business value</h2>
    <p><strong>${bv.total_hours_saved} hours</strong> estimated saved this run: ${esc(bv.breakdown_summary)}.</p>
    <p><em>${esc(bv.caveat)}</em></p>

    <h2 class="section">7. Health score breakdown</h2>
    <ul class="plain">${deductionsHtml}</ul>
  </div>

  <div id="panel-dev" class="panel"><h2 class="section">For a new developer</h2>
    <strong>Setup</strong><ul class="plain">${li(onboarding.setup_steps)}</ul>
    <strong>Architecture</strong><table><tr><th>File</th><th>Functions</th><th>Classes</th><th>Documented</th></tr>${archRows}</table>
    <strong>Start here</strong><ul class="plain">${li(onboarding.starter_tasks)}</ul>
  </div>

  <div id="panel-rev" class="panel"><h2 class="section">For a code reviewer</h2>
    <p>${esc(review.summary)}</p>
    <table><tr><th>Severity</th><th>Location</th><th>Finding</th><th>Status</th></tr>${findingsRows}</table>
    <strong>Confirmed by execution</strong><ul class="plain">${confirmedHtml}</ul>
  </div>

  <div id="panel-test" class="panel"><h2 class="section">For a tester</h2>
    <p>${testing.headline_coverage_pct}% ${esc(testing.headline_label)} &middot; ${testRunHtml}</p>
    <table><tr><th>Method</th><th>Path</th><th>File</th></tr>${uncoveredRows}</table>
  </div>

  <div id="panel-rel" class="panel"><h2 class="section">For a release manager</h2>
    <p><span class="badge ${goBadge}">${release.recommendation}</span></p>
    <strong>Dependency advisories</strong><ul class="plain">${depFlags}</ul>
    <table><tr><th>Check</th><th>Status</th></tr>${checklistRows}</table>
    <p><strong>${bv.total_hours_saved}h</strong> estimated saved: ${esc(bv.breakdown_summary)}.</p>
  </div>

  <div id="panel-fix" class="panel">
    <h2 class="section">Auto-Fix</h2>
    <p class="hint">Preview below is safe to read -- nothing is written until you confirm below. Applying commits locally to a new branch (<code>devpilot/auto-fixes</code>) and never pushes; opening a PR is always a separate step you run yourself with your own GitHub token.</p>
    ${fixHtml}
    ${canFix ? `
    <button id="applyFixBtn" onclick="showFixConfirm()">Make it flawless -- apply these fixes locally</button>
    <div id="fixConfirmBox" class="fixconfirm" style="display:none">
      <p><strong>About to write to disk and commit locally:</strong></p>
      <ul class="plain">${(auto_fix.candidate_fixes||[]).map(c => `<li>${esc(c.file)} -- ${c.fixes.map(esc).join('; ')}</li>`).join('')}</ul>
      <p>This creates/updates local branch <code>devpilot/auto-fixes</code> and runs the test suite. <strong>It will not push to GitHub or open a PR</strong> -- that always requires running <code>push_and_pr.py --confirm</code> yourself with your own token.</p>
      <label class="checkline"><input type="checkbox" id="fixAck"> I've reviewed the diff(s) above and want to apply them now.</label>
      <div class="fixconfirm-actions">
        <button class="secondary" onclick="hideFixConfirm()">Cancel</button>
        <button id="fixConfirmBtn" onclick="applyFix()" disabled>Yes, apply these fixes now</button>
      </div>
    </div>
    ` : ''}
    <div id="fixResultBox"></div>
  </div>

  <div id="panel-br" class="panel">
    <h2 class="section">Blast Radius -- if this breaks, what else does?</h2>
    <p class="hint">For each top finding, DevPilot searches the real import graph of this repo for files that depend on it -- a concrete, computed signal, not a simulation.</p>
    ${brHtml}
  </div>

  <div id="panel-pm" class="panel">
    <h2 class="section">Pre-Mortem Intelligence -- how this could fail</h2>
    <p class="hint">Plain-English failure scenarios for the top findings, built from this run's own severity, message, and blast-radius data -- not a simulation trained on incident history.</p>
    ${pmHtml}
  </div>

  <div id="panel-exec" class="panel">
    <h2 class="section">Executive Intelligence</h2>
    <div class="exec-card">
      <div class="exec-grid">
        <div><div class="n">${execIntel.health_score ?? '?'}/100</div><div class="l">health score (${esc(execIntel.health_label||'n/a')})</div></div>
        <div><div class="n">${esc(execIntel.release_recommendation||'?')}</div><div class="l">release recommendation</div></div>
        <div><div class="n">${execIntel.hours_saved ?? '?'}h</div><div class="l">estimated hours saved</div></div>
        <div><div class="n">${(execIntel.risk_portfolio||[]).length}</div><div class="l">standout risks this run</div></div>
      </div>
    </div>
    <h2 class="section">Risk portfolio</h2>
    <table><tr><th>Status</th><th>Severity</th><th>Location</th><th>Blast radius</th><th>Finding</th></tr>${portfolioRows}</table>
    <p class="exec-caveat">${esc(execIntel.caveat||'')}</p>
  </div>

  <div id="panel-hist" class="panel">
    <h2 class="section">Engineering Historian</h2>
    <p>${hist.runs_recorded||1} run(s) recorded for this project &middot; trend: <span class="hist-trend ${trendClass}">${esc(trendWord)}</span> (${esc(deltaHtml)})</p>
    ${sparklineSvg(hist.series)}
    <h2 class="section">Recurring patterns</h2>
    <p class="hint">Rules that keep showing up run after run -- these are architectural weaknesses, not one-off mistakes.</p>
    <ul class="plain">${recurringHtml}</ul>
  </div>

  <div id="panel-depmap" class="panel">
    <h2 class="section">Dependency Map</h2>
    <p class="hint">Every module in this repo and who imports whom -- the same real import-graph scan behind Blast Radius, applied to the whole codebase. ${esc(depMapCaption)}.</p>
    ${dependencyMapSvg(depMap)}
  </div>

  <div id="panel-ask" class="panel">
    <h2 class="section">Ask DevPilot</h2>
    ${qaHtml}
    <div id="askThread"></div>
    <form id="askForm" onsubmit="return askQuestion(event)">
      <input type="text" id="askInput" placeholder="Ask anything about this run -- e.g. 'is this safe to ship?'">
      <button type="submit">Ask</button>
    </form>
  </div>

  <div id="panel-demo" class="panel">
    <h2 class="section">Demo Video</h2>
    ${videoHtml}
  </div>
  `;

  document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
    tab.classList.add('active');
  }));

  const gaugeEl = document.getElementById('gaugeScore');
  if (gaugeEl) animateScore(gaugeEl, health.score);
}

async function askQuestion(evt) {
  evt.preventDefault();
  const input = document.getElementById('askInput');
  const question = input.value.trim();
  if (!question) return false;
  const thread = document.getElementById('askThread');
  const pending = document.createElement('div');
  pending.className = 'qa';
  pending.innerHTML = `<div class="q">Q: ${esc(question)}</div><div class="a"><em>thinking ...</em></div>`;
  thread.appendChild(pending);
  input.value = '';
  input.disabled = true;

  try {
    const resp = await fetch('/api/ask/' + jobId, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question}),
    });
    const data = await resp.json();
    pending.innerHTML = `<div class="q">Q: ${esc(question)}</div><div class="a">A: ${esc(data.answer)} <span class="src">[${esc(data.source)}]</span></div>`;
  } catch (e) {
    pending.innerHTML = `<div class="q">Q: ${esc(question)}</div><div class="a">Could not reach DevPilot: ${esc(String(e))}</div>`;
  }
  input.disabled = false;
  return false;
}

function showFixConfirm() {
  document.getElementById('applyFixBtn').style.display = 'none';
  document.getElementById('fixConfirmBox').style.display = 'block';
}

function hideFixConfirm() {
  document.getElementById('fixConfirmBox').style.display = 'none';
  document.getElementById('fixAck').checked = false;
  document.getElementById('fixConfirmBtn').disabled = true;
  document.getElementById('applyFixBtn').style.display = 'inline-block';
}

document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'fixAck') {
    document.getElementById('fixConfirmBtn').disabled = !e.target.checked;
  }
});

async function applyFix() {
  const ack = document.getElementById('fixAck');
  if (!ack || !ack.checked) return;  // belt-and-suspenders; button is disabled until checked

  const confirmBtn = document.getElementById('fixConfirmBtn');
  confirmBtn.disabled = true;
  confirmBtn.textContent = 'Applying ...';
  const box = document.getElementById('fixResultBox');
  box.innerHTML = '';
  try {
    const resp = await fetch('/api/fix/' + jobId, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) {
      box.innerHTML = `<div class="fixresult bad">ERROR: ${esc(data.error)}</div>`;
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Yes, apply these fixes now';
      return;
    }
    const t = data.test_run || {};
    const testLine = t.ran ? (t.passed ? 'Tests passed after the fix.' : 'Tests still failing after the fix -- review before merging.') : `Tests not run (${esc(t.reason||'n/a')}).`;
    const gitLine = data.git_committed
      ? `Committed locally on branch <code>${esc(data.git_branch)}</code>. Nothing pushed.`
      : `Files were changed on disk but the local commit failed${data.git_error ? ': ' + esc(data.git_error) : '.'} (is this a git repo?)`;
    box.innerHTML = `<div class="fixresult ${t.ran && !t.passed ? 'bad' : ''}"><strong>${data.files_changed} file(s) changed.</strong><br>${testLine}<br>${gitLine}</div>`;
    document.getElementById('fixConfirmBox').style.display = 'none';
    confirmBtn.textContent = 'Applied';
  } catch (e) {
    box.innerHTML = `<div class="fixresult bad">Could not reach DevPilot: ${esc(String(e))}</div>`;
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Yes, apply these fixes now';
  }
}
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean; the browser console (above) is the UI

    def _send(self, code, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, INDEX_HTML, "text/html")
        elif path.startswith("/api/status/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._send(404, {"error": "unknown job"})
                return
            self._send(200, {"status": job["status"], "log": job["log"], "error": job.get("error")})
        elif path.startswith("/api/report_json/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or job.get("status") != "done":
                self._send(404, {"error": "report not ready"})
                return
            report = dict(job["report"])
            report["_has_demo_video"] = os.path.exists(DEMO_VIDEO_PATH) and os.path.getsize(DEMO_VIDEO_PATH) > 0
            self._send(200, report)
        elif path == "/demo_video.mp4":
            self._send_video()
        else:
            self._send(404, {"error": "not found"})

    def _send_video(self):
        if not os.path.exists(DEMO_VIDEO_PATH) or os.path.getsize(DEMO_VIDEO_PATH) == 0:
            self._send(404, {"error": "no demo_video.mp4 next to webui.py"})
            return
        size = os.path.getsize(DEMO_VIDEO_PATH)
        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if range_header and range_header.startswith("bytes="):
            try:
                rng = range_header.split("=", 1)[1].split("-")
                start = int(rng[0]) if rng[0] else 0
                end = int(rng[1]) if len(rng) > 1 and rng[1] else size - 1
                status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200
        chunk_len = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(chunk_len))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(DEMO_VIDEO_PATH, "rb") as f:
            f.seek(start)
            remaining = chunk_len
            while remaining > 0:
                data = f.read(min(65536, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return

        if path == "/api/run":
            source_type = body.get("source_type")
            value = (body.get("value") or "").strip()
            pr_diff = (body.get("pr_diff") or "").strip()
            if source_type not in ("github", "local") or not value:
                self._send(400, {"error": "source_type and value are required"})
                return

            job_id = uuid.uuid4().hex
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "status": "running", "log": [], "report": None, "error": None, "started": time.time(),
                    "project_path": None, "cloned": False,
                }
            threading.Thread(target=_run_job, args=(job_id, source_type, value, pr_diff), daemon=True).start()
            self._send(200, {"job_id": job_id})

        elif path.startswith("/api/ask/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or job.get("status") != "done":
                self._send(404, {"error": "no completed report for this job"})
                return
            question = (body.get("question") or "").strip()
            if not question:
                self._send(400, {"error": "question is required"})
                return
            result = ask.answer(question, job["report"])
            self._send(200, result)

        elif path.startswith("/api/fix/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or job.get("status") != "done":
                self._send(404, {"error": "no completed report for this job"})
                return
            project_path = job.get("project_path")
            if not project_path or not os.path.isdir(project_path):
                self._send(410, {"error": "the project's files are no longer available (clone may have been discarded or swept) -- run DevPilot again"})
                return
            try:
                result = fix_agent.run(project_path, apply=True)
            except Exception as e:
                self._send(500, {"error": str(e)})
                return
            with JOBS_LOCK:
                job["fix_result"] = result
            self._send(200, result)

        elif path.startswith("/api/discard/"):
            job_id = path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self._send(404, {"error": "unknown job"})
                    return
                if job.get("cloned") and job.get("project_path"):
                    shutil.rmtree(job["project_path"], ignore_errors=True)
                job["project_path"] = None
                job["cloned"] = False
            self._send(200, {"discarded": True})

        else:
            self._send(404, {"error": "not found"})


def main():
    default_port = int(os.environ.get("PORT", 8765))
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()

    threading.Thread(target=_sweep_loop, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"DevPilot web UI running at http://localhost:{args.port}")
    if not watsonx_client.available():
        print("(watsonx.ai not configured -- set WATSONX_API_KEY / WATSONX_PROJECT_ID in .env for AI-generated summaries.)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")


if __name__ == "__main__":
    main()
