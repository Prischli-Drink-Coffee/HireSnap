from service.repositories import personality_vacancy_repository
from service.domain.models import PersonalityVacancies
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists
from service.services.personality_services import get_personality_by_id
from service.services.vacancy_services import get_vacancy_by_id
from typing import Dict


def get_all_personality_vacancies():
    personality_vacancies = personality_vacancy_repository.get_all_personality_vacancies()
    return [PersonalityVacancies(**personality_vacancy) for personality_vacancy in personality_vacancies]


def get_personality_vacancy_by_id(personality_vacancy_id: int):
    personality_vacancy = personality_vacancy_repository.get_personality_vacancy_by_id(personality_vacancy_id)
    if not personality_vacancy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Personality vacancy not found')
    return PersonalityVacancies(**personality_vacancy) if personality_vacancy else None


def create_personality_vacancy(personality_vacancy: PersonalityVacancies):
    get_personality_by_id(personality_vacancy.PersonalityID)
    get_vacancy_by_id(personality_vacancy.VacancyID)
    personality_vacancy_id = personality_vacancy_repository.create_personality_vacancy(personality_vacancy)
    return get_personality_vacancy_by_id(personality_vacancy_id)


def update_personality_vacancy(personality_vacancy_id: int, personality_vacancy: PersonalityVacancies):
    get_personality_vacancy_by_id(personality_vacancy_id)
    if personality_vacancy.get("personality_id"):
        get_personality_by_id(personality_vacancy.get("personality_id"))
    if personality_vacancy.get("vacancy_id"):
        get_vacancy_by_id(personality_vacancy.get("vacancy_id"))
    personality_vacancy_repository.update_personality_vacancy(personality_vacancy_id, personality_vacancy)
    return {"message": "Personality vacancy updated successfully"}


def delete_personality_vacancy(personality_vacancy_id: int):
    get_personality_vacancy_by_id(personality_vacancy_id)
    personality_vacancy_repository.delete_personality_vacancy(personality_vacancy_id)
    return {"message": "Personality vacancy deleted successfully"}
