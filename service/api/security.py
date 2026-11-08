"""HTTP Bearer JWT dependency (moved from `src/utils/jwt_bearer.py`).

The behaviour is unchanged: scheme must be `Bearer`, the token must decode with
the RS256 public key, and `access_level=1` additionally requires
`sub == "admin"`. Only the import of the decoder moved, from
`src.utils.token` to `service.shared.tokens`.
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
            if not credentials.scheme == "Bearer":
                ex = HTTPException(status_code=403, detail="Invalid authentication scheme.")
                log.exception(ex)
                raise ex
            if not self.verify_jwt(credentials.credentials):
                ex = HTTPException(status_code=403, detail="Invalid token or expired token.")
                log.exception(ex)
                raise ex
            return credentials.credentials
        ex = HTTPException(status_code=403, detail="Invalid authorization code.")
        log.exception(ex)
        raise ex

    def verify_jwt(self, jwtoken: str) -> bool:
        isTokenValid: bool = False
        try:
            payload = decode_jwt(jwtoken)
            role = payload.get("sub")
            if self.access_level == 1 and role != "admin":
                ex = HTTPException(status_code=401, detail="Have not access")
                log.exception(ex)
                raise ex
        except Exception as ex:
            log.exception(ex)
            payload = None
        if payload:
            isTokenValid = True
        return isTokenValid
