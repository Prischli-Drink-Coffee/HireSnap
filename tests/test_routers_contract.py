"""Every mounted route on /server, driven from the application, not from a list of files.

WHY THE CATALOGUE IS DERIVED: a hand-written list of twelve router files goes stale the
first time somebody adds a route, and a coverage list that is stale is worse than none
because it reports green. This module walks the ROUTES THE APPLICATION ACTUALLY MOUNTED and
cross-checks them against `service.api.registry.ROUTERS`, so a slice that is written but not
mounted fails here, and a route deleted from a mounted router stops being reported as
covered instead of leaving a phantom test behind.

WHY THE COLLABORATOR IS REPLACED THROUGH `dependency_overrides`: every handler takes its service
as `service=Depends(get_<x>_service)`, and FastAPI resolves that default when the route is
DECLARED, so patching `service.composition.state` after import has no effect on an already-built
route. `dependency_overrides` is the seam the framework honours per request, keyed by the exact
callable (or the `JWTBearer` instance) the route captured.

WHAT A GREEN RUN PROVES: for every operation, that the handler forwards what the client sent
to exactly one service call, that it returns what the service returned, and that a
`ServiceError` becomes that error's own status and `{"detail": ...}` body WHILE an
exception record is still written. WHAT IT DOES NOT PROVE: the rules
(test_slices_application.py), the SQL (test_slices_repositories.py) or the request schema
(test_critical_endpoint_snapshots.py).

WHY THE ERROR CASES RUN AGAINST EVERY ROUTE: the failure mapping is the same try/except text
copied twelve times. A copy that catches `HTTPException` instead of `ServiceError`,
or that re-raises a different object, or that swallows the error and returns 200,
type-checks and passes every happy-path test in this file.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Union, get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from service.api.app import create_app
from service.api.registry import ROUTERS
from service.api.security import JWTBearer
from service.configuration.logging import get_logger
from service.shared.errors import (
    InvalidPayloadError,
    NotFoundError,
    ServiceError,
    UnauthorizedError,
)

MOUNT = "/server"

#: Two form fields carry an enum constraint owned by the model the handler assembles, so
#: they cannot be sampled from the field annotation alone; every other form field is a free
#: string. Values are the first member of TypeUser and RoleEnum.
FORM_OVERRIDES = {"type_user": "candidate", "role": "user"}

#: The three errors the API is required to render, with the status each carries.
SERVICE_ERRORS = (
    pytest.param(NotFoundError, 404, id="not-found-404"),
    pytest.param(InvalidPayloadError, 400, id="invalid-payload-400"),
    pytest.param(UnauthorizedError, 401, id="unauthorized-401"),
)

_APP = create_app()
_SERVER = next(route.app for route in _APP.routes if getattr(route, "path", None) == MOUNT)
_CLIENT = TestClient(_APP)


# --- turning a route into something drivable --------------------------------


def _inner(annotation):
    """Peel Annotated, Optional and list down to the type that decides a sample value."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _inner(get_args(annotation)[0])
    if origin is Union:
        args = [item for item in get_args(annotation) if item is not type(None)]
        return _inner(args[0]) if args else Any
    if origin is list:
        args = get_args(annotation)
        return _inner(args[0]) if args else Any
    return annotation


def _sample_value(annotation):
    """A JSON value pydantic accepts for this annotation."""
    annotation = _inner(annotation)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return next(iter(annotation)).value
    if annotation is bool:
        return True
    if annotation is int:
        return 7
    if annotation is float:
        return 1.0
    if annotation is str:
        return "sample"
    if annotation is Decimal:
        return "0.5"
    if annotation is datetime:
        return "2024-01-02T03:04:05"
    if annotation is date:
        return "2024-01-02"
    if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
        return _sample_payload(annotation)
    return {"name": "sample"}


def _sample_payload(model) -> dict:
    """A full payload for a model, keyed by ALIAS because the alias is what the wire uses."""
    payload = {}
    for name, field in model.model_fields.items():
        payload[field.alias or name] = _sample_value(field.annotation)
    return payload


