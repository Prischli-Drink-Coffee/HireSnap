from service.services import user_services
from service.domain.models import Users, TokenInfo, Auth
from service.shared.hashing import hash_password, validate_password
from fastapi import HTTPException, Depends, Form, status
from datetime import datetime, timedelta
from service.shared.tokens import (encode_jwt, decode_jwt, create_jwt,
                             create_access_token, create_refresh_token, validate_token_type)
from typing import Dict


def validate_auth_user(auth: Auth):
    user = user_services.get_user_by_email(auth.Email)
    if not validate_password(password=auth.Password, hashed_password=user.Password.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    return user


def validate_reg_user(user: Users):
    if not user.Type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid type user")
    try:
        user_services.get_user_by_email(user.Email)
    except Exception:
        pass
    return user


def get_user_by_payload(payload):
    user_id = payload.get("user_id")
    user = user_services.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def signup(user: Users):
    user = validate_reg_user(user)
    current_user = user_services.create_user(user)
    return TokenInfo(access_token=create_access_token(current_user),
                     refresh_token=create_refresh_token(current_user))


def signin(auth: Auth):
    current_user = validate_auth_user(auth)
    return TokenInfo(access_token=create_access_token(current_user),
                     refresh_token=create_refresh_token(current_user))


def get_current_token_payload(token: str):
    payload = decode_jwt(token=token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token error")
    return payload


class UserGetFromToken:
    def __init__(self, token_type: str):
        self.token_type = token_type

    def __call__(self, payload: Dict = Depends(get_current_token_payload)):
        validate_token_type(payload, self.token_type)
        return get_user_by_payload(payload)


def auth_refresh_jwt(user: Users):
    return TokenInfo(access_token=create_access_token(user))
