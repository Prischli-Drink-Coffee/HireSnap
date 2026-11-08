"""The business rules of the candidate_vacancies slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/candidate_vacancy_services.py`,
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

PERSISTENCE: `service/services/candidate_vacancies/persistence/`, holding the same raw SQL as
before the move. A link table holding the score the matching slice computed; both foreign
keys are checked first.
"""

from __future__ import annotations

from service.models import CandidateVacancies
from service.services.candidate_vacancies import persistence
from service.shared.errors import NotFoundError


class CandidateVacancyService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None, user=None, vacancy=None) -> None:
        self._repository = repository if repository is not None else persistence
        self._user_override = user
        self._vacancy_override = vacancy

    @property
    def _user(self):
        """Resolved on first use: the composition layer decides which instance this is."""
        if self._user_override is None:
            from service.composition import state

            self._user_override = state.get_user_service()
        return self._user_override

    @property
    def _vacancy(self):
        """Resolved on first use: the composition layer decides which instance this is."""
        if self._vacancy_override is None:
            from service.composition import state

            self._vacancy_override = state.get_vacancy_service()
        return self._vacancy_override

    def get_all_candidate_vacancies(self):
        candidate_vacancies = self._repository.get_all_candidate_vacancies()
        return [
            CandidateVacancies(**candidate_vacancy) for candidate_vacancy in candidate_vacancies
        ]

    def get_candidate_vacancy_by_id(self, candidate_vacancy_id: int):
        candidate_vacancy = self._repository.get_candidate_vacancy_by_id(candidate_vacancy_id)
        if not candidate_vacancy:
            raise NotFoundError("Candidate vacancy not found")
        return CandidateVacancies(**candidate_vacancy) if candidate_vacancy else None

    def create_candidate_vacancy(self, candidate_vacancy: CandidateVacancies):
        self._user.get_user_by_id(candidate_vacancy.UserID)
        self._vacancy.get_vacancy_by_id(candidate_vacancy.VacancyID)
        candidate_vacancy_id = self._repository.create_candidate_vacancy(candidate_vacancy)
        return self.get_candidate_vacancy_by_id(candidate_vacancy_id)

    def update_candidate_vacancy(
        self, candidate_vacancy_id: int, candidate_vacancy: CandidateVacancies
    ):
        self.get_candidate_vacancy_by_id(candidate_vacancy_id)
        if candidate_vacancy.get("user_id"):
            self._user.get_user_by_id(candidate_vacancy.get("user_id"))
        if candidate_vacancy.get("vacancy_id"):
            self._vacancy.get_vacancy_by_id(candidate_vacancy.get("vacancy_id"))
        self._repository.update_candidate_vacancy(candidate_vacancy_id, candidate_vacancy)
        return {"message": "Candidate vacancy updated successfully"}

    def delete_candidate_vacancy(self, candidate_vacancy_id: int):
        self.get_candidate_vacancy_by_id(candidate_vacancy_id)
        self._repository.delete_candidate_vacancy(candidate_vacancy_id)
        return {"message": "Candidate vacancy deleted successfully"}
