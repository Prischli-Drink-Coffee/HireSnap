"""candidate_vacancies router, now served by the candidate_vacancies slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/candidate_vacancy_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/candidate_vacancies/application/`, and the object is resolved through
`service/composition/state.py`, so a test can substitute it without editing this
file.

WHY THE HANDLERS STILL LOOK THE SAME: the decorators, paths, tags, response models,
JWT dependencies and docstrings are the API contract and were carried over one for
one. Two things differ - each handler takes its collaborator as a `Depends`
parameter instead of importing a module, and the wrapper catches `ServiceError`
instead of `HTTPException`. `service/api/app.py` maps a `ServiceError` to the same
status code and the same body shape the old
exception produced, so a client cannot tell the difference.

Paths keep their leading slash and are mounted under /server, so the public URL is
/server<path>; the gateway rewrites /api/ to /server/.
"""

from typing import Dict

from fastapi import APIRouter, Depends

from service.api.security import JWTBearer
from service.composition.state import get_candidate_vacancy_service
from service.configuration.logging import get_logger
from service.models import CandidateVacancies
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get(
    "/candidate_vacancies/", response_model=list[CandidateVacancies], tags=["CandidateVacancy"]
)
async def get_all_candidate_vacancies(service=Depends(get_candidate_vacancy_service)):
    """
    Route for get all candidate vacancies from basedata.

    :return: response model List[CandidateVacancies].
    """
    try:
        return service.get_all_candidate_vacancies()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/candidate_vacancies/candidate_vacancy_id/{candidate_vacancy_id}",
    response_model=CandidateVacancies,
    tags=["CandidateVacancy"],
)
async def get_candidate_vacancy_by_id(
    candidate_vacancy_id: int, service=Depends(get_candidate_vacancy_service)
):
    """
    Route for get candidate vacancy by CandidateVacancyID.

    :param candidate_vacancy_id: ID by candidate vacancy. [int]

    :return: response model CandidateVacancies.
    """
    try:
        return service.get_candidate_vacancy_by_id(candidate_vacancy_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/candidate_vacancies/",
    response_model=CandidateVacancies,
    tags=["CandidateVacancy"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_candidate_vacancy(
    candidate_vacancy: CandidateVacancies, service=Depends(get_candidate_vacancy_service)
):
    """
    Route for create candidate vacancy in basedata.

    :param candidate_vacancy: Model candidate vacancy. [CandidateVacancies]

    :return: response model CandidateVacancies.
    """
    try:
        return service.create_candidate_vacancy(candidate_vacancy)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/candidate_vacancies/{candidate_vacancy_id}",
    response_model=Dict,
    tags=["CandidateVacancy"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_candidate_vacancy(
    candidate_vacancy_id, candidate_vacancy: Dict, service=Depends(get_candidate_vacancy_service)
):
    """
    Route for update candidate vacancy in basedata.

    :param candidate_vacancy_id: ID by candidate vacancy. [int]

    :param candidate_vacancy: Model candidate vacancy. [CandidateVacancies]

    :return: response model dict.
    """
    try:
        return service.update_candidate_vacancy(candidate_vacancy_id, candidate_vacancy)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/candidate_vacancies/{candidate_vacancy_id}",
    response_model=Dict,
    tags=["CandidateVacancy"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_candidate_vacancy(
    candidate_vacancy_id, service=Depends(get_candidate_vacancy_service)
):
    """
    Route for delete candidate vacancy from basedata.

    :param candidate_vacancy_id: ID by candidate vacancy. [int]

    :return: response model dict.
    """
    try:
        return service.delete_candidate_vacancy(candidate_vacancy_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
