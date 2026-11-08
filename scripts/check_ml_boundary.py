#!/usr/bin/env python3
r"""ML boundary: the API must not carry ML code, and the sidecar must not leak in.

WHY THIS GUARD EXISTS
---------------------
HireSnap used to run torch inside the API process. That cost the API a CUDA
context, several seconds of start-up and roughly two gigabytes of resident memory
it never needed on a login request, and it turned a CUDA out-of-memory kill into
an outage of the process serving authentication. The ML pipeline now lives in its
own service (ml/, docs/SIDECARS.md) and the API reaches it over HTTP.

That separation is a PROPERTY OF THE TREE, not a task that was completed once.
Nothing stops the next commit from writing "import torch" in a route handler to
"just" compute one embedding inline: the tests still pass, the image still
builds, and the API quietly needs a GPU again. No other check in this repository
can see that - ruff does not know which imports are architecturally forbidden and
the compose guards read YAML, not Python - so the rule has to be executable.

The scan uses ast, not a grep. A grep would fire on every comment that explains
the rule (this file's own docstring, backend/service/infrastructure/ml_sidecar.py's history)
and would miss nothing else; a real import, including one nested inside a
function - which is exactly how the in-process fallback is written - is an
ImportFrom node and cannot hide from the parser.

WHAT IS ASSERTED, for backend/
--------------------------------
  1. FAIL  a module under service/ imports a GPU/ML library (torch, torchvision,
           torchaudio, torchmetrics, torcheval, transformers,
           sentence_transformers, moviepy, librosa, cosmos_tokenizer) or the
           deleted src.modelling package.
  2. FAIL  backend/requirements.txt or requirements-platform.txt names one of
           those distributions - an installed dependency is how the next inline
           import gets written, and it puts torch's download size and CVE surface
           into a service that only serves HTTP.
  3. FAIL  a directory named modelling/ or cosmos_tokenizer/ exists ANYWHERE
           under backend/, not only at the two paths the move emptied. A file
           named after the old location is the easy case; the expensive one is
           backend/service/modelling/ quietly growing back in the new tree, and
           a check that only knows the old paths would watch it do so.
  4. WARN  the backend ML client (service/infrastructure/ml_sidecar.py, called
           service/services/ml_client.py before the vertical-slice move) still
           carries an in-process transport
           (a fallback that imports the ML stack locally). That fallback is the
           one path that can silently re-link the API against torch, so it must
           stay explicit and visible rather than quietly deleted from the check.
  5. FAIL  the backend ROOT holds a loose .py/.sh/.bat/.sql/.txt file outside a
           short documented allowlist (pyproject.toml, requirements*.txt,
           README/LICENSE). This is the assertion that stops the pile from
           growing back: the layout puts code in service/, scripts in scripts/,
           the schema in database/ and assets in docs/, so a module sitting at
           the root is a file that was never placed. It is reported by path so
           the fix is a move, not an argument.

Tests are excluded from the import scan: a test may legitimately assert that
"importing the route module does not import torch", and the only way to write
that assertion is usually to name the module it must not import. Files inside a
FORBIDDEN_DIR_NAMES directory are not scanned either - the directory's existence is
already the failure, and reporting every line inside it would bury the one line
that matters.

Failures are deduplicated per (file, top-level module): one import of torch in a
file is a violation, forty lines of torch calls below it are the same violation.

Mirror of the same class of rule in R:\GPTHub\scripts\check_*.py: a gate that
states an architectural invariant in prose only is a gate that will be violated
by the third contributor.

Exit codes: 0 ok (warnings allowed), 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

CHECK = "ml-boundary"
ROOT = Path(__file__).resolve().parent.parent
BACKEND = Path("backend")
# The ONLY importable package. The refactor replaced the src/ tree wholesale, so
# every path in this file that used to end in src/ now ends in service/ - see the
# module docstring for why the layout is a rule and not a preference.
BACKEND_SRC = BACKEND / "service"

# Directory NAMES that must not exist anywhere under backend/, keyed by name and
# carrying where the content belongs. Deliberately not a list of the two paths the
# move emptied: the expensive regression is not backend/src/modelling coming back,
# it is backend/service/modelling appearing BESIDE the new tree, and a check that
# only knows the old location would watch that happen and report a pass.
FORBIDDEN_DIR_NAMES = {
    "modelling": "ml/service/ - the inference modules moved to the sidecar",
    "cosmos_tokenizer": "ml/ - the tokenizer is a model dependency of the sidecar",
}

# Directories that are not part of the layout and would make the walk slow or
# noisy. Pruned, never descended into.
WALK_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}

# Loose files the backend ROOT is allowed to keep. Everything else with one of the
# extensions below belongs in a subdirectory: code in service/, entry points in
# scripts/, the schema in database/, assets in docs/. Each entry is here because a
# tool demands that exact path - configuration and metadata are read from the
# project root, and README/LICENSE are conventions no tool can relocate - and the
# list is kept tiny on purpose: a generous allowlist is a guard that documents the
# mess it permits.
#
# WHY the three packaging files are legitimate and not a loophole: a Python
# project's root IS where its metadata lives, and moving any of these breaks a tool
# rather than tidying the tree.
#   pyproject.toml   PEP 621 metadata plus [tool.ruff], [tool.mypy] and
#                    [tool.pytest.ini_options]; every one of those tools searches
#                    upward from the file it is run on and STOPS at this file, so a
#                    copy under service/ would silently stop applying to the root
#                    files it also governs.
#   pytest.ini       pytest reads its rootdir configuration from the arguments'
#                    first common ancestor; with the suite in tests/ the ancestor
#                    is this root, and an ini file one level deeper would only be
#                    found by pointing pytest at it explicitly - which is how the
#                    Makefile, CI and a developer end up running three different
#                    configurations.
#   .python-version  the toolchain pin (pyenv, uv, asdf and mise all read this exact
#                    path). It records the interpreter the project is tested on and
#                    cannot live under service/ without the pin silently lapsing to
#                    whatever the shell happens to have.
# None of the three is code, none is importable, and none is the "stray script at
# the root" this rule exists to catch - backend/scripts/ still owns anything
# executable.
ROOT_FILE_ALLOWLIST = {
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".python-version",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "pytest.ini",
}
ROOT_FILE_PATTERNS = [
    # requirements.txt, requirements-platform.txt / requirements_platform.txt (the
    # separator has been spelled both ways: docs/ARCHITECTURE.md writes the hyphen
    # and the checked-in file uses the underscore, and the Dockerfile ARGs point at
    # whichever the file is actually called), and any future requirements-*.
    re.compile(r"^requirements([._-][A-Za-z0-9._-]+)?\.txt$"),
    re.compile(r"^LICENSE(\..*)?$"),
    re.compile(r"^README(\..*)?$"),
]
# Only these extensions make a root file "loose code". A .toml or a .md at the root
# is configuration or prose; a .py, a shell script, a .bat, the schema dump or a
# .txt is something that should have a home, and .txt is included because that is
# how a stray note or a stray requirements copy starts.
LOOSE_ROOT_SUFFIXES = {".py", ".sh", ".bat", ".sql", ".txt"}

# Top-level module names that must not be importable from backend/service.
# torchmetrics and torcheval are listed even though the ticket named only torch:
# they are torch-only packages, so importing one is the same violation with a
# different spelling, and a guard that has to be edited the first time somebody
# reaches for a sibling package is a guard nobody edits.
FORBIDDEN_MODULES = {
    "torch": "PyTorch runtime",
    "torchvision": "torch vision transforms",
    "torchaudio": "torch audio I/O",
    "torchmetrics": "torch metrics",
    "torcheval": "torch evaluation metrics",
    "transformers": "Hugging Face transformers",
    "sentence_transformers": "sentence-transformers",
    "moviepy": "video/audio decoding",
    "librosa": "audio analysis",
    "cosmos_tokenizer": "Cosmos tokenizer (vendored, now under ml/)",
}

DELETED_PACKAGES = {
    "src.modelling": "the deleted backend ML package (now ml/service/)",
    "service.modelling": "the deleted backend ML package (now ml/service/)",
}

# Distributions that must not appear in a hand-maintained requirements file.
# Keys are normalised the way requirement_name() normalises: lower case, '_'
# turned into '-'.
FORBIDDEN_DISTRIBUTIONS = {
    "torch": "torch",
    "torchvision": "torchvision",
    "torchaudio": "torchaudio",
    "torchmetrics": "torchmetrics",
    "torcheval": "torcheval",
    "transformers": "transformers",
    "sentence-transformers": "sentence-transformers",
    "moviepy": "moviepy",
    "librosa": "librosa",
    "cosmos-tokenizer": "cosmos_tokenizer",
}

# Files a human edits. A *lock* file is a generated snapshot, so a hit there is a
# warning with the regeneration instruction: editing a lock by hand is not the
# fix, and failing on it would only teach people to skip the guard.
HAND_MAINTAINED_REQUIREMENTS = [
    BACKEND / "requirements.txt",
    # Renamed from requirements_platform.txt in the same refactor that moved
    # src/ to service/: the hyphen is what the GPTHub layout uses, and a
    # requirements file that the image no longer installs is a file that can
    # carry torch for months without anybody noticing.
    BACKEND / "requirements-platform.txt",
]
GENERATED_REQUIREMENTS = [
    BACKEND / "docker" / "requirements.lock.txt",
]

# Markers of an in-process transport inside the backend's ML client, matched
# case-insensitively against every line, comments included - the point of the
# warning is exactly that the fallback still EXISTS, and its explanation is the
# most reliable evidence of it.
INPROCESS_MARKERS = [
    "in-process",
    "in process",
    "src.modelling",
    "service.modelling",
    "local fallback",
]



# Exceptions that make an import OPTIONAL rather than hard. An
# "try: import torch / except ImportError: torch = None" is a different claim from
# a module-scope "import torch": the first says "use it if the image happens to
# have it", the second says "this image must have it". Only the second can pull a
# multi-gigabyte CUDA stack into the API, so only the second is a failure - but the
# first is still reported, because an optional import is the seam through which a
# real dependency comes back.
OPTIONAL_GUARDS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}

# The boundary runs in BOTH directions, and the second one is not symmetry for its
# own sake. ml/ is built from its own build context and its contract tests run on a
# runner that checks out ml/ alone, so a module-level "from src.modelling import ..."
# inside ml/ makes the sidecar unbuildable and untestable without backend/ beside it -
# and it puts ML code back in the API tree, which is the violation this guard exists
# to prevent. A sys.path insert into another submodule is the same coupling written
# as a side effect.
SIDECAR = Path("ml")
SIDECAR_SRC = SIDECAR / "service"
SIDECAR_TESTS = SIDECAR / "tests"
FORBIDDEN_SIDECAR_TREES = {
    "src": "the API's src/ package",
    "backend": "the API checkout",
}
SYSPATH_MUTATORS = {"insert", "append"}

REQUIREMENT_NAME = re.compile(r"^[ \t]*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")

# Deliberately narrow: a Windows drive path or an absolute POSIX path. Anything
# else in a requirement line is a version specifier or an option, not a path.
ABS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")


class Report:
    def __init__(self) -> None:
        self.failures = []
        self.warnings = []
        self.skips = []
        self.ok = []

    def status(self):
        if self.failures:
            return "fail"
        if self.skips and not self.ok:
            return "skip"
        return "pass"

    def emit(self, as_json):
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


def rel(path):
    """Workspace-relative POSIX path, for stable output on every platform."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def forbidden_dir_for(path):
    """Return the forbidden directory NAME that path sits under, or None."""
    for part in path.parts:
        if part in FORBIDDEN_DIR_NAMES:
            return part
    return None


