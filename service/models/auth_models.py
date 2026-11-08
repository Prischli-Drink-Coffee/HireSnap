"""Session models and the JWT settings they carry.

Split out of `service/domain/models.py` with no field change. What moved here and why:
`AuthJWT`, `Settings`, `TokenInfo` and `Auth` describe a session, not a table row, so
they do not belong next to the row models; the RS256 key resolution and the token
lifetime constants moved with them because `service/shared/tokens.py` is their only
reader.

THE ONE BEHAVIOURAL CHANGE is older than this split and is repeated here because this
is where a reader of the JWT settings looks. The lifetimes used to be read as
`int(env.__getattr__("ACCESS_TOKEN_EXPIRE_MINUTES"))` at import time, and
`Env.__getattr__` RAISES for a missing key, so importing any model - and therefore the
whole application - required the JWT expiry variables to be present. They are now read
with the documented defaults of the env manifest (15 minutes / 7 days,
`docker/.env.example` section 3), and an explicitly set variable still wins. A
deployment that sets nothing behaves like the manifest default instead of dying at
import; one that sets a value is unaffected.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, Optional

from pydantic import BaseModel, Field, PrivateAttr, StrictStr
from pydantic_settings import BaseSettings

from service.configuration.paths import CERTS_DIR

DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 15
DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS = 7


def _key_path(variable: str, filename: str) -> "os.PathLike":
    """Resolve one member of the RS256 key pair.

    Order: the explicit variable, then the packaged default under CERTS_DIR. The
    path is returned UNRESOLVED on purpose - the file does not have to exist while
    the app is being imported (only when a token is signed or verified), and
    resolving it eagerly would turn a missing key into an import error again.
    """
    raw = os.environ.get(variable)
    if raw and raw.strip():
        return Path(raw.strip())
    return CERTS_DIR / filename


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


ACCESS_TOKEN_EXPIRE_MINUTES = _int_env(
    "ACCESS_TOKEN_EXPIRE_MINUTES", DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
)
REFRESH_TOKEN_EXPIRE_DAYS = _int_env("REFRESH_TOKEN_EXPIRE_DAYS", DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS)


class AuthJWT(BaseModel):
    private_key_path: Optional[str] = None
    public_key_path: Optional[str] = None
    _private_key_content: str = PrivateAttr()
    _public_key_content: str = PrivateAttr()
    access_token_expire_minutes: ClassVar[int] = ACCESS_TOKEN_EXPIRE_MINUTES
    refresh_token_expire_days: ClassVar[int] = REFRESH_TOKEN_EXPIRE_DAYS

    def __init__(self, **data):
        super().__init__(**data)
        # JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH win when set, because that is
        # what docker/.env.example section 3 declares and what the compose stack
        # mounts (../infra/certs -> /app/secrets/certs). Falling back to CERTS_DIR
        # keeps a bare-metal checkout working with no environment at all: the pair
        # then lives at backend/service/certs/, which is where the old tree kept
        # it, so a developer machine does not have to set anything.
        self._private_key_content = _key_path("JWT_PRIVATE_KEY_PATH", "jwt-private.pem")
        self._public_key_content = _key_path("JWT_PUBLIC_KEY_PATH", "jwt-public.pem")

    @property
    def private_key_content(self):
        return self._private_key_content

    @property
    def public_key_content(self):
        return self._public_key_content


class Settings(BaseSettings):
    auth_jwt: AuthJWT = AuthJWT()
    algoritm: str = "RS256"


class TokenInfo(BaseModel):
    """
    Model of information about token
    """

    AccessToken: StrictStr = Field(..., alias="access_token")
    RefreshToken: Optional[StrictStr] = Field(None, alias="refresh_token")
    Type: StrictStr = Field("Bearer", alias="token_type")


class Auth(BaseModel):
    """
    Model of auth
    """

    Email: StrictStr = Field(..., alias="email", examples=["john.doe@example.com"])
    Password: StrictStr = Field(..., alias="password", examples=["password"])
