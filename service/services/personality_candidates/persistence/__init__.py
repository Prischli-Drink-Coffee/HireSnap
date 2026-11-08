"""Persistent storage for the personality_candidates slice.

The SQL lives in `personality_candidate_repository` beside this file, moved verbatim out of the old
`service/repositories/` package. This package re-exports its public functions so
a caller writes `from ..persistence import get_all_...` and does not depend on
the module name, which is what lets one slice hold several tables later without
touching its application layer.

WHY the statements did not change when the layout did: every query below is a
raw `%s`-parameterised statement against MySQL through
`service.infrastructure.database`, so moving the file cannot change behaviour.
A rewrite into an ORM would be a different change with a different test plan.
"""

from service.services.personality_candidates.persistence.personality_candidate_repository import (
    create_personality_candidate,
    delete_personality_candidate,
    get_all_personality_candidates,
    get_personality_candidate_by_id,
    update_personality_candidate,
)

__all__ = [
    "get_all_personality_candidates",
    "get_personality_candidate_by_id",
    "create_personality_candidate",
    "update_personality_candidate",
    "delete_personality_candidate",
]