def _response_result(response_model):
    """A value a fake service can return so the declared response model validates."""
    if response_model is None:
        return {}
    if get_origin(response_model) is list:
        args = get_args(response_model)
        inner = _inner(args[0]) if args else Any
        if isinstance(inner, type) and hasattr(inner, "model_fields"):
            return [_sample_payload(inner)]
        return [{"name": "sample"}]
    inner = _inner(response_model)
    if isinstance(inner, type) and hasattr(inner, "model_fields"):
        return _sample_payload(inner)
    return {"message": "ok"}


def _service_call(endpoint) -> tuple:
    """(method name, is awaited), read from the handler body rather than its own name.

    The name is not the method: `get_all_candidataes` calls `service.get_all_candidates` (a
    misspelling kept because it is the operationId), and the auth handlers call
    `signup`/`signin`. Reading the AST keeps this correct when that changes; reading the
    `await` keeps a synchronous fake from silently returning a coroutine object.
    """
    tree = ast.parse(inspect.getsource(endpoint).lstrip())
    names = []
    awaited = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            awaited = True
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "service":
                names.append(node.attr)
    assert len(set(names)) == 1, "%s calls %r on its service" % (endpoint.__name__, names)
    return names[0], awaited


def _leaves(value):
    """Every scalar inside a payload, so "did this value arrive" is a set operation."""
    if isinstance(value, BaseModel):
        return _leaves(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_leaves(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_leaves(item))
        return out
    if isinstance(value, Enum):
        return [value.value]
    if isinstance(value, datetime):
        return [value.isoformat()]
    if value is None:
        return []
    return [value]


class Route:
    """One operation with everything a driver needs, computed once at import."""

    def __init__(self, owner: str, route: APIRoute):
        self.owner = owner
        self.route = route
        self.method = sorted(route.methods)[0]
        self.path = route.path
        self.endpoint = route.endpoint
        self.dependencies = list(route.dependant.dependencies)
        self.service_dependencies = [
            dep for dep in self.dependencies if not isinstance(dep.call, JWTBearer)
        ]
        self.service_method = None
        self.is_async = False
        if self.service_dependencies:
            self.service_method, self.is_async = _service_call(route.endpoint)

        self.path_values = {}
        for name in re.findall(r"\{([^}]+)\}", route.path):
            annotation = inspect.signature(route.endpoint).parameters[name].annotation
            self.path_values[name] = 7 if _inner(annotation) is int else "sample"

        self.query_values = {
            field.name: _sample_value(field.field_info.annotation)
            for field in route.dependant.query_params
        }

        self.request_kwargs: dict = {}
        self.sent: list = []
        self.expects_file = False
        body = route.body_field
        if body is not None:
            media = body.field_info.media_type
            if media == "multipart/form-data":
                self.request_kwargs["files"] = {"file": ("sample.mp4", b"media-bytes", "video/mp4")}
                self.expects_file = True
            elif media == "application/x-www-form-urlencoded":
                form = {
                    field.name: FORM_OVERRIDES.get(field.name, "sample")
                    for field in route.dependant.body_params
                }
                self.request_kwargs["data"] = form
                self.sent.extend(form.values())
            else:
                inner = _inner(body.field_info.annotation)
                payload = (
                    _sample_payload(inner)
                    if isinstance(inner, type) and hasattr(inner, "model_fields")
                    else {"name": "sample"}
                )
                self.request_kwargs["json"] = payload
                self.sent.extend(_leaves(payload))
        if self.query_values:
            self.request_kwargs["params"] = self.query_values
            self.sent.extend(_leaves(self.query_values))
        self.sent.extend(_leaves(self.path_values))
        self.result = _response_result(route.response_model)

    @property
    def id(self) -> str:
        return "%s::%s %s" % (self.owner, self.method, self.path)

    def url(self) -> str:
        path = self.path
        for name, value in self.path_values.items():
            path = path.replace("{%s}" % name, str(value))
        return MOUNT + path

    def has_auth(self) -> bool:
        return any(isinstance(dep.call, JWTBearer) for dep in self.dependencies)


def _mounted_routes() -> list:
    """Business operations of the /server sub-app, keyed back to the router that declared it.

    WHY THE KEY IS THE ENDPOINT FUNCTION AND NOT THE ROUTE OBJECT: include_router() builds a
    NEW APIRoute for every included route, so the object mounted on /server is not the object
    listed in module.router.routes and identity comparison silently yields an empty catalogue.
    The endpoint callable is the one thing that is shared.
    """
    owners = {}
    for module in ROUTERS:
        owner = module.__name__.rsplit(".", 1)[-1]
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                owners[id(route.endpoint)] = owner
    found = []
    for route in _SERVER.routes:
        if isinstance(route, APIRoute) and id(route.endpoint) in owners:
            found.append(Route(owners[id(route.endpoint)], route))
    return found


CATALOGUE = _mounted_routes()
CATALOGUE_IDS = [route.id for route in CATALOGUE]


@pytest.fixture(autouse=True)
def _clean_overrides():
    """Each test installs its own fakes; a leaked override would fake a later test."""
    _SERVER.dependency_overrides.clear()
    yield
    _SERVER.dependency_overrides.clear()


@pytest.fixture
def records(caplog):
    """Collect the handler's own log records.

    WHY NOT `caplog.set_level` ALONE: the application logger is "sd", configured with
    `propagate=False` because it owns a rich handler and a file handler. Its records never reach
    the root logger caplog listens on, so `caplog.records` stays empty and an assertion about
    logging would pass while proving nothing. Attaching caplog's handler to the application
    logger is what makes the claim real; the level is raised for the same reason.
    """
    logger = get_logger()
    logger.addHandler(caplog.handler)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous_level)


