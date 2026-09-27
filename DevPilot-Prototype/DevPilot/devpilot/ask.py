"""Ask DevPilot: a small Q&A layer over a completed report.

Grounded in the actual report JSON -- never invents a finding. Uses
watsonx.ai to phrase (and, for open-ended questions, to reason a little
about) the answer when WATSONX_API_KEY/WATSONX_PROJECT_ID are set; falls
back to a deterministic templated answer otherwise, so a demo never
depends on having live credentials.
"""
from devpilot import watsonx_client

FIXED_QUESTIONS = [
    "What should I fix first?",
    "Is it safe to release?",
    "Where should a new engineer start?",
    "How much time did this run save?",
]


def _highest_priority_finding(report):
    r = report["results"]
    confirmed = report.get("confirmed_findings", [])
    if confirmed:
        return confirmed[0]
    findings = r.get("code_review", {}).get("findings", [])
    return findings[0] if findings else None


def _template_answer(question, report):
    r = report["results"]
    q = question.lower()

    if "fix first" in q or "prioriti" in q or "most urgent" in q or "worst" in q:
        f = _highest_priority_finding(report)
        if not f:
            return "No code-review findings on this run -- nothing blocking to fix."
        conf = " (confirmed by two independent subagents)" if f in report.get("confirmed_findings", []) else ""
        return f"Start with {f.get('file')}: {f.get('message', f.get('rule'))}{conf}."

    if "safe to release" in q or ("release" in q and "note" not in q):
        rel = r["release_readiness"]
        reasons = [d["note"] for d in rel["dependency_flags"]]
        return f"Recommendation: {rel['recommendation']}." + (f" Reason: {reasons[0]}" if reasons else " No blocking dependency issues.")

    if "new engineer" in q or "onboard" in q or ("start" in q and "restart" not in q):
        tasks = r["onboarding"]["starter_tasks"]
        return "Start here: " + (tasks[0] if tasks else "no starter tasks generated -- the codebase looks well-documented already.")

    if "time" in q or "save" in q or "hours" in q or "value" in q:
        bv = report.get("business_value", {})
        return f"Roughly {bv.get('total_hours_saved', '?')} hours of manual effort saved on this run ({bv.get('breakdown_summary', '')})."

    if "health" in q or "score" in q or "overall" in q or "how is" in q or "how's" in q:
        h = report.get("health", {})
        top_deduction = h.get("deductions", [{}])[0].get("reason") if h.get("deductions") else None
        return (
            f"Health score: {h.get('score', '?')}/100 ({h.get('label', 'n/a')})."
            + (f" Biggest drag on the score: {top_deduction}." if top_deduction else " No deductions this run.")
        )

    if "coverage" in q or "test" in q or "tested" in q:
        t = r["testing"]
        return f"{t.get('headline_coverage_pct', '?')}% {t.get('headline_label', '')}. {len(t.get('uncovered_endpoints', []))} endpoint(s) still uncovered."

    if "security" in q or "vulnerab" in q or "risk" in q or "cve" in q:
        counts = r["code_review"]["counts"]
        confirmed = report.get("confirmed_findings", [])
        return f"{counts['high']} high, {counts['medium']} medium, {counts['low']} low severity finding(s); {len(confirmed)} confirmed by a failing generated test, not just flagged once."

    if "moderniz" in q or "legacy" in q or "tech debt" in q or "technical debt" in q:
        m = r["modernization"]
        return m["summary"]

    if "sql" in q or "injection" in q:
        hits = [f for f in r["code_review"]["findings"] if f["rule"] == "sql-injection"]
        if not hits:
            return "No SQL-injection pattern detected in this run."
        return f"{len(hits)} SQL-injection-shaped finding(s), e.g. {hits[0]['file']}: {hits[0]['message']}"

    if "dependen" in q or "package" in q or "upgrade" in q:
        flags = r["release_readiness"]["dependency_flags"]
        if not flags:
            return "No flagged dependencies this run."
        d = flags[0]
        return f"{d['package']}=={d['pinned_version']}: {d['note']}"

    if "fix" in q and ("apply" in q or "auto" in q or "how many" in q or "candidate" in q):
        fixes = r.get("auto_fix", {}).get("candidate_fixes", [])
        if not fixes:
            return "No safe, mechanical auto-fix candidates this run."
        return f"{len(fixes)} file(s) have a candidate auto-fix ready to preview, e.g. {fixes[0]['file']} ({'; '.join(fixes[0]['fixes'])})."

    return (
        "I can answer questions about what to fix first, release safety, security findings, test coverage, "
        "modernization/tech debt, onboarding, health score, and time saved -- try rephrasing around one of those, "
        "or ask something more specific and I'll do my best with what this run found."
    )


def answer(question, report):
    template = _template_answer(question, report)
    if not watsonx_client.available():
        return {"answer": template, "source": "template"}

    prompt = (
        "You are DevPilot, an assistant summarizing a software project health report. "
        "Answer the developer's question in 2-3 plain sentences, using ONLY the facts given below. "
        "Do not invent numbers or findings that aren't listed.\n\n"
        f"Facts: {template}\n\nQuestion: {question}\nAnswer:"
    )
    generated = watsonx_client.generate(prompt, max_tokens=150)
    if generated:
        return {"answer": generated, "source": "watsonx.ai"}
    return {"answer": template, "source": "template (watsonx.ai call failed, fell back)"}


def suggested_qa(report):
    return [{"question": q, **answer(q, report)} for q in FIXED_QUESTIONS]
