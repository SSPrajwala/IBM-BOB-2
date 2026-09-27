"""Onboarding subagent.

Scans an unfamiliar repository -- any real Python repo, not just the demo
project -- and produces: an architecture summary (modules and what each one
does, inferred from AST + docstrings), a setup guide, and a ranked list of
"starter tasks" -- small, safe entry points for a new engineer.
"""
import os

from devpilot.scanutil import iter_py_files, module_summary


def run(project_path):
    modules = {}
    undocumented = []
    total_loc = 0
    for full, rel in iter_py_files(project_path):
        if os.path.basename(rel) == "__init__.py" and os.path.getsize(full) == 0:
            continue
        summary = module_summary(full)
        modules[rel] = summary
        total_loc += summary["loc"]
        if not summary["has_docstring"]:
            undocumented.append(rel)

    has_tests = any("test" in m.lower() for m in modules) or os.path.isdir(os.path.join(project_path, "tests"))
    has_readme = any(
        os.path.exists(os.path.join(project_path, f)) for f in ("README.md", "README.rst", "README.txt", "readme.md")
    )
    has_dockerfile = os.path.exists(os.path.join(project_path, "Dockerfile"))

    starter_tasks = []
    for rel in sorted(undocumented)[:6]:
        starter_tasks.append(f"Add a module docstring to `{rel}` explaining its responsibility.")
    if not has_tests:
        starter_tasks.append("No test suite detected -- add a `tests/` module as a first task.")

    architecture = [
        {
            "file": rel,
            "functions": len(s["functions"]),
            "classes": len(s["classes"]),
            "documented": s["has_docstring"],
            "loc": s["loc"],
        }
        for rel, s in sorted(modules.items(), key=lambda kv: -kv[1]["loc"])
    ]

    setup_steps = []
    if os.path.exists(os.path.join(project_path, "requirements.txt")):
        setup_steps.append("pip install -r requirements.txt")
    elif os.path.exists(os.path.join(project_path, "pyproject.toml")):
        setup_steps.append("pip install -e . (or: poetry install)")
    if has_dockerfile:
        setup_steps.append("or: docker build -t app . && docker run -p 8000:8000 app")
    entry_candidates = [f for f in ("app/main.py", "main.py", "manage.py", "app.py", "microblog.py") if os.path.exists(os.path.join(project_path, f))]
    if entry_candidates:
        setup_steps.append(f"Entry point detected: {entry_candidates[0]}")
    setup_steps.append("Run the test suite first to confirm a clean baseline before changing anything.")

    return {
        "agent": "onboarding",
        "modules_scanned": len(modules),
        "total_loc": total_loc,
        "undocumented_modules": undocumented,
        "has_readme": has_readme,
        "has_tests": has_tests,
        "has_dockerfile": has_dockerfile,
        "architecture_map": architecture[:25],
        "setup_steps": setup_steps,
        "starter_tasks": starter_tasks[:8],
    }
