# DevPilot

A multi-agent developer-workflow copilot prototype built for the IBM Bob 2.0 hackathon.

DevPilot fans a single request ("assess this project") out to six subagents
that run **in parallel** -- onboarding, code review, testing, release
readiness, legacy modernization, and auto-fix -- then reduces their output
into one consolidated, role-scoped report a human can act on immediately,
instead of running six separate tools by hand and stitching the results
together. Every run is also rolled into a single 0-100 health score and a
plain-English executive summary, so the headline is legible before anyone
reads a single table.

## Option A: the web UI (no terminal needed after this one command)

    cd DevPilot
    python3 webui.py

Then open **http://localhost:8765** in a browser. Paste a GitHub repo URL
(e.g. `https://github.com/miguelgrinberg/microblog.git`) or a local folder
path, click **Run DevPilot**, and watch the six subagents report in live as
they finish. The dashboard (health score, New Developer / Reviewer / Tester
/ Release Manager / Auto-Fix / Ask DevPilot tabs) renders natively on the
same page -- it's a real single-page app, not a static file embedded in an
iframe, which is what makes the next two things possible:

* **Ask DevPilot, for real.** Type any question in the Ask DevPilot tab
  ("is this safe to ship?", "what's the health score?", "tell me about the
  SQL injection risk") and get a live, grounded answer from
  `/api/ask/<job_id>` -- not a fixed FAQ.
* **"Make it flawless."** The Auto-Fix tab shows a diff preview for every
  safe, mechanical fix DevPilot found. Click the button, review the exact
  files and diffs in the confirmation panel, tick "I've reviewed the
  diff(s)", and it writes those changes to disk and commits them locally on
  a new `devpilot/auto-fixes` branch, then reports the test result. It
  never pushes anywhere -- that's still a separate, explicitly confirmed
  step (see `push_and_pr.py` below).
* **Blast Radius.** For the top confirmed/high/medium findings, DevPilot
  searches the repo's real import graph for every other file that depends
  on the buggy one, and draws it as a small radiating diagram -- "if this
  breaks, it takes N other files down with it," computed from the actual
  codebase, not simulated.
* **Pre-Mortem Intelligence.** For the same top findings, a plain-English
  failure timeline -- how the bug actually gets triggered, what happens
  next, and what it takes to prevent it -- built from this run's own
  severity/message/blast-radius data (optionally phrased by watsonx.ai).
* **Executive Intelligence.** A leadership-facing view: health score,
  release call, hours saved, and a ranked risk portfolio. It deliberately
  does **not** invent MTTR/MTBF/cost-of-downtime numbers -- those need real
  production incident telemetry a static-analysis tool doesn't have, and
  the tab says so outright instead of dressing up a guess as a metric.
* **Engineering Historian.** Every run is appended to a small local history
  log for that exact project (keyed by its repo URL or local path), so a
  score-trend sparkline and "which rules keep recurring across runs" build
  up automatically the more you use it -- no database, just local JSON.
* **Dependency Map.** The whole repo's real module-to-module import graph,
  drawn as a chord diagram -- the same reverse-dependency scan behind Blast
  Radius, applied to every file instead of just the top findings.
* **Demo Video tab.** Drop a `demo_video.mp4` next to `webui.py` (or next
  to a generated `devpilot_report.html`) and it plays right there in its
  own tab, with proper seek/scrub support -- no video, no problem, it just
  shows a friendly placeholder.
* **Cinematic touches.** Panels fade in on switch, the health-score gauge
  counts up on load, and a pulsing "LIVE" badge marks the agent log while a
  run is in progress.

A GitHub URL is cloned into a temp folder that's kept alive for the rest of
the session (so Ask/Fix have files to work with), auto-deleted after an
hour of inactivity by a background sweep, or removed immediately with the
Discard action. The whole UI runs on a modern light pastel theme (baby
pink / light green / light purple / lemon-yellow accents) with pill-shaped
tabs and rounded cards. It's stdlib-only -- nothing to `pip install` to
try it.

## Option B: the CLI

Assess a local project:

    python3 run_devpilot.py --project ../TaskFlow-API --pr-diff ../TaskFlow-API/pr/pr-142-search-by-owner.diff

Assess a real public GitHub repo (shallow-cloned automatically, and deleted
again once the report is written):

    python3 run_devpilot.py --repo-url https://github.com/miguelgrinberg/microblog.git

Apply DevPilot's safe, mechanical fixes and commit them locally (never
pushes -- see `push_and_pr.py` for that, which requires `--confirm` and your
own `GITHUB_TOKEN`):

    python3 run_devpilot.py --project ../TaskFlow-API --apply-fixes

Ask a question about the resulting report from the command line:

    python3 ask_devpilot.py --report devpilot_report.json "what should I fix first?"

Both entry points write `devpilot_report.json` and `devpilot_report.html`
(open the HTML file directly in a browser too, if you'd rather skip the
server), plus auto-generated pytest stubs written straight into the target
project's `tests/` folder.

## Optional: real watsonx.ai

Copy `.env.example` to `.env`, fill in `WATSONX_API_KEY` and
`WATSONX_PROJECT_ID` once you have IBM Cloud Lite credits, then `source .env`
before running either entry point above. Without it, every feature (Ask
DevPilot included) falls back to deterministic, rule-based text -- nothing
breaks either way.
