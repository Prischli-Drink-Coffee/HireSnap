"""embeddings router, now served by the embeddings slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/embedding_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/embeddings/application/`, and the object is resolved through
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
from service.composition.state import get_embedding_service
from service.configuration.logging import get_logger
from service.models import Embeddings
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.get("/embeddings/", response_model=list[Embeddings], tags=["Embedding"])
async def get_all_embeddings(service=Depends(get_embedding_service)):
    """
    Route for get all embeddings from basedata.

    :return: response model List[Embeddings].
    """
    try:
        return service.get_all_embeddings()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/embeddings/embedding_id/{embedding_id}", response_model=Embeddings, tags=["Embedding"]
)
async def get_embedding_by_id(embedding_id: int, service=Depends(get_embedding_service)):
    """
    Route for get embedding by EmbeddingID.

    :param embedding_id: ID by embedding. [int]

    :return: response model Embeddings.
    """
    try:
        return service.get_embedding_by_id(embedding_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/embeddings/",
    response_model=Embeddings,
    tags=["Embedding"],
    dependencies=[Depends(JWTBearer(access_level=0))],
)
async def create_embedding(embedding: Embeddings, service=Depends(get_embedding_service)):
    """
    Route for create embedding in basedata.

    :param embedding: Model embedding. [Embeddings]

    :return: response model Embeddings.
    """
    try:
        return service.create_embedding(embedding)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.put(
    "/embeddings/{embedding_id}",
    response_model=Dict,
    tags=["Embedding"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def update_embedding(embedding_id, embedding: Dict, service=Depends(get_embedding_service)):
    """
    Route for update embedding in basedata.

    :param embedding_id: ID by embedding. [int]

    :param embedding: Model embedding. [Embeddings]

    :return: response model dict.
    """
    try:
        return service.update_embedding(embedding_id, embedding)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/embeddings/{embedding_id}",
    response_model=Dict,
    tags=["Embedding"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def delete_embedding(embedding_id, service=Depends(get_embedding_service)):
    """
    Route for delete embedding from basedata.

    :param embedding_id: ID by embedding. [int]

    :return: response model dict.
    """
    try:
        return service.delete_embedding(embedding_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
