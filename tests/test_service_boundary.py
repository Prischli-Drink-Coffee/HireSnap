"""Architecture contract: the target layout is asserted, not just intended.

WHY this file exists: the restructure into vertical slices is only durable if the
next change cannot quietly undo it. A reviewer sees an import added inside a
router; nobody sees the hundredth one, and by then the layering claim in
docs/ARCHITECTURE.md is fiction. Every rule below is one property the old
tree violated, so a regression names the rule it broke.

The rules, in the order they are asserted:

1. No module imports the retired `src` / `src.*` namespace. This is the failure
   mode that survives a move: it works in the checkout, where `src/` is still on
   disk, and dies in the image, which COPYs `service/` only.
2. A vertical's `presentation/` layer imports only what a delivery mechanism may
   see: models, shared, its OWN application layer, the security dependency and the
   composition accessor. It must not reach into its own or another vertical's
   persistence or into infrastructure - a router that can query the database
   directly is how the business rules ended up in the handlers.
3. `application/` never imports fastapi's Depends/APIRouter and never imports the
   messaging layer. Business rules that take a `Depends` cannot be unit-tested or
   reused from a worker; a service that enqueues a celery task has to know the
   broker's name.
4. A vertical's `domain/` layer imports no web framework and no database driver.
5. `models/` imports neither `service.services` nor `service.api`. Models are the
   innermost layer; a model that depends on a router makes the graph circular.

WHY EVERY RULE SKIPS INSTEAD OF FAILING WHILE THE TREE IS MID-MOVE
------------------------------------------------------------------
These assertions describe the TARGET tree. While the vertical slices are still
being written, a missing directory is not a violation - it is work in progress,
and a red suite during a refactor trains everyone to ignore red. So each rule
first asks "does this part of the target exist yet?" and, when it does not,
skips with `not yet migrated: <path>`. As soon as the directory exists the rule
becomes live: a mistake added to a directory that was created after this file
was written still fails, because the check is structural rather than a hardcoded
list of today's modules.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_DIR.parent
SERVICE = BACKEND_ROOT / "service"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

#: Packages that make up a vertical slice.
VERTICAL_LAYERS = ("application", "persistence", "presentation", "domain")

#: What a router module may import from the project. Anything else under
#: `service.` is a layering violation; third-party and stdlib imports are fine
#: (the router legitimately needs fastapi).
PRESENTATION_ALLOWED_PREFIXES = (
    "service.models",
    "service.shared",
    "service.api",
    "service.composition.state",
    "service.configuration",  # get_logger only; a router needs a logger to fail loudly
)

#: The only layers of its OWN slice a presentation module may import. Skipping a
#: layer downwards (router -> persistence) is still a violation: it bypasses the
#: rules, which is the coupling this layout removed. Its own `presentation` is
#: allowed because a router package contains its own `schemas` module.
OWN_PRESENTATION_ALLOWED_LAYERS = ("application", "domain", "presentation")

#: Business logic may not see these at all.
APPLICATION_FORBIDDEN_PREFIXES = ("service.infrastructure.messaging",)

#: fastapi is banned outright in the application layer, not merely the request
#: objects: the rule is that a rule cannot know it is being called over HTTP. A
#: domain failure is raised as a ServiceError and one handler in the HTTP layer maps
#: it to a status, so `HTTPException` here is as wrong as `Depends`.
APPLICATION_FORBIDDEN_MODULES = ("fastapi",)

#: Domain code is framework-free and driver-free.
DOMAIN_FORBIDDEN_MODULES = (
    "fastapi",
    "starlette",
    "pymysql",
    "celery",
    "redis",
    "boto3",
    "botocore",
    "numpy",
    "sklearn",
)

#: The innermost layer may not depend on the outer ones.
MODELS_FORBIDDEN_PREFIXES = ("service.services", "service.api")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _python_files(root):
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _module_name(path):
    """`service/services/users/application/x.py` -> 'service.services.users.application.x'.

    A path outside the backend (the self-check builds one in a temporary directory)
    falls back to its bare stem, which is enough to resolve a relative import
    against a package named after its parent directory.
    """
    try:
        relative = path.resolve().relative_to(BACKEND_ROOT)
    except ValueError:
        return path.stem
    return ".".join(relative.with_suffix("").parts)


def _imports(path):
    """(module, names, lineno) for every import in `path`, relative ones resolved.

    Relative imports are resolved against the importing module's package because
    `from ..persistence import x` is exactly as much of a layering violation as
    the absolute spelling, and leaving it out would make the rule bypassable by
    one dot.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path).rsplit(".", 1)[0]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, set(), node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                base = package.split(".")
                # level 1 means "this package", level 2 its parent, and so on.
                keep = len(base) - (node.level - 1)
                base = base[: max(keep, 0)]
                module = ".".join(base + ([module] if module else []))
            names = {alias.name for alias in node.names}
            found.append((module, names, node.lineno))
    return found


