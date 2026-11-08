"""The ten table models: the wire shape of every row the API returns.

Split out of `service/domain/models.py` with no field change. Every field keeps its
alias, its example and its Russian description, because this file IS the OpenAPI
schema: `tests/snapshots/critical_endpoint_openapi.json` pins the generated document, so
a reworded description is a snapshot diff and a renamed alias is a broken client.

WHY the three enums live here rather than beside the auth models: `RoleEnum`, `TypeUser`
and `GenderUser` are the vocabulary of the STORED data (`users.role`, `users.type`,
`info_candidates.gender`), so a change to one is a migration, not an auth change.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, StrictInt, StrictStr, condecimal


class RoleEnum(StrictStr, Enum):
    """
    Model of role enum
    """

    user = "user"
    admin = "admin"


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

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    Email: StrictStr = Field(
        ..., alias="email", examples=["john.doe@example.com"], description="Почта пользователя"
    )
    Password: StrictStr = Field(
        ..., alias="password", examples=["password"], description="Пароль пользователя"
    )
    Type: TypeUser = Field(
        ..., alias="type", examples=[TypeUser.candidate], description="Тип пользователя"
    )
    InfoID: Optional[StrictInt] = Field(
        None, alias="info_id", examples=[1], description="Информация по пользователю"
    )
    CreatedAt: Optional[datetime] = Field(
        datetime.now(),
        alias="created_at",
        examples=[f"{datetime.now()}"],
        description="Дата создания пользователя",
    )
    Role: Optional[RoleEnum] = Field(RoleEnum.user, alias="role", examples=["user"])


class InfoCandidates(BaseModel):
    """
    Модель информации по кандидату
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    Name: Optional[StrictStr] = Field(
        None, alias="name", examples=["John Doe"], description="Имя кандидата"
    )
    Phone: Optional[StrictStr] = Field(
        None, alias="phone", examples=["123-456-7890"], description="Номер телефона кандидата"
    )
    Gender: Optional[GenderUser] = Field(
        None, alias="gender", examples=[GenderUser.male], description="Пол кандидата"
    )
    DateBirth: Optional[datetime] = Field(
        None,
        alias="date_birth",
        examples=[f"{datetime.now()}"],
        description="Дата рождения кандидата",
    )
    FileID: Optional[StrictInt] = Field(
        None, alias="file_id", examples=[1], description="ID видеовизитки кандидата"
    )
    EmbeddingID: Optional[StrictInt] = Field(
        None, alias="embedding_id", examples=[1], description="Эмбеддинг кандидата"
    )


class Vacancies(BaseModel):
    """
    Модель вакансии
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    Name: StrictStr = Field(
        ..., alias="name", examples=["Программист Python"], description="Название вакансии"
    )
    Salary: Optional[StrictStr] = Field(
        None, alias="salary", examples=["80000, 90000"], description="Зарплата вакансии"
    )
    Description: StrictStr = Field(
        ...,
        alias="description",
        examples=["Разработка и поддержка веб-приложений на Python, написание REST API"],
        description="Описание вакансии",
    )
    Skill: Optional[StrictStr] = Field(
        None,
        alias="skill",
        examples=["Python, Django, REST API, SQL"],
        description="Навыки вакансии",
    )
    EmbeddingID: Optional[StrictInt] = Field(
        None, alias="embedding_id", examples=[1], description="Эмбеддинг вакансии"
    )


class Personalities(BaseModel):
    """
    Модель типа личности
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    Name: StrictStr = Field(
        ..., alias="name", examples=["Personality 1"], description="Название личности"
    )
    Description: StrictStr = Field(
        ...,
        alias="description",
        examples=["Description of personality 1"],
        description="Описание личности",
    )
    EmbeddingID: Optional[StrictInt] = Field(
        None, alias="embedding_id", examples=[1], description="Эмбеддинг личности"
    )


class PersonalityVacancies(BaseModel):
    """
    Модель связи личности и вакансии
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    PersonalityID: StrictInt = Field(
        ..., alias="personality_id", examples=[1], description="ID личности"
    )
    VacancyID: StrictInt = Field(..., alias="vacancy_id", examples=[1], description="ID вакансии")
    Distance: condecimal(max_digits=6, decimal_places=6) = Field(
        ...,
        alias="distance",
        examples=[0.563215],
        description="Семантическое сходство личности и вакансии",
    )


class PersonalityCandidates(BaseModel):
    """
    Модель связи личности и вакансии
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    PersonalityID: StrictInt = Field(
        ..., alias="personality_id", examples=[1], description="ID личности"
    )
    UserID: StrictInt = Field(..., alias="user_id", examples=[1], description="ID кандидата")
    Distance: condecimal(max_digits=6, decimal_places=6) = Field(
        ...,
        alias="distance",
        examples=[0.563215],
        description="Семантическое сходство личности и вакансии",
    )


class CandidateVacancies(BaseModel):
    """
    Модель связи кандидата и вакансии
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    UserID: StrictInt = Field(..., alias="user_id", examples=[1], description="ID пользователя")
    VacancyID: StrictInt = Field(..., alias="vacancy_id", examples=[1], description="ID вакансии")
    Distance: condecimal(max_digits=6, decimal_places=6) = Field(
        ...,
        alias="distance",
        examples=[0.563215],
        description="Семантическое сходство вакансии и вакансии",
    )


class Magics(BaseModel):
    """
    Модель карт таро
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    Name: StrictStr = Field(
        ..., alias="name", examples=["Дурак"], description="Название карты таро"
    )
    CandidateExplanation: StrictStr = Field(
        ...,
        alias="candidate_explanation",
        examples=[
            "Эта карта символизирует новые начинания и готовность к приключениям."
            " Откликнуться на вакансию будет правильным шагом, так как это может"
            " привести к новым возможностям."
        ],
        description="Значение карты для кандидата",
    )
    VacancyExplanation: StrictStr = Field(
        ...,
        alias="vacancy_explanation",
        examples=[
            "Этот аркан символизирует новые начала и открытость к новым"
            " возможностям. Кандидат готов к экспериментам и может принести"
            " свежие идеи в команду."
        ],
        description="Значение карты для вакансии",
    )
    CandidateAnswer: StrictStr = Field(
        ..., alias="candidate_answer", examples=["Да"], description="Ответ кандидату"
    )
    VacancyAnswer: StrictStr = Field(
        ..., alias="vacancy_answer", examples=["Нет"], description="Ответ вакансии"
    )
    FileID: Optional[StrictInt] = Field(
        None, alias="file_id", examples=[1], description="ID изображения карты таро"
    )


class Embeddings(BaseModel):
    """
    Модель венденции
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    Url: StrictStr = Field(
        ...,
        alias="url",
        examples=["./data/embeddings/embedding.npy"],
        description="Путь к эмбеддингу",
    )


class Files(BaseModel):
    """
    Модель файлов
    """

    ID: Optional[int] = Field(None, alias="id", examples=[1])
    Url: StrictStr = Field(
        ..., alias="url", examples=["./data/files/file.png"], description="Путь к файлу"
    )
