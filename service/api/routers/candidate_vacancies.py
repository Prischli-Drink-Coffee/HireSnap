"""candidate_vacancies router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import CandidateVacancies
from service.services import candidate_vacancy_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/candidate_vacancies/", response_model=list[CandidateVacancies], tags=["CandidateVacancy"])
async def get_all_candidate_vacancies():
    """
    Route for get all candidate vacancies from basedata.

    :return: response model List[CandidateVacancies].
    """
    try:
        return candidate_vacancy_services.get_all_candidate_vacancies()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/candidate_vacancies/candidate_vacancy_id/{candidate_vacancy_id}", response_model=CandidateVacancies,
                tags=["CandidateVacancy"])
async def get_candidate_vacancy_by_id(candidate_vacancy_id: int):
    """
    Route for get candidate vacancy by CandidateVacancyID.

    :param candidate_vacancy_id: ID by candidate vacancy. [int]

    :return: response model CandidateVacancies.
    """
    try:
        return candidate_vacancy_services.get_candidate_vacancy_by_id(candidate_vacancy_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/candidate_vacancies/", response_model=CandidateVacancies, tags=["CandidateVacancy"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_candidate_vacancy(candidate_vacancy: CandidateVacancies):
    """
    Route for create candidate vacancy in basedata.

    :param candidate_vacancy: Model candidate vacancy. [CandidateVacancies]

    :return: response model CandidateVacancies.
    """
    try:
        return candidate_vacancy_services.create_candidate_vacancy(candidate_vacancy)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/candidate_vacancies/{candidate_vacancy_id}", response_model=Dict, tags=["CandidateVacancy"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_candidate_vacancy(candidate_vacancy_id, candidate_vacancy: Dict):
    """
    Route for update candidate vacancy in basedata.

    :param candidate_vacancy_id: ID by candidate vacancy. [int]

    :param candidate_vacancy: Model candidate vacancy. [CandidateVacancies]

    :return: response model dict.
    """
    try:
        return candidate_vacancy_services.update_candidate_vacancy(candidate_vacancy_id, candidate_vacancy)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/candidate_vacancies/{candidate_vacancy_id}", response_model=Dict, tags=["CandidateVacancy"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_candidate_vacancy(candidate_vacancy_id):
    """
    Route for delete candidate vacancy from basedata.

    :param candidate_vacancy_id: ID by candidate vacancy. [int]

    :return: response model dict.
    """
    try:
        return candidate_vacancy_services.delete_candidate_vacancy(candidate_vacancy_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