def _offenders(paths, predicate):
    """'relative/path.py:12 -> module' for every import matching `predicate`."""
    problems = []
    for path in paths:
        for module, names, lineno in _imports(path):
            if predicate(module, names):
                problems.append(
                    "%s:%d -> %s" % (path.relative_to(BACKEND_ROOT).as_posix(), lineno, module)
                )
    return sorted(problems)


def _verticals():
    """Directories under service/services that are vertical slices on disk.

    Detected by structure, not by a list of names: a new vertical appears here the
    moment it is created, so the rules apply to it without an edit to this file.
    """
    root = SERVICE / "services"
    if not root.is_dir():
        return []
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir()
        and child.name != "__pycache__"
        and any((child / layer).is_dir() for layer in VERTICAL_LAYERS)
    )


# --------------------------------------------------------------------------- #
# 1. the retired namespace
# --------------------------------------------------------------------------- #
def test_no_module_imports_the_retired_src_namespace():
    files = _python_files(SERVICE)
    assert files, "service/ holds no Python module at all; the tree is not where it says it is"
    problems = _offenders(files, lambda module, names: module == "src" or module.startswith("src."))
    assert problems == [], (
        "imports of the retired src.* namespace: %r. The image COPYs service/ only, so "
        "these pass in the checkout and fail in the container" % (problems,)
    )


# --------------------------------------------------------------------------- #
# 2. presentation layer
# --------------------------------------------------------------------------- #
def test_presentation_layers_import_only_what_a_delivery_mechanism_may_see():
    verticals = _verticals()
    if not verticals:
        pytest.skip("not yet migrated: service/services/<vertical>/ - no vertical slice exists yet")

    checked = 0
    for vertical in verticals:
        presentation = vertical / "presentation"
        files = _python_files(presentation)
        if not files:
            continue
        checked += len(files)
        own_slice = "service.services.%s." % (vertical.name,)

        def allowed(module, names, own=own_slice, layers=OWN_PRESENTATION_ALLOWED_LAYERS):
            if not (module == "service" or module.startswith("service.")):
                return False
            if module.startswith(PRESENTATION_ALLOWED_PREFIXES):
                return False
            if module.startswith(own):
                # Own slice: only the layers a delivery mechanism may see.
                remainder = module[len(own) :]
                return remainder.split(".")[0] not in layers
            return True

        problems = _offenders(files, allowed)
        assert problems == [], (
            "presentation layer of %r imports outside its allowed set "
            "(service.models, service.shared, service.api, service.composition.state, "
            "its own application/): %r. A router that imports persistence or "
            "infrastructure can run queries and skip the business rules, which is the "
            "exact coupling this layout removed" % (vertical.name, problems)
        )
    if not checked:
        pytest.skip("not yet migrated: service/services/<vertical>/presentation/ holds no module")


def test_presentation_layers_do_not_import_another_verticals_persistence():
    """Stated separately so the failure names the cross-vertical case directly."""
    verticals = _verticals()
    if not verticals:
        pytest.skip("not yet migrated: service/services/<vertical>/ - no vertical slice exists yet")

    problems = []
    for vertical in verticals:
        for path in _python_files(vertical / "presentation"):
            for module, _names, lineno in _imports(path):
                for other in verticals:
                    if other.name == vertical.name:
                        continue
                    prefix = "service.services.%s.persistence" % (other.name,)
                    if module.startswith(prefix):
                        problems.append(
                            "%s:%d -> %s"
                            % (path.relative_to(BACKEND_ROOT).as_posix(), lineno, module)
                        )
    assert problems == [], (
        "a router imports another vertical's persistence: %r. Cross-vertical work goes "
        "through that vertical's service, or the two slices are actually one" % (problems,)
    )


