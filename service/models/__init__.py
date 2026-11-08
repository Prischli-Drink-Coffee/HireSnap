"""Data models: table rows in `db/`, sessions in `auth_models`.

WHY this directory is the innermost layer and imports nothing from the outer ones: a
model that imports a router drags fastapi and the whole composition graph into every
importer, and `backend/tests/test_backend_structure.py` fails the build if `models/`
imports `service.api`, `service.services`, `service.repositories` or
`service.composition`.

Import the package (`from service.models import Users`); the per-module path is an
implementation detail. Re-exporting here means a future move of a model touches this
file instead of forty importers.
"""

from service.models.auth_models import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
    DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS,
    REFRESH_TOKEN_EXPIRE_DAYS,
    Auth,
    AuthJWT,
    Settings,
    TokenInfo,
)
from service.models.db.db_models import (
    CandidateVacancies,
    Embeddings,
    Files,
    GenderUser,
    InfoCandidates,
    Magics,
    Personalities,
    PersonalityCandidates,
    PersonalityVacancies,
    RoleEnum,
    TypeUser,
    Users,
    Vacancies,
)

__all__ = [
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "Auth",
    "AuthJWT",
    "CandidateVacancies",
    "DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS",
    "Embeddings",
    "Files",
    "GenderUser",
    "InfoCandidates",
    "Magics",
    "Personalities",
    "PersonalityCandidates",
    "PersonalityVacancies",
    "REFRESH_TOKEN_EXPIRE_DAYS",
    "RoleEnum",
    "Settings",
    "TokenInfo",
    "TypeUser",
    "Users",
    "Vacancies",
]
