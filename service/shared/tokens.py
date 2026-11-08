"""JWT creation and verification.

ONE BEHAVIOURAL CHANGE, and why: the RS256 key material used to be read as a
DEFAULT ARGUMENT VALUE -

    def encode_jwt(payload, private_key=settings.auth_jwt.private_key_content.read_text())

- which Python evaluates when the `def` statement executes, i.e. at import time.
The consequence: importing this module required the key pair to exist on disk, so
the whole application could not be imported on a machine or in a build step where
infra/certs is not mounted. The keys are now read inside the functions, on first
use, and the error a caller sees when they are missing is the same FileNotFoundError
one call later than before. No signature changed for any caller that passes a key
explicitly, and the two call sites in this service pass none.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional

import jwt
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer

from service.models import Settings, Users

settings = Settings()
http_bearer = HTTPBearer()


def _private_key() -> str:
    """The RS256 private key, read when a token is actually signed."""
    return settings.auth_jwt.private_key_content.read_text()


def _public_key() -> str:
    """The RS256 public key, read when a token is actually verified."""
    return settings.auth_jwt.public_key_content.read_text()


# >>> private_key = b"-----BEGIN PRIVATE KEY-----\nMIGEAgEAMBAGByqGSM49AgEGBS..."
# >>> public_key = b"-----BEGIN PUBLIC KEY-----\nMHYwEAYHKoZIzj0CAQYFK4EEAC..."


def encode_jwt(
    payload: dict,
    private_key: Optional[str] = None,
    algorithm: str = settings.algoritm,
    expire_minutes: int = settings.auth_jwt.access_token_expire_minutes,
    expire_timedelta: Optional[timedelta] = None,
) -> str:
    to_encode = payload.copy()
    now = datetime.utcnow()
    if expire_timedelta:
        expire = now + expire_timedelta
    else:
        expire = now + timedelta(minutes=expire_minutes)
    to_encode.update(
        exp=expire,
        iat=now,
        # TODO можно добавить jti (JWT ID) [jti=str(uuid.uuid4())] для реализации blacklist
    )
    encoded = jwt.encode(
        to_encode,
        private_key if private_key is not None else _private_key(),
        algorithm=algorithm,
    )
    return encoded


def decode_jwt(
    token: str | bytes, public_key: Optional[str] = None, algorithm: str = settings.algoritm
) -> dict:
    decoded = jwt.decode(
        token,
        public_key if public_key is not None else _public_key(),
        algorithms=[algorithm],
    )
    return decoded


def create_jwt(
    token_type: str,
    token_data: dict,
    expire_minutes: int = settings.auth_jwt.access_token_expire_minutes,
    expire_timedelta: timedelta | None = None,
) -> str:
    jwt_payload = {"TOKEN_TYPE_FIELD": token_type}
    jwt_payload.update(token_data)
    return encode_jwt(
        payload=jwt_payload, expire_minutes=expire_minutes, expire_timedelta=expire_timedelta
    )


def create_access_token(user: Users) -> str:
    jwt_payload = {"sub": user.Role, "user_id": user.ID, "type": user.Type.value}
    return create_jwt(
        token_type="access_token_type",
        token_data=jwt_payload,
        expire_minutes=settings.auth_jwt.access_token_expire_minutes,
    )


def create_refresh_token(user: Users) -> str:
    jwt_payload = {"sub": user.Role}
    return create_jwt(
        token_type="refresh_token_type",
        token_data=jwt_payload,
        expire_timedelta=timedelta(days=settings.auth_jwt.refresh_token_expire_days),
    )


def validate_token_type(
    payload: Dict,
    token_type: str,
) -> bool:
    # TODO при добавлении jti (JWT ID) [jti=str(uuid.uuid4())] сделать проверку на хранящиеся
    #  уникальные айди токенов в базе для реализации blacklist
    current_token_type = payload.get("TOKEN_TYPE_FIELD")
    if current_token_type == token_type:
        return True
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Invalid token type {token_type!r} excepted {current_token_type!r}",
    )
