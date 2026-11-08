"""info_candidates router, now served by the candidates slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/info_candidate_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/candidates/application/`, and the object is resolved through
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
from service.composition.state import get_info_candidate_service
from service.configuration.logging import get_logger
from service.models import InfoCandidates
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get("/info_candidates/", response_model=list[InfoCandidates], tags=["InfoCandidate"])
async def get_all_info_candidates(service=Depends(get_info_candidate_service)):
    """
    Route for get all info candidates from basedata.

    :return: response model List[InfoCandidates].
    """
    try:
        return service.get_all_info_candidates()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/info_candidates/info_candidate_id/{info_candidate_id}",
    response_model=InfoCandidates,
    tags=["InfoCandidate"],
)
async def get_info_candidate_by_id(
    info_candidate_id: int, service=Depends(get_info_candidate_service)
):
    """
    Route for get info candidate by InfoCandidateID.

    :param info_candidate_id: ID by info candidate. [int]

    :return: response model InfoCandidates.
    """
    try:
        return service.get_info_candidate_by_id(info_candidate_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/info_candidates/",
    response_model=InfoCandidates,
    tags=["InfoCandidate"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_info_candidate(
    info_candidate: InfoCandidates, service=Depends(get_info_candidate_service)
):
    """
    Route for create info candidate in basedata.

    :param info_candidate: Model info candidate. [InfoCandidates]

    :return: response model InfoCandidates.
    """
    try:
        return service.create_info_candidate(info_candidate)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/info_candidates/{info_candidate_id}",
    response_model=Dict,
    tags=["InfoCandidate"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def update_info_candidate(
    info_candidate_id, info_candidate: Dict, service=Depends(get_info_candidate_service)
):
    """
    Route for update info candidate in basedata.

    :param info_candidate_id: ID by info candidate. [int]

    :param info_candidate: Model info candidate. [InfoCandidates]

    :return: response model dict.
    """
    try:
        return service.update_info_candidate(info_candidate_id, info_candidate)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/info_candidates/{info_candidate_id}",
    response_model=Dict,
    tags=["InfoCandidate"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_info_candidate(info_candidate_id, service=Depends(get_info_candidate_service)):
    """
    Route for delete info candidate from basedata.

    :param info_candidate_id: ID by info candidate. [int]

    :return: response model dict.
    """
    try:
        return service.delete_info_candidate(info_candidate_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
