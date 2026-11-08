"""The persistence registry: which module owns the SQL for which slice.

WHY THIS IS A TABLE AND NOT A CLASS: a repository here is a MODULE of `%s`-parameterised
statements (see `service/services/users/persistence/user_repository.py`). It has no state,
so an instance would be a wrapper around nothing. What the container actually needs to know
is the mapping from a domain to the place its SQL lives - that is this table.

WHY THE REPOSITORIES ARE NOT SHARED ACROSS SLICES: two slices that used the same repository
module would be one slice with two names. Where SQL genuinely spans slices - the link
tables that the matching pipeline writes - it lives in the slice that OWNS the table, and
the other slice reads it through that slice's service. `service/shared/repositories/` is
the placeholder for the day a query needs two tables and belongs to neither.

WHY THE MODULES ARE IMPORTED LAZILY: `service.infrastructure.database.connection` builds a
`Database` object at import time (the connection itself is lazy), and a worker or a test
that only wants to know which module holds which SQL must not pay for that.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Optional

#: domain -> the package that re-exports its SQL. A slice with no table of its own is absent.
REPOSITORY_MODULES: Dict[str, str] = {
    "users": "service.services.users.persistence",
    "candidates": "service.services.candidates.persistence",
    "vacancies": "service.services.vacancies.persistence",
    "magics": "service.services.magics.persistence",
    "personalities": "service.services.personalities.persistence",
    "personality_vacancies": "service.services.personality_vacancies.persistence",
    "personality_candidates": "service.services.personality_candidates.persistence",
    "candidate_vacancies": "service.services.candidate_vacancies.persistence",
    "files": "service.services.files.persistence",
    "embeddings": "service.services.embeddings.persistence",
}


def get_repository(domain: str) -> Optional[Any]:
    """The persistence package for one domain, or None when the slice owns no table."""
    module_name = REPOSITORY_MODULES.get(domain)
    if module_name is None:
        return None
    return importlib.import_module(module_name)


def describe() -> Dict[str, str]:
    """The mapping as plain strings, so a diagnostic can print it without importing SQL."""
    return dict(REPOSITORY_MODULES)
