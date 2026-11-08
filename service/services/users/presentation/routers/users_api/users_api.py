"""users router, now served by the users slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/user_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/users/application/`, and the object is resolved through
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
from service.composition.state import get_user_service
from service.configuration.logging import get_logger
from service.models import Users
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get("/users/", response_model=list[Users], tags=["User"])
async def get_all_users(service=Depends(get_user_service)):
    """
    Route for get all users from basedata.

    :return: response model List[Users].
    """
    try:
        return service.get_all_users()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get("/users/user_id/{user_id}", response_model=Users, tags=["User"])
async def get_user_by_id(user_id: int, service=Depends(get_user_service)):
    """
    Route for get user by UserID.

    :param user_id: ID by user. [int]

    :return: response model Users.
    """
    try:
        return service.get_user_by_id(user_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get("/users/email/{email}", response_model=Users, tags=["User"])
async def get_user_by_email(email: str, service=Depends(get_user_service)):
    """
    Route for get user by user email.

    :param email: Email by user. [int]

    :return: response model Users.
    """
    try:
        return service.get_user_by_email(email)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/users/",
    response_model=Users,
    tags=["User"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_user(user: Users, service=Depends(get_user_service)):
    """
    Route for create user in basedata.

    :param user: Model user. [Users]

    :return: response model Users.
    """
    try:
        return service.create_user(user)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/users/{user_id}",
    response_model=Dict,
    tags=["User"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_user(user_id, user: Dict, service=Depends(get_user_service)):
    """
    Route for update user in basedata.

    :param user_id: ID by user. [int]

    :param user: Model user. [Users]

    :return: response model dict.
    """
    try:
        return service.update_user(user_id, user)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/users/{user_id}",
    response_model=Dict,
    tags=["User"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_user(user_id, service=Depends(get_user_service)):
    """
    Route for delete user from basedata.

    :param user_id: ID by user. [int]

    :return: response model dict.
    """
    try:
        return service.delete_user(user_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
