"""Shared repo-scanning helpers used by every subagent.

Kept separate from the agents themselves so "how do we walk a real,
messy, third-party repository" is solved once, generally, instead of
re-implemented (and re-hardcoded) per agent.
"""
import ast
import os
import re

EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "venv", ".venv", "env",
    "node_modules", "dist", "build", "migrations", ".mypy_cache",
    ".idea", ".vscode", ".tox", "site-packages", "egg-info",
}

TEST_NAME_RE = re.compile(r"(^test_.*\.py$)|(.*_test\.py$)|(^tests?\.py$)")
TEST_DIR_RE = re.compile(r"(^|/)tests?($|/)")


def iter_py_files(root, skip_tests=False):
    """Yield every .py file under root, skipping noise directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if skip_tests and looks_like_test_file(rel):
                continue
            yield full, rel


def looks_like_test_file(rel_path):
    fname = os.path.basename(rel_path)
    return bool(TEST_NAME_RE.match(fname)) or bool(TEST_DIR_RE.search(rel_path.replace(os.sep, "/")))


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ---- route detection: FastAPI (@router.get/post/...) and Flask (@bp.route / @app.route) ----
_FASTAPI_DECORATOR = re.compile(r"^\s*@\w+\.(get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")
_FLASK_DECORATOR = re.compile(r"^\s*@\w+\.route\(\s*[\"']([^\"']*)[\"'](?:.*?methods\s*=\s*\[([^\]]*)\])?")
_DEF_LINE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")


def find_routes(source):
    """Return a list of {method, path, function} dicts, by scanning for a
    route decorator (FastAPI or Flask style) and the `def` line it attaches
    to -- possibly several decorators, then one def. Line-based rather than
    a single regex so the associated function name is captured too."""
    routes = []
    lines = source.splitlines()
    pending = []  # list of (method, path)
    for line in lines:
        m = _FASTAPI_DECORATOR.match(line)
        if m:
            pending.append((m.group(1).lower(), m.group(2)))
            continue
        m = _FLASK_DECORATOR.match(line)
        if m:
            path, methods_blob = m.group(1), m.group(2)
            if methods_blob:
                methods = [x.strip(" '\"").lower() for x in methods_blob.split(",") if x.strip(" '\"")]
            else:
                methods = ["get"]
            for meth in methods:
                pending.append((meth, path))
            continue
        m = _DEF_LINE.match(line)
        if m and pending:
            fname = m.group(1)
            for method, path in pending:
                routes.append({"method": method, "path": path, "function": fname})
            pending = []
        elif line.strip() and not line.strip().startswith("@"):
            pending = []  # decorator wasn't immediately followed by a def
    return routes


# ---- test-call detection: client.get(...), self.client.get(...), test_client().get(...) ----
_CLIENT_CALL = re.compile(r"(?:self\.)?client\.(get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")


def find_client_calls(source):
    return [(m.lower(), p.split("?")[0]) for m, p in _CLIENT_CALL.findall(source)]


_MOUNT_CALL = re.compile(
    r"(?:include_router|register_blueprint)\(\s*(\w+)\.\w+\s*,[^)]*?"
    r"(?:prefix|url_prefix)\s*=\s*[\"']([^\"']*)[\"']"
)


def find_route_prefixes(project_path):
    """Best-effort: map a router/blueprint module's basename to the prefix
    it's mounted under (`app.include_router(tasks.router, prefix="/tasks")`
    or `app.register_blueprint(bp.bp, url_prefix="/api")`). Real apps vary
    a lot here -- this covers the common "imported module, mounted with a
    string prefix" shape; anything else just contributes its bare,
    un-prefixed path, which is still useful, just less precise."""
    prefixes = {}
    for full, rel in iter_py_files(project_path, skip_tests=True):
        source = read_text(full)
        for module_alias, prefix in _MOUNT_CALL.findall(source):
            prefixes[module_alias] = prefix
    return prefixes


_APP_ASSIGN = re.compile(r"^(\w+)\s*=\s*(FastAPI|Flask)\(")
_FACTORY_DEF = re.compile(r"^def\s+(create_app)\s*\(")


def find_app_entrypoint(project_path):
    """Best-effort: locate a FastAPI/Flask app instance (or a create_app
    factory) anywhere in the repo, so generated tests can import it without
    the human having to wire that up by hand. Returns None if nothing is
    found -- callers should fall back to a graceful pytest.skip."""
    candidates = []
    factory_candidates = []
    for full, rel in iter_py_files(project_path, skip_tests=True):
        source = read_text(full)
        module_dotpath = rel[:-3].replace(os.sep, ".").replace("/", ".")
        for line in source.splitlines():
            m = _APP_ASSIGN.match(line.strip())
            if m:
                priority = 0 if os.path.basename(rel) in ("main.py", "app.py", "wsgi.py", "asgi.py") else 1
                candidates.append((priority, module_dotpath, m.group(1)))
            m2 = _FACTORY_DEF.match(line.strip())
            if m2:
                factory_candidates.append(module_dotpath)

    if candidates:
        candidates.sort(key=lambda c: c[0])
        _, module_dotpath, varname = candidates[0]
        return {"kind": "instance", "module": module_dotpath, "var": varname}
    if factory_candidates:
        # prefer the package root (shortest dotpath) since create_app is
        # usually re-exported from the top-level package __init__
        module_dotpath = min(factory_candidates, key=len)
        return {"kind": "factory", "module": module_dotpath, "var": "create_app"}
    return None


def normalize_path(path):
    path = re.sub(r"[<{][^>}]+[>}]", "1", path)  # <int:id> or {id} -> 1
    return path.rstrip("/") or "/"


# ---- AST-level module summary, shared by onboarding + modernization agents ----
def module_summary(path):
    source = read_text(path)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"functions": [], "classes": [], "has_docstring": False, "loc": len(source.splitlines()), "top_level_defs": []}

    functions, classes, top_level = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level.append(node)

    return {
        "functions": functions,
        "classes": classes,
        "has_docstring": ast.get_docstring(tree) is not None,
        "loc": len(source.splitlines()),
        "top_level_defs": top_level,
        "source": source,
    }
