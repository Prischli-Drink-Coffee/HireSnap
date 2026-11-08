"""personalities router, now served by the personalities slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/personality_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/personalities/application/`, and the object is resolved through
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
from service.composition.state import get_personality_service
from service.configuration.logging import get_logger
from service.models import Personalities
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get("/personalities/", response_model=list[Personalities], tags=["Personality"])
async def get_all_personalities(service=Depends(get_personality_service)):
    """
    Route for get all personalities from basedata.

    :return: response model List[Personalities].
    """
    try:
        return service.get_all_personalities()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/personalities/personality_id/{personality_id}",
    response_model=Personalities,
    tags=["Personality"],
)
async def get_personality_by_id(personality_id: int, service=Depends(get_personality_service)):
    """
    Route for get personality by PersonalityID.

    :param personality_id: ID by personality. [int]

    :return: response model Personalities.
    """
    try:
        return service.get_personality_by_id(personality_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/personalities/",
    response_model=Personalities,
    tags=["Personality"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_personality(personality: Personalities, service=Depends(get_personality_service)):
    """
    Route for create personality in basedata.

    :param personality: Model personality. [Personalities]

    :return: response model Personalities.
    """
    try:
        return service.create_personality(personality)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/personalities/{personality_id}",
    response_model=Dict,
    tags=["Personality"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_personality(
    personality_id, personality: Dict, service=Depends(get_personality_service)
):
    """
    Route for update personality in basedata.

    :param personality_id: ID by personality. [int]

    :param personality: Model personality. [Personalities]

    :return: response model dict.
    """
    try:
        return service.update_personality(personality_id, personality)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/personalities/{personality_id}",
    response_model=Dict,
    tags=["Personality"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_personality(personality_id, service=Depends(get_personality_service)):
    """
    Route for delete personality from basedata.

    :param personality_id: ID by personality. [int]

    :return: response model dict.
    """
    try:
        return service.delete_personality(personality_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
