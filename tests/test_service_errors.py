"""One HTTP mapping for every domain failure, asserted where it is registered.

WHY this test exists: the vertical-slice rule says the application layer imports no
framework, so a rule that fails raises a `ServiceError` from
`service/shared/errors.py` and `service/api/app.py` registers the one handler that
turns it into `{'detail': ...}` with the error's own status. That mapping is now the
single place a status code is decided, which means it is also the single place a
refactor can silently change the API: a status that drifts from 404 to 500, or a body
that drifts from `{'detail': ...}` to a plain string, breaks every client that reads
the detail string, and no route test notices because no route is involved.

The mapping is taken from the app's own registration rather than by calling the
handler function by name, so renaming the function does not make this test vacuous -
and if the handler is not registered at all, the test fails rather than skips.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_DIR.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

ERRORS_MODULE = BACKEND_ROOT / "service" / "shared" / "errors.py"

#: The status each class carries, and the legacy HTTPException code it replaces. Both
#: 404s are deliberate: the old handlers raised 404 for "User not found" AND for
#: "Email already exist", and the frontend keys its messages off the detail string, not
#: the status. Changing either is a contract change, so it has to be an edit to this row.
EXPECTED_STATUS = {
    "NotFoundError": 404,
    "ConflictError": 404,
    "InvalidPayloadError": 400,
    "UnauthorizedError": 401,
}


def _errors():
    if not ERRORS_MODULE.is_file():
        pytest.skip("not yet migrated: service/shared/errors.py does not exist yet")
    import service.shared.errors as module

    return module


def _server_app():
    """The /server sub-app, where the single handler is registered."""
    from service.api.app import create_app

    for route in create_app().routes:
        if getattr(route, "path", None) == "/server" and hasattr(route, "app"):
            return route.app
    raise AssertionError("create_app() no longer mounts a /server sub-app")


def _request():
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/server/users/user_id/1",
            "raw_path": b"/server/users/user_id/1",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }
    )


def _invoke(handler, exception):
    response = handler(_request(), exception)
    if asyncio.iscoroutine(response):
        response = asyncio.run(response)
    return response


def test_every_domain_error_carries_the_documented_status():
    errors = _errors()
    for name, expected in EXPECTED_STATUS.items():
        error_class = getattr(errors, name, None)
        assert error_class is not None, "service/shared/errors.py lost %s" % (name,)
        assert issubclass(error_class, errors.ServiceError), (
            "%s must subclass ServiceError, otherwise the registered handler never sees it"
            % (name,)
        )
        instance = error_class("sample detail")
        assert int(getattr(instance, "status_code", 0)) == expected, (
            "%s.status_code is %r, expected %d"
            % (name, getattr(instance, "status_code", None), expected)
        )
        assert "sample detail" in str(getattr(instance, "detail", "")), (
            "%s must keep the message it was given; the frontend renders it verbatim" % (name,)
        )


@pytest.mark.parametrize("name, expected_status", sorted(EXPECTED_STATUS.items()))
def test_registered_handler_renders_a_domain_error_as_detail_and_status(name, expected_status):
    """The handler itself, fetched from the app, produces status + {'detail': ...}.

    A handler that returned `{"message": ...}` passes every route-level test that only
    checks the status, and breaks the client that reads `detail`; hence the exact body.
    """
    errors = _errors()
    server = _server_app()

    error_class = getattr(errors, name)
    handlers = server.exception_handlers
    handler = handlers.get(error_class)
    if handler is None:
        # A handler registered on the base class still catches every subclass, which is
        # how FastAPI resolves an exception; accept both registrations.
        handler = handlers.get(errors.ServiceError)
    assert handler is not None, (
        "no exception handler registered for %s or ServiceError on the /server app: %r. "
        "An unhandled ServiceError becomes a 500" % (name, sorted(map(str, handlers)))
    )

    response = _invoke(handler, error_class("sample detail"))
    assert response.status_code == expected_status, "%s rendered as %d, expected %d" % (
        name,
        response.status_code,
        expected_status,
    )
    body = response.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    assert '"detail"' in body and '"sample detail"' in body, (
        "the error body must be {'detail': <message>}, got %r. Clients read the detail "
        "string to choose a message; a different shape renders as an empty toast" % (body,)
    )
