"""The data models, unchanged in shape, plus the JWT settings they carry.

ONE BEHAVIOURAL CHANGE, and why: the token lifetimes used to be read as

    ACCESS_TOKEN_EXPIRE_MINUTES = int(env.__getattr__("ACCESS_TOKEN_EXPIRE_MINUTES"))

at import time, and Env.__getattr__ RAISES for a missing key. The result was that
importing any model - and therefore the whole application - required the JWT
expiry variables to be present, which made "import service.api.app" impossible in
a test, a linter or a build step. They are now read with the documented defaults of
the env manifest (15 minutes / 7 days, docker/.env.example section 3) and an
explicitly set variable still wins. A deployment that sets nothing therefore
behaves like the manifest default instead of crashing at import; a deployment that
sets a value is unaffected.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import ClassVar, List, Optional

from pydantic import (BaseModel, Field, PrivateAttr, StrictBool, StrictBytes,
                      StrictInt, StrictStr, condecimal, root_validator)
from pydantic_settings import BaseSettings

from service.configuration.paths import CERTS_DIR

#: Defaults mirror docker/.env.example section 3 (Auth). They exist so that
#: importing this module needs no environment at all; see the module docstring.
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
REFRESH_TOKEN_EXPIRE_DAYS = _int_env(
    "REFRESH_TOKEN_EXPIRE_DAYS", DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS
)


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
        self._private_key_content = _key_path(
            "JWT_PRIVATE_KEY_PATH", "jwt-private.pem")
        self._public_key_content = _key_path(
            "JWT_PUBLIC_KEY_PATH", "jwt-public.pem")

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
    AccessToken: StrictStr = Field(...,
                                   alias="access_token")
    RefreshToken: Optional[StrictStr] = Field(None,
                                              alias="refresh_token")
    Type: StrictStr = Field("Bearer",
                            alias="token_type")


class Auth(BaseModel):
    """
    Model of auth
    """
    Email: StrictStr = Field(...,
                             alias="email",
                             examples=["john.doe@example.com"])
    Password: StrictStr = Field(...,
                                alias="password",
                                examples=["password"])


class RoleEnum(StrictStr, Enum):
    """
    Model of role enum
    """
    user = 'user'
    admin = 'admin'


class TypeUser(Enum):
    """
    Enum of type user
    """
    candidate = "candidate"
    employer = "employer"


class GenderUser(Enum):
    """
    Enum of gender
    """
    male = "male"
    female = "female"


class Users(BaseModel):
    """
    Модель пользователя
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    Email: StrictStr = Field(...,
                             alias="email",
                             examples=["john.doe@example.com"],
                             description="Почта пользователя")
    Password: StrictStr = Field(...,
                                alias="password",
                                examples=["password"],
                                description="Пароль пользователя")
    Type: TypeUser = Field(...,
                           alias="type",
                           examples=[TypeUser.candidate],
                           description="Тип пользователя")
    InfoID: Optional[StrictInt] = Field(None,
                                        alias="info_id",
                                        examples=[1],
                                        description="Информация по пользователю")
    CreatedAt: Optional[datetime] = Field(datetime.now(),
                                          alias="created_at",
                                          examples=[f"{datetime.now()}"],
                                          description="Дата создания пользователя")
    Role: Optional[RoleEnum] = Field(RoleEnum.user,
                                     alias="role",
                                     examples=["user"])


class InfoCandidates(BaseModel):
    """
    Модель информации по кандидату
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    Name: Optional[StrictStr] = Field(None,
                                      alias="name",
                                      examples=["John Doe"],
                                      description="Имя кандидата")
    Phone: Optional[StrictStr] = Field(None,
                                       alias="phone",
                                       examples=["123-456-7890"],
                                       description="Номер телефона кандидата")
    Gender: Optional[GenderUser] = Field(None,
                                         alias="gender",
                                         examples=[GenderUser.male],
                                         description="Пол кандидата")
    DateBirth: Optional[datetime] = Field(None,
                                          alias="date_birth",
                                          examples=[f"{datetime.now()}"],
                                          description="Дата рождения кандидата")
    FileID: Optional[StrictInt] = Field(None,
                                        alias="file_id",
                                        examples=[1],
                                        description="ID видеовизитки кандидата")
    EmbeddingID: Optional[StrictInt] = Field(None,
                                             alias="embedding_id",
                                             examples=[1],
                                             description="Эмбеддинг кандидата")


class Vacancies(BaseModel):
    """
    Модель вакансии
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    Name: StrictStr = Field(...,
                            alias="name",
                            examples=["Программист Python"],
                            description="Название вакансии")
    Salary: Optional[StrictStr] = Field(None,
                                        alias="salary",
                                        examples=["80000, 90000"],
                                        description="Зарплата вакансии")
    Description: StrictStr = Field(...,
                                   alias="description",
                                   examples=["Разработка и поддержка веб-приложений на Python, написание REST API"],
                                   description="Описание вакансии")
    Skill: Optional[StrictStr] = Field(None,
                                       alias="skill",
                                       examples=["Python, Django, REST API, SQL"],
                                       description="Навыки вакансии")
    EmbeddingID: Optional[StrictInt] = Field(None,
                                             alias="embedding_id",
                                             examples=[1],
                                             description="Эмбеддинг вакансии")


