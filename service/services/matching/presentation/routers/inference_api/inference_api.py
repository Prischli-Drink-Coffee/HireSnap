"""inference router, now served by the matching slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/matching.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/matching/application/`, and the object is resolved through
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

from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, Form, UploadFile

# The service behind these four routes is the `matching` slice. The handlers below once
# said `main_services.`, a name this module never imported - every one of these routes
# raised NameError the moment it was called. ruff's F821 found it, not a test, which is
# why the api-prefix contract alone was not enough.
from service.api.security import JWTBearer
from service.composition.state import get_matching_service
from service.configuration.logging import get_logger
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.post(
    "/upload_video/inference/{user_id}",
    response_model=Dict,
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def upload_video(
    user_id: int,
    file: UploadFile,
    name: str = None,
    phone: str = None,
    date_birth: datetime = None,
    gender: str = None,
    service=Depends(get_matching_service),
):
    """
    Загрузка видео и получение профиля кандидата
    """
    try:
        return await service.upload_video(file, user_id, name, phone, date_birth, gender)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/upload_vacancy/inference/employer",
    response_model=Dict,
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def upload_vacancy(
    name: str = Form(...),
    description: str = Form(...),
    salary: str = Form(None),
    skill: str = Form(None),
    service=Depends(get_matching_service),
):
    """
    Добавление вакансии нанимателем
    """
    try:
        return service.upload_vacancy(name, description, salary, skill)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/get_all_vacancies/inference/candidate/{user_id}",
    response_model=list[Dict],
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def get_all_vacancies(user_id: int, service=Depends(get_matching_service)):
    """
    Получение всех рекомендованных пользователю вакансий
    """
    try:
        return service.get_all_vacancies(user_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/get_all_candidates/inference/vacancy/{vacancy_id}",
    response_model=list[Dict],
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def get_all_candidataes(vacancy_id: int, service=Depends(get_matching_service)):
    """
    Получение всех рекомендованных нанимателю кандидатов
    """
    try:
        # This route lists CANDIDATES for a vacancy, so calling the
        # vacancy-for-user function was a copy-paste bug: it looked up
        # `vacancy_id` as a user id. `get_all_candidates` exists for exactly this
        # route (service/services/service.py) and was never called.
        return service.get_all_candidates(vacancy_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
