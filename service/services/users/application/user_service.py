"""The business rules of the users slice, and no framework.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/user_services.py`,
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

PERSISTENCE: `service/services/users/persistence/`, holding the same raw SQL as
before the move. The password is hashed on create and again when an update carries one;
uniqueness is the email address.
"""

from __future__ import annotations

from typing import Dict

from service.models import Users
from service.services.users import persistence
from service.shared.errors import NotFoundError
from service.shared.hashing import hash_password
from service.shared.validation import check_for_duplicates, check_if_exists


class UserService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, repository=None) -> None:
        self._repository = repository if repository is not None else persistence

    def get_all_users(self):
        users = self._repository.get_all_users()
        return [Users(**user) for user in users]

    def get_user_by_id(self, user_id: int):
        user = self._repository.get_user_by_id(user_id)
        if not user:
            raise NotFoundError("User not found")
        return Users(**user) if user else None

    def get_user_by_email(self, email: str):
        user = self._repository.get_user_by_email(email)
        if not user:
            raise NotFoundError("User not found")
        return Users(**user) if user else None

    def create_user(self, user: Users):
        check_if_exists(
            get_all=self.get_all_users,
            attr_name="Email",
            attr_value=user.Email,
            exception_detail="Email already exist",
        )
        user.Password = hash_password(user.Password)
        user_id = self._repository.create_user(user)
        return self.get_user_by_id(user_id)

    def update_user(self, user_id: int, user: Dict):
        check_for_duplicates(
            get_all=self.get_all_users,
            check_id=user_id,
            attr_name="Email",
            attr_value=user.get("email"),
            exception_detail="Email already exist",
        )
        if user.get("password"):
            user["password"] = hash_password(user.get("password"))
        self._repository.update_user(user_id, user)
        return {"message": "User updated successfully"}

    def delete_user(self, user_id: int):
        self.get_user_by_id(user_id)
        self._repository.delete_user(user_id)
        return {"message": "User deleted successfully"}
