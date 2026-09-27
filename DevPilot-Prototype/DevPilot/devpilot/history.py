"""Engineering Historian: turns each run into institutional knowledge.

Every run appends to a small local log for this project (keyed by its repo
URL if it was cloned, or its local path otherwise), so a health-score trend
and recurring architectural weaknesses survive across sessions instead of
being re-discovered -- and re-argued about -- every time someone runs the
tool. Pure local JSON; no external database.
"""
import hashlib
import json
import os
import time
from collections import Counter

HISTORY_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".devpilot_history"))
MAX_ENTRIES = 50


def _key(identifier):
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]


def _path(identifier):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    return os.path.join(HISTORY_DIR, f"{_key(identifier)}.json")


def load(identifier):
    p = _path(identifier)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _save(identifier, entries):
    try:
        with open(_path(identifier), "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except OSError:
        pass  # history is a nice-to-have; never fail a run over it


def record(identifier, report):
    entries = load(identifier)
    rules = sorted({f["rule"] for f in report["results"]["code_review"]["findings"]})
    entries.append({
        "timestamp": time.time(),
        "project": report["project"],
        "score": report["health"]["score"],
        "label": report["health"]["label"],
        "high": report["results"]["code_review"]["counts"]["high"],
        "medium": report["results"]["code_review"]["counts"]["medium"],
        "rules": rules,
    })
    entries = entries[-MAX_ENTRIES:]
    _save(identifier, entries)
    return entries


def analyze(entries):
    if len(entries) < 2:
        return {
            "runs_recorded": len(entries),
            "trend": "not enough runs yet -- run DevPilot on this project again to start a trend",
            "score_delta": None,
            "recurring_rules": [],
            "series": [{"t": e["timestamp"], "score": e["score"]} for e in entries],
        }
    prev, latest = entries[-2], entries[-1]
    delta = latest["score"] - prev["score"]
    trend = "improving" if delta > 0 else ("declining" if delta < 0 else "unchanged")

    rule_counts = Counter(r for e in entries for r in e.get("rules", []))
    threshold = max(2, len(entries) // 2)
    recurring = [{"rule": r, "seen_in_runs": c} for r, c in rule_counts.items() if c >= threshold]
    recurring.sort(key=lambda x: -x["seen_in_runs"])

    return {
        "runs_recorded": len(entries),
        "trend": trend,
        "score_delta": delta,
        "recurring_rules": recurring[:8],
        "series": [{"t": e["timestamp"], "score": e["score"]} for e in entries],
    }


def record_and_analyze(identifier, report):
    entries = record(identifier, report)
    return analyze(entries)
