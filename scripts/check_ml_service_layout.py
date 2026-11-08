#!/usr/bin/env python3
"""Guard the shape of ml/ itself, the way scripts/check_ml_boundary.py guards backend/.

WHY THIS GUARD EXISTS
---------------------
ml/ is a submodule that is also a standalone checkout, and its acceptance as a submodule
depends on properties no compiler checks: that the modules stay FLAT in service/, that the
one nested package is still the vendored tokenizer, that the process entry points stay
importable on a host with no CUDA stack, and that README.md - which AGENTS.md names as the
canonical contract - still describes the routes app.py actually serves.

Each of those has already been violated once in this repository: the modules were reached
through a sys.path shim, a helper was left outside service/ so the image could not import
it, app.py briefly imported the pipeline, and the README grew a route that no longer
existed. A reviewer catches those; a guard catches them every time.

WHAT IS ASSERTED
----------------
1. NO MODULE AT THE ml/ ROOT. A *.py file beside pyproject.toml means either a script that
   should live in scripts/ or a module that escaped service/. Both break the rule that
   ml/service is the only thing on sys.path in the image.
2. ONE NESTED PACKAGE, THE VENDORED ONE. Every directory under service/ that carries an
   __init__.py must be inside service/cosmos_tokenizer/. A second package would mean the
   flat layout is becoming a tree and the image (PYTHONPATH=/app/service) would need
   package imports the test suite does not have.
3. app.py AND settings.py IMPORT NO FRAMEWORK AT MODULE SCOPE. They are what the process
   imports before it can answer /health. engine.py is allowed to import torch, because it
   is the module that owns the models and it is imported lazily enough that neither of the
   other two pulls it in; the suite proves the effective result (no torch in sys.modules
   after importing app).
4. README.md DESCRIBES THE ROUTES app.py SERVES. The route table is parsed out of both and
   compared as sets: a route added to the code without its row, or a row left behind after
   a rename, fails here. The committed snapshot in ml/tests/snapshots/ml_routes.json is
   checked the same way, so the three copies of the contract cannot drift apart between
   pytest runs.

Parsed with ast, never imported: importing app.py would need fastapi and the app would
start an engine, and this guard must run on any python in the repository.

USAGE
-----
    python ml/scripts/check_ml_service_layout.py
    python ml/scripts/check_ml_service_layout.py --json

Exit codes: 0 clean, 1 violations, 2 usage or unexpected error. Same [OK]/[FAIL]/[WARN]/
[SUMMARY] output as the superproject guards, so `make guards` can absorb it unchanged.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

CHECK = "ml-service-layout"

# <checkout>/ml/scripts/check_ml_service_layout.py -> <checkout>/ml, whether that checkout is
# the superproject (ml/ nested) or the ml submodule checked out on its own. Deriving ML from
# the file beats deriving the repository root from the depth, which differs in a bare clone.
ML = Path(__file__).resolve().parents[1]
ROOT = ML.parent
SERVICE = ML / "service"
README = ML / "README.md"
SNAPSHOT = ML / "tests" / "snapshots" / "ml_routes.json"

VENDORED_PACKAGE = "cosmos_tokenizer"

# Modules that must not be imported at module scope by the two entry-point modules. These
# are the imports that would make `import app` cost a CUDA init or fail on a CPU host; the
# lazy-import rule lives in the pipeline modules and is asserted here only for the files
# the process touches before it serves its first request.
HEAVY_MODULES = (
    "torch",
    "torchvision",
    "torchaudio",
    "torchmetrics",
    "torcheval",
    "transformers",
    "sentence_transformers",
    "moviepy",
    "librosa",
)

ENTRY_MODULES = ("app.py", "settings.py")

WALK_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "venv"}

class Report:
    """Collects [OK]/[FAIL]/[WARN] lines and prints them in the house format.

    Deliberately a copy of the helper in scripts/check_ml_boundary.py rather than an
    import of it: the guard must run from a bare ml/ checkout, where ../scripts does not
    exist. Twenty duplicated lines are cheaper than a guard that needs its neighbour.
    """

    def __init__(self) -> None:
        self.failures: List[str] = []
        self.warnings: List[str] = []
        self.skips: List[str] = []
        self.ok: List[str] = []

    def status(self) -> str:
        if self.failures:
            return "fail"
        if self.skips and not self.ok:
            return "skip"
        return "pass"

    def emit(self, as_json: bool) -> int:
        if as_json:
            print(json.dumps({
                "check": CHECK,
                "status": self.status(),
                "failures": self.failures,
                "warnings": self.warnings,
                "skips": self.skips,
                "ok": self.ok,
            }, indent=2, sort_keys=True))
            return 1 if self.failures else 0
        for message in self.failures:
            print("[FAIL] " + CHECK + ": " + message)
        for message in self.warnings:
            print("[WARN] " + CHECK + ": " + message)
        for message in self.skips:
            print("[SKIP] " + CHECK + ": " + message)
        for message in self.ok:
            print("[OK] " + CHECK + ": " + message)
        print(
            "[SUMMARY] check=" + CHECK + " status=" + self.status()
            + " failures=" + str(len(self.failures))
            + " warnings=" + str(len(self.warnings))
            + " skipped=" + str(len(self.skips))
        )
        return 1 if self.failures else 0


def rel(path: Path) -> str:
    """Workspace-relative POSIX path, so the output is identical on Windows and in CI."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def parse_module(path: Path) -> Tuple[Optional[ast.Module], str]:
    """Parse one file as (tree, error). A syntax error is reported, never raised.

    A file that does not parse is not evidence that the layout is wrong, but it is
    evidence that this guard could not check it - and a silent pass over an unreadable
    entry point is worse than a noisy failure, so the message is carried back to the
    caller that knows which rule was being checked.
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path)), ""
    except SyntaxError as exc:
        return None, (str(exc).splitlines() or ["SyntaxError"])[0]


def module_level_imports(tree: ast.Module) -> List[Tuple[int, str]]:
    """Imports that run when the module is imported, as (lineno, top-level name).

    Walks the module body through control flow (if/try/with/for) but NOT into function or
    class bodies: `def load(): import torch` is the lazy form this repository asks for and
    must not be flagged, while `try: import torch` at module scope runs on import and must
    be. That distinction is the whole point of the rule, so it is implemented on the tree
    rather than guessed from indentation.
    """
    found: List[Tuple[int, str]] = []

    def record(node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.append((node.lineno, node.module.split(".")[0]))

    def walk_block(statements: List[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            record(node)
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if isinstance(block, list):
                    walk_block([item for item in block if isinstance(item, ast.stmt)])
            for handler in getattr(node, "handlers", []) or []:
                walk_block(handler.body)

    walk_block(tree.body)
    return found


def root_modules() -> List[Path]:
    """*.py files sitting directly in ml/, where no module belongs."""
    if not ML.is_dir():
        return []
    return sorted(path for path in ML.glob("*.py") if path.is_file())


def nested_packages() -> List[Path]:
    """Directories under service/ that carry an __init__.py, the vendored one excluded."""
    if not SERVICE.is_dir():
        return []
    packages = []
    for init in sorted(SERVICE.rglob("__init__.py")):
        directory = init.parent
        if "__pycache__" in directory.parts:
            continue
        relative = directory.relative_to(SERVICE)
        if relative.parts and relative.parts[0] == VENDORED_PACKAGE:
            continue
        packages.append(directory)
    return packages


HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def declared_routes(path: Path) -> Set[str]:
    """Route strings from @app.<verb>("...") decorators, as "METHOD /path".

    Parsed rather than imported: importing app.py needs fastapi and starts an engine, and
    this guard has to run on the stdlib interpreter from the repository root.
    """
    tree, _ = parse_module(path)
    if tree is None:
        return set()
    routes: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute) or func.attr.lower() not in HTTP_METHODS:
                continue
            if not decorator.args:
                continue
            argument = decorator.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                routes.add(func.attr.upper() + " " + argument.value)
    return routes


def readme_routes() -> Set[str]:
    """Route strings from the README route table, as "METHOD /path".

    The table is the contract a human reads, and the shape is stable: a row per route,
    method in the first cell, the backticked path in the second. Rows whose first cell
    is not a method (the error table, for one) are skipped rather than guessed at.
    """
    if not README.is_file():
        return set()
    routes: Set[str] = set()
    for line in README.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        method, path = cells[0].upper(), cells[1].strip("`")
        if method in ("GET", "POST", "PUT", "PATCH", "DELETE") and path.startswith("/"):
            routes.add(method + " " + path)
    return routes


def snapshot_routes() -> Optional[Set[str]]:
    """Route strings pinned in tests/snapshots/ml_routes.json, None when unreadable."""
    if not SNAPSHOT.is_file():
        return None
    try:
        document = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    routes = document.get("routes")
    if not isinstance(routes, dict):
        return None
    return set(routes.keys())


def check_root_modules(report: Report) -> None:
    """Rule 1: ml/ itself holds no importable module."""
    offenders = root_modules()
    if not offenders:
        report.ok.append("ml/ holds no module of its own; every module is under ml/service/")
        return
    for path in offenders:
        report.failures.append(
            rel(path) + " sits at the ml/ root. sys.path in the image is /app/service,"
            " so this file is either unreachable at runtime or a script that belongs in"
            " ml/scripts/. Move it into service/ (import by bare name) or into scripts/."
        )


def check_nested_packages(report: Report) -> None:
    """Rule 2: the only nested package is the vendored tokenizer."""
    offenders = nested_packages()
    if not offenders:
        report.ok.append(
            "service/ is flat: the only nested package is the vendored " + VENDORED_PACKAGE
        )
        return
    for directory in offenders:
        report.failures.append(
            rel(directory) + " is a nested package inside service/. The layout is flat: a"
            " module is imported by its bare name in both the image (PYTHONPATH=/app/service)"
            " and the suite. Only service/" + VENDORED_PACKAGE + " may be a package, because it"
            " is vendored upstream code kept verbatim."
        )


def check_entry_module_imports(report: Report) -> None:
    """Rule 3: app.py and settings.py import no framework at module scope."""
    checked = 0
    for name in ENTRY_MODULES:
        path = SERVICE / name
        if not path.is_file():
            report.failures.append(
                rel(path) + " is missing; ml/service is not a service without it"
            )
            continue
        tree, error = parse_module(path)
        if tree is None:
            report.failures.append(
                rel(path) + " could not be parsed (" + error + "), so its imports were not checked"
            )
            continue
        checked += 1
        heavy = [
            (lineno, module)
            for lineno, module in module_level_imports(tree)
            if module in HEAVY_MODULES
        ]
        if not heavy:
            report.ok.append(rel(path) + " imports no ML framework at module scope")
            continue
        for lineno, module in heavy:
            report.failures.append(
                rel(path) + ":" + str(lineno) + " imports " + module + " at module scope."
                " /health has to answer on a host with no CUDA stack, and this module is"
                " imported before the first request; import it inside the function that"
                " needs it (engine.py may import it at the top, app.py and settings.py may"
                " not)."
            )
    if checked == 0 and not report.failures:
        report.skips.append(
            "no entry module could be parsed, so no import was verified. NOT a pass."
        )


def check_readme_contract(report: Report) -> None:
    """Rule 4: app.py, README.md and the snapshot describe the same routes."""
    app_path = SERVICE / "app.py"
    if not app_path.is_file():
        report.failures.append(rel(app_path) + " is missing, so the routes cannot be compared")
        return
    code = declared_routes(app_path)
    if not code:
        report.failures.append(
            rel(app_path)
            + " declares no route; the service would answer 404 everywhere"
        )
        return
    documented = readme_routes()
    if not documented:
        report.failures.append(
            rel(README) + " has no route table. ml/README.md is the canonical contract"
            " (AGENTS.md); a route that exists only in the code is undocumented."
        )
    for route in sorted(code - documented):
        report.failures.append(
            rel(README) + " does not document " + route + ", which " + rel(app_path)
            + " serves. Add the row in the same change as the route."
        )
    for route in sorted(documented - code):
        report.failures.append(
            rel(README) + " documents " + route + ", which " + rel(app_path)
            + " no longer serves. Remove the row in the same change as the rename."
        )
    if code == documented:
        report.ok.append(
            "README.md documents exactly the " + str(len(code)) + " route(s) app.py serves"
        )
    pinned = snapshot_routes()
    if pinned is None:
        report.failures.append(
            rel(SNAPSHOT) + " is missing or unreadable. The snapshot is what"
            " tests/test_route_snapshot.py asserts against; without it the contract has"
            " one fewer copy and the suite cannot run."
        )
        return
    for route in sorted(code - pinned):
        report.failures.append(
            rel(SNAPSHOT) + " does not pin " + route + " that " + rel(app_path) + " serves."
            " Add it (tests/test_route_snapshot.py compares the two in both directions)."
        )
    if code <= pinned:
        report.ok.append(rel(SNAPSHOT) + " pins every route app.py serves")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="assert that ml/ keeps the flat sidecar layout and a truthful README"
    )
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args(argv)

    if not SERVICE.is_dir():
        print("[FAIL] " + CHECK + ": " + rel(SERVICE) + " does not exist")
        print("[SUMMARY] check=" + CHECK + " status=fail failures=1 warnings=0 skipped=0")
        return 1

    report = Report()
    check_root_modules(report)
    check_nested_packages(report)
    check_entry_module_imports(report)
    check_readme_contract(report)
    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - a guard that crashes is a guard that fails
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