class _Service:
    """Stands in for a slice service: one method, the one the handler body calls."""

    def __init__(self, call, method_name):
        self._call = call
        self._method_name = method_name

    def __getattr__(self, name):
        if name.startswith("__"):
            # copy/deepcopy/pickle probe for magic methods on the instance; a fake service
            # must look like an ordinary object to them, not like a broken collaborator.
            raise AttributeError(name)
        if name != self._method_name:
            raise AssertionError(
                "the handler called %r but this route only declares %r" % (name, self._method_name)
            )
        return self._call


def _install(route: Route, *, result=None, error=None):
    """Point the route at a recording fake and return the call log."""
    calls = []

    if route.is_async:

        async def call(*args, **kwargs):
            calls.append((args, kwargs))
            if error is not None:
                raise error
            return result

    else:

        def call(*args, **kwargs):
            calls.append((args, kwargs))
            if error is not None:
                raise error
            return result

    for dep in route.dependencies:
        if isinstance(dep.call, JWTBearer):
            _SERVER.dependency_overrides[dep.call] = lambda: "test-token"
        else:
            service = _Service(call, route.service_method)
            _SERVER.dependency_overrides[dep.call] = lambda service=service: service
    return calls


# --- the catalogue itself ----------------------------------------------------


def test_every_mounted_business_operation_is_in_the_catalogue():
    """The catalogue IS the mounted surface; the registry only supplies names."""
    declared = sum(
        1 for module in ROUTERS for route in module.router.routes if isinstance(route, APIRoute)
    )
    assert len(CATALOGUE) == declared == 57


def test_every_catalogue_entry_belongs_to_a_registry_router():
    owners = {route.owner for route in CATALOGUE}
    assert owners == {module.__name__.rsplit(".", 1)[-1] for module in ROUTERS}


