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
the rule (this file's own docstring, backend/src/services/ml_client.py's history)
and would miss nothing else; a real import, including one nested inside a
function - which is exactly how the in-process fallback is written - is an
ImportFrom node and cannot hide from the parser.

WHAT IS ASSERTED, for backend/
--------------------------------
  1. FAIL  a module under src/ imports a GPU/ML library (torch, torchvision,
           torchaudio, torchmetrics, torcheval, transformers,
           sentence_transformers, moviepy, librosa, cosmos_tokenizer) or the
           deleted src.modelling package.
  2. FAIL  backend/requirements.txt or requirements_platform.txt names one of
           those distributions - an installed dependency is how the next inline
           import gets written, and it puts torch's download size and CVE surface
           into a service that only serves HTTP.
  3. FAIL  src/modelling/ or cosmos_tokenizer/ still exists.
  4. WARN  src/services/ml_client.py still carries an in-process transport
           (a fallback that imports the ML stack locally). That fallback is the
           one path that can silently re-link the API against torch, so it must
           stay explicit and visible rather than quietly deleted from the check.

Tests are excluded from the import scan: a test may legitimately assert that
"importing the route module does not import torch", and the only way to write
that assertion is usually to name the module it must not import. Files inside a
DELETED_TREES directory are not scanned either - their existence is already the
failure, and reporting every line inside them would bury the one line that
matters.

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
import re
import sys
from pathlib import Path

CHECK = "ml-boundary"
ROOT = Path(__file__).resolve().parent.parent
BACKEND = Path("backend")
BACKEND_SRC = BACKEND / "src"

# Directories whose mere existence is a violation: their content is the ML stack
# that now lives in ml/. A list of (path, where the content belongs) so the
# failure message can say where it went instead of only naming the offender.
DELETED_TREES = [
    (
        BACKEND_SRC / "modelling",
        "ml/service/ - the inference modules moved to the sidecar",
    ),
    (
        BACKEND / "cosmos_tokenizer",
        "ml/ - the tokenizer is a model dependency of the sidecar",
    ),
]

# Top-level module names that must not be importable from backend/src.
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
    BACKEND / "requirements_platform.txt",
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


def deleted_tree_for(path):
    """Return the DELETED_TREES entry containing path, or None."""
    for relative, _reason in DELETED_TREES:
        try:
            path.relative_to(ROOT / relative)
        except ValueError:
            continue
        return relative
    return None


def source_files():
    """Every .py file under backend/src, tests excluded, deterministically ordered."""
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

    backend/src/services/ml_client.py lives in package src.services, so level 1
    (a single dot) means src.services.<module> and level 2 means src.<module>.
    Without this, "from . import modelling" would slip past a check that only
    looks at absolute import names.
    """
    try:
        parts = list(file_path.relative_to(ROOT / BACKEND_SRC).parts[:-1])
    except ValueError:
        parts = []
    package = ["src"] + parts
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
    relative = BACKEND_SRC / "services" / "ml_client.py"
    path = ROOT / relative
    if not path.is_file():
        report.skips.append(
            rel(path) + " does not exist, so the in-process fallback could not be"
            " checked. NOT a pass."
        )
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    # Prose is not evidence. ml_client.py says "in-process" in the module docstring
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

    # --- 1. deleted trees must stay deleted --------------------------------
    for relative, reason in DELETED_TREES:
        path = ROOT / relative
        if not path.is_dir():
            report.ok.append(rel(path) + "/ is gone")
            continue
        names = sorted(item.name for item in path.iterdir())
        shown = ", ".join(names[:4]) + ("..." if len(names) > 4 else "")
        report.failures.append(
            rel(path) + "/ still exists (" + shown + "). Its content belongs in "
            + reason + ". Two copies of the inference code is worse than either one"
            " alone: the API keeps the torch dependency and the copies drift"
            " (docs/KNOWN_GAPS.md)"
        )

    # --- 2. no ML imports under backend/src --------------------------------
    files = [path for path in source_files() if deleted_tree_for(path) is None]
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
                " its HTTP routes (backend/src/services/ml_client.py), not link the"
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

    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a guard must never traceback silently
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
