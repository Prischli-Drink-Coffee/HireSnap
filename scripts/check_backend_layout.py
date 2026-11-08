#!/usr/bin/env python3
"""Guard the SHAPE of backend/, the way check_ml_service_layout.py guards ml/.

WHY THIS GUARD EXISTS
---------------------
backend/ is a submodule that is also a standalone checkout, and the vertical-slice layout
is a property no compiler checks. Python does not care whether a rule lives in
application/ or beside the router, whether a router is mounted or merely present, or
whether a module reaches back up the tree. Every one of those has already been violated
once here: the flat service/services/*.py pile, service/domain/ and service/repositories/
left behind after the move, a router that no registry included.

A reviewer sees the first violation. A guard sees the hundredth, on every commit, in a
directory the superproject can only see as one commit hash.

WHAT IS ASSERTED (8 rules)
--------------------------
1. service/ IS THE ONLY PACKAGE AT THE BACKEND ROOT, besides tests/. A second package at
   the root means the image needs a second COPY line and a second PYTHONPATH entry, and
   the one that gets forgotten is the one that only works in a developer checkout.
2. NO LOOSE CODE AT THE BACKEND ROOT. A .py/.sh/.bat/.cmd/.sql/.txt file beside
   pyproject.toml is a script that should live in scripts/ or a module that escaped
   service/. The old tree had create_sql.py, env.py, main.sh, setup.sh and HireSnap.sql
   there, all COPYed into the image at hand-picked paths. backend/database/HireSnap.sql
   must exist: it is what the MySQL init mount points at, and a rename there is a silent
   empty database.
3. NO FLAT MODULE UNDER service/services/. A .py file directly in service/services/ means
   the slice layout is optional again; the only file there is __init__.py (plus README.md,
   which is documentation and not code).
4. SLICES HAVE THEIR LAYERS. A directory under service/services/ that has any of
   application/, persistence/, presentation/ or domain/ must have application/ and
   presentation/. jobs/ is the documented exception: it has application/ and no
   presentation, because nothing serves it over HTTP.
5. EVERY ROUTER IS MOUNTED. A module under service/services/*/presentation/routers/*/ that
   defines a module-scope "router" must be named in the ROUTERS tuple of
   service/api/registry.py. An unmounted router is the worst kind of dead code: the file
   exists, passes lint and its tests, and no request ever reaches it.
6. THE RETIRED DIRECTORIES ARE GONE. service/domain/, service/repositories/ and
   service/api/routers/ must not exist. A directory that lingers keeps its imports alive
   and lets a new module be written against the old layout by accident.
7. models/ AND shared/ IMPORT NO UPWARD LAYER. service/models/** and service/shared/** may
   not import service.api, service.services, service.repositories or service.composition.
   They are the bottom of the tree; if they can reach up, the dependency graph is a cycle
   and the import direction stops meaning anything.
8. ONE PYTEST CONFIGURATION. backend/pyproject.toml holds [tool.pytest.ini_options]; a
   pytest.ini, tox.ini or setup.cfg beside it must not exist. pytest gives pytest.ini
   priority over pyproject.toml, so a second file is not dead configuration - it silently
   wins, and the settings the tree is tested with stop matching the settings in the diff.
   (scripts/check_ml_boundary.py lists pytest.ini in its root allowlist because it is not
   CODE, which is right and is why the duplication rule has to be enforced here.)

Parsed with ast, never imported: importing service.api.app needs fastapi, pymysql and the
whole dependency tree, and this guard has to run on any interpreter in the repository.

USAGE
-----
    python backend/scripts/check_backend_layout.py
    python backend/scripts/check_backend_layout.py --json

Exit codes: 0 clean, 1 violations, 2 nothing was checked (the tree is not there - which is
NOT a pass). Same [OK]/[FAIL]/[WARN]/[SKIP]/[SUMMARY] output as the superproject guards,
so 'make guards' absorbs it unchanged.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

CHECK = "backend-layout"

# <checkout>/backend/scripts/check_backend_layout.py -> <checkout>/backend, whether that
# checkout is the superproject (backend/ nested) or the backend submodule on its own.
# Derived from the file rather than the depth, which differs in a bare clone.
BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SERVICE = BACKEND / "service"
SERVICES = SERVICE / "services"
REGISTRY = SERVICE / "api" / "registry.py"
SCHEMA = BACKEND / "database" / "HireSnap.sql"

# Rule 1: a package at the backend root is a COPY line in the image.
ALLOWED_ROOT_PACKAGES = {"service", "tests"}

# Rule 2: everything that may sit at the backend root, by exact name. requirements.txt and
# requirements-platform.txt end in .txt and are here because they ARE the install path, not
# code; the entrypoint is deliberately absent because there is none (python -m service.main).
ALLOWED_ROOT_FILES = {
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".python-version",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-platform.txt",
}
LOOSE_ROOT_SUFFIXES = {".py", ".sh", ".bat", ".cmd", ".sql", ".txt"}

# Directories that legitimately exist at the backend root. data/ and logs/ are the
# container mounts (DATA_PATH and the logging.yaml filename) and are gitignored; they may
# or may not exist in a given checkout, so their absence is never a failure.
ALLOWED_ROOT_DIRS = {"database", "docker", "docs", "notebooks", "scripts", "service", "tests"}
GENERATED_ROOT_DIRS = {"data", "logs"}
CACHE_DIRS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv", "__pycache__"}

# Rule 3: the only module allowed directly in service/services/.
SERVICES_ALLOWED_FILES = {"__init__.py"}

# Rule 4: a slice is a slice once it has any layer directory; the layer is a placeholder
# when it holds only __init__.py, which is documented as legitimate, not as a to-do.
LAYER_DIRS = ("application", "persistence", "presentation", "domain")
REQUIRED_LAYERS = ("application", "presentation")
# jobs is the documented exception (AGENTS.md): no HTTP surface, so no presentation layer.
SLICES_WITHOUT_PRESENTATION = {"jobs"}

# Rule 5: a module in a routers/ directory that is a helper rather than a router. schemas.py
# is documented in AGENTS.md as the place a router puts a model the domain does not own.
ROUTER_HELPER_MODULES = {"schemas", "__init__"}

# Rule 6
RETIRED_DIRS = (
    (SERVICE / "domain", "service/models/ and the slices own the entities and the exceptions now"),
    (SERVICE / "repositories", "the slice persistence/ layer owns the SQL now"),
    (
        SERVICE / "api" / "routers",
        "each slice owns its router under service/services/<domain>/presentation/routers/ now",
    ),
)

# Rule 7: the bottom of the tree may not import the layers above it.
BOTTOM_LAYER_DIRS = ("models", "shared")
FORBIDDEN_UPWARD = {"api", "services", "repositories", "composition"}

WALK_SKIP_DIRS = CACHE_DIRS

HTTP_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")


class Report:
    """Collects [OK]/[FAIL]/[WARN]/[SKIP] lines and prints them in the house format.

    Deliberately a copy of the helper in scripts/check_*.py rather than an import of it:
    this guard must run from a bare backend/ checkout, where ../scripts does not exist.
    Twenty duplicated lines are cheaper than a guard that needs its neighbour.
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
                + "the tree this guard asserts does not exist yet"
            )
            return 2
        return 1 if self.failures else 0


