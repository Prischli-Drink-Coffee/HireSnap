"""The /server HTTP contract is frozen in a file; this test keeps it frozen.

WHY this test exists: the backend was restructured from one 1102-line module into
vertical slices, and the only property that made that safe is that no route
moved. Assertions written by hand ("this path exists") cannot prove that - they
pass just as happily on a tree where twelve other paths disappeared. Comparing
the generated OpenAPI document against a committed snapshot can, because FastAPI
derives the document from the live routers: a dropped route, a renamed
operationId, a reordered tag, a changed response model or a new auth requirement
all show up as a diff.

The failure message names the route and the field. That is the whole point of
tests/snapshot_tool.py existing separately: when this fails, the reviewer needs
"paths./users/.get.operationId changed", not "the JSON differs".

Regenerating is a deliberate act - it is how an intended contract change is
recorded - so the command is printed in the failure message.

Also pinned here, because they are part of the public URL contract rather than of
the document:
  * GET /server/health answers exactly {status, service, version}: the gateway's
    healthcheck and the compose `depends_on: service_healthy` both depend on
    this shape, and an extra key would still be "200 OK" while breaking a strict
    consumer.
  * The /public mount exposes no business route. It is mounted so the URL space
    exists; one handler there is reachable without the gateway's /api/ prefix,
    which is exactly the kind of accident this test should prevent.
"""

from __future__ import annotations

import pathlib
import sys

TESTS_DIR = pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests import snapshot_tool  # noqa: E402  (needs the sys.path insert above)

SNAPSHOT_PATH = TESTS_DIR / "snapshots" / "critical_endpoint_openapi.json"

#: Keys the health probe must return, and nothing else.
HEALTH_KEYS = {"status", "service", "version"}

#: Route names FastAPI registers on every app for its own documentation UI. They
#: are not business routes, so the /public assertion filters exactly these.
DOCUMENTATION_ROUTE_NAMES = {"openapi", "swagger_ui_html", "swagger_ui_redirect", "redoc_html"}


def _client():
    from fastapi.testclient import TestClient

    from service.api.app import create_app

    return TestClient(create_app())


def test_snapshot_file_is_committed():
    """A missing snapshot must fail loudly, not silently compare nothing."""
    assert SNAPSHOT_PATH.is_file(), (
        "missing %s - it is the committed description of the /server contract; "
        "create it with: %s" % (SNAPSHOT_PATH, snapshot_tool.how_to_regenerate())
    )


def test_server_openapi_matches_the_committed_snapshot():
    """The whole live document, field for field, against the file."""
    problems = snapshot_tool.diff_snapshots(
        snapshot_tool.load_snapshot(SNAPSHOT_PATH),
        snapshot_tool.build_snapshot(),
    )
    assert problems == [], (
        "the /server OpenAPI contract changed in %d place(s):\n%s\n\n"
        "Each line is '<document path>: <committed> -> <live>'. A change to a path, "
        "method, operationId, tag, auth requirement, parameter, response model or "
        "schema field is a breaking change for a typed client. If the change is "
        "intended, record it with: %s"
        % (
            len(problems),
            "\n".join("  - " + problem for problem in problems),
            snapshot_tool.how_to_regenerate(),
        )
    )


def test_snapshot_covers_every_route_the_server_app_mounts():
    """Guard against a snapshot that drifted out of date without being detected.

    The diff above already fails on an added path, so this assertion is about the
    opposite failure: the snapshot being regenerated while the tool read a
    stale or partial application. It compares the snapshot's own path set with
    the live router table, which catches a tool that silently skipped a mount.
    """
    live_paths = set(snapshot_tool.server_application().openapi().get("paths", {}))
    snapshotted = set(snapshot_tool.load_snapshot(SNAPSHOT_PATH)["paths"])
    assert snapshotted == live_paths, (
        "snapshot paths and live paths differ: missing from snapshot %r, stale in "
        "snapshot %r. Regenerate with: %s"
        % (
            sorted(live_paths - snapshotted),
            sorted(snapshotted - live_paths),
            snapshot_tool.how_to_regenerate(),
        )
    )


def test_health_probe_answers_exactly_three_keys():
    """/server/health is the gateway's liveness target; its shape is contracted."""
    response = _client().get("/server/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == HEALTH_KEYS, (
        "GET /server/health must answer exactly %r, got %r - a consumer that reads "
        "the three keys tolerates an extra one, but a consumer that asserts the "
        "documented shape does not" % (sorted(HEALTH_KEYS), sorted(body))
    )
    assert body["status"] == "ok"
    assert body["service"] == "backend"
    assert body["version"], "the health payload must carry the package version"


def test_health_probe_does_not_touch_the_database_or_the_network():
    """A liveness probe that needs MySQL makes the container unhealthy when it is not.

    `compose up` waits on this endpoint before starting dependants; if it queried
    the database, a slow MySQL would look like a dead API and a restart loop would
    follow. The implementation is asserted by reading it: the handler body must
    not call into the repositories or the connection module.
    """
    import ast
    import inspect

    from service.api import app as app_module

    source = inspect.getsource(app_module)
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("health", "healthcheck"):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    offenders.append(
                        "%s:%s" % (getattr(inner, "lineno", "?"), ast.dump(inner)[:80])
                    )
    assert offenders == [], (
        "the health handler imports something; it must stay pure so it cannot fail "
        "because a neighbour is down: %r" % (offenders,)
    )


def test_public_mount_exposes_no_business_route():
    """/public is a URL space, not an API: nothing there may be reachable."""
    app = snapshot_tool.server_application().__class__  # noqa: F841  (documents intent)
    from service.api.app import create_app

    root = create_app()
    public = [route for route in root.routes if getattr(route, "path", None) == "/public"]
    assert public, "the /public mount disappeared; it is part of the public URL contract"
    business = [
        "%s %s"
        % (
            ",".join(sorted(getattr(route, "methods", None) or [])),
            getattr(route, "path", "?"),
        )
        for route in public[0].app.routes
        if getattr(route, "name", None) not in DOCUMENTATION_ROUTE_NAMES
    ]
    assert business == [], (
        "the /public app grew business route(s) %r. Anything mounted there answers "
        "without the gateway's /api/ prefix, so it bypasses the rewrite, the "
        "authentication story and the documented URL surface" % (business,)
    )
