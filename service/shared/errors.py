"""One exception per kind of domain failure, and no framework in sight.

WHY this module exists: the vertical-slice rule says an `application/` module imports
no framework, so a business rule cannot raise `fastapi.HTTPException`. Instead it raises
one of the four classes below, and `service/api/app.py` registers ONE handler that turns
any `ServiceError` into `{"detail": <message>}` with the error's own status. That makes
the status code a property of the failure rather than of the handler that happened to
raise it, and it is what lets the same rule run from a celery worker where there is no
request and no HTTPException to raise.

WHY TWO CLASSES CARRY 404: `NotFoundError` and `ConflictError` both render as 404 on
purpose, because that is what the API has always returned and the frontend keys its
messages off the `detail` STRING, not the status ("Email already exist" arrives as a 404
and the signup form prints it). A conflict that returned 409 would be more correct and
would break the screen, so the honest move is to keep 404 and say so here. Changing
either number is a contract change: `backend/tests/test_service_errors.py` pins the table,
and `tests/snapshots/critical_endpoint_openapi.json` pins the generated document.
"""

from __future__ import annotations

from typing import Optional


class ServiceError(Exception):
    """Base class: a failure the application layer knows how to describe.

    `detail` is the message rendered to the client. It is kept byte-identical to the
    strings the old `HTTPException` calls used, because those strings are the contract
    the frontend reads.
    """

    status_code: int = 500

    def __init__(self, detail: str = "", *, status_code: Optional[int] = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = int(status_code)

    def to_payload(self) -> dict:
        return {"detail": self.detail}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "%s(status_code=%r, detail=%r)" % (
            type(self).__name__,
            self.status_code,
            self.detail,
        )


class NotFoundError(ServiceError):
    """The row (or the referenced row) does not exist. Rendered as 404."""

    status_code = 404


class ConflictError(ServiceError):
    """A uniqueness rule said no. Rendered as 404 for historical reasons - see above."""

    status_code = 404


class InvalidPayloadError(ServiceError):
    """The payload cannot be applied as given. Rendered as 400."""

    status_code = 400


class UnauthorizedError(ServiceError):
    """The caller is not who the token says, or the token is wrong. Rendered as 401."""

    status_code = 401


def error_payload(error: ServiceError) -> dict:
    """The body shape every client reads. Kept as a function so it has one spelling."""
    payload: dict = {"detail": error.detail}
    return payload
