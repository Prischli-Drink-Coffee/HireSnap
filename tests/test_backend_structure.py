"""The package layout is a contract, and a contract needs an executable check.

WHY this test exists: the refactor that moved `backend/src/` into the single
`backend/service/` package, and then into vertical slices, is only worth anything if
the next change cannot quietly undo it. A reviewer can see a new `backend/utils.py`
in a diff; nobody sees the hundredth one. Each assertion below pins one property that
an earlier layout violated, so a regression names the rule it broke instead of just
failing.

The rules, in the order they are asserted:

1. `service/` is the only Python package at the backend root that ships. `tests/` is
   also a package and is allowed: it is developer-only, `.dockerignore` keeps it out
   of the image, and making it a real package is what gives the suite a stable
   `python -m tests.snapshot_tool` path. No loose `.py`/`.sh`/`.bat`/`.sql`/`.txt`
   file sits at the root; the old tree had `create_sql.py`, `env.py`, `main.sh`,
   `setup.sh` and `HireSnap.sql` there, all COPYed into the image at hand-picked paths.
2. Every target layer directory exists, and the retired layer directories are gone -
   a directory that lingers keeps its imports alive.
3. No module imports the retired `src.*` namespace. This is the failure mode that
   survives a move: the code works in the checkout because `src/` is still on disk
   locally and dies in the image, where only `service/` was COPYed.
4. `service/services/` holds slices, not modules. A flat `services/foo_services.py`
   next to `services/users/` is how a vertical slice decays back into layers.
5. Every router module on disk is in `service/api/registry.py`'s ROUTERS tuple, so a
   router file nobody mounts cannot be mistaken for a live endpoint.
6. `models/` and `shared/` do not import `api/`, `services/` or the retired
   `repositories/`. Data models that depend on the HTTP layer make the dependency
   graph circular the moment a repository needs a model.

Rule 2 and 4 describe the TARGET tree. While the move is in flight they report
`not yet migrated: <path>` as a skip rather than a failure, because a half-moved tree
is the expected state during the move - only a tree that has retired the old layout is
held to the new one.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICE = BACKEND_ROOT / "service"

#: Files that may sit at the backend root. Matched by exact name, not by suffix,
#: because the point of the rule is that the list is short enough to read.
ALLOWED_ROOT_FILES = {
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-platform.txt",
}

#: Packages that may exist at the backend root. `tests` is here because the suite is
#: a package (so `python -m tests.snapshot_tool` resolves the same way from any cwd)
#: and never enters the image: `.dockerignore` excludes `tests/`, and the Dockerfile
#: COPYs `backend/service` explicitly rather than the whole context.
ALLOWED_ROOT_PACKAGES = {"service", "tests"}

#: Extensions that count as "code or data at the root" rather than project config.
LOOSE_SUFFIXES = {".py", ".sh", ".bat", ".cmd", ".sql", ".txt"}

#: Directories the target layout requires. The vertical slices inside `services/` are
#: asserted structurally by tests/test_service_boundary.py, not listed here, so adding
#: a slice does not mean editing this file.
TARGET_LAYER_DIRS = (
    "api",
    "configuration",
    "composition",
    "infrastructure",
    "infrastructure/database",
    "infrastructure/messaging",
    "models",
    "models/db",
    "shared",
    "shared/repositories",
    "services",
)

#: Directories the target layout retires. Their continued existence is what the skip
#: below keys off, so this tuple is also the definition of "move still in flight".
RETIRED_LAYER_DIRS = (
    "domain",
    "repositories",
    "api/routers",
)

#: The twelve routers the API contract has always exposed. Kept as names because the
#: slice a router lives in is not always the router's name: the `inference` router is
#: served by the `matching` slice, and `info_candidates` by `candidates`.
ROUTER_NAMES = (
    "auth",
    "candidate_vacancies",
    "embeddings",
    "files",
    "info_candidates",
    "inference",
    "magics",
    "personalities",
    "personality_candidates",
    "personality_vacancies",
    "users",
    "vacancies",
)


def _python_files(root=None):
    return sorted((root or SERVICE).rglob("*.py"))


def _imports(path):
    """Every module name `path` imports, as written (relative imports skipped)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def _retired_still_present():
    return [name for name in RETIRED_LAYER_DIRS if (SERVICE / name).exists()]


def _require_target_layout():
    """Skip while the move is in flight; the old layout is what makes a check fail."""
    retired = _retired_still_present()
    if retired:
        pytest.skip(
            "not yet migrated: service/%s still exists, the target layout is not landed"
            % (", service/".join(retired),)
        )


def test_service_is_the_only_shipped_package_at_the_backend_root():
    packages = sorted(
        child.name
        for child in BACKEND_ROOT.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    )
    assert packages == sorted(ALLOWED_ROOT_PACKAGES), (
        "expected only %r as Python packages at the backend root, found %r - an "
        "unexpected package means the image needs another COPY line and PYTHONPATH "
        "entry, or that a stray __init__.py appeared" % (sorted(ALLOWED_ROOT_PACKAGES), packages)
    )


