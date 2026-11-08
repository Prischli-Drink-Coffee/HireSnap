"""The committed HTTP contract: the live app must still speak tests/snapshots/ml_routes.json.

WHY A SNAPSHOT AND NOT JUST MORE ASSERTIONS
--------------------------------------------
Assertions encode what someone thought to check; a snapshot encodes what the service
actually answered. The failure this catches is the quiet one: a route renamed while its
tests were renamed with it, a field added to a 200 body, a status code nudged from 200 to
201, an error code added without its table row in README.md. None of those break a single
existing assertion, and all of them break a caller (backend/service/services/ml_client.py
in particular). R:/GPTHub/backend/tests/test_critical_endpoint_snapshots.py solves the same
problem one layer down, against app.openapi(); this file solves it against the responses
themselves, because app.py answers with JSONResponse and plain dicts, so the OpenAPI schema
is thinner than the real body.

HOW TO UPDATE IT (and when you may)
------------------------------------
Only in the same commit that changes app.py AND README.md AND test_contract.py - the
snapshot is the third copy of the contract on purpose, and a snapshot regenerated alone
records the drift instead of stopping it. `python -m pytest ml/tests -q -k snapshot` after
editing shows the diff in the assertion message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.routing import APIRoute

import app as app_module

SNAPSHOT_NAME = "ml_routes.json"

# A minimal payload per body-carrying route. The stub engine answers without decoding
# it, so the bytes only have to satisfy validation: a media route wants a media content
# type, and /v1/embed/text wants the one field its model declares.
CALLS = {
    "GET /health": {},
    "GET /v1/capabilities": {},
    "POST /v1/embed/video": {
        "content": b"\x00\x00\x00\x18ftypmp42snapshot",
        "headers": {"content-type": "video/mp4"},
    },
    "POST /v1/embed/text": {"json": {"text": "a transcript"}},
    "POST /v1/transcribe": {
        "content": b"\x00\x00\x00\x18ftypmp42snapshot",
        "headers": {"content-type": "video/mp4"},
    },
}


def _load_snapshot(snapshots_dir: Path) -> Dict[str, Any]:
    return json.loads((snapshots_dir / SNAPSHOT_NAME).read_text(encoding="utf-8"))


def _live_operations() -> Dict[str, APIRoute]:
    """The APIRoutes the process actually serves, keyed "METHOD /path".

    The default /openapi.json route is a starlette Route, not an APIRoute, so it is
    filtered out here and asserted separately below: it is framework furniture, not part
    of the contract, and docs_url/redoc_url are disabled in app.py.
    """
    operations: Dict[str, APIRoute] = {}
    for route in app_module.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            operations["%s %s" % (method, route.path)] = route
    return operations


def test_route_table_matches_the_snapshot(snapshots_dir: Path) -> None:
    """No route added, removed or renamed without the snapshot saying so."""
    expected = _load_snapshot(snapshots_dir)["routes"]
    actual = _live_operations()
    assert sorted(actual) == sorted(expected), (
        "the served route table drifted from tests/snapshots/%s\n"
        "  only in the snapshot: %s\n"
        "  only in the app:      %s"
    ) % (
        SNAPSHOT_NAME,
        sorted(set(expected) - set(actual)),
        sorted(set(actual) - set(expected)),
    )


def test_operation_ids_match_the_snapshot(snapshots_dir: Path) -> None:
    """`operation_id` is what a generated client names its method after."""
    expected = _load_snapshot(snapshots_dir)["routes"]
    actual = _live_operations()
    wrong = {
        key: (actual[key].name, spec["operation_id"])
        for key, spec in expected.items()
        if key in actual and actual[key].name != spec["operation_id"]
    }
    assert not wrong, "operation ids changed (served, snapshot): %s" % (wrong,)


def test_every_declared_route_is_callable_with_the_documented_shape(
    snapshots_dir: Path, client, stub
) -> None:
    """Call each route and diff status + the EXACT top-level key set against the file.

    Exact, not a subset: an extra field is how two deployments stop being comparable,
    and docs/SERVICE_CONTRACTS.md 1.1 makes the /health body exactly three keys.
    """
    snapshot = _load_snapshot(snapshots_dir)
    problems: List[str] = []
    for key, spec in sorted(snapshot["routes"].items()):
        method, path = key.split(" ", 1)
        response = client.request(method, path, **CALLS.get(key, {}))
        if response.status_code != spec["status"]:
            problems.append(
                "%s -> %d, snapshot says %d"
                % (key, response.status_code, spec["status"])
            )
            continue
        body = response.json()
        actual_keys = sorted(body)
        if actual_keys != spec["response_keys"]:
            problems.append(
                "%s keys %s, snapshot says %s"
                % (key, actual_keys, spec["response_keys"])
            )
        for field, nested in spec["nested_keys"].items():
            nested_actual = sorted(body.get(field) or {})
            if nested_actual != nested:
                problems.append(
                    "%s.%s keys %s, snapshot says %s"
                    % (key, field, nested_actual, nested)
                )
    assert not problems, "the response shape drifted from the snapshot:\n  " + "\n  ".join(
        problems
    )
    assert stub.calls, "the stub engine was never reached; is app.engine still patched?"


def test_error_registry_matches_the_snapshot(snapshots_dir: Path) -> None:
    """The code set is closed and every code keeps its documented status.

    app.py declares the set and the handlers hard-code the status per raise site, so the
    two halves can drift: a code added to the set but never raised is dead weight, and a
    status changed in a handler is a breaking change for a caller that switches on the
    number. test_contract.py drives every code for real; this test pins the enumeration a
    client generates its exception classes from.
    """
    snapshot = _load_snapshot(snapshots_dir)
    assert sorted(app_module.ERROR_CODES) == snapshot["error_codes"]
    assert sorted(snapshot["error_statuses"]) == snapshot["error_codes"]


def test_error_body_is_exactly_error_and_detail(client) -> None:
    """One error shape for every failure, asserted through a real 404."""
    snapshot_response = client.get("/v1/does-not-exist")
    assert snapshot_response.status_code == 404
    snapshot = json.loads(snapshot_response.text)
    assert sorted(snapshot) == ["detail", "error"]


def test_disabled_documentation_routes_stay_disabled(snapshots_dir: Path, client) -> None:
    """docs_url/redoc_url are None on purpose: this service has no browser surface."""
    for path in _load_snapshot(snapshots_dir)["disabled_routes"]:
        assert client.get(path).status_code == 404


def test_snapshot_file_is_the_only_committed_copy(snapshots_dir: Path) -> None:
    """A stray second snapshot would be silently ignored; fail instead."""
    assert sorted(p.name for p in snapshots_dir.glob("*.json")) == [SNAPSHOT_NAME]


@pytest.mark.parametrize("key", sorted(CALLS))
def test_every_snapshot_route_has_a_call(key: str, snapshots_dir: Path) -> None:
    """The CALLS table and the snapshot must not drift apart either."""
    assert key in _load_snapshot(snapshots_dir)["routes"]
