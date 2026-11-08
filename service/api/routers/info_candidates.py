"""info_candidates router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import InfoCandidates
from service.services import info_candidate_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/info_candidates/", response_model=list[InfoCandidates], tags=["InfoCandidate"])
async def get_all_info_candidates():
    """
    Route for get all info candidates from basedata.

    :return: response model List[InfoCandidates].
    """
    try:
        return info_candidate_services.get_all_info_candidates()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/info_candidates/info_candidate_id/{info_candidate_id}", response_model=InfoCandidates,
        tags=["InfoCandidate"])
async def get_info_candidate_by_id(info_candidate_id: int):
    """
    Route for get info candidate by InfoCandidateID.

    :param info_candidate_id: ID by info candidate. [int]

    :return: response model InfoCandidates.
    """
    try:
        return info_candidate_services.get_info_candidate_by_id(info_candidate_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/info_candidates/", response_model=InfoCandidates, tags=["InfoCandidate"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_info_candidate(info_candidate: InfoCandidates):
    """
    Route for create info candidate in basedata.

    :param info_candidate: Model info candidate. [InfoCandidates]

    :return: response model InfoCandidates.
    """
    try:
        return info_candidate_services.create_info_candidate(info_candidate)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/info_candidates/{info_candidate_id}", response_model=Dict, tags=["InfoCandidate"],
                dependencies=[Depends(JWTBearer(access_level=0))])
async def update_info_candidate(info_candidate_id, info_candidate: Dict):
    """
    Route for update info candidate in basedata.

    :param info_candidate_id: ID by info candidate. [int]

    :param info_candidate: Model info candidate. [InfoCandidates]

    :return: response model dict.
    """
    try:
        return info_candidate_services.update_info_candidate(info_candidate_id, info_candidate)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/info_candidates/{info_candidate_id}", response_model=Dict, tags=["InfoCandidate"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_info_candidate(info_candidate_id):
    """
    Route for delete info candidate from basedata.

    :param info_candidate_id: ID by info candidate. [int]

    :return: response model dict.
    """
    try:
        return info_candidate_services.delete_info_candidate(info_candidate_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
