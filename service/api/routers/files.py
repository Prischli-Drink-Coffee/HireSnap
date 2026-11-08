"""files router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, File, HTTPException
from service.domain.models import Files
from service.services import file_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/files/", response_model=list[Files], tags=["File"])
async def get_all_files():
    """
    Route for get all files from basedata.

    :return: response model List[Files].
    """
    try:
        return file_services.get_all_files()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/files/file_id/{file_id}", response_model=Files, tags=["File"])
async def get_file_by_id(file_id: int):
    """
    Route for get file by FileID.

    :param file_id: ID by file. [int]

    :return: response model Files.
    """
    try:
        return file_services.get_file_by_id(file_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/files/", response_model=Files, tags=["File"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_file(file: Files):
    """
    Route for create file in basedata.

    :param file: Model file. [Files]

    :return: response model Files.
    """
    try:
        return file_services.create_file(file)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/files/{file_id}", response_model=Dict, tags=["File"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_file(file_id, file: Dict):
    """
    Route for update file in basedata.

    :param file_id: ID by file. [int]

    :param file: Model file. [Files]

    :return: response model dict.
    """
    try:
        return file_services.update_file(file_id, file)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/files/{file_id}", response_model=Dict, tags=["File"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_file(file_id):
    """
    Route for delete file from basedata.

    :param file_id: ID by file. [int]

    :return: response model dict.
    """
    try:
        return file_services.delete_file(file_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
