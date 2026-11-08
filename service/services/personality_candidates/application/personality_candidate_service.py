"""The business rules of the personality_candidates slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in
`service/services/personality_candidate_services.py`,
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

PERSISTENCE: `service/services/personality_candidates/persistence/`, holding the same raw SQL as
before the move. A link table: both foreign keys are checked before the row is written, so
a bad id fails as a 404 naming the missing side.
"""

from __future__ import annotations

from typing import Dict

from service.models import PersonalityCandidates
from service.services.personality_candidates import persistence
from service.shared.errors import NotFoundError


class PersonalityCandidateService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None, personality=None, user=None) -> None:
        self._repository = repository if repository is not None else persistence
        self._personality_override = personality
        self._user_override = user

    @property
    def _personality(self):
        """Resolved on first use: the composition layer decides which instance this is."""
        if self._personality_override is None:
            from service.composition import state

            self._personality_override = state.get_personality_service()
        return self._personality_override

    @property
    def _user(self):
        """Resolved on first use: the composition layer decides which instance this is."""
        if self._user_override is None:
            from service.composition import state

            self._user_override = state.get_user_service()
        return self._user_override

    def get_all_personality_candidates(self):
        personality_candidates = self._repository.get_all_personality_candidates()
        return [
            PersonalityCandidates(**personality_candidate)
            for personality_candidate in personality_candidates
        ]

    def get_personality_candidate_by_id(self, personality_candidate_id: int):
        personality_candidate = self._repository.get_personality_candidate_by_id(
            personality_candidate_id
        )
        if not personality_candidate:
            raise NotFoundError("Personality candidate not found")
        return PersonalityCandidates(**personality_candidate) if personality_candidate else None

    def create_personality_candidate(self, personality_candidate: PersonalityCandidates):
        self._personality.get_personality_by_id(personality_candidate.PersonalityID)
        self._user.get_user_by_id(personality_candidate.UserID)
        personality_candidate_id = self._repository.create_personality_candidate(
            personality_candidate
        )
        return self.get_personality_candidate_by_id(personality_candidate_id)

    def update_personality_candidate(
        self, personality_candidate_id: int, personality_candidate: Dict
    ):
        self.get_personality_candidate_by_id(personality_candidate_id)
        if personality_candidate.get("personality_id"):
            self._personality.get_personality_by_id(personality_candidate.get("personality_id"))
        if personality_candidate.get("user_id"):
            self._user.get_user_by_id(personality_candidate.get("user_id"))
        self._repository.update_personality_candidate(
            personality_candidate_id, personality_candidate
        )
        return {"message": "Personality candidate updated successfully"}

    def delete_personality_candidate(self, personality_candidate_id: int):
        self.get_personality_candidate_by_id(personality_candidate_id)
        self._repository.delete_personality_candidate(personality_candidate_id)
        return {"message": "Personality candidate deleted successfully"}