def scan_forbidden_dirs(report):
    """Fail on any modelling/ or cosmos_tokenizer/ directory anywhere under backend/.

    WHY the walk covers the whole subtree instead of the two paths the refactor
    emptied: the move deleted backend/src/modelling and backend/cosmos_tokenizer,
    and checking those two paths proves only that the deletion happened. The
    regression that costs money is the next copy - backend/service/modelling/
    grown back beside the new tree, or an inference helper vendored under
    backend/tests/fixtures - and a path-based check would report a pass while the
    API links torch again. The walk is pruned of caches and virtualenvs so it stays
    fast on a working checkout.
    """
    base = ROOT / BACKEND
    if not base.is_dir():
        report.skips.append(
            rel(base) + " does not exist, so no deleted tree could be checked."
            " NOT a pass."
        )
        return
    found = []
    for current, dirnames, _filenames in os.walk(base):
        dirnames[:] = sorted(name for name in dirnames if name not in WALK_SKIP_DIRS)
        for name in dirnames:
            if name in FORBIDDEN_DIR_NAMES:
                found.append(Path(current) / name)
    if not found:
        report.ok.append(
            "no modelling/ or cosmos_tokenizer/ directory exists anywhere under "
            + rel(base)
        )
        return
    for path in found:
        reason = FORBIDDEN_DIR_NAMES[path.name]
        members = sorted(item.name for item in path.iterdir())[:4] if path.is_dir() else []
        shown = ", ".join(members) + ("..." if len(members) >= 4 else "")
        report.failures.append(
            rel(path) + "/ still exists (" + shown + "). Its content belongs in "
            + reason + ". Two copies of the inference code is worse than either one"
            " alone: the API keeps the torch dependency and the copies drift"
            " (docs/KNOWN_GAPS.md)"
        )