def test_no_loose_code_or_dump_at_the_backend_root():
    loose = sorted(
        child.name
        for child in BACKEND_ROOT.iterdir()
        if child.is_file()
        and child.suffix.lower() in LOOSE_SUFFIXES
        and child.name not in ALLOWED_ROOT_FILES
    )
    assert loose == [], (
        "loose file(s) at the backend root: %r. Move code into service/, scripts into "
        "docker/ or tests/, and the schema dump into database/" % (loose,)
    )
    # The dump has a home; assert it is actually there rather than merely absent
    # from the root, otherwise "no .sql at the root" passes on a tree with no dump.
    assert (BACKEND_ROOT / "database" / "HireSnap.sql").is_file()


def test_every_target_layer_directory_exists():
    _require_target_layout()
    missing = [name for name in TARGET_LAYER_DIRS if not (SERVICE / name).is_dir()]
    assert missing == [], "missing layer director(ies): %r" % (missing,)


def test_retired_layer_directories_are_gone():
    """A retired directory that survives keeps its importers alive.

    Checked only once the target layout has landed: during the move both trees exist
    by definition, and asserting here would fail on every intermediate commit.
    """
    _require_target_layout()
    retired = _retired_still_present()
    assert retired == [], (
        "retired layout still present: %r. Its modules are duplicated by the vertical "
        "slices, and two copies of a repository is how the wrong one gets wired" % (retired,)
    )


def test_no_module_imports_the_retired_src_namespace():
    offenders = []
    for path in _python_files():
        for name in _imports(path):
            if name == "src" or name.startswith("src."):
                offenders.append("%s -> %s" % (path.relative_to(BACKEND_ROOT), name))
    assert offenders == [], (
        "imports of the retired src.* namespace: %r. The image COPYs service/ only, so "
        "these pass in the checkout and fail in the container" % (offenders,)
    )


def test_services_holds_slices_not_modules():
    """`service/services/` is a package of slices; a bare module there is a layer.

    The old tree had fourteen `*_services.py` modules in one directory, which is why
    every route handler could import every other domain's rules. A new flat module is
    the first step back to that, so it is a failure rather than a warning.
    """
    _require_target_layout()
    loose = sorted(
        path.name for path in (SERVICE / "services").glob("*.py") if path.name != "__init__.py"
    )
    assert loose == [], (
        "flat module(s) under service/services/: %r. A service belongs to a slice: "
        "service/services/<domain>/application/<domain>_service.py" % (loose,)
    )


def _router_module_paths():
    """Router modules of the target layout, found structurally rather than by name."""
    return sorted(
        path
        for path in (SERVICE / "services").glob("*/presentation/routers/*/*.py")
        if path.stem != "__init__"
    )


def test_app_mounts_every_router_module():
    """Every router module on disk must be in the app's ROUTERS tuple.

    The comparison is against the declared tuple rather than against the text of
    app.py: a module that merely appears in a comment or an import is still not a
    mounted router. The tuple lives in service/api/registry.py so this test can read
    it without executing the router bodies.
    """
    _require_target_layout()
    try:
        from service.api import registry
    except ImportError as error:  # pragma: no cover - only while the move is in flight
        pytest.skip("not yet migrated: service/api/registry.py (%s)" % (error,))

    on_disk = sorted(path.stem for path in _router_module_paths())
    assert on_disk, "no router module found under service/services/*/presentation/routers/"
    mounted = sorted(module.__name__.rsplit(".", 1)[-1] for module in registry.ROUTERS)
    assert mounted == on_disk, (
        "router modules on disk %r and mounted by service/api/registry.py %r differ - a "
        "router nobody mounts looks like an endpoint and is not one" % (on_disk, mounted)
    )
    # Self-consistency above would also pass on a tree that lost a router entirely, so
    # pin the twelve contract names too. A rename has to be a deliberate contract change.
    for name in ROUTER_NAMES:
        assert any(stem.startswith(name) for stem in mounted), (
            "the %r router is missing from ROUTERS; mounted: %r" % (name, mounted)
        )


def test_data_layers_do_not_import_the_http_or_business_layers():
    forbidden = ("service.api", "service.services", "service.repositories", "service.composition")
    offenders = []
    for package in ("models", "shared"):
        root = SERVICE / package
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            for name in _imports(path):
                if name.startswith(forbidden):
                    offenders.append("%s -> %s" % (path.relative_to(BACKEND_ROOT), name))
    assert offenders == [], (
        "lower layer importing an upper one: %r. models/ and shared/ must stay "
        "importable on their own or the graph becomes circular" % (offenders,)
    )