def rel(path: Path) -> str:
    """Workspace-relative POSIX path, so the output is identical on Windows and in CI."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def parse_module(path: Path) -> Tuple[Optional[ast.Module], str]:
    """Parse one file as (tree, error). A syntax error is reported, never raised."""
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path)), ""
    except SyntaxError as exc:
        return None, (str(exc).splitlines() or ["SyntaxError"])[0]


def package_parts(path: Path) -> List[str]:
    """Dotted package components of the directory holding path, relative to BACKEND.

    service/services/auth/application/auth_service.py -> ["service", "services", "auth",
    "application"]. Used to resolve relative imports into absolute ones, which is the only
    way to answer "does this reach up the tree" for 'from ...shared import x'.
    """
    try:
        relative = path.resolve().relative_to(BACKEND)
    except ValueError:
        return []
    return list(relative.parent.parts)


def resolve_relative(parts: Sequence[str], level: int, module: Optional[str]) -> Optional[str]:
    """Absolute dotted target of a relative import, or None when it escapes the package.

    The level counts dots: level 1 is the current package, level 2 its parent. For
    service/models/x.py the package is ['service','models'], so 'from .. import y' (level 2)
    drops 'models' and yields 'service.y'.
    """
    if level <= 0:
        return module
    keep = len(parts) - (level - 1)
    if keep < 0:
        return None
    base = list(parts[:keep])
    if module:
        base.extend(module.split("."))
    return ".".join(base) if base else None


def imported_targets(path: Path, tree: ast.Module) -> List[Tuple[int, str]]:
    """Every absolute module this file imports, as (lineno, dotted name).

    Relative imports are resolved rather than skipped: 'from ...api import app' inside
    service/services/ is exactly the upward import rule 7 exists to stop, and it is the
    form a re-export takes when someone moves a helper.
    """
    parts = package_parts(path)
    found: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                target = resolve_relative(parts, node.level, node.module)
                if target:
                    found.append((node.lineno, target))
                    for alias in node.names:
                        found.append((node.lineno, target + "." + alias.name))
            elif node.module:
                found.append((node.lineno, node.module))
                for alias in node.names:
                    found.append((node.lineno, node.module + "." + alias.name))
    return found


def root_packages() -> List[Path]:
    """Directories directly under backend/ that carry an __init__.py."""
    if not BACKEND.is_dir():
        return []
    return sorted(
        child
        for child in BACKEND.iterdir()
        if child.is_dir() and child.name not in CACHE_DIRS and (child / "__init__.py").is_file()
    )


def slice_dirs() -> List[Path]:
    """Directories under service/services/, the package marker files excluded."""
    if not SERVICES.is_dir():
        return []
    return sorted(
        child for child in SERVICES.iterdir() if child.is_dir() and child.name not in CACHE_DIRS
    )


def router_modules() -> List[Path]:
    """Modules under service/services/*/presentation/routers/<pkg>/*.py."""
    found: List[Path] = []
    for slice_dir in slice_dirs():
        routers = slice_dir / "presentation" / "routers"
        if not routers.is_dir():
            continue
        for path in sorted(routers.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            # routers/__init__.py is the package marker of the routers/ directory itself,
            # not a router and not a helper. Counting it would either demand that the
            # directory be "mounted" or list it as a schema-style helper, and both readings
            # are noise in the one line a reader actually looks at.
            if path.parent == routers and path.name == "__init__.py":
                continue
            found.append(path)
    return found


def defines_module_router(path: Path) -> bool:
    """True when the module assigns a top-level name 'router' (whatever it is assigned to)."""
    tree, _ = parse_module(path)
    if tree is None:
        return False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "router":
                    return True
        elif isinstance(node, ast.AnnAssign):
            named_router = isinstance(node.target, ast.Name) and node.target.id == "router"
            if named_router and node.value is not None:
                return True
    return False


def registry_module_names(path: Path) -> Tuple[Set[str], str]:
    """Module names the ROUTERS tuple of service/api/registry.py references.

    Read by AST, never by import. Two shapes are accepted, because both are legitimate
    spellings of the same registry: '(auth_api.router, ...)' names the MODULE through an
    attribute, and '(auth_api, ...)' names the OBJECT imported from the module. Only
    imports that come from a routers package are considered, so 'from fastapi import
    APIRouter' cannot make an unrelated name look mounted.
    """
    tree, error = parse_module(path)
    if tree is None:
        return set(), error

    names: Set[str] = set()

    def collect(node: ast.AST) -> None:
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                names.add(node.value.id)
            else:
                collect(node.value)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Call):
            for argument in node.args:
                collect(argument)
            for keyword in node.keywords:
                collect(keyword.value)
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for element in node.elts:
                collect(element)
        elif isinstance(node, ast.Dict):
            for value in node.values:
                if value is not None:
                    collect(value)

    for node in tree.body:
        value: Optional[ast.AST] = None
        targets: List[ast.AST] = []
        if isinstance(node, ast.Assign):
            value, targets = node.value, list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, [node.target]
        if value is None:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "ROUTERS" for target in targets):
            continue
        collect(value)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and ".routers" in node.module:
            names.add(node.module.split(".")[-1])
            for alias in node.names:
                names.add(alias.asname or alias.name)

    return names, ""


def check_root_packages(report: Report) -> None:
    """Rule 1."""
    packages = root_packages()
    unexpected = [package for package in packages if package.name not in ALLOWED_ROOT_PACKAGES]
    for package in unexpected:
        report.failures.append(
            rel(package) + " is a second Python package at the backend root. The image COPYs"
            " the tree and sets PYTHONPATH=/app, so every package at the root needs its own"
            " COPY line and its own path entry - and the one that gets forgotten works only"
            " in a developer checkout. Move the module into service/ (scripts/ is not a"
            " package and cannot be imported)."
        )
    if not unexpected:
        report.ok.append("the backend root holds no package other than service/ and tests/")


def check_root_files(report: Report) -> None:
    """Rule 2."""
    if not BACKEND.is_dir():
        report.skips.append("backend/ does not exist, so the root file rule was not checked")
        return
    offenders: List[Path] = []
    for child in sorted(BACKEND.iterdir()):
        if child.is_dir():
            if child.name in CACHE_DIRS or child.name in GENERATED_ROOT_DIRS:
                continue
            if child.name not in ALLOWED_ROOT_DIRS:
                report.warnings.append(
                    rel(child) + " is a directory at the backend root that this guard does"
                    " not know about. If it belongs (a new top-level area), add it to"
                    " ALLOWED_ROOT_DIRS in backend/scripts/check_backend_layout.py with the"
                    " reason; if it is generated output, add it to GENERATED_ROOT_DIRS and"
                    " to .gitignore."
                )
            continue
        if child.name in ALLOWED_ROOT_FILES:
            continue
        if child.suffix.lower() in LOOSE_ROOT_SUFFIXES:
            offenders.append(child)
    for offender in offenders:
        report.failures.append(
            rel(offender) + " is a loose " + offender.suffix + " file at the backend root."
            " Move it into service/ (code) or scripts/ (a tool), or - if an external tool"
            " demands that exact root path - add it to ALLOWED_ROOT_FILES in"
            " backend/scripts/check_backend_layout.py with the reason (AGENTS.md)."
        )
    if not offenders:
        report.ok.append(
            "the backend root holds no loose .py/.sh/.bat/.cmd/.sql/.txt file outside the"
            " documented allowlist"
        )
    # ONE source of pytest configuration. scripts/check_ml_boundary.py accepts a
    # backend/pytest.ini at the root because it is not CODE - and it is right, which is
    # exactly why the rule has to live here instead. pytest reads the first of pytest.ini,
    # pyproject.toml, tox.ini and setup.cfg that it finds, and pytest.ini WINS over
    # pyproject.toml. A second file would therefore not be dead: it would quietly take over
    # testpaths/pythonpath/addopts for every developer and for CI, and the two files could
    # disagree with nobody noticing. The configuration lives in pyproject.toml.
    for duplicate in (BACKEND / "pytest.ini", BACKEND / "tox.ini", BACKEND / "setup.cfg"):
        if duplicate.is_file():
            report.failures.append(
                rel(duplicate) + " exists, but pytest configuration lives in"
                " backend/pyproject.toml. pytest reads pytest.ini before pyproject.toml, so"
                " this file would silently override testpaths, pythonpath and addopts there -"
                " the two would drift and the gate would depend on which one pytest picked."
                " Move any setting it needs into [tool.pytest.ini_options] and delete it."
            )
    if not any((BACKEND / name).is_file() for name in ("pytest.ini", "tox.ini", "setup.cfg")):
        report.ok.append("pyproject.toml is the only pytest configuration file at the backend root")
    if SCHEMA.is_file():
        report.ok.append(rel(SCHEMA) + " exists - the MySQL init mount has something to load")
    else:
        report.failures.append(
            rel(SCHEMA) + " is missing. docker/docker-compose.yaml mounts this exact path at"
            " /opt/hiresnap/HireSnap.sql and infra/mysql/init/00-init.sh loads it; without it"
            " the database starts empty and the first request fails on a missing table."
        )


def check_flat_modules(report: Report) -> None:
    """Rule 3."""
    if not SERVICES.is_dir():
        report.skips.append(
            "service/services/ does not exist, so the flat-module rule was not checked"
        )
        return
    flat = sorted(
        child
        for child in SERVICES.iterdir()
        if child.is_file() and child.suffix == ".py" and child.name not in SERVICES_ALLOWED_FILES
    )
    for path in flat:
        report.failures.append(
            rel(path) + " is a module directly under service/services/. Every bounded context"
            " is a DIRECTORY there: service/services/<domain>/{domain,application,"
            "persistence,presentation}/. A flat module is the layout this refactor removed;"
            " move it into the slice that owns it."
        )
    if not flat:
        report.ok.append(
            "service/services/ holds no flat module: every bounded context is a slice directory"
        )


def check_slice_layers(report: Report) -> None:
    """Rule 4."""
    slices = slice_dirs()
    if not slices:
        report.skips.append("service/services/ has no slice directory, so no layer was checked")
        return
    incomplete: List[str] = []
    for slice_dir in slices:
        present = {layer for layer in LAYER_DIRS if (slice_dir / layer).is_dir()}
        if not present:
            continue
        required = [layer for layer in REQUIRED_LAYERS]
        if slice_dir.name in SLICES_WITHOUT_PRESENTATION:
            required = [layer for layer in required if layer != "presentation"]
            if "presentation" in present:
                report.warnings.append(
                    rel(slice_dir)
                    + "/presentation exists, but "
                    + slice_dir.name
                    + "/ is documented as having no HTTP surface (AGENTS.md). Either the"
                    " slice gained a route - then this exception is stale and must go - or"
                    " the directory is an empty placeholder that suggests one exists."
                )
        missing = [layer for layer in required if layer not in present]
        if missing:
            incomplete.append(
                rel(slice_dir)
                + " has "
                + ", ".join(sorted(present))
                + " but is missing "
                + ", ".join(missing)
                + "/"
            )
    for message in incomplete:
        report.failures.append(
            message + ". A slice that started one layer is a slice: the rules belong in"
            " application/, the route in presentation/. Delete the half-built layers"
            " instead if the domain is not a slice yet."
        )
    if not incomplete:
        report.ok.append(
            "all " + str(len(slices)) + " slice(s) under service/services/ have the layers"
            " their shape implies (jobs/ exempt from presentation/)"
        )


def check_routers_mounted(report: Report) -> None:
    """Rule 5."""
    modules = router_modules()
    if not modules:
        report.skips.append(
            "no router module exists under service/services/*/presentation/routers/, so"
            " nothing was checked against service/api/registry.py"
        )
        return
    if not REGISTRY.is_file():
        report.failures.append(
            rel(REGISTRY) + " is missing, but " + str(len(modules)) + " router module(s) exist."
            " Every router has to be listed in its ROUTERS tuple; without the registry no"
            " route reaches the app and the whole API answers 404."
        )
        return
    names, error = registry_module_names(REGISTRY)
    if error:
        report.failures.append(
            rel(REGISTRY) + " could not be parsed (" + error + "), so no router could be"
            " checked against it"
        )
        return
    unmounted: List[str] = []
    helpers: List[str] = []
    for path in modules:
        # A router written as a PACKAGE is served from its __init__.py, whose stem is
        # "__init__" - comparing that to ROUTERS would fail every correctly mounted
        # package-style router. The mount identity of an __init__.py is its directory.
        name = path.parent.name if path.name == "__init__.py" else path.stem
        if not defines_module_router(path):
            if path.stem in ROUTER_HELPER_MODULES or path.name == "__init__.py":
                helpers.append(name)
                continue
            report.warnings.append(
                rel(path) + " sits in a routers/ directory, defines no module-level 'router',"
                " and is not named in ROUTERS. If it is a helper the router imports (schemas),"
                " rename it to schemas.py; otherwise it is a router nobody can mount."
            )
            continue
        if name in names:
            continue
        unmounted.append(rel(path))
    for message in unmounted:
        report.failures.append(
            message + " defines a router that service/api/registry.py does not include. The"
            " file is live code that no request reaches: add its module name to the ROUTERS"
            " tuple, or delete it."
        )
    if not unmounted:
        extra = ""
        if helpers:
            extra = " (" + ", ".join(sorted(set(helpers))) + " is a documented helper)"
        report.ok.append(
            "all " + str(len(modules)) + " module(s) under a routers/ directory are mounted"
            " in service/api/registry.py's ROUTERS" + extra
        )


