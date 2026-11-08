from service.repositories import vacancy_repository
from service.domain.models import Vacancies
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists
from typing import Dict


def get_all_vacancies():
    vacancies = vacancy_repository.get_all_vacancies()
    return [Vacancies(**vacancy) for vacancy in vacancies]


def get_vacancy_by_id(vacancy_id: int):
    vacancy = vacancy_repository.get_vacancy_by_id(vacancy_id)
    if not vacancy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Vacancy not found')
    return Vacancies(**vacancy) if vacancy else None


def create_vacancy(vacancy: Vacancies):
    check_if_exists(
        get_all=get_all_vacancies,
        attr_name="Name",
        attr_value=vacancy.Name,
        exception_detail='Name already exist'
    )
    vacancy_id = vacancy_repository.create_vacancy(vacancy)
    return get_vacancy_by_id(vacancy_id)


def update_vacancy(vacancy_id: int, vacancy: Dict):
    check_for_duplicates(
        get_all=get_all_vacancies,
        check_id=vacancy_id,
        attr_name="Name",
        attr_value=vacancy.get("name"),
        exception_detail='Name already exist'
    )
    vacancy_repository.update_vacancy(vacancy_id, vacancy)
    return {"message": "Vacancy updated successfully"}


def delete_vacancy(vacancy_id: int):
    get_vacancy_by_id(vacancy_id)
    vacancy_repository.delete_vacancy(vacancy_id)
    return {"message": "Vacancy deleted successfully"}
