"""The business rules of the files slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/file_services.py`,
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

PERSISTENCE: `service/services/files/persistence/`, holding the same raw SQL as
before the move. A stored object is described by its URL, and the URL is the uniqueness key.
"""

from __future__ import annotations

from service.models import Files
from service.services.files import persistence
from service.shared.errors import NotFoundError
from service.shared.validation import check_for_duplicates, check_if_exists


class FileService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None) -> None:
        self._repository = repository if repository is not None else persistence

    def get_all_files(self):
        files = self._repository.get_all_files()
        return [Files(**file) for file in files]

    def get_file_by_id(self, file_id: int):
        file = self._repository.get_file_by_id(file_id)
        if not file:
            raise NotFoundError("File not found")
        return Files(**file) if file else None

    def create_file(self, file: Files):
        check_if_exists(
            get_all=self.get_all_files,
            attr_name="Url",
            attr_value=file.Url,
            exception_detail="File already exist",
        )
        file_id = self._repository.create_file(file)
        return self.get_file_by_id(file_id)

    def update_file(self, file_id: int, file: Files):
        check_for_duplicates(
            get_all=self.get_all_files,
            check_id=file_id,
            attr_name="Url",
            attr_value=file.Url,
            exception_detail="File already exist",
        )
        self._repository.update_file(file_id, file)
        return {"message": "File updated successfully"}

    def delete_file(self, file_id: int):
        self.get_file_by_id(file_id)
        self._repository.delete_file(file_id)
        return {"message": "File deleted successfully"}
