"""The business rules of the vacancies slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/vacancy_services.py`,
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

PERSISTENCE: `service/services/vacancies/persistence/`, holding the same raw SQL as
before the move. Uniqueness is the vacancy name, the same rule every named table uses.
"""

from __future__ import annotations

from typing import Dict

from service.models import Vacancies
from service.services.vacancies import persistence
from service.shared.errors import NotFoundError
from service.shared.validation import check_for_duplicates, check_if_exists


class VacancyService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None) -> None:
        self._repository = repository if repository is not None else persistence

    def get_all_vacancies(self):
        vacancies = self._repository.get_all_vacancies()
        return [Vacancies(**vacancy) for vacancy in vacancies]

    def get_vacancy_by_id(self, vacancy_id: int):
        vacancy = self._repository.get_vacancy_by_id(vacancy_id)
        if not vacancy:
            raise NotFoundError("Vacancy not found")
        return Vacancies(**vacancy) if vacancy else None

    def create_vacancy(self, vacancy: Vacancies):
        check_if_exists(
            get_all=self.get_all_vacancies,
            attr_name="Name",
            attr_value=vacancy.Name,
            exception_detail="Name already exist",
        )
        vacancy_id = self._repository.create_vacancy(vacancy)
        return self.get_vacancy_by_id(vacancy_id)

    def update_vacancy(self, vacancy_id: int, vacancy: Dict):
        check_for_duplicates(
            get_all=self.get_all_vacancies,
            check_id=vacancy_id,
            attr_name="Name",
            attr_value=vacancy.get("name"),
            exception_detail="Name already exist",
        )
        self._repository.update_vacancy(vacancy_id, vacancy)
        return {"message": "Vacancy updated successfully"}

    def delete_vacancy(self, vacancy_id: int):
        self.get_vacancy_by_id(vacancy_id)
        self._repository.delete_vacancy(vacancy_id)
        return {"message": "Vacancy deleted successfully"}
