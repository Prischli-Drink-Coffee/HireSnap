"""personalities router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import Personalities
from service.services import personality_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/personalities/", response_model=list[Personalities], tags=["Personality"])
async def get_all_personalities():
    """
    Route for get all personalities from basedata.

    :return: response model List[Personalities].
    """
    try:
        return personality_services.get_all_personalities()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/personalities/personality_id/{personality_id}", response_model=Personalities,
        tags=["Personality"])
async def get_personality_by_id(personality_id: int):
    """
    Route for get personality by PersonalityID.

    :param personality_id: ID by personality. [int]

    :return: response model Personalities.
    """
    try:
        return personality_services.get_personality_by_id(personality_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/personalities/", response_model=Personalities, tags=["Personality"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_personality(personality: Personalities):
    """
    Route for create personality in basedata.

    :param personality: Model personality. [Personalities]

    :return: response model Personalities.
    """
    try:
        return personality_services.create_personality(personality)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/personalities/{personality_id}", response_model=Dict, tags=["Personality"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_personality(personality_id, personality: Dict):
    """
    Route for update personality in basedata.

    :param personality_id: ID by personality. [int]

    :param personality: Model personality. [Personalities]

    :return: response model dict.
    """
    try:
        return personality_services.update_personality(personality_id, personality)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/personalities/{personality_id}", response_model=Dict, tags=["Personality"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_personality(personality_id):
    """
    Route for delete personality from basedata.

    :param personality_id: ID by personality. [int]

    :return: response model dict.
    """
    try:
        return personality_services.delete_personality(personality_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
