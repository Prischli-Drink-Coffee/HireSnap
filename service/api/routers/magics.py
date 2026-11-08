"""magics router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import Magics
from service.services import magic_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/magics/", response_model=list[Magics], tags=["Magic"])
async def get_all_magics():
    """
    Route for get all magics from basedata.

    :return: response model List[Magics].
    """
    try:
        return magic_services.get_all_magics()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/magics/magic_id/{magic_id}", response_model=Magics, tags=["Magic"])
async def get_magic_by_id(magic_id: int):
    """
    Route for get magic by MagicID.

    :param magic_id: ID by magic. [int]

    :return: response model Magics.
    """
    try:
        return magic_services.get_magic_by_id(magic_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/magics/", response_model=Magics, tags=["Magic"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_magic(magic: Magics):
    """
    Route for create magic in basedata.

    :param magic: Model magic. [Magics]

    :return: response model Magics.
    """
    try:
        return magic_services.create_magic(magic)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/magics/{magic_id}", response_model=Dict, tags=["Magic"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_magic(magic_id, magic: Dict):
    """
    Route for update magic in basedata.

    :param magic_id: ID by magic. [int]

    :param magic: Model magic. [Magics]

    :return: response model dict.
    """
    try:
        return magic_services.update_magic(magic_id, magic)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/magics/{magic_id}", response_model=Dict, tags=["Magic"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_magic(magic_id):
    """
    Route for delete magic from basedata.

    :param magic_id: ID by magic. [int]

    :return: response model dict.
    """
    try:
        return magic_services.delete_magic(magic_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
