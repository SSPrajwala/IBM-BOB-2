# Session 4 — 12:50, 27-09-2026
## Real single-page web UI

**Goal:** Replace a static report file with something live — a page that
can answer questions and apply fixes, not just display JSON.

**What was built:** `webui.py`, a small stdlib-only HTTP server
(`http.server` + `threading`) serving a real single-page app: paste a
GitHub URL or local path, click Run DevPilot, and watch the six subagents
report in live via a polling `/api/report_json/<job_id>` endpoint. Added
`/api/ask/<job_id>` for grounded Q&A and `/api/fix/<job_id>` for the
auto-fix flow — gated behind an explicit "I've reviewed the diff(s)"
checkbox that must be ticked before the confirm button enables at all.

**Outcome:** A live app, not an iframe around a static file — the first
version where Ask DevPilot and Make-it-flawless actually work from the
browser.
