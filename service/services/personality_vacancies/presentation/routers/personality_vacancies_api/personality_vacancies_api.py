"""personality_vacancies router, now served by the personality_vacancies slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/personality_vacancy_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/personality_vacancies/application/`, and the object is resolved through
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
from service.composition.state import get_personality_vacancy_service
from service.configuration.logging import get_logger
from service.models import PersonalityVacancies
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get(
    "/personality_vacancies/",
    response_model=list[PersonalityVacancies],
    tags=["PersonalityVacancy"],
)
async def get_all_personality_vacancies(service=Depends(get_personality_vacancy_service)):
    """
    Route for get all personality vacancies from basedata.

    :return: response model List[PersonalityVacancies].
    """
    try:
        return service.get_all_personality_vacancies()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/personality_vacancies/personality_vacancy_id/{personality_vacancy_id}",
    response_model=PersonalityVacancies,
    tags=["PersonalityVacancy"],
)
async def get_personality_vacancy_by_id(
    personality_vacancy_id: int, service=Depends(get_personality_vacancy_service)
):
    """
    Route for get personality vacancy by PersonalityVacancyID.

    :param personality_vacancy_id: ID by personality vacancy. [int]

    :return: response model PersonalityVacancies.
    """
    try:
        return service.get_personality_vacancy_by_id(personality_vacancy_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/personality_vacancies/",
    response_model=PersonalityVacancies,
    tags=["PersonalityVacancy"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_personality_vacancy(
    personality_vacancy: PersonalityVacancies, service=Depends(get_personality_vacancy_service)
):
    """
    Route for create personality vacancy in basedata.

    :param personality_vacancy: Model personality vacancy. [PersonalityVacancies]

    :return: response model PersonalityVacancies.
    """
    try:
        return service.create_personality_vacancy(personality_vacancy)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/personality_vacancies/{personality_vacancy_id}",
    response_model=Dict,
    tags=["PersonalityVacancy"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_personality_vacancy(
    personality_vacancy_id,
    personality_vacancy: Dict,
    service=Depends(get_personality_vacancy_service),
):
    """
    Route for update personality vacancy in basedata.

    :param personality_vacancy_id: ID by personality vacancy. [int]

    :param personality_vacancy: Model personality vacancy. [PersonalityVacancies]

    :return: response model dict.
    """
    try:
        return service.update_personality_vacancy(personality_vacancy_id, personality_vacancy)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/personality_vacancies/{personality_vacancy_id}",
    response_model=Dict,
    tags=["PersonalityVacancy"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_personality_vacancy(
    personality_vacancy_id, service=Depends(get_personality_vacancy_service)
):
    """
    Route for delete personality vacancy from basedata.

    :param personality_vacancy_id: ID by personality vacancy. [int]

    :return: response model dict.
    """
    try:
        return service.delete_personality_vacancy(personality_vacancy_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
