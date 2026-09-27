"""Code review subagent.

Statically analyzes changed files (or, if a PR diff is present, the diff
itself) for common risk patterns. This goes well past the seeded demo's one
SQL-injection bug: it looks for injection, insecure deserialization, command
execution, weak crypto, permissive network/CORS config, and sloppy error
handling -- the kinds of things that actually show up across a real,
messy codebase. Produces a severity-ranked review summary instead of raw
findings.
"""
import ast
import os
import re

from devpilot.scanutil import iter_py_files

RISK_PATTERNS = [
    ("sql-injection", re.compile(
        r"(execute|executemany)\s*\(\s*f[\"']"          # execute(f"...")
        r"|(execute\w*)\(.*%s.*%"                          # execute(...) with % formatting
        r"|(execute\w*)\(.*\+\s*\w+"                       # execute(... + var)
        r"|=\s*f[\"'][^\"']*\b(SELECT|INSERT|UPDATE|DELETE)\b",  # query = f"SELECT ... {var} ..."
        re.IGNORECASE,
    )),
    ("unsafe-yaml", re.compile(r"yaml\.load\((?!.*Loader=)")),
    ("hardcoded-secret", re.compile(r"(password|secret|api_key|token)\s*=\s*[\"'][^\"']{4,}[\"']", re.IGNORECASE)),
    ("bare-except", re.compile(r"except\s*:\s*$")),
    ("eval-exec", re.compile(r"\b(eval|exec)\s*\(")),
    ("insecure-deserialization", re.compile(r"\b(pickle|marshal)\.loads?\(|yaml\.unsafe_load\(")),
    ("shell-injection", re.compile(r"(subprocess\.\w+|os\.system|os\.popen)\([^)]*shell\s*=\s*True|os\.system\(.*\+")),
    ("weak-hash", re.compile(r"hashlib\.(md5|sha1)\(")),
    ("insecure-random", re.compile(r"\brandom\.(random|randint|choice)\(.*(token|password|secret|otp|session)", re.IGNORECASE)),
    ("permissive-cors", re.compile(r"allow_origins\s*=\s*\[\s*[\"']\*[\"']\s*\]|CORS\(.*origins\s*=\s*[\"']\*[\"']")),
    ("debug-enabled", re.compile(r"\bdebug\s*=\s*True\b")),
    ("broad-except-pass", re.compile(r"except\s+Exception\s*(as\s+\w+)?\s*:\s*$")),
    ("assert-for-validation", re.compile(r"^\s*assert\s+\w+.*,\s*[\"']")),
]
RISK_META = {
    "sql-injection": ("high", "Query text is built with string formatting instead of parameter binding -- classic SQL-injection entry point."),
    "unsafe-yaml": ("high", "yaml.load() without an explicit safe Loader can execute arbitrary code on untrusted input."),
    "hardcoded-secret": ("high", "Credential-shaped literal committed directly in source."),
    "bare-except": ("medium", "Bare except swallows all errors, including ones callers need to see."),
    "eval-exec": ("high", "eval()/exec() on any value that can be influenced by user input is arbitrary code execution."),
    "insecure-deserialization": ("high", "pickle/marshal/yaml.unsafe_load can execute arbitrary code when deserializing untrusted data."),
    "shell-injection": ("high", "Shell invoked with shell=True (or string concatenation) is a command-injection risk if any part is user-controlled."),
    "weak-hash": ("medium", "MD5/SHA1 are broken for security purposes (passwords, tokens, signatures) -- use bcrypt/argon2 or SHA-256+."),
    "insecure-random": ("medium", "Python's `random` module is not cryptographically secure -- use `secrets` for tokens, passwords, or session IDs."),
    "permissive-cors": ("medium", "Wildcard CORS origin allows any website to call this API with credentials -- scope it to known origins."),
    "debug-enabled": ("low", "Debug mode left on can leak stack traces and internals in production; confirm this is dev-only."),
    "broad-except-pass": ("low", "Catching bare `Exception` hides the real failure mode from callers and logs -- narrow it or re-raise."),
    "assert-for-validation": ("low", "`assert` is stripped when Python runs with -O -- don't rely on it for input validation or auth checks."),
}


