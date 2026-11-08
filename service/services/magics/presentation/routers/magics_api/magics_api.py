"""magics router, now served by the magics slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/magic_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/magics/application/`, and the object is resolved through
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
from service.composition.state import get_magic_service
from service.configuration.logging import get_logger
from service.models import Magics
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get("/magics/", response_model=list[Magics], tags=["Magic"])
async def get_all_magics(service=Depends(get_magic_service)):
    """
    Route for get all magics from basedata.

    :return: response model List[Magics].
    """
    try:
        return service.get_all_magics()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get("/magics/magic_id/{magic_id}", response_model=Magics, tags=["Magic"])
async def get_magic_by_id(magic_id: int, service=Depends(get_magic_service)):
    """
    Route for get magic by MagicID.

    :param magic_id: ID by magic. [int]

    :return: response model Magics.
    """
    try:
        return service.get_magic_by_id(magic_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/magics/",
    response_model=Magics,
    tags=["Magic"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_magic(magic: Magics, service=Depends(get_magic_service)):
    """
    Route for create magic in basedata.

    :param magic: Model magic. [Magics]

    :return: response model Magics.
    """
    try:
        return service.create_magic(magic)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/magics/{magic_id}",
    response_model=Dict,
    tags=["Magic"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_magic(magic_id, magic: Dict, service=Depends(get_magic_service)):
    """
    Route for update magic in basedata.

    :param magic_id: ID by magic. [int]

    :param magic: Model magic. [Magics]

    :return: response model dict.
    """
    try:
        return service.update_magic(magic_id, magic)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/magics/{magic_id}",
    response_model=Dict,
    tags=["Magic"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_magic(magic_id, service=Depends(get_magic_service)):
    """
    Route for delete magic from basedata.

    :param magic_id: ID by magic. [int]

    :return: response model dict.
    """
    try:
        return service.delete_magic(magic_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
