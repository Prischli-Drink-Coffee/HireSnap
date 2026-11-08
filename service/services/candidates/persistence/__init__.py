"""Persistent storage for the candidates slice.

The SQL lives in `info_candidate_repository` beside this file, moved verbatim out of the old
`service/repositories/` package. This package re-exports its public functions so
a caller writes `from ..persistence import get_all_...` and does not depend on
the module name, which is what lets one slice hold several tables later without
touching its application layer.

WHY the statements did not change when the layout did: every query below is a
raw `%s`-parameterised statement against MySQL through
`service.infrastructure.database`, so moving the file cannot change behaviour.
A rewrite into an ORM would be a different change with a different test plan.
"""

from service.services.candidates.persistence.info_candidate_repository import (
    create_info_candidate,
    delete_info_candidate,
    get_all_info_candidates,
    get_info_candidate_by_id,
    update_info_candidate,
)

__all__ = [
    "get_all_info_candidates",
    "get_info_candidate_by_id",
    "create_info_candidate",
    "update_info_candidate",
    "delete_info_candidate",
]
