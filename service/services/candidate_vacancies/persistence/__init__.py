"""Persistent storage for the candidate_vacancies slice.

The SQL lives in `candidate_vacancy_repository` beside this file, moved verbatim out of the old
`service/repositories/` package. This package re-exports its public functions so
a caller writes `from ..persistence import get_all_...` and does not depend on
the module name, which is what lets one slice hold several tables later without
touching its application layer.

WHY the statements did not change when the layout did: every query below is a
raw `%s`-parameterised statement against MySQL through
`service.infrastructure.database`, so moving the file cannot change behaviour.
A rewrite into an ORM would be a different change with a different test plan.
"""

from service.services.candidate_vacancies.persistence.candidate_vacancy_repository import (
    create_candidate_vacancy,
    delete_candidate_vacancy,
    get_all_candidate_vacancies,
    get_candidate_vacancy_by_id,
    update_candidate_vacancy,
)

__all__ = [
    "get_all_candidate_vacancies",
    "get_candidate_vacancy_by_id",
    "create_candidate_vacancy",
    "update_candidate_vacancy",
    "delete_candidate_vacancy",
]