def _find_unchecked_lookup(path, source):
    """Heuristic: an endpoint fetches a single row (fetchone/get) and returns
    it (dict(row) / row.x) without a None-check anywhere in the function."""
    findings = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return findings
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_lines = [l for l in (ast.get_source_segment(source, node) or "").splitlines() if not l.strip().startswith("#")]
        body_src = "\n".join(body_lines)
        fetches_one = "fetchone(" in body_src or ".first()" in body_src
        checks_none = (
            ("is None" in body_src) or ("if not row" in body_src) or ("if row is None" in body_src)
            or ("HTTPException" in body_src) or ("abort(" in body_src)
        )
        if fetches_one and not checks_none:
            findings.append({
                "rule": "unchecked-lookup",
                "severity": "medium",
                "file": path,
                "function": node.name,
                "message": f"`{node.name}` fetches a single row and returns it without checking for a missing/None result -- a bad id will raise a 500 instead of a 404.",
            })

        # Mutable default argument -- shared across every call that doesn't
        # pass that arg explicitly; a classic, hard-to-spot Python footgun.
        for arg, default in zip(reversed(node.args.args), reversed(node.args.defaults or [])):
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                findings.append({
                    "rule": "mutable-default-arg",
                    "severity": "medium",
                    "file": path,
                    "function": node.name,
                    "message": f"`{node.name}`'s parameter `{arg.arg}` defaults to a mutable {type(default).__name__.lower()} -- it's shared across every call and can leak state between requests.",
                })

        # Long parameter lists are a maintainability + review-risk smell:
        # hard to call correctly, hard to review a diff that adds one more.
        n_params = len([a for a in node.args.args if a.arg not in ("self", "cls")])
        if n_params > 6:
            findings.append({
                "rule": "long-parameter-list",
                "severity": "low",
                "file": path,
                "function": node.name,
                "message": f"`{node.name}` takes {n_params} parameters -- consider a config object/dataclass; easy to pass args in the wrong order.",
            })
    return findings


def _scan_file(path, project_path):
    rel = os.path.relpath(path, project_path)
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    findings = []
    for lineno, line in enumerate(source.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        for rule, pattern in RISK_PATTERNS:
            if pattern.search(line):
                severity, message = RISK_META[rule]
                findings.append({
                    "rule": rule, "severity": severity, "file": rel, "line": lineno, "message": message,
                })
    findings.extend({**f, "file": rel} for f in _find_unchecked_lookup(rel, source))
    return findings


def run(project_path, pr_diff_path=None):
    findings = []
    for full, _rel in iter_py_files(project_path, skip_tests=True):
        findings.extend(_scan_file(full, project_path))

    diff_note = None
    if pr_diff_path and os.path.exists(pr_diff_path):
        with open(pr_diff_path, "r", encoding="utf-8") as f:
            diff_text = f.read()
        added_lines = [l[1:] for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
        touched = [l for l in added_lines if any(p.search(l) for _, p in RISK_PATTERNS)]
        diff_note = {
            "file": os.path.basename(pr_diff_path),
            "lines_added": len(added_lines),
            "risky_lines_added": len(touched),
        }

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: severity_rank.get(f["severity"], 3))
    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    return {
        "agent": "code_review",
        "findings": findings,
        "counts": counts,
        "rules_checked": len(RISK_META) + 2,  # + mutable-default-arg, long-parameter-list, unchecked-lookup (AST-based, not in RISK_META)
        "pr_diff_reviewed": diff_note,
        "summary": (
            f"{len(findings)} finding(s): {counts['high']} high, {counts['medium']} medium, {counts['low']} low. "
            + ("Blocking issues found -- recommend requesting changes before merge." if counts["high"] else "No blocking issues; safe to approve with minor follow-ups.")
        ),
    }
