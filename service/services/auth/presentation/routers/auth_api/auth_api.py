"""auth router, now served by the auth slice.

WHAT MOVED: the handlers used to call module-level functions in
`service/services/auth_services.py`, which reached the database directly and
raised `fastapi.HTTPException`. The rules live in
`service/services/auth/application/`, and the object is resolved through
`service/composition/state.py`, so a test can substitute it without editing this
file.

WHY THE HANDLERS STILL LOOK THE SAME: the decorators, paths, tags, response models,
JWT dependencies and docstrings are the API contract and were carried over one for
one. Two things differ - each handler takes its collaborator as a `Depends`
parameter instead of importing a module, and the wrapper catches `ServiceError`
instead of `HTTPException`. `service/api/app.py` maps a `ServiceError` to the same
status code and the same body shape the old
exception produced, so a client cannot tell the difference.

Paths keep their leading slash and are mounted under /server, so the public URL is
/server<path>; the gateway rewrites /api/ to /server/.
"""

from fastapi import APIRouter, Depends, Form

from service.composition.state import get_auth_service
from service.configuration.logging import get_logger
from service.models import Auth, TokenInfo, Users
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


@router.post("/signup/", response_model=TokenInfo, tags=["Main"])
async def signup(
    email: str = Form(...),
    password: str = Form(...),
    type_user: str = Form(...),
    role: str = Form("user"),
    service=Depends(get_auth_service),
):
    """
    Регистрация нового пользователя.
    """
    try:
        return service.signup(Users(email=email, password=password, type=type_user, role=role))
    except ServiceError as ex:
        log.exception("Error during registration", exc_info=ex)
        raise ex


@router.post("/signin/", response_model=TokenInfo, tags=["Main"])
async def signin(
    email: str = Form(...), password: str = Form(...), service=Depends(get_auth_service)
):
    """
    Авторизация пользователя.
    """
    try:
        return service.signin(Auth(email=email, password=password))
    except ServiceError as ex:
        log.exception("Error during registration", exc_info=ex)
        raise ex
