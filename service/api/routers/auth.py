"""auth router.

The handlers are unchanged; only the decoration and the imports are. Paths keep
their leading slash and are mounted by service/api/app.py under /server, so the
public URL is /server<path> (the gateway rewrites /api/ to /server/).
"""

from fastapi import Form, HTTPException
from service.domain.models import Users, TokenInfo, Auth
from service.services import auth_services
from service.configuration.logging import get_logger

from fastapi import APIRouter

router = APIRouter()
log = get_logger()


@router.post("/signup/", response_model=TokenInfo, tags=["Main"])
async def signup(email: str = Form(...),
                 password: str = Form(...),
                 type_user: str = Form(...),
                 role: str = Form("user")):
    """
    Регистрация нового пользователя.
    """
    try:

        return auth_services.signup(Users(email=email, password=password,
                                          type=type_user, role=role))
    except HTTPException as ex:
        log.exception("Error during registration", exc_info=ex)
        raise ex


@router.post("/signin/", response_model=TokenInfo, tags=["Main"])
async def signin(email: str = Form(...),
                 password: str = Form(...)):
    """
    Авторизация пользователя.
    """
    try:
        return auth_services.signin(Auth(email=email, password=password))
    except HTTPException as ex:
        log.exception("Error during registration", exc_info=ex)
        raise ex
