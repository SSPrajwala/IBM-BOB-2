# Session 8 — 16:45, 27-09-2026
## Dependency Map, Demo Video tab, pastel redesign + cinematic polish

**Goal:** Extend the same import scan repo-wide, add a home for a demo
video, and give the whole thing a friendlier look.

**What was built:** `blast_radius.full_graph()` — the whole repo's
module-to-module import graph rendered as a chord diagram (23 nodes / 29
edges on the microblog test repo). A Demo Video tab in `webui.py` with
real HTTP Range/206 support so a dropped-in `demo_video.mp4` actually
seeks and scrubs. The full UI — both the static report and the live
web UI — was redone in a pastel palette (baby pink, light green, light
purple, lemon yellow), with pill-shaped tabs, rounded cards, fade-in panel
transitions, a count-up health gauge, and a pulsing LIVE badge during a
run.

**Outcome:** Every tab from this point on (report and web UI) shares the
same modern, cute look — verified end-to-end against both TaskFlow-API and
a real cloned GitHub repo.
