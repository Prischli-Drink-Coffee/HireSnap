#!/usr/bin/env python3
r"""Ratchet module SIZE and COMPLEXITY for backend/, the way R:\GPTHub does it.

WHY THIS GUARD EXISTS
---------------------
Every other guard in this repository asks whether the tree has the right SHAPE. None of
them asks whether a module has quietly become the place where everything happens. A file
that grows from 300 to 900 lines, or a function that grows a fifth nested branch, passes
the layout rules, passes ruff's fatal subset, passes the import proof and ships. The cost
lands months later, on the person who has to change one line of it.

A limit alone would be a lie here: this tree already carries the debt of a migration, and
a threshold that is red on the day it is switched on is a threshold everybody learns to
pass with --no-verify (the argument R:\GPTHub\.github\workflows\ci.yml:41-51 makes about
ruff). So the guard is a RATCHET: today's measurements are recorded in a tracked file,
the file may only shrink, and any measurement that is not recorded - or that grew past
its recorded value - fails. Nothing is excused; the debt is written down where a reviewer
sees it.

WHAT IS MEASURED (five numbers, same five as GPTHub)
---------------------------------------------------
1. LINES PER FILE          - len(source.splitlines()) per module.
2. FUNCTION LENGTH         - end_lineno - lineno + 1 per def/async def.
3. CYCLOMATIC COMPLEXITY   - per def, counted exactly as GPTHub's CyclomaticVisitor
                             (If/For/AsyncFor/While/IfExp/ExceptHandler/With/AsyncWith/
                             Assert/Try +1, BoolOp +len(values)-1, Match +len(cases),
                             base 1). Two footnotes would only matter on 3.10+, and the
                             container runtime is 3.9: ast.Match does not exist there, so
                             it is looked up defensively, and  except*  (TryStar) is 3.11
                             syntax that cannot appear in this tree at all. The counts are
                             therefore IDENTICAL, not merely similar.
4. IMPORT FAN-OUT          - distinct modules a CRITICAL module imports.
5. IMPORT FAN-IN           - how many modules import a CRITICAL module.

Fan-in and fan-out are only evaluated for modules under --critical (service/services),
because a limit on how many imports infrastructure/cache.py has would be meaningless noise;
what the rule is for is "this slice module has become the hub everything reaches into".

RATCHET SEMANTICS (the three cases, spelled out because they are the whole design)
---------------------------------------------------------------------------------
* key NOT in complexity_baseline.json  -> FAIL, "new violation". New debt is not allowed.
* key IN the baseline, value GREW      -> FAIL, "debt grew, was N". The recorded number is
                                          a ceiling, not a target.
* key IN the baseline, no longer measured -> the key is PRINTED for removal, and that does
                                          not fail: paying debt off must never be the thing
                                          that breaks the build.
Keys carry no line numbers - 'path::lines', 'path::function::length',
'path::function::complexity', 'module::fan_out', 'module::fan_in' - because an edit above
a function would otherwise move its line and make already-recorded debt look brand new.
The function name is in the key; the line is only in the message.

Parsed with ast, never imported: importing a module here needs fastapi, pymysql, celery and
the whole dependency tree, and this guard has to run on any interpreter, inside a commit
hook, with no virtualenv.

USAGE
-----
    python backend/scripts/check_module_complexity.py
    python backend/scripts/check_module_complexity.py --json
    python backend/scripts/check_module_complexity.py --max-lines-per-file 600 --root service

Exit codes: 0 clean, 1 violations, 2 nothing was checked (the tree is not there - which is
NOT a pass). Same [OK]/[FAIL]/[WARN]/[SKIP]/[SUMMARY] output as check_backend_layout.py, so
'make structure', 'make guards' and the pre-commit hook absorb it unchanged.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

CHECK = "module-complexity"

# <checkout>/backend/scripts/check_module_complexity.py -> <checkout>/backend, whether that
# checkout is the superproject (backend/ nested) or the backend submodule on its own.
# Derived from the file rather than from the depth, which differs in a bare clone.
BACKEND = Path(__file__).resolve().parents[1]

# WHY the baseline path comes from THIS FILE and not from --root: root points at service/,
# so a root-relative baseline path would look for service/scripts/complexity_baseline.json,
# silently miss it, and then declare every recorded violation to be brand new debt. GPTHub
# hit exactly that and left the note in check_module_complexity.py:63-66.
BASELINE_FILE = Path(__file__).resolve().parent / "complexity_baseline.json"

CACHE_DIRS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv", "__pycache__"}

# ast.Match is 3.10+. On the 3.9 runtime this repository targets there is no match
# STATEMENT to count, so looking it up defensively changes no count - it only keeps the
# guard runnable on the interpreter that ships in the container.
MATCH_NODE = getattr(ast, "Match", None)

BRANCHING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.IfExp,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
    ast.Try,
)


@dataclass(frozen=True)
class Thresholds:
    """The five ceilings, in the order they appear in the summary."""

    max_lines_per_file: int
    max_function_length: int
    max_cyclomatic_complexity: int
    max_import_fan_out: int
    max_import_fan_in: int


class CyclomaticVisitor(ast.NodeVisitor):
    r"""Count decision points exactly as R:\GPTHub\backend\scripts\check_module_complexity.py.

    A copy rather than an import: the reference implementation lives in another repository
    and this guard must run from a bare backend/ checkout. The counting rules are the
    contract; keeping them identical is what makes a threshold of 70 mean the same number
    in both trees.

    Base 1: a function with no branch at all scores 1, not 0, so 'complexity' reads as the
    number of paths through the function rather than the number of ifs.
    """

    def __init__(self) -> None:
        self.complexity = 1

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, BRANCHING_NODES):
            self.complexity += 1
        if isinstance(node, ast.BoolOp):
            # 'a and b and c' is two decisions, not one: each extra operand is another
            # short-circuit that a test has to cover. max(..., 0) because a BoolOp always
            # holds at least two values, so the term can never be negative.
            self.complexity += max(len(node.values) - 1, 0)
        if MATCH_NODE is not None and isinstance(node, MATCH_NODE):
            self.complexity += len(node.cases)
        super().generic_visit(node)


class Report:
    """Collects [OK]/[FAIL]/[WARN]/[SKIP] lines and prints them in the house format.

    Deliberately a copy of the helper in check_backend_layout.py rather than an import of
    it: this guard must run from a bare backend/ checkout, where ../scripts does not
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
        if not self.ok:
            return "skip"
        return "pass"

    def emit(self, as_json: bool) -> int:
        nothing_checked = not self.ok and not self.failures
        if as_json:
            print(
                json.dumps(
                    {
                        "check": CHECK,
                        "status": self.status(),
                        "failures": self.failures,
                        "warnings": self.warnings,
                        "skips": self.skips,
                        "ok": self.ok,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            if nothing_checked:
                return 2
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
            "[SUMMARY] check="
            + CHECK
            + " status="
            + self.status()
            + " failures="
            + str(len(self.failures))
            + " warnings="
            + str(len(self.warnings))
            + " skipped="
            + str(len(self.skips))
        )
        if nothing_checked:
            print(
                "[FAIL] "
                + CHECK
                + ": nothing was checked, which is not a pass - "
                + "the tree this guard measures does not exist yet"
            )
            return 2
        return 1 if self.failures else 0


def display(path: Path) -> str:
    """Workspace-relative POSIX path for a message, so output is identical on Windows/CI."""
    try:
        return path.resolve().relative_to(BACKEND).as_posix()
    except ValueError:
        return path.as_posix()


def resolve(path_text: str) -> Path:
    """A --root/--critical value: absolute as given, relative to backend/ otherwise.

    Relative to BACKEND and not to the process CWD, because the guard is invoked from the
    superproject root by CI, from backend/ by a developer and from the repository root by
    pre-commit. Resolving against the CWD would make one of those three measure nothing.
    """
    candidate = Path(path_text)
    return (candidate if candidate.is_absolute() else BACKEND / candidate).resolve()


def collect_python_files(root: Path) -> List[Path]:
    """Every *.py under root, cache directories excluded, sorted for stable output."""
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*.py") if path.is_file() and not (set(path.parts) & CACHE_DIRS)
    )


def module_name(root: Path, path: Path) -> str:
    """Dotted module name relative to root, e.g. services.auth.application.auth_service."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.stem
    return ".".join(relative.with_suffix("").parts)


def in_root_package(dotted: str, root_name: str) -> str:
    """Strip the root package name from an absolute import, for graph matching.

    module_name() yields names relative to --root (service/), so a module is
    'services.auth.application.auth_service' while its own imports say
    'service.services.auth.application.auth_service'. Both spellings have to land on the
    same node or fan-in would be measured as zero for every module while looking correct.
    An import of something outside the package (fastapi, pymysql) is returned unchanged and
    simply never matches a module name.
    """
    if dotted == root_name:
        return ""
    if dotted.startswith(root_name + "."):
        return dotted[len(root_name) + 1 :]
    return dotted


def imported_module(base_module: str, node_module: Optional[str], level: int) -> Optional[str]:
    """Absolute dotted target of an import, relative levels resolved.

    Same arithmetic as GPTHub (check_module_complexity.py:114-123): level counts dots, so
    level 1 is the module's own package - 'from . import x' inside a.b.c targets a.b.
    """
    if level == 0:
        return node_module
    parts = base_module.split(".")
    if len(parts) < level:
        return None
    prefix = parts[: len(parts) - level]
    if node_module:
        prefix.extend(node_module.split("."))
    return ".".join(prefix)


def function_nodes(tree: ast.AST) -> List[ast.AST]:
    """Every def/async def in the module, nested ones included."""
    return [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def import_targets(module: str, tree: ast.AST, root_name: str) -> Set[str]:
    """The set of modules this file imports, as root-relative names."""
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(in_root_package(alias.name, root_name))
        elif isinstance(node, ast.ImportFrom):
            target = imported_module(module, node.module, node.level)
            if target:
                found.add(in_root_package(target, root_name))
    found.discard("")
    return found


def critical_imports(imported: str, critical_modules: Set[str]) -> Iterable[str]:
    """Critical modules that an imported dotted name reaches.

    The name itself and every package prefix of it are tested, so an import of
    'services.auth.application.auth_service' counts toward 'services.auth.application'.
    A PARENT import ('import service.services.auth') is deliberately NOT counted toward
    every module below it: Python loads the package's __init__ only, so claiming the
    children were imported would inflate fan-in with imports that never touch them.
    """
    if imported in critical_modules:
        yield imported
    parts = imported.split(".")
    for index in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:index])
        if candidate in critical_modules and candidate != imported:
            yield candidate


def measure(
    root: Path,
    critical_dirs: Sequence[Path],
    thresholds: Thresholds,
) -> Tuple[List[Tuple[str, int, str]], Dict[str, Tuple[int, str]], int, int, List[str]]:
    """Run the five metrics.

    Returns (violations, maxima, modules_measured, critical_measured, parse_errors), where a
    violation is (key, value, message) and maxima maps a metric to (value, label) for the
    worst measurement seen. The maxima are reported even when everything passes: they are
    how a reader - and a reviewer of this guard - can tell that it measured the tree instead
    of printing [OK] from an empty loop.
    """
    files = collect_python_files(root)
    root_name = root.name
    violations: List[Tuple[str, int, str]] = []
    maxima: Dict[str, Tuple[int, str]] = {
        name: (0, "-") for name in ("lines", "length", "complexity", "fan_out", "fan_in")
    }
    parse_errors: List[str] = []
    import_graph: Dict[str, Set[str]] = defaultdict(set)
    critical_modules: Set[str] = set()

    for file_path in files:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            parse_errors.append(display(file_path) + " (" + str(exc) + ")")
            continue
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as exc:
            detail = (str(exc).splitlines() or [""])[0]
            parse_errors.append(display(file_path) + " (" + detail + ")")
            continue

        module = module_name(root, file_path)
        relative = file_path.relative_to(root).as_posix()

        if any(_is_within(file_path, directory) for directory in critical_dirs):
            critical_modules.add(module)

        line_count = len(source.splitlines())
        if line_count > maxima["lines"][0]:
            maxima["lines"] = (line_count, display(file_path))
        if line_count > thresholds.max_lines_per_file:
            violations.append(
                (
                    relative + "::lines",
                    line_count,
                    display(file_path)
                    + " is "
                    + str(line_count)
                    + " line(s), over the ceiling of "
                    + str(thresholds.max_lines_per_file)
                    + ". Split it by responsibility: persistence into persistence/, rules into"
                    " application/, wiring into composition/.",
                )
            )

        for function in function_nodes(tree):
            length = (function.end_lineno or function.lineno) - function.lineno + 1
            label = display(file_path) + ":" + str(function.lineno) + " " + function.name
            if length > maxima["length"][0]:
                maxima["length"] = (length, label)
            if length > thresholds.max_function_length:
                violations.append(
                    (
                        relative + "::" + function.name + "::length",
                        length,
                        label
                        + " is "
                        + str(length)
                        + " line(s), over the ceiling of "
                        + str(thresholds.max_function_length)
                        + ". Extract the steps into named helpers.",
                    )
                )

            visitor = CyclomaticVisitor()
            visitor.visit(function)
            if visitor.complexity > maxima["complexity"][0]:
                maxima["complexity"] = (visitor.complexity, label)
            if visitor.complexity > thresholds.max_cyclomatic_complexity:
                violations.append(
                    (
                        relative + "::" + function.name + "::complexity",
                        visitor.complexity,
                        label
                        + " has cyclomatic complexity "
                        + str(visitor.complexity)
                        + ", over the ceiling of "
                        + str(thresholds.max_cyclomatic_complexity)
                        + ". Each branch is a path a test has to cover; split the decisions"
                        " into separate functions.",
                    )
                )

        import_graph[module] = import_targets(module, tree, root_name)

    fan_out = {module: len(imports) for module, imports in import_graph.items()}
    fan_in: Counter = Counter()
    for imports in import_graph.values():
        for imported in imports:
            for target in critical_imports(imported, critical_modules):
                fan_in[target] += 1

    for module in sorted(critical_modules):
        out = fan_out.get(module, 0)
        if out > maxima["fan_out"][0]:
            maxima["fan_out"] = (out, module)
        if out > thresholds.max_import_fan_out:
            violations.append(
                (
                    module + "::fan_out",
                    out,
                    module
                    + " imports "
                    + str(out)
                    + " module(s), over the ceiling of "
                    + str(thresholds.max_import_fan_out)
                    + ". A slice module that imports this much is doing work another layer"
                    " owns - usually because it builds its own collaborators instead of"
                    " taking them from service/composition/.",
                )
            )
        fin = fan_in.get(module, 0)
        if fin > maxima["fan_in"][0]:
            maxima["fan_in"] = (fin, module)
        if fin > thresholds.max_import_fan_in:
            violations.append(
                (
                    module + "::fan_in",
                    fin,
                    module
                    + " is imported by "
                    + str(fin)
                    + " module(s), over the ceiling of "
                    + str(thresholds.max_import_fan_in)
                    + ". Something that many modules reach into is a shared utility wearing"
                    " a slice's name; move it to service/shared/ or behind composition/.",
                )
            )

    return violations, maxima, len(files), len(critical_modules), parse_errors


def _is_within(path: Path, directory: Path) -> bool:
    """True when path is inside directory, without relying on Path.is_relative_to (3.9)."""
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True


def load_baseline(path: Path) -> Tuple[Dict[str, int], str]:
    """The recorded debt, plus a note explaining why it could not be used.

    A baseline that cannot be read is NOT a pass and NOT an error: it is an empty baseline,
    which makes every violation new. That is the strict direction - a broken file cannot
    silently excuse debt - and the note says out loud what happened.
    """
    if not path.is_file():
        return {}, (
            display(path)
            + " is missing, so nothing is recorded and every violation below counts as new"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {}, (
            display(path) + " could not be read (" + str(exc) + "), so every violation"
            " counts as new"
        )
    allowed = document.get("allowed") if isinstance(document, dict) else None
    if not isinstance(allowed, dict):
        return {}, (
            display(path) + " has no 'allowed' object, so every violation below counts as new"
        )
    recorded = {
        str(key): value
        for key, value in allowed.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    return recorded, ""


def report_ratchet(
    report: Report,
    violations: Sequence[Tuple[str, int, str]],
    baseline: Dict[str, int],
    baseline_note: str,
) -> None:
    """Apply the three ratchet cases and put the result in the report."""
    if baseline_note:
        report.warnings.append(baseline_note)

    measured = {key: value for key, value, _ in violations}
    for key, value, message in sorted(violations):
        recorded = baseline.get(key)
        if recorded is None:
            report.failures.append(
                "new violation: "
                + message
                + " (key '"
                + key
                + "'). Either fix it or record it in "
                + BASELINE_FILE.name
                + " - deliberately accepting debt has to be visible in review, which is the"
                " only thing that keeps this file honest."
            )
        elif value > recorded:
            report.failures.append(
                "debt grew, was "
                + str(recorded)
                + ": "
                + message
                + " (key '"
                + key
                + "'). The recorded value is a ceiling, not a target; raise it in "
                + BASELINE_FILE.name
                + " only if the growth is deliberate and reviewed."
            )

    paid = sorted(set(baseline) - set(measured))
    for key in paid:
        report.warnings.append(
            "debt paid, remove from "
            + BASELINE_FILE.name
            + ": '"
            + key
            + "'. This does not fail the check - paying debt off must never break the build -"
            " but a stale entry is a lie about the tree, so delete the line."
        )


def report_maxima(
    report: Report,
    maxima: Dict[str, Tuple[int, str]],
    modules: int,
    critical: int,
    thresholds: Thresholds,
) -> None:
    """Print the worst measurement per metric, passing or failing.

    WHY: five [OK] lines that say "no violation" are indistinguishable from a guard that
    iterated an empty directory. These lines carry the measured number, so a wrong count is
    visible in the output instead of hidden behind a green tick.
    """
    lines_value, lines_label = maxima["lines"]
    report.ok.append(
        str(modules)
        + " module(s) measured; longest file "
        + lines_label
        + " "
        + str(lines_value)
        + " line(s), ceiling "
        + str(thresholds.max_lines_per_file)
    )
    length_value, length_label = maxima["length"]
    report.ok.append(
        "longest function "
        + length_label
        + " "
        + str(length_value)
        + " line(s), ceiling "
        + str(thresholds.max_function_length)
    )
    complexity_value, complexity_label = maxima["complexity"]
    report.ok.append(
        "highest cyclomatic complexity "
        + complexity_label
        + " "
        + str(complexity_value)
        + ", ceiling "
        + str(thresholds.max_cyclomatic_complexity)
    )
    fan_out_value, fan_out_label = maxima["fan_out"]
    report.ok.append(
        str(critical)
        + " critical module(s) measured; highest fan-out "
        + fan_out_label
        + " "
        + str(fan_out_value)
        + ", ceiling "
        + str(thresholds.max_import_fan_out)
    )
    fan_in_value, fan_in_label = maxima["fan_in"]
    report.ok.append(
        "highest fan-in "
        + fan_in_label
        + " "
        + str(fan_in_value)
        + ", ceiling "
        + str(thresholds.max_import_fan_in)
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ratchet the size and cyclomatic complexity of backend modules"
    )
    parser.add_argument(
        "--root",
        default="service",
        help="directory to measure, relative to backend/ unless absolute (default: service)",
    )
    parser.add_argument(
        "--critical",
        nargs="+",
        default=["service/services"],
        help=(
            "directories whose modules also get fan-in and fan-out limits, relative to"
            " backend/ unless absolute (default: service/services)"
        ),
    )
    parser.add_argument("--max-lines-per-file", type=int, default=750)
    parser.add_argument("--max-function-length", type=int, default=250)
    parser.add_argument("--max-cyclomatic-complexity", type=int, default=70)
    parser.add_argument("--max-import-fan-out", type=int, default=120)
    parser.add_argument("--max-import-fan-in", type=int, default=160)
    parser.add_argument(
        "--baseline",
        default=None,
        help=(
            "baseline file to read (default: complexity_baseline.json beside this script)."
            " Overriding it is for testing the ratchet itself against a throwaway tree -"
            " the checked-in default is the only one that gates a commit."
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args(argv)

    root = resolve(args.root)
    critical_dirs = [resolve(path) for path in args.critical]
    baseline_path = Path(args.baseline).resolve() if args.baseline else BASELINE_FILE
    thresholds = Thresholds(
        max_lines_per_file=args.max_lines_per_file,
        max_function_length=args.max_function_length,
        max_cyclomatic_complexity=args.max_cyclomatic_complexity,
        max_import_fan_out=args.max_import_fan_out,
        max_import_fan_in=args.max_import_fan_in,
    )

    if not root.is_dir():
        print("[FAIL] " + CHECK + ": " + display(root) + " does not exist")
        print("[SUMMARY] check=" + CHECK + " status=fail failures=1 warnings=0 skipped=0")
        return 2

    violations, maxima, modules, critical, parse_errors = measure(root, critical_dirs, thresholds)
    if modules == 0:
        print("[FAIL] " + CHECK + ": " + display(root) + " holds no Python module")
        print("[SUMMARY] check=" + CHECK + " status=fail failures=1 warnings=0 skipped=0")
        return 2

    report = Report()
    for error in parse_errors:
        report.warnings.append(
            error + " could not be parsed, so its size and complexity were NOT measured."
            " 'python -m compileall' and ruff own the syntax error itself."
        )
    if critical == 0:
        report.skips.append(
            "no module under "
            + ", ".join(display(directory) for directory in critical_dirs)
            + " was found, so fan-in and fan-out were not measured. NOT a pass."
        )
    report_ratchet(report, violations, *load_baseline(baseline_path))
    report_maxima(report, maxima, modules, critical, thresholds)
    if not violations:
        recorded, _ = load_baseline(baseline_path)
        report.ok.append(
            "no violation measured, and "
            + BASELINE_FILE.name
            + " records "
            + str(len(recorded))
            + " key(s): the ratchet is at "
            + ("zero" if not recorded else "its recorded level")
            + " and can only shrink from here"
        )
    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - a guard that crashes is a guard that fails
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
