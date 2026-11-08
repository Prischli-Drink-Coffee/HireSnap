"""The business rules of the personality_vacancies slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/personality_vacancy_services.py`,
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

PERSISTENCE: `service/services/personality_vacancies/persistence/`, holding the same raw SQL as
before the move. A link table: both foreign keys are checked before the row is written, so
a bad id fails as a 404 naming the missing side.
"""

from __future__ import annotations

from service.models import PersonalityVacancies
from service.services.personality_vacancies import persistence
from service.shared.errors import NotFoundError


class PersonalityVacancyService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None, personality=None, vacancy=None) -> None:
        self._repository = repository if repository is not None else persistence
        self._personality_override = personality
        self._vacancy_override = vacancy

    @property
    def _personality(self):
        """Resolved on first use: the composition layer decides which instance this is."""
        if self._personality_override is None:
            from service.composition import state

            self._personality_override = state.get_personality_service()
        return self._personality_override

    @property
    def _vacancy(self):
        """Resolved on first use: the composition layer decides which instance this is."""
        if self._vacancy_override is None:
            from service.composition import state

            self._vacancy_override = state.get_vacancy_service()
        return self._vacancy_override

    def get_all_personality_vacancies(self):
        personality_vacancies = self._repository.get_all_personality_vacancies()
        return [
            PersonalityVacancies(**personality_vacancy)
            for personality_vacancy in personality_vacancies
        ]

    def get_personality_vacancy_by_id(self, personality_vacancy_id: int):
        personality_vacancy = self._repository.get_personality_vacancy_by_id(personality_vacancy_id)
        if not personality_vacancy:
            raise NotFoundError("Personality vacancy not found")
        return PersonalityVacancies(**personality_vacancy) if personality_vacancy else None

    def create_personality_vacancy(self, personality_vacancy: PersonalityVacancies):
        self._personality.get_personality_by_id(personality_vacancy.PersonalityID)
        self._vacancy.get_vacancy_by_id(personality_vacancy.VacancyID)
        personality_vacancy_id = self._repository.create_personality_vacancy(personality_vacancy)
        return self.get_personality_vacancy_by_id(personality_vacancy_id)

    def update_personality_vacancy(
        self, personality_vacancy_id: int, personality_vacancy: PersonalityVacancies
    ):
        self.get_personality_vacancy_by_id(personality_vacancy_id)
        if personality_vacancy.get("personality_id"):
            self._personality.get_personality_by_id(personality_vacancy.get("personality_id"))
        if personality_vacancy.get("vacancy_id"):
            self._vacancy.get_vacancy_by_id(personality_vacancy.get("vacancy_id"))
        self._repository.update_personality_vacancy(personality_vacancy_id, personality_vacancy)
        return {"message": "Personality vacancy updated successfully"}

    def delete_personality_vacancy(self, personality_vacancy_id: int):
        self.get_personality_vacancy_by_id(personality_vacancy_id)
        self._repository.delete_personality_vacancy(personality_vacancy_id)
        return {"message": "Personality vacancy deleted successfully"}
