"""The package layout is a contract, and a contract needs an executable check.

WHY this test exists: the refactor that moved `backend/src/` into the single
`backend/service/` package is only worth anything if the next change cannot quietly
undo it. A reviewer can see a new `backend/utils.py` in a diff; nobody sees the
hundredth one. Each assertion below pins one property that the pre-refactor layout
violated, so a regression names the rule it broke instead of just failing.

The rules, in the order they are asserted:

1. `service/` is the only Python package at the backend root and no loose
   `.py`/`.sh`/`.bat`/`.sql`/`.txt` file sits beside it. The old tree had
   `create_sql.py`, `env.py`, `main.sh`, `setup.sh` and `HireSnap.sql` at the root,
   all of which had to be COPYed into the image at hand-picked paths.
2. Every layer directory exists. A layer that silently disappears is a layer whose
   imports someone re-inlined.
3. No module imports the retired `src.*` namespace. This is the failure mode that
   survives a move: the code works in the checkout because `src/` is still on disk
   locally and dies in the image, where only `service/` was COPYed.
4. The HTTP layer is split into routers and `app.py` mounts every one of them, so a
   router file that nobody includes cannot be mistaken for a live endpoint.
5. Layer direction: `domain/` and `shared/` do not import `api/`, `repositories/` or
   `services/`. Data models that depend on the HTTP layer make the dependency graph
   circular the moment a repository needs a model.
"""

from __future__ import annotations

import ast
import pathlib

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
    "requirements.txt",
    "requirements-platform.txt",
}

#: Extensions that count as "code or data at the root" rather than project config.
LOOSE_SUFFIXES = {".py", ".sh", ".bat", ".cmd", ".sql", ".txt"}

LAYER_DIRS = (
    "api",
    "api/routers",
    "configuration",
    "domain",
    "infrastructure",
    "infrastructure/database",
    "infrastructure/messaging",
    "repositories",
    "services",
    "shared",
)


def _python_files():
    return sorted(SERVICE.rglob("*.py"))


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


def test_service_is_the_only_package_at_the_backend_root():
    packages = sorted(
        child.name
        for child in BACKEND_ROOT.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    )
    assert packages == ["service"], (
        "expected exactly one Python package at the backend root, found %r - a second "
        "one means the image needs a second COPY line and PYTHONPATH entry" % (packages,)
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


def test_every_layer_directory_exists():
    missing = [name for name in LAYER_DIRS if not (SERVICE / name).is_dir()]
    assert missing == [], "missing layer director(ies): %r" % (missing,)


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


def test_app_mounts_every_router_module():
    """Every router file on disk must be in the app's ROUTERS tuple.

    The comparison is against the declared tuple rather than against the text of
    app.py: a module that merely appears in a comment or an import is still not a
    mounted router.
    """
    from service.api import app as app_module

    on_disk = sorted(
        path.stem
        for path in (SERVICE / "api" / "routers").glob("*.py")
        if path.stem != "__init__"
    )
    assert on_disk, "service/api/routers holds no router modules"
    mounted = sorted(module.__name__.rsplit(".", 1)[-1] for module in app_module.ROUTERS)
    assert mounted == on_disk, (
        "router modules on disk %r and mounted by service/api/app.py %r differ - a "
        "router nobody mounts looks like an endpoint and is not one"
        % (on_disk, mounted)
    )


def test_data_layers_do_not_import_the_http_or_business_layers():
    forbidden = ("service.api", "service.repositories", "service.services")
    offenders = []
    for package in ("domain", "shared"):
        for path in sorted((SERVICE / package).rglob("*.py")):
            for name in _imports(path):
                if name.startswith(forbidden):
                    offenders.append("%s -> %s" % (path.relative_to(BACKEND_ROOT), name))
    assert offenders == [], (
        "lower layer importing an upper one: %r. domain/ and shared/ must stay "
        "importable on their own or the graph becomes circular" % (offenders,)
    )
