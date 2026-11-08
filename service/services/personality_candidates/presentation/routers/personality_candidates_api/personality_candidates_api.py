"""personality_candidates router, now served by the personality_candidates slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/personality_candidate_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/personality_candidates/application/`, and the object is resolved through
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
from service.composition.state import get_personality_candidate_service
from service.configuration.logging import get_logger
from service.models import PersonalityCandidates
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get(
    "/personality_candidates/",
    response_model=list[PersonalityCandidates],
    tags=["PersonalityCandidate"],
)
async def get_all_personality_candidates(service=Depends(get_personality_candidate_service)):
    """
    Route for get all personality candidates from basedata.

    :return: response model List[PersonalityCandidates].
    """
    try:
        return service.get_all_personality_candidates()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/personality_candidates/personality_candidate_id/{personality_candidate_id}",
    response_model=PersonalityCandidates,
    tags=["PersonalityCandidate"],
)
async def get_personality_candidate_by_id(
    personality_candidate_id: int, service=Depends(get_personality_candidate_service)
):
    """
    Route for get personality candidate by PersonalityCandidateID.

    :param personality_candidate_id: ID by personality candidate. [int]

    :return: response model PersonalityCandidates.
    """
    try:
        return service.get_personality_candidate_by_id(personality_candidate_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/personality_candidates/",
    response_model=PersonalityCandidates,
    tags=["PersonalityCandidate"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_personality_candidate(
    personality_candidate: PersonalityCandidates, service=Depends(get_personality_candidate_service)
):
    """
    Route for create personality candidate in basedata.

    :param personality_candidate: Model personality candidate. [PersonalityCandidates]

    :return: response model PersonalityCandidates.
    """
    try:
        return service.create_personality_candidate(personality_candidate)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/personality_candidates/{personality_candidate_id}",
    response_model=Dict,
    tags=["PersonalityCandidate"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_personality_candidate(
    personality_candidate_id,
    personality_candidate: Dict,
    service=Depends(get_personality_candidate_service),
):
    """
    Route for update personality candidate in basedata.

    :param personality_candidate_id: ID by personality candidate. [int]

    :param personality_candidate: Model personality candidate. [PersonalityCandidates]

    :return: response model dict.
    """
    try:
        return service.update_personality_candidate(personality_candidate_id, personality_candidate)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/personality_candidates/{personality_candidate_id}",
    response_model=Dict,
    tags=["PersonalityCandidate"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_personality_candidate(
    personality_candidate_id, service=Depends(get_personality_candidate_service)
):
    """
    Route for delete personality candidate from basedata.

    :param personality_candidate_id: ID by personality candidate. [int]

    :return: response model dict.
    """
    try:
        return service.delete_personality_candidate(personality_candidate_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
