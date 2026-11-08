"""vacancies router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import HTTPException
from service.domain.models import Vacancies
from service.services import vacancy_services
from typing import Dict
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/vacancies/", response_model=list[Vacancies], tags=["Vacancy"])
async def get_all_vacancies():
    """
    Route for get all vacancies from basedata.

    :return: response model List[Vacancies].
    """
    try:
        return vacancy_services.get_all_vacancies()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/vacancies/vacancy_id/{vacancy_id}", response_model=Vacancies, tags=["Vacancy"])
async def get_vacancy_by_id(vacancy_id: int):
    """
    Route for get vacancy by id from basedata.

    :param vacancy_id: id of vacancy.
    :return: response model Vacancy.
    """
    try:
        return vacancy_services.get_vacancy_by_id(vacancy_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/vacancies/", response_model=Vacancies, tags=["Vacancy"])
async def create_vacancy(vacancy: Vacancies):
    """
    Route for create vacancy in basedata.

    :param vacancy: Model vacancy. [Vacancy]

    :return: response model Vacancy.
    """
    try:
        return vacancy_services.create_vacancy(vacancy)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/vacancies/{vacancy_id}", response_model=Dict, tags=["Vacancy"])
async def update_vacancy(vacancy_id: int, vacancy: Dict):
    """
    Route for update vacancy in basedata.

    :param vacancy_id: id of vacancy.
    :param vacancy: Model vacancy. [Vacancy]

    :return: response model Vacancy.
    """
    try:
        return vacancy_services.update_vacancy(vacancy_id, vacancy)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/vacancies/{vacancy_id}", response_model=Dict, tags=["Vacancy"])
async def delete_vacancy(vacancy_id: int):
    """
    Route for delete vacancy in basedata.

    :param vacancy_id: id of vacancy.

    :return: response model Vacancy.
    """
    try:
        return vacancy_services.delete_vacancy(vacancy_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
