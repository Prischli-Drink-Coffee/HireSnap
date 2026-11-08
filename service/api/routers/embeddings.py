"""embeddings router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Depends, HTTPException
from service.domain.models import Embeddings
from service.services import embedding_services
from typing import Dict
from service.api.security import JWTBearer
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.get("/embeddings/", response_model=list[Embeddings], tags=["Embedding"])
async def get_all_embeddings():
    """
    Route for get all embeddings from basedata.

    :return: response model List[Embeddings].
    """
    try:
        return embedding_services.get_all_embeddings()
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.get("/embeddings/embedding_id/{embedding_id}", response_model=Embeddings, tags=["Embedding"])
async def get_embedding_by_id(embedding_id: int):
    """
    Route for get embedding by EmbeddingID.

    :param embedding_id: ID by embedding. [int]

    :return: response model Embeddings.
    """
    try:
        return embedding_services.get_embedding_by_id(embedding_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.post("/embeddings/", response_model=Embeddings, tags=["Embedding"],
                 dependencies=[Depends(JWTBearer(access_level=0))])
async def create_embedding(embedding: Embeddings):
    """
    Route for create embedding in basedata.

    :param embedding: Model embedding. [Embeddings]

    :return: response model Embeddings.
    """
    try:
        return embedding_services.create_embedding(embedding)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.put("/embeddings/{embedding_id}", response_model=Dict, tags=["Embedding"],
                dependencies=[Depends(JWTBearer(access_level=1))])
async def update_embedding(embedding_id, embedding: Dict):
    """
    Route for update embedding in basedata.

    :param embedding_id: ID by embedding. [int]

    :param embedding: Model embedding. [Embeddings]

    :return: response model dict.
    """
    try:
        return embedding_services.update_embedding(embedding_id, embedding)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex


@router.delete("/embeddings/{embedding_id}", response_model=Dict, tags=["Embedding"],
                   dependencies=[Depends(JWTBearer(access_level=1))])
async def delete_embedding(embedding_id):
    """
    Route for delete embedding from basedata.

    :param embedding_id: ID by embedding. [int]

    :return: response model dict.
    """
    try:
        return embedding_services.delete_embedding(embedding_id)
    except HTTPException as ex:
        log.exception(f"Error", exc_info=ex)
        raise ex
