"""Legacy modernization subagent (the 5th workflow from the brief).

Reuses the same AST pass as the onboarding agent to flag maintainability and
modernization opportunities: undocumented/untyped functions, oversized
"god functions", star imports, old-style string formatting, deprecated
typing imports, print-based logging, deep nesting, stale TODOs, and
dependency pins that block automatic patch-level upgrades. Every finding is
a concrete, file-and-line-level suggestion, not a generic "modernize your
stack" note.
"""
import ast
import os
import re

from devpilot.scanutil import iter_py_files, module_summary, read_text

OLD_FORMAT_RE = re.compile(r"[\"'][^\"']*%[sd][^\"']*[\"']\s*%\s")
STAR_IMPORT_RE = re.compile(r"^\s*from\s+[\w.]+\s+import\s+\*", re.MULTILINE)
DEPRECATED_TYPING_RE = re.compile(r"^\s*from typing import[^\n]*\b(List|Dict|Tuple|Set)\b", re.MULTILINE)
TODO_RE = re.compile(r"#\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)
PRINT_RE = re.compile(r"(?<!\w)print\s*\(")
OVERSIZED_LOC = 60
MAX_NESTING = 4


def _is_typed(node):
    if node.returns is not None:
        return True
    args = [a for a in node.args.args if a.arg not in ("self", "cls")]
    return bool(args) and all(a.annotation is not None for a in args)


def _max_nesting_depth(node, depth=0):
    """How many levels of if/for/while/try are nested inside this function --
    a proxy for how hard the function is to reason about or test."""
    best = depth
    nesting_nodes = (ast.If, ast.For, ast.While, ast.Try, ast.With)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, nesting_nodes):
            best = max(best, _max_nesting_depth(child, depth + 1))
        elif not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            best = max(best, _max_nesting_depth(child, depth))
    return best


def _uses_global(node):
    return any(isinstance(n, ast.Global) for n in ast.walk(node))


def _analyze_module(rel, summary):
    findings = []
    defs = summary.get("top_level_defs", [])
    typed = sum(1 for d in defs if _is_typed(d))
    oversized = [d for d in defs if (getattr(d, "end_lineno", d.lineno) - d.lineno) > OVERSIZED_LOC]

    source = summary.get("source", "")
    star_imports = len(STAR_IMPORT_RE.findall(source))
    old_format = len(OLD_FORMAT_RE.findall(source))
    deprecated_typing = len(DEPRECATED_TYPING_RE.findall(source))
    todos = TODO_RE.findall(source)
    print_calls = len(PRINT_RE.findall(source))

    for d in oversized:
        findings.append({
            "type": "oversized-function",
            "file": rel, "function": d.name, "lines": d.end_lineno - d.lineno,
            "suggestion": f"`{d.name}` is {d.end_lineno - d.lineno} lines -- consider splitting it into smaller, independently testable functions.",
        })
        depth = _max_nesting_depth(d)
        if depth > MAX_NESTING:
            findings.append({
                "type": "deep-nesting",
                "file": rel, "function": d.name, "lines": None,
                "suggestion": f"`{d.name}` nests {depth} levels deep (if/for/while/try) -- consider early returns or extracting the inner block into its own function.",
            })
        if _uses_global(d):
            findings.append({
                "type": "global-mutation",
                "file": rel, "function": d.name, "lines": None,
                "suggestion": f"`{d.name}` mutates module-level state with `global` -- makes behavior order-dependent and hard to test in isolation.",
            })

    if star_imports:
        findings.append({
            "type": "star-import", "file": rel, "function": None, "lines": None,
            "suggestion": f"{star_imports} wildcard import(s) (`from x import *`) -- makes it unclear what names are in scope; import explicitly.",
        })
    if old_format:
        findings.append({
            "type": "old-string-formatting", "file": rel, "function": None, "lines": None,
            "suggestion": f"{old_format} use(s) of %-style string formatting -- modernize to f-strings for readability.",
        })
    if deprecated_typing:
        findings.append({
            "type": "deprecated-typing-import", "file": rel, "function": None, "lines": None,
            "suggestion": f"Imports `List`/`Dict`/`Tuple`/`Set` from `typing` -- on Python 3.9+ the builtin generics (`list[int]`, `dict[str, int]`) work directly and need one less import.",
        })
    if print_calls >= 3:
        findings.append({
            "type": "print-based-logging", "file": rel, "function": None, "lines": None,
            "suggestion": f"{print_calls} `print()` call(s) -- in a service this should go through the `logging` module so it has levels, timestamps, and can be routed/filtered in production.",
        })
    if todos:
        findings.append({
            "type": "stale-marker", "file": rel, "function": None, "lines": None,
            "suggestion": f"{len(todos)} TODO/FIXME/HACK comment(s) -- worth triaging into tracked tickets so they don't get lost.",
        })

    return findings, len(defs), typed


def _dependency_findings(project_path):
    req_path = os.path.join(project_path, "requirements.txt")
    if not os.path.exists(req_path):
        return [], 0, 0
    exact_pins = 0
    total = 0
    for line in read_text(req_path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        total += 1
        if re.match(r"^[A-Za-z0-9_\-.]+==[\w.]+\s*$", line):
            exact_pins += 1
    findings = []
    if total and exact_pins == total:
        findings.append({
            "type": "dependency-pinning", "file": "requirements.txt", "function": None, "lines": None,
            "suggestion": f"All {total} dependencies are pinned with exact `==` versions and no lock file -- security patches require a manual bump. Consider a lock file (pip-tools / poetry) with compatible-release ranges.",
        })
    return findings, total, exact_pins


def run(project_path):
    findings = []
    total_defs, typed_defs = 0, 0
    unparseable = []

    for full, rel in iter_py_files(project_path, skip_tests=True):
        try:
            ast.parse(read_text(full))
        except SyntaxError:
            unparseable.append(rel)
            continue
        summary = module_summary(full)
        mod_findings, defs, typed = _analyze_module(rel, summary)
        findings.extend(mod_findings)
        total_defs += defs
        typed_defs += typed

    dep_findings, dep_total, dep_exact = _dependency_findings(project_path)
    findings.extend(dep_findings)

    type_hint_pct = round(100 * typed_defs / total_defs, 1) if total_defs else 100.0

    return {
        "agent": "modernization",
        "findings": findings,
        "type_hint_coverage_pct": type_hint_pct,
        "functions_considered": total_defs,
        "unparseable_files": unparseable,
        "summary": (
            f"{len(findings)} modernization opportunit{'y' if len(findings) == 1 else 'ies'} found "
            f"across {total_defs} functions ({type_hint_pct}% type-hinted)."
        ),
    }
