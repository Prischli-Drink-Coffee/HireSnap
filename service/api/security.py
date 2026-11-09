"""HTTP Bearer JWT dependency (moved from `src/utils/jwt_bearer.py`).

The scheme must be `Bearer` and the token must decode with the RS256 public key;
`access_level=1` additionally requires `sub == "admin"`.

Two decisions here are deliberate and are pinned by `tests/test_security_jwt.py`:

* There is NO scheme check in this class. Starlette's `HTTPBearer` validates the scheme
  inside `super().__call__`, before this method runs, so a branch of our own could never
  be reached: with `auto_error=True` a non-bearer request is answered
  `403 Invalid authentication credentials`, and with `auto_error=False` the superclass
  returns `None` and this class answers `403 Invalid authorization code.`. Those two
  messages belong to the base class; claiming them here would only look like a check.
* A token that DOES verify but whose subject fails `access_level=1` is rejected as
  `403 Have not access` - distinct from the `403 Invalid token or expired token.` that a
  token which did not verify produces. Both are denials, but only the second one means
  "your session is gone, sign in again"; a client cannot re-authenticate its way into an
  admin account, so the two cases must stay distinguishable.
"""

# NO "from __future__ import annotations" in this module, on purpose: FastAPI reads
# __call__'s signature when it builds a route that depends on JWTBearer, and with
# postponed annotations the parameter arrives as the string "Request", which
# pydantic then fails to resolve ("PydanticUndefinedAnnotation: name 'Request' is
# not defined") and the whole router import dies. Real annotation objects avoid a
# resolution step that has no reason to exist here.

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from service.configuration.logging import get_logger
from service.shared.tokens import decode_jwt

log = get_logger()


class JWTBearer(HTTPBearer):
    def __init__(self, access_level: int = 0, auto_error: bool = True):
        super().__init__(auto_error=auto_error)
        self.access_level = access_level

    async def __call__(self, request: Request):  # type: ignore[override]
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        if credentials:
            # No scheme check here: the base class already rejected every other scheme,
            # so this line is the first one a non-bearer request never reaches.
            if not self.verify_jwt(credentials.credentials):
                ex = HTTPException(status_code=403, detail="Invalid token or expired token.")
                log.exception(ex)
                raise ex
            return credentials.credentials
        ex = HTTPException(status_code=403, detail="Invalid authorization code.")
        log.exception(ex)
        raise ex

    def verify_jwt(self, jwtoken: str) -> bool:
        """True when the token verifies AND satisfies this dependency's access level.

        Returns False for anything that did not verify - a malformed token, a bad
        signature, an expired token, a missing key file, a missing `cryptography`
        package - so the caller owns the status and the client never receives a 500
        carrying a filesystem path. The exception is logged, not propagated.

        Raises `HTTPException(403, "Have not access")` when the token DID verify but
        the subject is not an admin on an `access_level=1` route. That check sits
        OUTSIDE the `except` below on purpose: raising it inside (the shape this code
        had until now) let `except Exception` swallow its own raise, so a signed-in
        non-admin was reported as having an expired token.
        """
        payload = None
        try:
            payload = decode_jwt(jwtoken)
        except Exception as ex:
            log.exception(ex)
        if not payload:
            return False
        if self.access_level == 1 and payload.get("sub") != "admin":
            ex = HTTPException(status_code=403, detail="Have not access")
            log.exception(ex)
            raise ex
        return True
