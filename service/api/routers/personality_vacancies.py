"""personality_vacancies router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import PersonalityVacancies
from service.services import personality_vacancy_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/personality_vacancies/", response_model=list[PersonalityVacancies], tags=["PersonalityVacancy"])
async def get_all_personality_vacancies():
    """
    Route for get all personality vacancies from basedata.

    :return: response model List[PersonalityVacancies].
    """
    try:
        return personality_vacancy_services.get_all_personality_vacancies()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/personality_vacancies/personality_vacancy_id/{personality_vacancy_id}", response_model=PersonalityVacancies,
                tags=["PersonalityVacancy"])
async def get_personality_vacancy_by_id(personality_vacancy_id: int):
    """
    Route for get personality vacancy by PersonalityVacancyID.

    :param personality_vacancy_id: ID by personality vacancy. [int]

    :return: response model PersonalityVacancies.
    """
    try:
        return personality_vacancy_services.get_personality_vacancy_by_id(personality_vacancy_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/personality_vacancies/", response_model=PersonalityVacancies, tags=["PersonalityVacancy"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_personality_vacancy(personality_vacancy: PersonalityVacancies):
    """
    Route for create personality vacancy in basedata.

    :param personality_vacancy: Model personality vacancy. [PersonalityVacancies]

    :return: response model PersonalityVacancies.
    """
    try:
        return personality_vacancy_services.create_personality_vacancy(personality_vacancy)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/personality_vacancies/{personality_vacancy_id}", response_model=Dict, tags=["PersonalityVacancy"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_personality_vacancy(personality_vacancy_id, personality_vacancy: Dict):
    """
    Route for update personality vacancy in basedata.

    :param personality_vacancy_id: ID by personality vacancy. [int]

    :param personality_vacancy: Model personality vacancy. [PersonalityVacancies]

    :return: response model dict.
    """
    try:
        return personality_vacancy_services.update_personality_vacancy(personality_vacancy_id, personality_vacancy)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/personality_vacancies/{personality_vacancy_id}", response_model=Dict, tags=["PersonalityVacancy"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_personality_vacancy(personality_vacancy_id):
    """
    Route for delete personality vacancy from basedata.

    :param personality_vacancy_id: ID by personality vacancy. [int]

    :return: response model dict.
    """
    try:
        return personality_vacancy_services.delete_personality_vacancy(personality_vacancy_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
