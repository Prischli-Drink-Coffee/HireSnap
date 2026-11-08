"""Persistent storage for the vacancies slice.

The SQL lives in `vacancy_repository` beside this file, moved verbatim out of the old
`service/repositories/` package. This package re-exports its public functions so
a caller writes `from ..persistence import get_all_...` and does not depend on
the module name, which is what lets one slice hold several tables later without
touching its application layer.

WHY the statements did not change when the layout did: every query below is a
raw `%s`-parameterised statement against MySQL through
`service.infrastructure.database`, so moving the file cannot change behaviour.
A rewrite into an ORM would be a different change with a different test plan.
"""

from service.services.vacancies.persistence.vacancy_repository import (
    create_vacancy,
    delete_vacancy,
    get_all_vacancies,
    get_vacancy_by_id,
    update_vacancy,
)

__all__ = [
    "get_all_vacancies",
    "get_vacancy_by_id",
    "create_vacancy",
    "update_vacancy",
    "delete_vacancy",
]
