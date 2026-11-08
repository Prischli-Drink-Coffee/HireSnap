"""vacancies router, now served by the vacancies slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/vacancy_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/vacancies/application/`, and the object is resolved through
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

from service.composition.state import get_vacancy_service
from service.configuration.logging import get_logger
from service.models import Vacancies
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get("/vacancies/", response_model=list[Vacancies], tags=["Vacancy"])
async def get_all_vacancies(service=Depends(get_vacancy_service)):
    """
    Route for get all vacancies from basedata.

    :return: response model List[Vacancies].
    """
    try:
        return service.get_all_vacancies()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get("/vacancies/vacancy_id/{vacancy_id}", response_model=Vacancies, tags=["Vacancy"])
async def get_vacancy_by_id(vacancy_id: int, service=Depends(get_vacancy_service)):
    """
    Route for get vacancy by id from basedata.

    :param vacancy_id: id of vacancy.
    :return: response model Vacancy.
    """
    try:
        return service.get_vacancy_by_id(vacancy_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post("/vacancies/", response_model=Vacancies, tags=["Vacancy"])
async def create_vacancy(vacancy: Vacancies, service=Depends(get_vacancy_service)):
    """
    Route for create vacancy in basedata.

    :param vacancy: Model vacancy. [Vacancy]

    :return: response model Vacancy.
    """
    try:
        return service.create_vacancy(vacancy)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put("/vacancies/{vacancy_id}", response_model=Dict, tags=["Vacancy"])
async def update_vacancy(vacancy_id: int, vacancy: Dict, service=Depends(get_vacancy_service)):
    """
    Route for update vacancy in basedata.

    :param vacancy_id: id of vacancy.
    :param vacancy: Model vacancy. [Vacancy]

    :return: response model Vacancy.
    """
    try:
        return service.update_vacancy(vacancy_id, vacancy)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete("/vacancies/{vacancy_id}", response_model=Dict, tags=["Vacancy"])
async def delete_vacancy(vacancy_id: int, service=Depends(get_vacancy_service)):
    """
    Route for delete vacancy in basedata.

    :param vacancy_id: id of vacancy.

    :return: response model Vacancy.
    """
    try:
        return service.delete_vacancy(vacancy_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
