from service.repositories import candidate_vacancy_repository
from service.domain.models import CandidateVacancies
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists
from service.services.user_services import get_user_by_id
from service.services.vacancy_services import get_vacancy_by_id
from typing import Dict


def get_all_candidate_vacancies():
    candidate_vacancies = candidate_vacancy_repository.get_all_candidate_vacancies()
    return [CandidateVacancies(**candidate_vacancy) for candidate_vacancy in candidate_vacancies]


def get_candidate_vacancy_by_id(candidate_vacancy_id: int):
    candidate_vacancy = candidate_vacancy_repository.get_candidate_vacancy_by_id(candidate_vacancy_id)
    if not candidate_vacancy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Candidate vacancy not found')
    return CandidateVacancies(**candidate_vacancy) if candidate_vacancy else None


def create_candidate_vacancy(candidate_vacancy: CandidateVacancies):
    get_user_by_id(candidate_vacancy.UserID)
    get_vacancy_by_id(candidate_vacancy.VacancyID)
    candidate_vacancy_id = candidate_vacancy_repository.create_candidate_vacancy(candidate_vacancy)
    return get_candidate_vacancy_by_id(candidate_vacancy_id)


def update_candidate_vacancy(candidate_vacancy_id: int, candidate_vacancy: CandidateVacancies):
    get_candidate_vacancy_by_id(candidate_vacancy_id)
    if candidate_vacancy.get("user_id"):
        get_user_by_id(candidate_vacancy.get("user_id"))
    if candidate_vacancy.get("vacancy_id"):
        get_vacancy_by_id(candidate_vacancy.get("vacancy_id"))
    candidate_vacancy_repository.update_candidate_vacancy(candidate_vacancy_id, candidate_vacancy)
    return {"message": "Candidate vacancy updated successfully"}


def delete_candidate_vacancy(candidate_vacancy_id: int):
    get_candidate_vacancy_by_id(candidate_vacancy_id)
    candidate_vacancy_repository.delete_candidate_vacancy(candidate_vacancy_id)
    return {"message": "Candidate vacancy deleted successfully"}
