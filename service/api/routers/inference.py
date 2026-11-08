"""inference router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, Form, HTTPException, UploadFile
# The service module behind these four routes is `matching` (it was
# `src/services/main_services.py`; the refactor renamed it because "main" named a
# file, not a responsibility). The handlers below still said `main_services.`, a
# name this module never imports - every one of these routes raised NameError the
# moment it was called. ruff's F821 found it, not a test, which is why the
# api-prefix contract alone was not enough.
from service.services import matching
from typing import Dict
from datetime import datetime
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.post("/upload_video/inference/{user_id}", response_model=Dict, tags=["Main"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def upload_video(user_id: int,
                       file: UploadFile,
                       name: str = None,
                       phone: str = None,
                       date_birth: datetime = None,
                       gender: str = None):
    """
    Загрузка видео и получение профиля кандидата
    """
    try:
        return await matching.upload_video(file,
                                                user_id,
                                                name,
                                                phone,
                                                date_birth,
                                                gender)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/upload_vacancy/inference/employer", response_model=Dict, tags=["Main"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def upload_vacancy(name: str = Form(...),
                         description: str = Form(...),
                         salary: str = Form(None),
                         skill: str = Form(None)):
    """
    Добавление вакансии нанимателем
    """
    try:
        return matching.upload_vacancy(name,
                                            description,
                                            salary,
                                            skill)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/get_all_vacancies/inference/candidate/{user_id}", response_model=list[Dict], tags=["Main"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def get_all_vacancies(user_id: int):
    """
    Получение всех рекомендованных пользователю вакансий
    """
    try:
        return matching.get_all_vacancies(user_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/get_all_candidates/inference/vacancy/{vacancy_id}", response_model=list[Dict], tags=["Main"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def get_all_candidataes(vacancy_id: int):
    """
    Получение всех рекомендованных нанимателю кандидатов
    """
    try:
        # This route lists CANDIDATES for a vacancy, so calling the
        # vacancy-for-user function was a copy-paste bug: it looked up
        # `vacancy_id` as a user id. `get_all_candidates` exists for exactly this
        # route (service/services/matching.py) and was never called.
        return matching.get_all_candidates(vacancy_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