def test_the_catalogue_covers_every_operation_the_document_advertises():
    document = _SERVER.openapi()
    advertised = {
        (path, method)
        for path, operations in document["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "delete", "patch", "head", "options"}
    }
    mounted = {(route.path, route.method.lower()) for route in CATALOGUE}
    assert advertised - mounted == {("/health", "get")}
    assert mounted - advertised == set()


@pytest.mark.parametrize("route", CATALOGUE, ids=CATALOGUE_IDS)
def test_success_path_forwards_to_one_service_call_and_returns_its_result(route):
    calls = _install(route, result=route.result)

    response = _CLIENT.request(route.method, route.url(), **route.request_kwargs)

    assert response.status_code == 200, response.text
    assert json.loads(response.text) == json.loads(json.dumps(route.result, default=str))
    assert len(calls) == 1
    args, kwargs = calls[0]
    if route.expects_file:
        # starlette's UploadFile, not fastapi.UploadFile: the multipart parser constructs
        # the former and fastapi's is a SUBCLASS of it, so the obvious isinstance against
        # the fastapi name silently reports zero uploads.
        uploads = [item for item in args if isinstance(item, UploadFile)]
        assert len(uploads) == 1
        assert uploads[0].filename == "sample.mp4"
    received = _leaves(list(args)) + _leaves(kwargs)
    missing = [value for value in route.sent if value not in received]
    assert missing == [], "the handler did not forward %r" % (missing,)
    if route.has_auth():
        assert route.dependencies, "a token-protected route must declare its dependency"


@pytest.mark.parametrize("route", CATALOGUE, ids=CATALOGUE_IDS)
@pytest.mark.parametrize("error_class,status", SERVICE_ERRORS)
def test_service_error_maps_to_its_status_and_is_still_logged(route, error_class, status, records):
    error = error_class("boom from the service")
    _install(route, error=error)

    response = _CLIENT.request(route.method, route.url(), **route.request_kwargs)

    assert response.status_code == status, response.text
    assert response.json() == {"detail": "boom from the service"}
    failures = [
        record for record in records.records if record.levelno >= logging.ERROR and record.exc_info
    ]
    assert failures, "the handler swallowed %r instead of logging and re-raising" % (
        error_class.__name__,
    )


@pytest.mark.parametrize("route", CATALOGUE, ids=CATALOGUE_IDS)
def test_an_unhandled_exception_is_not_dressed_up_as_an_api_error(route):
    """Only ServiceError is translated; anything else stays a 500 the caller can trace.

    Pinned because catching `Exception` in a handler would turn a programming error into a
    plausible-looking 404 with a message the frontend would render to a user.
    """
    if not route.service_dependencies:
        pytest.skip("not yet migrated: this route calls no service")
    _install(route, error=RuntimeError("kaboom"))

    with TestClient(_APP, raise_server_exceptions=False) as client:
        response = client.request(route.method, route.url(), **route.request_kwargs)

    assert response.status_code == 500


_PROTECTED = [route for route in CATALOGUE if route.has_auth()]
_PROTECTED_IDS = [route.id for route in _PROTECTED]


@pytest.mark.parametrize("route", _PROTECTED, ids=_PROTECTED_IDS)
def test_a_token_protected_route_rejects_a_missing_token(route):
    """The 403 comes from JWTBearer, so no fake is installed for it here."""
    _CLIENT.app.dependency_overrides.clear()

    response = _CLIENT.request(route.method, route.url(), **route.request_kwargs)

    assert response.status_code == 403
    assert response.json() == {"detail": "Not authenticated"}


def test_public_routes_do_not_require_a_token():
    public = [route for route in CATALOGUE if not route.has_auth()]
    assert public, "every route declaring a token dependency would make the check vacuous"
    for route in public:
        calls = _install(route, result=route.result)
        assert _CLIENT.request(route.method, route.url(), **route.request_kwargs).status_code == 200
        assert len(calls) == 1


def test_service_error_base_class_still_renders_as_500():
    """A bare ServiceError is "unexpected"; only the subclasses carry a business status."""
    assert ServiceError.status_code == 500
    assert ServiceError("x").to_payload() == {"detail": "x"}
