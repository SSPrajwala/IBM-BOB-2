"""Turns subagent findings into an estimated hours-saved figure.

These multipliers are an illustrative, clearly-labeled heuristic (typical
manual review/read/write times), not a measured or audited metric -- the
point is to make the report speak in terms a non-engineering stakeholder
can act on, not to claim false precision.
"""

RATES_HOURS = {
    "undocumented_module_read": 0.25,     # time to read one undocumented module unassisted
    "onboarding_baseline": 1.0,            # architecture map + setup guide, flat, when onboarding gaps exist
    "review_finding_high": 0.5,            # manual security-review time per high finding
    "review_finding_medium": 0.25,
    "test_stub_write": 0.25,               # time to hand-write one endpoint test
    "release_audit": 1.0,                  # manual release checklist + dependency audit, flat, if flags exist
    "modernization_item": 0.15,
}


def estimate(report):
    r = report["results"]
    breakdown = []
    total = 0.0

    onboarding = r.get("onboarding", {})
    undocumented = len(onboarding.get("undocumented_modules", []))
    if undocumented:
        hours = undocumented * RATES_HOURS["undocumented_module_read"] + RATES_HOURS["onboarding_baseline"]
        total += hours
        breakdown.append(f"{hours:.1f}h onboarding ({undocumented} undocumented modules + setup guide)")

    review = r.get("code_review", {})
    counts = review.get("counts", {})
    review_hours = counts.get("high", 0) * RATES_HOURS["review_finding_high"] + counts.get("medium", 0) * RATES_HOURS["review_finding_medium"]
    if review_hours:
        total += review_hours
        breakdown.append(f"{review_hours:.1f}h code review ({counts.get('high', 0)} high, {counts.get('medium', 0)} medium findings)")

    testing = r.get("testing", {})
    uncovered = len(testing.get("uncovered_endpoints", []))
    if uncovered:
        hours = uncovered * RATES_HOURS["test_stub_write"]
        total += hours
        breakdown.append(f"{hours:.1f}h test writing ({uncovered} stub tests auto-generated)")

    release = r.get("release_readiness", {})
    if release.get("dependency_flags") or release.get("recommendation") == "NO-GO":
        total += RATES_HOURS["release_audit"]
        breakdown.append(f"{RATES_HOURS['release_audit']:.1f}h release audit")

    modernization = r.get("modernization", {})
    mod_count = len(modernization.get("findings", []))
    if mod_count:
        hours = mod_count * RATES_HOURS["modernization_item"]
        total += hours
        breakdown.append(f"{hours:.1f}h modernization triage ({mod_count} item(s))")

    return {
        "total_hours_saved": round(total, 1),
        "breakdown": breakdown,
        "breakdown_summary": "; ".join(breakdown) if breakdown else "no gaps found this run",
        "caveat": "Illustrative estimate based on typical manual review/read/write times, not a measured or audited metric.",
    }
