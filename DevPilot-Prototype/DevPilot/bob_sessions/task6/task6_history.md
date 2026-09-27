# Session 6 — 15:00, 27-09-2026
## Blast Radius + Pre-Mortem Intelligence (merged from hackathon 1.0)

**Goal:** Bring over the strongest ideas from the earlier IBM Bob
hackathon build (Nexus-IntelliBob), adapted to data DevPilot actually has.

**What was built:** `devpilot/blast_radius.py` — a real reverse-dependency
search over the repo's own import graph, so a finding like
`tasks.py`'s SQL injection shows exactly which other files depend on it
(`main.py`, in TaskFlow-API's case) — not a simulated number.
`devpilot/premortem.py` turns the same top findings into a plain-English
failure timeline (how the bug gets triggered, what happens next, how to
prevent it), optionally phrased by watsonx.ai with a deterministic
fallback.

**Outcome:** The screenshot attached is the actual Blast Radius + Pre-Mortem
slide from the rebuilt deck, rendered straight from this run's real
`taskflow_report.json` — not a mockup.
