"""Executive Intelligence: the same run, framed for a leader who skims.

Deliberately does NOT invent MTTR/MTBF/cost-of-downtime numbers -- those
require real production incident telemetry, which a static-analysis tool
run against source code doesn't have access to. Everything here is derived
honestly from this run's own findings, and the module says so explicitly
rather than dressing up an estimate as measured reliability data.
"""


def compute(report):
    r = report["results"]
    health = report["health"]
    bv = report["business_value"]
    release = r["release_readiness"]
    history = report.get("history", {})

    portfolio = [
        {
            "file": e["file"],
            "function": e.get("function"),
            "severity": e["severity"],
            "confirmed": e["confirmed"],
            "dependent_count": e["dependent_count"],
            "message": e["message"],
        }
        for e in report.get("blast_radius", [])
    ]

    return {
        "health_score": health["score"],
        "health_label": health["label"],
        "release_recommendation": release["recommendation"],
        "hours_saved": bv["total_hours_saved"],
        "risk_portfolio": portfolio,
        "trend": history.get("trend"),
        "score_delta": history.get("score_delta"),
        "runs_recorded": history.get("runs_recorded", 1),
        "caveat": (
            "Metrics like MTTR, MTBF, and cost-of-downtime need real production incident telemetry, which this "
            "static-analysis run doesn't have. Everything above comes directly from this run's own findings -- "
            "nothing here is a measured reliability statistic."
        ),
    }