def scan_backend_root(report):
    """Fail on loose .py/.sh/.bat/.sql/.txt files at the backend ROOT.

    WHY this exists at all: the layout is the rule. Code lives in service/,
    entry points in scripts/, the schema in database/, assets in docs/. A module
    sitting at the root is a file nobody placed, it is invisible to the package
    imports and to ruff's configured source root, and it is exactly how the old
    tree accumulated env.py, create_sql.py, main.sh, setup.sh and a second copy
    of the schema dump. The allowlist is short because a generous one documents
    the mess it permits: packaging and lint configuration MUST be at the project
    root, and README/LICENSE are conventions no tool will relocate.

    Every offending path is printed in its own [FAIL] line - a message that says
    "3 loose files" leaves the reader to guess which three, and the fix is a move
    per file.
    """
    base = ROOT / BACKEND
    if not base.is_dir():
        report.skips.append(
            rel(base) + " does not exist, so its root files were not checked."
            " NOT a pass."
        )
        return
    loose = []
    for path in sorted(base.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name in ROOT_FILE_ALLOWLIST:
            continue
        if any(pattern.match(name) for pattern in ROOT_FILE_PATTERNS):
            continue
        if path.suffix.lower() in LOOSE_ROOT_SUFFIXES:
            loose.append(path)
    if not loose:
        report.ok.append(
            "the root of " + rel(base) + " holds no loose .py/.sh/.bat/.sql/.txt"
            " file (only the documented allowlist: packaging metadata such as"
            " pyproject.toml/pytest.ini/.python-version, requirements, README/LICENSE)"
        )
        return
    for path in loose:
        report.failures.append(
            rel(path) + " is a loose file at the root of " + rel(base) + ". The"
            " layout gives every kind of file a home - code in service/, entry"
            " points in scripts/, the schema dump in database/, images and"
            " documents in docs/ - so a root file is a file that was never placed."
            " It is outside the package imports and outside the linters'"
            " configured roots, which is how the old tree accumulated env.py,"
            " create_sql.py, main.sh, setup.sh and a stale copy of the schema."
            " Move it (or, if a tool demands that exact root path, add it to"
            " ROOT_FILE_ALLOWLIST in scripts/check_ml_boundary.py with the reason)"
            " (AGENTS.md, docs/PLATFORM_LAYER.md)"
        )


def source_files():
    """Every .py under backend/service, tests excluded, deterministically ordered."""
    base = ROOT / BACKEND_SRC
    if not base.is_dir():
        return []
    found = []
    for path in base.rglob("*.py"):
        parts = path.relative_to(base).parts
        if any(part in ("tests", "test", "__pycache__") for part in parts):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        found.append(path)
    return sorted(found)


def dotted_from_relative(level, module, file_path):
    """Resolve "from ..x import y" against the file's own package path.

    backend/service/infrastructure/ml_sidecar.py lives in package
    service.infrastructure, so level 1 (a single dot) means
    service.infrastructure.<module> and level 2 means service.<module>. Without this, "from . import modelling" would slip past a
    check that only looks at absolute import names.
    """
    try:
        parts = list(file_path.relative_to(ROOT / BACKEND_SRC).parts[:-1])
    except ValueError:
        parts = []
    package = [BACKEND_SRC.name] + parts
    drop = level - 1
    if drop > 0:
        package = package[: len(package) - drop] if drop < len(package) else []
    if module:
        package = package + module.split(".")
    return ".".join(package)


def guarded_by_import_error(tree, node):
    r"""True when node sits inside a try/except that swallows a missing import.

    WHY this distinction exists at all: the failure this guard prevents is an API
    image that INSTALLS and LINKS the ML stack - that is what turns a GPU OOM into
    an outage for every request. A module-level "import torch" claims torch is
    required; "try: import torch / except ImportError: torch = None" claims the
    opposite, and on a torch-free image it simply sets None. Failing both would
    put the gate at odds with the code it is guarding on day one, which is how a
    gate gets disabled (the same reasoning that kept ruff advisory in R:\GPTHub
    until the debt was cleared). The optional form is reported as a warning, with
    the file and line, so it stays visible and cannot grow quietly.
    """
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Try):
            for handler in current.handlers:
                if handler.type is None:
                    return True
                names = []
                if isinstance(handler.type, ast.Name):
                    names = [handler.type.id]
                elif isinstance(handler.type, ast.Tuple):
                    names = [elt.id for elt in handler.type.elts if isinstance(elt, ast.Name)]
                if any(name in OPTIONAL_GUARDS for name in names):
                    return True
        current = parents.get(current)
    return False