def check_retired_dirs(report: Report) -> None:
    """Rule 6."""
    present = [(path, why) for path, why in RETIRED_DIRS if path.is_dir()]
    for path, why in present:
        report.failures.append(
            rel(path)
            + "/ still exists. It was retired by the vertical-slice move: "
            + why
            + ". Delete it - while it is on disk its imports keep resolving and the next"
            " module will be written against the old layout."
        )
    if not present:
        report.ok.append(
            "the retired service/domain, service/repositories and service/api/routers"
            " directories are gone"
        )


def check_bottom_layer_imports(report: Report) -> None:
    """Rule 7."""
    scanned = 0
    hits: List[str] = []
    for layer in BOTTOM_LAYER_DIRS:
        directory = SERVICE / layer
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree, error = parse_module(path)
            if tree is None:
                report.warnings.append(
                    rel(path) + " could not be parsed (" + error + "), so its imports were"
                    " not checked"
                )
                continue
            scanned += 1
            # One message per (file, line, upward target). imported_targets() yields both
            # "service.services.auth" and "service.services.auth.x" for a single
            # "from service.services.auth import x", because the alias form is what catches
            # "from service import services"; reporting both would print the same defect
            # twice and make a one-line mistake look like two.
            reported: Set[Tuple[int, str]] = set()
            for lineno, dotted in imported_targets(path, tree):
                parts = dotted.split(".")
                if len(parts) < 2 or parts[0] != "service":
                    continue
                if parts[1] not in FORBIDDEN_UPWARD:
                    continue
                upward = "service." + parts[1]
                if (lineno, upward) in reported:
                    continue
                reported.add((lineno, upward))
                hits.append(rel(path) + ":" + str(lineno) + " imports " + dotted)
    for hit in hits:
        report.failures.append(
            hit + ". service/models/ and service/shared/ are the bottom of the tree: they may"
            " not import "
            + ", ".join(sorted("service." + name for name in FORBIDDEN_UPWARD))
            + ". If a shared helper needs a service object, the helper belongs in"
            " service/composition/ (the one place wiring happens) or in the slice."
        )
    if scanned and not hits:
        report.ok.append(
            str(scanned) + " module(s) under service/models/ and service/shared/ import no"
            " service.api, service.services, service.repositories or service.composition"
        )
    elif not scanned:
        report.skips.append(
            "service/models/ and service/shared/ hold no Python module, so the import"
            " direction was not checked"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="assert that backend/ keeps the vertical-slice layout"
    )
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args(argv)

    if not SERVICE.is_dir():
        print("[FAIL] " + CHECK + ": " + rel(SERVICE) + " does not exist")
        print("[SUMMARY] check=" + CHECK + " status=fail failures=1 warnings=0 skipped=0")
        return 2

    report = Report()
    check_root_packages(report)
    check_root_files(report)
    check_flat_modules(report)
    check_slice_layers(report)
    check_routers_mounted(report)
    check_retired_dirs(report)
    check_bottom_layer_imports(report)
    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - a guard that crashes is a guard that fails
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
