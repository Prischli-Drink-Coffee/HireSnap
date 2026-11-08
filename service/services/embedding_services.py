from service.repositories import embedding_repository
from service.domain.models import Embeddings
from fastapi import HTTPException, status
from service.shared.validation import check_for_duplicates, check_if_exists


def get_all_embeddings():
    embeddings = embedding_repository.get_all_embeddings()
    return [Embeddings(**embedding) for embedding in embeddings]


def get_embedding_by_id(embedding_id: int):
    embedding = embedding_repository.get_embedding_by_id(embedding_id)
    if not embedding:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Embedding not found')
    return Embeddings(**embedding) if embedding else None


def create_embedding(embedding: Embeddings):
    check_if_exists(
        get_all=get_all_embeddings,
        attr_name="Url",
        attr_value=embedding.Url,
        exception_detail='Embedding already exist'
    )
    embedding_id = embedding_repository.create_embedding(embedding)
    return get_embedding_by_id(embedding_id)


def update_embedding(embedding_id: int, embedding: Embeddings):
    check_for_duplicates(
        get_all=get_all_embeddings,
        check_id=embedding_id,
        attr_name="Url",
        attr_value=embedding.Url,
        exception_detail='Embedding already exist'
    )
    embedding_repository.update_embedding(embedding_id, embedding)
    return {"message": "Embedding updated successfully"}


def delete_embedding(embedding_id: int):
    get_embedding_by_id(embedding_id)
    embedding_repository.delete_embedding(embedding_id)
    return {"message": "Embedding deleted successfully"}