class Personalities(BaseModel):
    """
    Модель типа личности
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    Name: StrictStr = Field(...,
                            alias="name",
                            examples=["Personality 1"],
                            description="Название личности")
    Description: StrictStr = Field(...,
                                   alias="description",
                                   examples=["Description of personality 1"],
                                   description="Описание личности")
    EmbeddingID: Optional[StrictInt] = Field(None,
                                             alias="embedding_id",
                                             examples=[1],
                                             description="Эмбеддинг личности")


class PersonalityVacancies(BaseModel):
    """
    Модель связи личности и вакансии
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    PersonalityID: StrictInt = Field(...,
                                     alias="personality_id",
                                     examples=[1],
                                     description="ID личности")
    VacancyID: StrictInt = Field(...,
                                 alias="vacancy_id",
                                 examples=[1],
                                 description="ID вакансии")
    Distance: condecimal(max_digits=6,
                         decimal_places=6) = Field(...,
                                                   alias="distance",
                                                   examples=[0.563215],
                                                   description="Семантическое сходство личности и вакансии")


class PersonalityCandidates(BaseModel):
    """
    Модель связи личности и вакансии
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    PersonalityID: StrictInt = Field(...,
                                     alias="personality_id",
                                     examples=[1],
                                     description="ID личности")
    UserID: StrictInt = Field(...,
                              alias="user_id",
                              examples=[1],
                              description="ID кандидата")
    Distance: condecimal(max_digits=6,
                         decimal_places=6) = Field(...,
                                                   alias="distance",
                                                   examples=[0.563215],
                                                   description="Семантическое сходство личности и вакансии")


class CandidateVacancies(BaseModel):
    """
    Модель связи кандидата и вакансии
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    UserID: StrictInt = Field(...,
                              alias="user_id",
                              examples=[1],
                              description="ID пользователя")
    VacancyID: StrictInt = Field(...,
                                 alias="vacancy_id",
                                 examples=[1],
                                 description="ID вакансии")
    Distance: condecimal(max_digits=6,
                         decimal_places=6) = Field(...,
                                                   alias="distance",
                                                   examples=[0.563215],
                                                   description="Семантическое сходство вакансии и вакансии")


class Magics(BaseModel):
    """
    Модель карт таро
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    Name: StrictStr = Field(...,
                            alias="name",
                            examples=["Дурак"],
                            description="Название карты таро")
    CandidateExplanation: StrictStr = Field(...,
                                            alias="candidate_explanation",
                                            examples=[
                                                "Эта карта символизирует новые начинания и готовность к приключениям."
                                                " Откликнуться на вакансию будет правильным шагом, так как это может"
                                                " привести к новым возможностям."],
                                            description="Значение карты для кандидата")
    VacancyExplanation: StrictStr = Field(...,
                                          alias="vacancy_explanation",
                                          examples=["Этот аркан символизирует новые начала и открытость к новым"
                                                    " возможностям. Кандидат готов к экспериментам и может принести"
                                                    " свежие идеи в команду."],
                                          description="Значение карты для вакансии")
    CandidateAnswer: StrictStr = Field(...,
                                       alias="candidate_answer",
                                       examples=["Да"],
                                       description="Ответ кандидату")
    VacancyAnswer: StrictStr = Field(...,
                                     alias="vacancy_answer",
                                     examples=["Нет"],
                                     description="Ответ вакансии")
    FileID: Optional[StrictInt] = Field(None,
                                        alias="file_id",
                                        examples=[1],
                                        description="ID изображения карты таро")


class Embeddings(BaseModel):
    """
    Модель венденции
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    Url: StrictStr = Field(...,
                           alias="url",
                           examples=["./data/embeddings/embedding.npy"],
                           description="Путь к эмбеддингу")


class Files(BaseModel):
    """
    Модель файлов
    """
    ID: Optional[int] = Field(None,
                              alias="id",
                              examples=[1])
    Url: StrictStr = Field(...,
                           alias="url",
                           examples=["./data/files/file.png"],
                           description="Путь к файлу")
