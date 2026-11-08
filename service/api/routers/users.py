"""users router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import Users
from service.services import user_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/users/", response_model=list[Users], tags=["User"])
async def get_all_users():
    """
    Route for get all users from basedata.

    :return: response model List[Users].
    """
    try:
        return user_services.get_all_users()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/users/user_id/{user_id}", response_model=Users, tags=["User"])
async def get_user_by_id(user_id: int):
    """
    Route for get user by UserID.

    :param user_id: ID by user. [int]

    :return: response model Users.
    """
    try:
        return user_services.get_user_by_id(user_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/users/email/{email}", response_model=Users, tags=["User"])
async def get_user_by_email(email: str):
    """
    Route for get user by user email.

    :param email: Email by user. [int]

    :return: response model Users.
    """
    try:
        return user_services.get_user_by_email(email)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/users/", response_model=Users, tags=["User"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_user(user: Users):
    """
    Route for create user in basedata.

    :param user: Model user. [Users]

    :return: response model Users.
    """
    try:
        return user_services.create_user(user)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/users/{user_id}", response_model=Dict, tags=["User"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_user(user_id, user: Dict):
    """
    Route for update user in basedata.

    :param user_id: ID by user. [int]

    :param user: Model user. [Users]

    :return: response model dict.
    """
    try:
        return user_services.update_user(user_id, user)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/users/{user_id}", response_model=Dict, tags=["User"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_user(user_id):
    """
    Route for delete user from basedata.

    :param user_id: ID by user. [int]

    :return: response model dict.
    """
    try:
        return user_services.delete_user(user_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
