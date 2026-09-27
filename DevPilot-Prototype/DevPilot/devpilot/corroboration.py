"""Cross-agent trust scoring.

DevPilot's subagents work independently, but some of their findings are
about the same underlying bug seen from two different angles: the code
reviewer flags a function statically; the testing agent's auto-generated
test then actually fails against the running app. When that happens, the
finding is promoted from "flagged" (one opinion) to "confirmed" (corroborated
by execution) -- this is the noise-reduction mechanism, not just a second
report bolted onto the first.
"""
import re
import subprocess

FAILED_RE = re.compile(r"FAILED\s+\S+::(\w+)")


def run_generated_tests(project_path, generated_test_file):
    """Best-effort: run just the generated stub file with pytest and report
    which stubs failed. Never raises -- a dependency-less/unrunnable repo
    just means this step reports 'not run', not a crashed pipeline."""
    import os
    full_path = os.path.join(project_path, generated_test_file)
    if not os.path.exists(full_path):
        return {"ran": False, "reason": "no generated tests to run"}
    try:
        proc = subprocess.run(
            ["python3", "-m", "pytest", generated_test_file, "-q"],
            cwd=project_path, capture_output=True, text=True, timeout=60,
        )
        failed = FAILED_RE.findall(proc.stdout + proc.stderr)
        return {"ran": True, "failed_tests": failed, "raw_tail": "\n".join((proc.stdout + proc.stderr).splitlines()[-20:])}
    except Exception as e:
        return {"ran": False, "reason": str(e)}


def correlate(report, failed_test_names):
    """Returns (confirmed_findings, annotated_review_findings)."""
    testing = report["results"].get("testing", {})
    stub_map = testing.get("stub_name_to_endpoint", {})
    review = report["results"].get("code_review", {})
    findings = review.get("findings", [])

    confirmed = []
    confirmed_keys = set()
    for name in failed_test_names:
        ep = stub_map.get(name)
        if not ep:
            continue
        for f in findings:
            if f.get("function") and f.get("function") == ep.get("function") and f.get("file") == ep.get("source_file"):
                key = (f["file"], f.get("function"), f["rule"])
                if key in confirmed_keys:
                    continue
                confirmed_keys.add(key)
                conf = dict(f)
                conf["corroborated_by"] = f"auto-generated test `{name}` failed against the running app"
                confirmed.append(conf)

    annotated = []
    for f in findings:
        f2 = dict(f)
        key = (f["file"], f.get("function"), f["rule"])
        f2["confirmed"] = key in confirmed_keys
        annotated.append(f2)

    return confirmed, annotated
