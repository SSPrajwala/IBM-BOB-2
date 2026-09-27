# Session 1 — 09:15, 27-09-2026
## Scaffold the demo project

**Goal:** Before DevPilot could analyze anything, it needed a real,
deployable target with real (seeded) bugs to catch — not a toy snippet.

**What was built:** TaskFlow-API — a FastAPI + SQLite task-management
service with a Dockerfile, a partial test suite (2 of 10 endpoints
covered), and a pending feature-branch diff (`pr/pr-142-search-by-owner.diff`)
for DevPilot to review. Five issues were seeded deliberately, one per
subagent DevPilot would eventually run: a SQL query built with an f-string
in `search_by_owner`, two endpoints that skip a null-check on `get_task`
and `get_user`, a `PyYAML==5.3` pin with a known load-advisory, and six
undocumented modules.

**Outcome:** A working, runnable service (`uvicorn app.main:app`) that
looks and behaves like a real small production API — the same repo used
in every DevPilot demo run since.
