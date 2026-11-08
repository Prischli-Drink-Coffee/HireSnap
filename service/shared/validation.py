"""Reusable uniqueness and existence checks, expressed as domain failures.

WHY no fastapi here any more: every caller of these helpers is an `application/`
module of a slice, and the vertical-slice rule is that the application layer imports
no framework (backend/tests/test_service_boundary.py enforces it). A helper that raised
`fastapi.HTTPException` would smuggle the framework in through the back door - the
import would be one level deeper and the rule would be false.

WHAT CHANGED FOR THE CLIENT: nothing. `ConflictError` renders as 404 through the single
handler in service/api/app.py, which is exactly the status the old `HTTPException(404,
detail=...)` produced, and the body keeps the same `{"detail": ...}` shape. The two 404s
in service/shared/errors.py explain why a conflict is not a 409.
"""

from __future__ import annotations

from typing import Any, Callable, List

from service.shared.errors import ConflictError


def check_for_duplicates(
    get_all: Callable[[], List[Any]],
    check_id: int,
    attr_name: str,
    attr_value: Any,
    exception_detail: str,
):
    """Fail when another row already holds this value.

    The row being updated is skipped by id, so re-saving a record with its own name is
    not a conflict. A missing id on either side is a programming error rather than a
    client error, and `int()` keeps the comparison honest when the id arrives as a
    string from a JSON body.
    """
    existing_items = get_all()
    for item in existing_items:
        if int(item.ID) == int(check_id):
            continue
        if getattr(item, attr_name) == attr_value:
            raise ConflictError(exception_detail)


def check_if_exists(
    get_all: Callable[[], List[Any]],
    attr_name: str,
    attr_value: Any,
    exception_detail: str,
):
    """Fail when ANY row already holds this value (the create path)."""
    existing_items = get_all()
    for item in existing_items:
        if getattr(item, attr_name) == attr_value:
            raise ConflictError(exception_detail)


def return_id_if_exists(get_all: Callable[[], List[Any]], attr_name: str, attr_value: Any):
    """The id of the row holding this value, or None. Kept: a later upsert wants it."""
    existing_items = get_all()
    for item in existing_items:
        if getattr(item, attr_name) == attr_value:
            return item.ID
    return None