def violations_in(path):
    """Return ([(lineno, dotted, reason, optional)], parse_error) for one file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        # A file that does not parse is not evidence of a boundary violation:
        # py_compile and ruff report it, and failing here would double-report the
        # same defect under a misleading reason.
        return [], (str(exc).splitlines()[0] if str(exc) else "SyntaxError")
    found = []

    def check(node, lineno, dotted):
        if not dotted:
            return
        optional = guarded_by_import_error(tree, node)
        top = dotted.split(".")[0]
        if top in FORBIDDEN_MODULES:
            found.append((lineno, dotted, FORBIDDEN_MODULES[top], optional))
            return
        for package, reason in DELETED_PACKAGES.items():
            if dotted == package or dotted.startswith(package + "."):
                found.append((lineno, dotted, reason, optional))
                return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check(node, node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                check(node, node.lineno, dotted_from_relative(node.level, module, path))
            else:
                check(node, node.lineno, module)
        elif isinstance(node, ast.Call):
            # importlib.import_module("torch") / __import__("torch") - the same
            # import wearing a string. Only a literal argument is decidable; a
            # computed one is left alone, because a false failure here cannot be
            # argued with and would get the guard disabled.
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    check(node, node.lineno, arg.value)
    return found, None


def requirement_name(line):
    stripped = line.split("#", 1)[0].strip()
    if not stripped or stripped.startswith("-"):
        return None
    match = REQUIREMENT_NAME.match(stripped)
    if not match:
        return None
    return match.group("name").lower().replace("_", "-")


def function_scope(tree, node):
    """True when the import sits inside a function or method body."""
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        current = parents.get(current)
    return False


def sidecar_files():
    """Every Python file of the sidecar, tests included."""
    found = []
    for directory in (SIDECAR_SRC, SIDECAR_TESTS):
        root = ROOT / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            parts = path.parts
            if "__pycache__" in parts:
                continue
            found.append(path)
    return found


def scan_sidecar(report):
    """Assert ml/ does not import or path-hack its way back into backend/.

    WHY the reverse direction is checked at all: ml/ is built from its own build
    context and its contract tests run on a runner that checks out ml/ alone, so a
    single module-level "from src.modelling import ..." makes the sidecar
    unbuildable and untestable without the API tree beside it. A sys.path insert
    into backend/ is the same coupling spelled as a side effect.

    A LAZY import inside a function is a warning, not a failure, for the same
    reason a try/except ImportError import is: it does not run at import time, so
    the sidecar still imports and still answers /health on a host with no backend
    tree. It is reported because it is the seam through which the coupling returns
    - and because the shim it usually belongs to (engine._ensure_sys_path) is the
    thing this guard exists to retire.
    """
    files = sidecar_files()
    if not files:
        report.skips.append(
            rel(ROOT / SIDECAR_SRC) + " holds no Python module, so the sidecar's"
            " imports were not checked. NOT a pass."
        )
        return
    offenders = 0
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            report.warnings.append(
                rel(path) + " could not be parsed (" + str(exc).splitlines()[0]
                + "), so its imports were not checked"
            )
            continue
        seen = set()
        for node in ast.walk(tree):
            dotted = ""
            if isinstance(node, ast.Import):
                for alias in node.names:
                    check_sidecar_import(
                        report, path, tree, node, alias.name, seen
                    )
                continue
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    # A relative import stays inside the sidecar package by
                    # construction; only an absolute one can escape it.
                    continue
                dotted = node.module or ""
            if dotted:
                check_sidecar_import(report, path, tree, node, dotted, seen)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else ""
            )
            if name not in SYSPATH_MUTATORS:
                continue
            first = node.func.value if isinstance(func, ast.Attribute) else None
            if not (isinstance(first, ast.Attribute) and first.attr == "path"):
                continue
            target = sidecar_syspath_target(node)
            if target is None:
                continue
            offenders += 1
            report.failures.append(
                rel(path) + ":" + str(node.lineno) + " inserts " + target
                + " on sys.path. The sidecar is a self-contained checkout: it is"
                " built from its own build context and its tests run without the API"
                " tree, so a path insert into another submodule makes its imports"
                " depend on where the checkout happens to sit on disk"
                " (docs/SIDECARS.md, docs/KNOWN_GAPS.md)"
            )
    if not report.failures or offenders == 0:
        report.ok.append(
            str(len(files)) + " sidecar module(s) under " + rel(ROOT / SIDECAR_SRC)
            + " reach neither into src/ nor into backend/ and never rewrite sys.path"
        )


def sidecar_syspath_target(node):
    """Describe the argument of a sys.path.<mutator> call, if it points outside."""
    if not node.args:
        return None
    arg = node.args[0]
    text = ""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        text = arg.value
    elif isinstance(arg, ast.Attribute):
        text = arg.attr
    elif isinstance(arg, ast.Name):
        text = arg.id
    elif isinstance(arg, ast.Call):
        func = arg.func
        text = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else ""
        )
    else:
        return None
    low = text.lower()
    if "backend" in low or low == "root" or "src" in low.split("/"):
        return repr(text) if text else "an expression"
    return None


def check_sidecar_import(report, path, tree, node, dotted, seen):
    """Report one sidecar import that escapes the sidecar's own tree."""
    if not dotted:
        return
    top = dotted.split(".")[0]
    if top not in FORBIDDEN_SIDECAR_TREES:
        return
    lazy = function_scope(tree, node) or guarded_by_import_error(tree, node)
    key = (top, lazy)
    if key in seen:
        return
    seen.add(key)
    where = rel(path) + ":" + str(node.lineno)
    reason = FORBIDDEN_SIDECAR_TREES[top]
    if lazy:
        report.warnings.append(
            where + " imports " + dotted + " (" + reason + ") inside a function"
            " body or a try/except, so it does not run at import time - the sidecar"
            " still imports and still answers /health without the API tree. Reported"
            " because it is the seam the coupling comes back through: the sidecar is"
            " built from its own context and tested on its own, so this path cannot"
            " work in the image (docs/SIDECARS.md)"
        )
    else:
        report.failures.append(
            where + " imports " + dotted + " (" + reason + ") at module scope. The"
            " sidecar is a self-contained checkout built from its own build context:"
            " a module-level import of the API tree makes ml/ unbuildable and its"
            " contract tests unrunnable without backend/ beside it. Move the module"
            " into ml/service/ and import it by bare name (docs/SIDECARS.md,"
            " docs/KNOWN_GAPS.md)"
        )


