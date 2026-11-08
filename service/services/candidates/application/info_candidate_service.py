"""The business rules of the candidates slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/info_candidate_services.py`,
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

PERSISTENCE: `service/services/candidates/persistence/`, holding the same raw SQL as
before the move. The public path keeps the historic info_candidates spelling even though
the slice is named candidates.
"""

from __future__ import annotations

from typing import Dict

from service.models import InfoCandidates
from service.services.candidates import persistence
from service.shared.errors import NotFoundError
from service.shared.validation import check_for_duplicates, check_if_exists


class InfoCandidateService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None) -> None:
        self._repository = repository if repository is not None else persistence

    def get_all_info_candidates(self):
        info_candidates = self._repository.get_all_info_candidates()
        return [InfoCandidates(**info_candidate) for info_candidate in info_candidates]

    def get_info_candidate_by_id(self, info_candidate_id: int):
        info_candidate = self._repository.get_info_candidate_by_id(info_candidate_id)
        if not info_candidate:
            raise NotFoundError("Info candidate not found")
        return InfoCandidates(**info_candidate) if info_candidate else None

    def create_info_candidate(self, info_candidate: InfoCandidates):
        check_if_exists(
            get_all=self.get_all_info_candidates,
            attr_name="Name",
            attr_value=info_candidate.Name,
            exception_detail="Name already exist",
        )
        info_candidate_id = self._repository.create_info_candidate(info_candidate)
        return self.get_info_candidate_by_id(info_candidate_id)

    def update_info_candidate(self, info_candidate_id: int, info_candidate: Dict):
        check_for_duplicates(
            get_all=self.get_all_info_candidates,
            check_id=info_candidate_id,
            attr_name="Name",
            attr_value=info_candidate.get("name"),
            exception_detail="Name already exist",
        )
        self._repository.update_info_candidate(info_candidate_id, info_candidate)
        return {"message": "Info candidate updated successfully"}

    def delete_info_candidate(self, info_candidate_id: int):
        self.get_info_candidate_by_id(info_candidate_id)
        self._repository.delete_info_candidate(info_candidate_id)
        return {"message": "Info candidate deleted successfully"}
