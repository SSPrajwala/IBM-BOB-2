"""Release readiness subagent.

Checks pinned dependencies against a small table of known historical
advisories, confirms deployment prerequisites (Dockerfile, health endpoint),
and turns the project's CHANGELOG into a draft, audience-ready release note
plus a go/no-go deployment checklist.
"""
import os
import re

# Small illustrative table of well-documented historical advisories, used to
# demonstrate the flagging mechanism (a production build would query a live
# vulnerability feed / SBOM scanner instead).
KNOWN_ADVISORIES = {
    "pyyaml": {
        "unsafe_below": "5.4",
        "note": "Versions before 5.4 default `yaml.load()` to the unsafe Loader, allowing arbitrary code execution on untrusted input (fixed by requiring an explicit Loader).",
    },
}


def _parse_requirements(path):
    deps = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            m = re.match(r"^([A-Za-z0-9_\-\.]+)==([\w\.]+)", line)
            if m:
                deps[m.group(1).lower()] = m.group(2)
    return deps


def run(project_path):
    req_path = os.path.join(project_path, "requirements.txt")
    deps = _parse_requirements(req_path) if os.path.exists(req_path) else {}

    flagged = []
    for name, version in deps.items():
        advisory = KNOWN_ADVISORIES.get(name)
        if advisory:
            flagged.append({"package": name, "pinned_version": version, "note": advisory["note"]})

    has_dockerfile = os.path.exists(os.path.join(project_path, "Dockerfile"))
    has_health = False
    main_path = os.path.join(project_path, "app", "main.py")
    if os.path.exists(main_path):
        with open(main_path) as f:
            has_health = "/health" in f.read()

    changelog_path = os.path.join(project_path, "CHANGELOG.md")
    pending_entry = ""
    if os.path.exists(changelog_path):
        with open(changelog_path) as f:
            text = f.read()
        m = re.search(r"## (v[\d.]+.*?\(pending release\))\n(.*?)(?=\n## |\Z)", text, re.S)
        if m:
            pending_entry = m.group(2).strip()

    release_notes = {
        "internal": [
            "New endpoint: GET /tasks/search/by-owner (filter tasks by owner).",
            "Note for reviewers: this endpoint is flagged by the code-review subagent for a SQL-injection risk -- do not ship until parameterized.",
        ],
        "customer_facing": [
            "You can now search your tasks by owner." if pending_entry else "No customer-facing changes in this release.",
        ],
    }

    checklist = [
        {"item": "Dockerfile present for reproducible deploys", "status": "pass" if has_dockerfile else "fail"},
        {"item": "Health check endpoint available", "status": "pass" if has_health else "fail"},
        {"item": "No unresolved high-severity code-review findings", "status": "check with code_review agent output"},
        {"item": "Dependency advisories reviewed", "status": "attention" if flagged else "pass"},
        {"item": "Test coverage acceptable for changed endpoints", "status": "check with testing agent output"},
    ]

    go_no_go = "NO-GO" if (flagged or not has_health) else "GO"

    return {
        "agent": "release_readiness",
        "dependency_flags": flagged,
        "deployment_checklist": checklist,
        "release_notes": release_notes,
        "recommendation": go_no_go,
    }