# --------------------------------------------------------------------------- #
# 3. application layer
# --------------------------------------------------------------------------- #
def test_application_layers_do_not_depend_on_the_http_layer_or_the_broker():
    verticals = _verticals()
    if not verticals:
        pytest.skip("not yet migrated: service/services/<vertical>/ - no vertical slice exists yet")

    checked = 0
    problems = []
    for vertical in verticals:
        files = _python_files(vertical / "application")
        if not files:
            continue
        checked += len(files)
        for path in files:
            for module, _names, lineno in _imports(path):
                location = "%s:%d -> %s" % (
                    path.relative_to(BACKEND_ROOT).as_posix(),
                    lineno,
                    module,
                )
                if module.startswith(APPLICATION_FORBIDDEN_PREFIXES):
                    problems.append(location + " (broker)")
                elif module.split(".")[0] in APPLICATION_FORBIDDEN_MODULES:
                    problems.append(location + " (web framework)")
    if not checked:
        pytest.skip("not yet migrated: service/services/<vertical>/application/ holds no module")
    assert problems == [], (
        "business logic importing the delivery mechanism: %r. A service that imports "
        "fastapi cannot run from a worker or a test without a request, and one that "
        "imports the messaging layer needs a broker to be unit-testable. Raise a "
        "ServiceError instead; service/api/app.py is the only place it becomes a status"
        % (problems,)
    )


# --------------------------------------------------------------------------- #
# 4. domain layer
# --------------------------------------------------------------------------- #
def test_vertical_domain_layers_stay_framework_and_driver_free():
    verticals = [vertical for vertical in _verticals() if (vertical / "domain").is_dir()]
    if not verticals:
        pytest.skip("not yet migrated: service/services/<vertical>/domain/ - no slice has one")

    problems = _offenders(
        [path for vertical in verticals for path in _python_files(vertical / "domain")],
        lambda module, names: module.split(".")[0] in DOMAIN_FORBIDDEN_MODULES,
    )
    assert problems == [], (
        "domain code importing a web framework, a driver or a numeric stack: %r. The "
        "domain layer is the part that must be readable and testable without any of "
        "them; numpy in particular is what makes a rule untestable on the base image" % (problems,)
    )


# --------------------------------------------------------------------------- #
# 5. models layer
# --------------------------------------------------------------------------- #
def test_models_layer_does_not_import_the_outer_layers():
    models_dir = SERVICE / "models"
    if not models_dir.is_dir():
        pytest.skip("not yet migrated: service/models/ does not exist yet")

    problems = _offenders(
        _python_files(models_dir),
        lambda module, names: module.startswith(MODELS_FORBIDDEN_PREFIXES),
    )
    assert problems == [], (
        "service/models/ importing the service or API layer: %r. Models are the "
        "innermost layer; a model that imports a router makes every importer of the "
        "model pull in fastapi and the whole composition graph" % (problems,)
    )


# --------------------------------------------------------------------------- #
# guard: the checks above must be able to fail
# --------------------------------------------------------------------------- #
def _self_check():
    """Prove the import collector sees both spellings, so the rules are not vacuous.

    Without this, a refactor of `_imports` could silently return nothing and every
    rule above would pass on a tree full of violations.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        package = pathlib.Path(directory) / "application"
        package.mkdir()
        path = package / "sample.py"
        path.write_text(
            "import os\n"
            "from fastapi import Depends\n"
            "from ..persistence.user_repository import UserRepository\n",
            encoding="utf-8",
        )
        found = {(module, tuple(sorted(names))) for module, names, _ in _imports(path)}
    assert ("os", ()) in found, "absolute imports are not collected"
    assert ("fastapi", ("Depends",)) in found, "imported names are not collected"
    assert any(module.endswith("persistence.user_repository") for module, _ in found), (
        "relative imports are not resolved: %r" % (sorted(found),)
    )


def test_import_collector_sees_absolute_relative_and_named_imports():
    """Runs the self-check through pytest so the guard is visible in the report."""
    _self_check()
