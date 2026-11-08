"""The business rules of the personalities slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/personality_services.py`,
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

PERSISTENCE: `service/services/personalities/persistence/`, holding the same raw SQL as
before the move. A personality row is one OCEAN dimension plus the embedding that represents it.
"""

from __future__ import annotations

from typing import Dict

from service.models import Personalities
from service.services.personalities import persistence
from service.shared.errors import NotFoundError
from service.shared.validation import check_for_duplicates, check_if_exists


class PersonalityService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None) -> None:
        self._repository = repository if repository is not None else persistence

    def get_all_personalities(self):
        personalities = self._repository.get_all_personalities()
        return [Personalities(**personality) for personality in personalities]

    def get_personality_by_id(self, personality_id: int):
        personality = self._repository.get_personality_by_id(personality_id)
        if not personality:
            raise NotFoundError("Personality not found")
        return Personalities(**personality) if personality else None

    def create_personality(self, personality: Personalities):
        check_if_exists(
            get_all=self.get_all_personalities,
            attr_name="Name",
            attr_value=personality.Name,
            exception_detail="Name already exist",
        )
        personality_id = self._repository.create_personality(personality)
        return self.get_personality_by_id(personality_id)

    def update_personality(self, personality_id: int, personality: Dict):
        check_for_duplicates(
            get_all=self.get_all_personalities,
            check_id=personality_id,
            attr_name="Name",
            attr_value=personality.get("name"),
            exception_detail="Name already exist",
        )
        self._repository.update_personality(personality_id, personality)
        return {"message": "Personality updated successfully"}

    def delete_personality(self, personality_id: int):
        self.get_personality_by_id(personality_id)
        self._repository.delete_personality(personality_id)
        return {"message": "Personality deleted successfully"}
