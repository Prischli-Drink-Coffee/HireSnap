"""files router, now served by the files slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/file_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/files/application/`, and the object is resolved through
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
from service.composition.state import get_file_service
from service.configuration.logging import get_logger
from service.models import Files
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get("/files/", response_model=list[Files], tags=["File"])
async def get_all_files(service=Depends(get_file_service)):
    """
    Route for get all files from basedata.

    :return: response model List[Files].
    """
    try:
        return service.get_all_files()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get("/files/file_id/{file_id}", response_model=Files, tags=["File"])
async def get_file_by_id(file_id: int, service=Depends(get_file_service)):
    """
    Route for get file by FileID.

    :param file_id: ID by file. [int]

    :return: response model Files.
    """
    try:
        return service.get_file_by_id(file_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/files/",
    response_model=Files,
    tags=["File"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_file(file: Files, service=Depends(get_file_service)):
    """
    Route for create file in basedata.

    :param file: Model file. [Files]

    :return: response model Files.
    """
    try:
        return service.create_file(file)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/files/{file_id}",
    response_model=Dict,
    tags=["File"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_file(file_id, file: Dict, service=Depends(get_file_service)):
    """
    Route for update file in basedata.

    :param file_id: ID by file. [int]

    :param file: Model file. [Files]

    :return: response model dict.
    """
    try:
        return service.update_file(file_id, file)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/files/{file_id}",
    response_model=Dict,
    tags=["File"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_file(file_id, service=Depends(get_file_service)):
    """
    Route for delete file from basedata.

    :param file_id: ID by file. [int]

    :return: response model dict.
    """
    try:
        return service.delete_file(file_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