def scan_requirements(relative, report, generated):
    path = ROOT / relative
    if not path.is_file():
        return
    hits = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        name = requirement_name(line)
        if name and name in FORBIDDEN_DISTRIBUTIONS:
            hits.append((number, name, line.strip()))
    if not hits:
        report.ok.append(rel(path) + " names no ML distribution")
        return
    for number, name, line in hits:
        if generated:
            report.warnings.append(
                rel(path) + ":" + str(number) + " pins " + line
                + " - expected in a generated lock snapshot, but it means the API"
                " image still installs the ML stack. Regenerate it from the pruned"
                " requirements rather than editing this file by hand"
            )
        else:
            report.failures.append(
                rel(path) + ":" + str(number) + " declares '" + name + "'."
                " The API image must not install the ML stack: an installed"
                " dependency is how the next inline import gets written. It"
                " belongs in ml/ (docs/KNOWN_GAPS.md)"
            )


def scan_inprocess_client(report):
    # THE CANDIDATE LIST, not one hardcoded path. The vertical-slice move renamed
    # service/services/ml_client.py to service/infrastructure/ml_sidecar.py, and when
    # this function kept the old path it degraded into a permanent [SKIP]: the guard
    # still exited 0, still printed a summary, and checked nothing at all. A rule that
    # can only ever skip is worse than no rule, because the skip line reads like an
    # environment problem rather than a stale assertion.
    #
    # Both spellings are listed, in the order the tree evolved, so the check follows the
    # module through the rename instead of having to be retargeted by hand. The first one
    # that exists wins; if neither does, the SKIP below says so and names both.
    candidates = [
        BACKEND_SRC / "infrastructure" / "ml_sidecar.py",
        BACKEND_SRC / "services" / "ml_client.py",
    ]
    path = next((ROOT / relative for relative in candidates if (ROOT / relative).is_file()), None)
    if path is None:
        report.skips.append(
            "neither " + " nor ".join(rel(ROOT / c) for c in candidates)
            + " exists, so the in-process fallback could not be checked. NOT a pass -"
            " if the ML client was renamed again, add the new path to the candidate"
            " list in scripts/check_ml_boundary.py:scan_inprocess_client"
        )
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    # Prose is not evidence. The ML client says "in-process" in the module docstring
    # that PROVES the fallback was removed ("WHY THERE IS NO IN-PROCESS PATH ANY
    # MORE") and again in method docstrings that explain what the old signature was.
    # Matching markers against raw lines would warn about exactly the text that
    # documents the refactor, and a warning that fires on correct code teaches people
    # to ignore the check. So every DOCSTRING - module, class and function - is
    # skipped, together with comment-only lines: what is left is executable code, and
    # executable code is what can actually bring the fallback back. The rule is
    # structural (a bare string-expression statement is a docstring) rather than a
    # list of negating phrases, because "contains the word 'not'" is not a fact about
    # behaviour.
    skip = set()
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Expr):
                continue
            value = node.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            start = getattr(value, "lineno", None)
            end = getattr(value, "end_lineno", start)
            if start and end:
                skip.update(range(start, end + 1))
    except SyntaxError:
        pass  # unparsable is reported by ruff/py_compile, not by this warning
    first = None
    for number, line in enumerate(text.splitlines(), start=1):
        if number in skip:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        low = line.lower()
        for marker in INPROCESS_MARKERS:
            if marker in low:
                first = (number, marker, stripped)
                break
        if first:
            break
    if not first:
        report.ok.append(
            rel(path) + " carries no in-process transport marker (checked: "
            + ", ".join(INPROCESS_MARKERS) + ")"
        )
        return
    number, marker, line = first
    report.warnings.append(
        rel(path) + ":" + str(number) + " still mentions '" + marker + "' ('" + line
        + "'). A local fallback is the one path that can silently pull the whole ML"
        " stack back into the API process, so it must stay explicit, documented and"
        " OFF by default - that is why this is a warning and not a silent"
        " exclusion. Retiring it is a tracked item in docs/KNOWN_GAPS.md"
    )


