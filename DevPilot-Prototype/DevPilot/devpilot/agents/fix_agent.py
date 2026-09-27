"""Auto-fix subagent.

Turns a *subset* of the code-review and release findings into actual code
changes -- but only where DevPilot has high confidence the fix is safe and
mechanical. Anything it isn't sure about is left as a flagged, manual-review
item rather than guessed at. This agent never runs as part of a normal
assessment -- it is opt-in (`--apply-fixes`) and only ever commits locally;
pushing to a remote and opening a pull request is a separate, explicitly
confirmed step (see push_and_pr.py).
"""
import difflib
import os
import re
import subprocess

from devpilot.scanutil import read_text


def _unified_diff(rel_path, old_source, new_source):
    return "".join(difflib.unified_diff(
        old_source.splitlines(keepends=True),
        new_source.splitlines(keepends=True),
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}",
    ))


def _fix_sql_injection(source):
    """query = f"SELECT ... WHERE col = '{var}'" -> parameterized query.

    Only applied when the pattern is an exact, unambiguous single-variable
    interpolation inside a quoted literal -- anything more complex is left
    for a human.
    """
    pattern = re.compile(
        r'(\w+)\s*=\s*f(["\'])SELECT (.+?) WHERE (\w+) = \'\{(\w+)\}\'\2'
    )
    m = pattern.search(source)
    if not m:
        return source, None
    var_name, _q, select_clause, column, interp_var = m.groups()
    old_line = m.group(0)
    new_query_line = f'{var_name} = "SELECT {select_clause} WHERE {column} = ?"'
    fixed = source.replace(old_line, new_query_line)
    # execute(query) -> execute(query, (var,))
    exec_pattern = re.compile(re.escape(var_name) + r"\)\.fetchall\(\)")
    fixed2 = exec_pattern.sub(f"{var_name}, ({interp_var},)).fetchall()", fixed)
    if fixed2 == fixed:
        # try the more general "execute(query)" shape
        fixed2 = re.sub(
            r"execute\(" + re.escape(var_name) + r"\)",
            f"execute({var_name}, ({interp_var},))",
            fixed,
        )
    changed = fixed2 != source
    return fixed2, ("sql-injection", f"Parameterized the query built from `{var_name}`.") if changed else (fixed2, None)


def _fix_unchecked_lookup_fastapi(source):
    """`row = conn.execute(...).fetchone(); ...; return dict(row)` ->
    add a 404 when the row is missing. Only applied when the exact shape
    (fetchone -> dict(row) with nothing in between) is found."""
    pattern = re.compile(
        r"(row = [^\n]*\.fetchone\(\)\n(?:[ \t]*conn\.close\(\)\n)?)([ \t]*)return dict\(row\)"
    )
    if not pattern.search(source):
        return source, None
    if "from fastapi import HTTPException" not in source and "HTTPException" not in source:
        source = source.replace(
            "from fastapi import APIRouter", "from fastapi import APIRouter, HTTPException", 1
        )

    def _repl(m):
        prefix, indent = m.group(1), m.group(2)
        return (
            f"{prefix}{indent}if row is None:\n"
            f"{indent}    raise HTTPException(status_code=404, detail=\"Not found\")\n"
            f"{indent}return dict(row)"
        )

    fixed = pattern.sub(_repl, source)
    changed = fixed != source
    return fixed, ("unchecked-lookup", "Added a 404 for a missing row instead of letting `dict(None)` crash.") if changed else (fixed, None)


def _bump_dependency(requirements_text, package, safe_version):
    # Preserve the original package name's casing as pinned (e.g. "PyYAML"),
    # rather than overwriting it with our lookup key's casing.
    pattern = re.compile(rf"^({re.escape(package)})==[\w.]+", re.IGNORECASE | re.MULTILINE)
    new_text, n = pattern.subn(lambda m: f"{m.group(1)}=={safe_version}", requirements_text)
    return new_text, (n > 0)


KNOWN_SAFE_VERSIONS = {"pyyaml": "6.0.1"}


def _run_tests(project_path):
    tests_dir = os.path.join(project_path, "tests")
    if not os.path.isdir(tests_dir):
        return {"ran": False, "reason": "no tests/ directory found"}
    try:
        proc = subprocess.run(
            ["python3", "-m", "pytest", "tests", "-q"],
            cwd=project_path, capture_output=True, text=True, timeout=60,
        )
        return {"ran": True, "passed": proc.returncode == 0, "output_tail": "\n".join(proc.stdout.splitlines()[-15:])}
    except Exception as e:  # pragma: no cover -- best-effort, never fatal
        return {"ran": False, "reason": f"could not run tests: {e}"}


def run(project_path, apply=False):
    """When apply=False (the default everywhere except an explicit
    --apply-fixes CLI run), this only reports what it *would* fix, without
    touching any files."""
    changes = []

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", "venv", ".venv", "node_modules")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, project_path)
            source = read_text(full)
            new_source = source
            file_changes = []

            new_source, note = _fix_sql_injection(new_source)
            if note:
                file_changes.append(note)
            new_source, note = _fix_unchecked_lookup_fastapi(new_source)
            if note:
                file_changes.append(note)

            if file_changes:
                changes.append({
                    "file": rel, "fixes": file_changes, "_new_source": new_source, "_full_path": full,
                    "_diff": _unified_diff(rel, source, new_source),
                })

    req_path = os.path.join(project_path, "requirements.txt")
    req_change = None
    if os.path.exists(req_path):
        req_text = read_text(req_path)
        new_req = req_text
        bumped = []
        for pkg, safe_version in KNOWN_SAFE_VERSIONS.items():
            new_req, did = _bump_dependency(new_req, pkg, safe_version)
            if did:
                bumped.append((pkg, safe_version))
        if bumped:
            req_change = {
                "file": "requirements.txt", "fixes": [("dependency-bump", f"Bumped {p} to {v}") for p, v in bumped],
                "_new_source": new_req, "_full_path": req_path,
                "_diff": _unified_diff("requirements.txt", req_text, new_req),
            }

    all_changes = changes + ([req_change] if req_change else [])

    result = {
        "agent": "auto_fix",
        "applied": False,
        "candidate_fixes": [
            {"file": c["file"], "fixes": [f[1] for f in c["fixes"]], "diff": c["_diff"]} for c in all_changes
        ],
        "files_changed": len(all_changes),
        "test_run": None,
        "git_branch": None,
    }

    if not apply or not all_changes:
        return result

    for c in all_changes:
        with open(c["_full_path"], "w", encoding="utf-8") as f:
            f.write(c["_new_source"])

    result["applied"] = True
    result["test_run"] = _run_tests(project_path)

    branch = "devpilot/auto-fixes"
    try:
        subprocess.run(["git", "-C", project_path, "checkout", "-B", branch], check=True, capture_output=True)
        subprocess.run(["git", "-C", project_path, "add", "-A"], check=True, capture_output=True)
        msg = "DevPilot auto-fix: " + "; ".join(f"{c['file']} ({', '.join(x[0] for x in c['fixes'])})" for c in all_changes)
        subprocess.run(["git", "-C", project_path, "commit", "-m", msg], check=True, capture_output=True)
        result["git_branch"] = branch
        result["git_committed"] = True
    except Exception as e:  # pragma: no cover
        result["git_branch"] = None
        result["git_committed"] = False
        result["git_error"] = str(e)

    return result
