"""Blast radius: for the most important findings, who else gets hurt?

A severity label ("high") tells you how bad a bug is in isolation. It
doesn't tell you how far the damage spreads if it fires. This module
answers that second question with a real (if heuristic) static-analysis
signal -- reverse-dependency search -- rather than a made-up number: for
each top finding, it finds every other file in the repo that imports the
buggy module, so "fix this first" comes with "...because N other files
depend on it."

This is intentionally a lightweight cousin of the "blast radius simulation"
concept from incident-prediction tooling -- built from data DevPilot already
collects (the repo's own import graph), not a fabricated cascading-failure
model.
"""
import os
import re

from devpilot.scanutil import iter_py_files, read_text

MAX_FINDINGS = 4


def _module_dotpath(rel_path):
    dotted = rel_path[:-3].replace(os.sep, ".").replace("/", ".")
    parent = dotted.rsplit(".", 1)[0] if "." in dotted else ""
    stem = dotted.rsplit(".", 1)[-1]
    return dotted, parent, stem


def _importer_pattern(dotted, parent, stem):
    parts = [
        rf"\bimport\s+{re.escape(dotted)}\b",
        rf"\bfrom\s+{re.escape(dotted)}\s+import\b",
    ]
    if parent:
        parts.append(rf"\bfrom\s+{re.escape(parent)}\s+import\s+[^\n]*\b{re.escape(stem)}\b")
    parts.append(rf"\bfrom\s+\.+\w*\s+import\s+[^\n]*\b{re.escape(stem)}\b")
    parts.append(rf"\bfrom\s+\.+{re.escape(stem)}\s+import\b")
    return re.compile("|".join(parts))


def _find_dependents(project_path, target_rel):
    dotted, parent, stem = _module_dotpath(target_rel)
    pattern = _importer_pattern(dotted, parent, stem)
    dependents = []
    for full, rel in iter_py_files(project_path, skip_tests=False):
        if rel == target_rel:
            continue
        if pattern.search(read_text(full)):
            dependents.append(rel)
    return sorted(dependents)


def _candidate_findings(report):
    confirmed = report.get("confirmed_findings", [])
    by_key = {}
    for f in confirmed:
        by_key[(f["file"], f.get("function"), f["rule"])] = {**f, "confirmed": True}
    for f in report["results"]["code_review"]["findings"]:
        if f["severity"] not in ("high", "medium"):
            continue
        key = (f["file"], f.get("function"), f["rule"])
        if key not in by_key:
            by_key[key] = {**f, "confirmed": f.get("confirmed", False)}

    ordered = list(by_key.values())
    rank = {"high": 0, "medium": 1, "low": 2}
    ordered.sort(key=lambda f: (not f.get("confirmed"), rank.get(f["severity"], 3)))
    return ordered[:MAX_FINDINGS]


MAX_GRAPH_NODES = 30


def full_graph(project_path, max_nodes=MAX_GRAPH_NODES):
    """The Nexus-style "dependency graph" concept, built the same honest
    way as blast radius: real reverse-dependency search across every module
    in the repo, not just the top findings. Capped so the diagram (and the
    O(n^2) scan behind it) stays readable and fast on a real-sized repo."""
    files = [rel for _, rel in iter_py_files(project_path, skip_tests=True)]
    truncated = len(files) > max_nodes
    files = files[:max_nodes]

    edges = []
    file_set = set(files)
    for rel in files:
        for dep in _find_dependents(project_path, rel):
            if dep in file_set:
                edges.append({"from": dep, "to": rel})

    return {"nodes": files, "edges": edges, "truncated": truncated, "total_files": len(list(iter_py_files(project_path, skip_tests=True)))}


def compute(project_path, report):
    findings = _candidate_findings(report)
    results = []
    for f in findings:
        dependents = _find_dependents(project_path, f["file"])
        results.append({
            "file": f["file"],
            "function": f.get("function"),
            "rule": f["rule"],
            "severity": f["severity"],
            "message": f["message"],
            "confirmed": bool(f.get("confirmed")),
            "dependents": dependents,
            "dependent_count": len(dependents),
        })
    results.sort(key=lambda r: -r["dependent_count"])
    return results