def main():
    parser = argparse.ArgumentParser(
        description="assert the ML stack lives in ml/ and not inside the API"
    )
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args()
    report = Report()

    # --- 1. the deleted trees must stay deleted everywhere -----------------
    scan_forbidden_dirs(report)

    # --- 2. no ML imports under backend/service ----------------------------
    files = [path for path in source_files() if forbidden_dir_for(path) is None]
    if not files:
        report.skips.append(
            rel(ROOT / BACKEND_SRC)
            + " has no Python source outside the deleted trees, so no import could"
            " be verified. NOT a pass."
        )
    checked = 0
    for path in files:
        checked += 1
        found, parse_error = violations_in(path)
        if parse_error:
            report.warnings.append(
                rel(path) + " could not be parsed (" + parse_error
                + "), so its imports were not checked"
            )
        hard, optional = {}, {}
        for lineno, dotted, reason, is_optional in found:
            target = optional if is_optional else hard
            key = dotted.split(".")[0] if dotted.split(".")[0] in FORBIDDEN_MODULES else dotted
            # One entry per (file, top module): forty lines of torch calls under
            # one "import torch" are the same violation.
            if key not in target:
                target[key] = (lineno, dotted, reason)
            elif len(target[key]) == 3:
                target[key] = target[key] + (1,)
            else:
                target[key] = target[key][:3] + (target[key][3] + 1,)
        for key in sorted(hard):
            entry = hard[key]
            lineno, dotted, reason = entry[0], entry[1], entry[2]
            extra = (" (+" + str(entry[3]) + " more line(s))") if len(entry) == 4 else ""
            report.failures.append(
                rel(path) + ":" + str(lineno) + " imports " + dotted + " (" + reason
                + ")" + extra + ". ML work is the sidecar's job: the API must call"
                " its HTTP routes (backend/service/infrastructure/ml_sidecar.py), not link"
                " library. This is the boundary, not a style preference - an"
                " in-process model turns a GPU OOM into an API outage"
                " (docs/KNOWN_GAPS.md)"
            )
        for key in sorted(optional):
            entry = optional[key]
            lineno, dotted, reason = entry[0], entry[1], entry[2]
            extra = (" (+" + str(entry[3]) + " more line(s))") if len(entry) == 4 else ""
            report.warnings.append(
                rel(path) + ":" + str(lineno) + " imports " + dotted + " (" + reason
                + ")" + extra + " inside try/except ImportError, so it is OPTIONAL"
                " rather than a hard dependency. Not a failure - the API still"
                " imports on a torch-free image - but this is the seam a real"
                " dependency comes back through, so keep it (a) guarded, (b) with"
                " the None branch doing something sensible, and (c) out of every new"
                " module (docs/KNOWN_GAPS.md)"
            )
    if checked:
        report.ok.append(
            str(checked) + " module(s) under " + rel(ROOT / BACKEND_SRC)
            + " import no ML library"
        )

    # --- 3. the sidecar must not reach back into the API -------------------
    # The same boundary in the other direction. It is not symmetry for its own
    # sake: ml/ is built from its own context and tested on its own (see the ml job
    # in .github/workflows/ci.yml), so a single "from src.modelling import ..."
    # makes the sidecar impossible to build or test without the API tree beside it,
    # and every sys.path insert into backend/ re-creates the coupling the split
    # removed. The rule lives here, next to its mirror image, because a reviewer
    # reading one list should see the whole contract.
    scan_sidecar(report)

    # --- 4. requirements must not pin the ML stack -------------------------
    for relative in HAND_MAINTAINED_REQUIREMENTS:
        scan_requirements(relative, report, generated=False)
    for relative in GENERATED_REQUIREMENTS:
        scan_requirements(relative, report, generated=True)

    # --- 5. the in-process fallback must remain visible --------------------
    scan_inprocess_client(report)

    # --- 6. the backend root is a layout, not a shelf ----------------------
    # LAST, after the import scan, because a loose root file is also a file the
    # import scan never reached: service/ is the only package, so anything at the
    # root is dead weight no reviewer is looking at. Reporting it after the
    # boundary violations keeps the summary ordered by cost - first "the API links
    # torch again", then "somebody dropped a script at the root".
    scan_backend_root(report)

    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a guard must never traceback silently
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
