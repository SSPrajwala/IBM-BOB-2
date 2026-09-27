"""One number and one paragraph a non-engineer can act on.

Everything else in the report is precise but plural -- five subagents,
a dozen tables. This module rolls all of it into a single 0-100 health
score plus a plain-English executive summary, the way a human lead would
open a status update: "here's the headline, here's why, here's the detail
if you want it."
"""

from devpilot import watsonx_client

RATING_BANDS = [
    (90, "Excellent", "3DBD8C"),
    (75, "Good", "6FCF97"),
    (55, "Needs Attention", "F2C94C"),
    (0, "Critical", "FF6F91"),
]


def _rating_for(score):
    for threshold, label, color in RATING_BANDS:
        if score >= threshold:
            return label, color
    return "Critical", "D64545"


def compute_score(report):
    r = report["results"]
    score = 100.0
    deductions = []

    review = r["code_review"]
    confirmed_keys = {(c["file"], c.get("function"), c["rule"]) for c in report.get("confirmed_findings", [])}
    sev_penalty = {"high": 12, "medium": 5, "low": 2}
    review_deduction = 0.0
    for f in review["findings"]:
        pen = sev_penalty.get(f["severity"], 2)
        if (f["file"], f.get("function"), f["rule"]) in confirmed_keys:
            pen += 3
        review_deduction += pen
    review_deduction = min(review_deduction, 45)
    if review_deduction:
        deductions.append(("Code review findings", review_deduction))
    score -= review_deduction

    testing = r["testing"]
    gap = max(0.0, 100.0 - (testing.get("headline_coverage_pct") or 100.0))
    test_deduction = min(gap * 0.15, 20)
    if test_deduction >= 1:
        deductions.append(("Test coverage gap", test_deduction))
    score -= test_deduction

    release = r["release_readiness"]
    release_deduction = 0.0
    if release["recommendation"] == "NO-GO":
        release_deduction += 15
    release_deduction += min(len(release["dependency_flags"]) * 5, 10)
    if release_deduction:
        deductions.append(("Release readiness", release_deduction))
    score -= release_deduction

    onboarding = r["onboarding"]
    onboarding_deduction = min(len(onboarding["undocumented_modules"]) * 0.5, 10)
    if onboarding_deduction >= 1:
        deductions.append(("Documentation gaps", onboarding_deduction))
    score -= onboarding_deduction

    modernization = r["modernization"]
    mod_deduction = min(len(modernization["findings"]) * 1.0, 10)
    if mod_deduction >= 1:
        deductions.append(("Modernization opportunities", mod_deduction))
    score -= mod_deduction

    score = max(0, min(100, round(score)))
    label, color = _rating_for(score)
    deductions.sort(key=lambda d: -d[1])
    return {
        "score": score,
        "label": label,
        "color": color,
        "deductions": [{"reason": reason, "points": round(pts, 1)} for reason, pts in deductions],
    }


def _template_summary(report, health):
    r = report["results"]
    project = report["project"]
    review = r["code_review"]
    release = r["release_readiness"]
    confirmed = report.get("confirmed_findings", [])
    bv = report.get("business_value", {})

    parts = [
        f"{project} scores {health['score']}/100 ({health['label']})."
    ]
    if confirmed:
        top = confirmed[0]
        parts.append(
            f"The most urgent issue is in {top['file']}"
            + (f" ({top['function']})" if top.get("function") else "")
            + f": {top['message']} This was independently confirmed by two subagents, not just flagged once."
        )
    elif review["counts"]["high"]:
        top = next(f for f in review["findings"] if f["severity"] == "high")
        parts.append(f"The most urgent issue is in {top['file']}: {top['message']}")
    else:
        parts.append("No high-severity code-review findings this run.")

    parts.append(
        f"Release recommendation: {release['recommendation']}."
        + (f" {release['dependency_flags'][0]['note']}" if release["dependency_flags"] else "")
    )
    if bv.get("total_hours_saved"):
        parts.append(f"This run is estimated to have saved about {bv['total_hours_saved']} hours of manual review, testing, and triage work.")

    return " ".join(parts)


def executive_summary(report, health):
    template = _template_summary(report, health)
    if not watsonx_client.available():
        return {"summary": template, "source": "template"}

    prompt = (
        "Rewrite the following software-project status update as a warm, plain-English paragraph "
        "(3-4 sentences) a non-engineer manager could understand. Do not invent any fact, number, "
        "or finding that isn't already present in the text below.\n\n" + template
    )
    generated = watsonx_client.generate(prompt, max_tokens=200)
    if generated:
        return {"summary": generated, "source": "watsonx.ai"}
    return {"summary": template, "source": "template (watsonx.ai call failed, fell back)"}
