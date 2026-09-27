"""Splits one assessment into the slice each audience actually needs.

A new developer doesn't need release-dependency advisories in their face,
and a release manager doesn't need the full architecture map. Same
underlying run, four honest, role-scoped views plus the full report.
"""


def build(report):
    r = report["results"]
    return {
        "new_developer": {
            "label": "New Developer",
            "headline": f"{r['onboarding']['modules_scanned']} modules mapped, {len(r['onboarding']['starter_tasks'])} starter tasks ready",
            "setup_steps": r["onboarding"]["setup_steps"],
            "architecture_map": r["onboarding"]["architecture_map"],
            "starter_tasks": r["onboarding"]["starter_tasks"],
            "ask": "Where should a new engineer start?",
        },
        "reviewer": {
            "label": "Code Reviewer",
            "headline": r["code_review"]["summary"],
            "findings": r["code_review"]["findings"],
            "confirmed_findings": report.get("confirmed_findings", []),
            "pr_diff_reviewed": r["code_review"].get("pr_diff_reviewed"),
            "ask": "What should I fix first?",
        },
        "tester": {
            "label": "Tester / QA",
            "headline": f"{r['testing']['headline_coverage_pct']}% {r['testing']['headline_label']}",
            "uncovered_endpoints": r["testing"].get("uncovered_endpoints", []),
            "function_coverage": r["testing"].get("function_coverage"),
            "generated_test_file": r["testing"].get("generated_test_file"),
            "test_run": report.get("generated_test_run"),
            "ask": "How much time did this run save?",
        },
        "release_manager": {
            "label": "Release Manager",
            "headline": f"Recommendation: {r['release_readiness']['recommendation']}",
            "dependency_flags": r["release_readiness"]["dependency_flags"],
            "deployment_checklist": r["release_readiness"]["deployment_checklist"],
            "release_notes": r["release_readiness"]["release_notes"],
            "business_value": report.get("business_value"),
            "ask": "Is it safe to release?",
        },
    }
