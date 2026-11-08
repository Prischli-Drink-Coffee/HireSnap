"""personality_candidates router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import PersonalityCandidates
from service.services import personality_candidate_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/personality_candidates/", response_model=list[PersonalityCandidates], tags=["PersonalityCandidate"])
async def get_all_personality_candidates():
    """
    Route for get all personality candidates from basedata.

    :return: response model List[PersonalityCandidates].
    """
    try:
        return personality_candidate_services.get_all_personality_candidates()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/personality_candidates/personality_candidate_id/{personality_candidate_id}", response_model=PersonalityCandidates,
                tags=["PersonalityCandidate"])
async def get_personality_candidate_by_id(personality_candidate_id: int):
    """
    Route for get personality candidate by PersonalityCandidateID.

    :param personality_candidate_id: ID by personality candidate. [int]

    :return: response model PersonalityCandidates.
    """
    try:
        return personality_candidate_services.get_personality_candidate_by_id(personality_candidate_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/personality_candidates/", response_model=PersonalityCandidates, tags=["PersonalityCandidate"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_personality_candidate(personality_candidate: PersonalityCandidates):
    """
    Route for create personality candidate in basedata.

    :param personality_candidate: Model personality candidate. [PersonalityCandidates]

    :return: response model PersonalityCandidates.
    """
    try:
        return personality_candidate_services.create_personality_candidate(personality_candidate)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/personality_candidates/{personality_candidate_id}", response_model=Dict, tags=["PersonalityCandidate"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_personality_candidate(personality_candidate_id, personality_candidate: Dict):
    """
    Route for update personality candidate in basedata.

    :param personality_candidate_id: ID by personality candidate. [int]

    :param personality_candidate: Model personality candidate. [PersonalityCandidates]

    :return: response model dict.
    """
    try:
        return personality_candidate_services.update_personality_candidate(personality_candidate_id, personality_candidate)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/personality_candidates/{personality_candidate_id}", response_model=Dict, tags=["PersonalityCandidate"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_personality_candidate(personality_candidate_id):
    """
    Route for delete personality candidate from basedata.

    :param personality_candidate_id: ID by personality candidate. [int]

    :return: response model dict.
    """
    try:
        return personality_candidate_services.delete_personality_candidate(personality_candidate_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
