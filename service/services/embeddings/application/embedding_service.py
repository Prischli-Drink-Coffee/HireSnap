"""The business rules of the embeddings slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/embedding_services.py`,
importing the repository module directly and raising `fastapi.HTTPException`. They are
methods of one class now, because three things follow and none of them did before:

  * the object is what `service/composition/state.py` hands out, so a test can pass
    a fake collaborator instead of monkeypatching a module global;
  * nothing here imports a web framework, so the same rule runs from a celery task,
    where there is no request to raise an HTTP error against;
  * a failure is a `ServiceError` from `service/shared/errors.py`, and exactly one
    handler in `service/api/app.py` turns it into a status code and a body.

WHY THE SIGNATURES AND THE DETAIL STRINGS ARE UNCHANGED: the 404s, their messages and
the order of the checks are the API contract - the frontend prints the detail string,
and the generated OpenAPI document is pinned by a snapshot test. So this is a rename
plus a different exception class, and nothing else.

PERSISTENCE: `service/services/embeddings/persistence/`, holding the same raw SQL as
before the move. An embedding row is a path to a .npy file; the path is the uniqueness key.
"""

from __future__ import annotations

from service.models import Embeddings
from service.services.embeddings import persistence
from service.shared.errors import NotFoundError
from service.shared.validation import check_for_duplicates, check_if_exists


class EmbeddingService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None) -> None:
        self._repository = repository if repository is not None else persistence

    def get_all_embeddings(self):
        embeddings = self._repository.get_all_embeddings()
        return [Embeddings(**embedding) for embedding in embeddings]

    def get_embedding_by_id(self, embedding_id: int):
        embedding = self._repository.get_embedding_by_id(embedding_id)
        if not embedding:
            raise NotFoundError("Embedding not found")
        return Embeddings(**embedding) if embedding else None

    def create_embedding(self, embedding: Embeddings):
        check_if_exists(
            get_all=self.get_all_embeddings,
            attr_name="Url",
            attr_value=embedding.Url,
            exception_detail="Embedding already exist",
        )
        embedding_id = self._repository.create_embedding(embedding)
        return self.get_embedding_by_id(embedding_id)

    def update_embedding(self, embedding_id: int, embedding: Embeddings):
        check_for_duplicates(
            get_all=self.get_all_embeddings,
            check_id=embedding_id,
            attr_name="Url",
            attr_value=embedding.Url,
            exception_detail="Embedding already exist",
        )
        self._repository.update_embedding(embedding_id, embedding)
        return {"message": "Embedding updated successfully"}

    def delete_embedding(self, embedding_id: int):
        self.get_embedding_by_id(embedding_id)
        self._repository.delete_embedding(embedding_id)
        return {"message": "Embedding deleted successfully"}
