"""The rules of the auth slice: who the caller is, and what a token proves.

WHAT MOVED AND WHY

These rules used to be module-level functions in `service/services/auth_services.py`,
which imported `service.services.user_services` directly and raised
`fastapi.HTTPException`. They are methods of one class now:

  * the instance is what `service/composition/state.py` hands out, so a test can pass a
    fake user lookup instead of monkeypatching a module global;
  * this module imports no web framework (backend/tests/test_service_boundary.py fails
    the build otherwise), so the same rule can run outside a request;
  * a failure is one of the four `ServiceError` classes, and exactly one handler in
    `service/api/app.py` turns it into a status code and a `{"detail": ...}` body.

WHY THE DETAIL STRINGS ARE UNCHANGED: "Invalid password", "Invalid type user", "User
not found" and "Invalid token error" are what the sign-in screen prints and what
`backend/tests/snapshots/critical_endpoint_openapi.json` was generated against.

WHAT IS DELIBERATELY PRESERVED AS-IS: `validate_reg_user` swallows the duplicate-email
exception, so registration of an existing address carries on to the INSERT and fails
there. That is the behaviour every deployment has today; fixing it is a product
decision about which error a duplicate signup should show, not a refactor.

The dependency-shaped helper that used `fastapi.Depends` (`UserGetFromToken`) moved to
`service/api/security.py`, where the rest of the request-level authentication lives -
the application layer may not import a framework even for a default argument.
"""

from __future__ import annotations

from typing import Dict

from service.models import Auth, TokenInfo, Users
from service.shared.errors import UnauthorizedError
from service.shared.hashing import validate_password
from service.shared.tokens import (
    create_access_token,
    create_refresh_token,
    decode_jwt,
    validate_token_type,
)


class AuthService:
    """One instance per process; it holds no state of its own."""

    def __init__(self, user=None) -> None:
        self._user_override = user

    @property
    def _user(self):
        """The users slice, resolved on first use so no import cycle is created."""
        if self._user_override is None:
            from service.composition import state

            self._user_override = state.get_user_service()
        return self._user_override

    def validate_auth_user(self, auth: Auth):
        """The password half of sign-in. A wrong password is 401, not 404."""
        user = self._user.get_user_by_email(auth.Email)
        if not validate_password(
            password=auth.Password, hashed_password=user.Password.encode("utf-8")
        ):
            raise UnauthorizedError("Invalid password")
        return user

    def validate_reg_user(self, user: Users):
        """Reject a registration with no user type; leave duplicates to the INSERT."""
        if not user.Type:
            raise UnauthorizedError("Invalid type user")
        try:
            self._user.get_user_by_email(user.Email)
        except Exception:
            pass
        return user

    def get_user_by_payload(self, payload: Dict):
        """Turn a decoded token payload into the row it names."""
        user_id = payload.get("user_id")
        user = self._user.get_user_by_id(user_id)
        if not user:
            raise UnauthorizedError("User not found")
        return user

    def signup(self, user: Users):
        user = self.validate_reg_user(user)
        current_user = self._user.create_user(user)
        return TokenInfo(
            access_token=create_access_token(current_user),
            refresh_token=create_refresh_token(current_user),
        )

    def signin(self, auth: Auth):
        current_user = self.validate_auth_user(auth)
        return TokenInfo(
            access_token=create_access_token(current_user),
            refresh_token=create_refresh_token(current_user),
        )

    def get_current_token_payload(self, token: str) -> Dict:
        """Decode an access token, or fail with the message the client already prints."""
        payload = decode_jwt(token=token)
        if not payload:
            raise UnauthorizedError("Invalid token error")
        return payload

    def user_from_token(self, token: str, token_type: str):
        """The whole `UserGetFromToken` path, without the dependency wrapper."""
        payload = self.get_current_token_payload(token)
        validate_token_type(payload, token_type)
        return self.get_user_by_payload(payload)

    def auth_refresh_jwt(self, user: Users):
        return TokenInfo(access_token=create_access_token(user))
