"""The business rules of the magics slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/magic_services.py`,
importing the repository module directly and raising `fastapi.HTTPException`. They are
methods of one class now, because three things follow and none of them did before:

  * the object is what `service/composition/state.py` hands out, so a test can pass
    a fake collaborator instead of monkeypatching a module global;
  * nothing here imports a web framework, so the same rule runs from a celery task,
    where there is no request to raise an HTTP error against;
  * a failure is a `ServiceError` from `service/shared/errors.py`, and exactly one
    handler in `service/api/app.py` turns it into a status code and a body.

WHY THE SIGNATURES AND THE DETAIL STRINGS ARE UNCHANGED: the 404s, their messages and
the order of the checks are the API contract - the frontend prints the detail string,
and the generated OpenAPI document is pinned by a snapshot test. So this is a rename
plus a different exception class, and nothing else.

PERSISTENCE: `service/services/magics/persistence/`, holding the same raw SQL as
before the move. Two explanation columns and two answer columns per row; the rules are the
generic named-table ones.
"""

from __future__ import annotations

from typing import Dict

from service.models import Magics
from service.services.magics import persistence
from service.shared.errors import NotFoundError
from service.shared.validation import check_for_duplicates, check_if_exists


class MagicService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None) -> None:
        self._repository = repository if repository is not None else persistence

    def get_all_magics(self):
        magics = self._repository.get_all_magics()
        return [Magics(**magic) for magic in magics]

    def get_magic_by_id(self, magic_id: int):
        magic = self._repository.get_magic_by_id(magic_id)
        if not magic:
            raise NotFoundError("Magic not found")
        return Magics(**magic) if magic else None

    def create_magic(self, magic: Magics):
        check_if_exists(
            get_all=self.get_all_magics,
            attr_name="Name",
            attr_value=magic.Name,
            exception_detail="Name already exist",
        )
        magic_id = self._repository.create_magic(magic)
        return self.get_magic_by_id(magic_id)

    def update_magic(self, magic_id: int, magic: Dict):
        check_for_duplicates(
            get_all=self.get_all_magics,
            check_id=magic_id,
            attr_name="Name",
            attr_value=magic.get("name"),
            exception_detail="Name already exist",
        )
        self._repository.update_magic(magic_id, magic)
        return {"message": "Magic updated successfully"}

    def delete_magic(self, magic_id: int):
        self.get_magic_by_id(magic_id)
        self._repository.delete_magic(magic_id)
        return {"message": "Magic deleted successfully"}
