"""The ordered list of mounted routers: the ONE place that says what /server exposes.

WHY THIS FILE EXISTS SEPARATELY FROM `app.py`: the router list is a contract, not
plumbing. `backend/tests/test_backend_structure.py` compares the modules named here
against the router modules that exist under `service/services/*/presentation/routers/`,
and `backend/scripts/check_backend_layout.py` fails when a router module is on disk but
absent from this tuple - a route nobody mounts is dead code that still compiles. Keeping
the list in its own module means that guard reads a file whose only job is the list,
instead of pattern-matching an assignment inside a 200-line application builder.

WHY THE ORDER IS PINNED: FastAPI generates the OpenAPI document in declaration order, so
reordering this tuple reorders `/server/docs` and changes the snapshot the test suite
holds. It is also a correctness matter in one place: a static path declared after a
parameterised one that can match it is shadowed. The order below is the order the old
single `src/pipeline/server.py` declared its handlers in, preserved on purpose.

WHY THESE ARE MODULES AND NOT `module.router`: the structure test asks for each mounted
module's name, and a tuple of `APIRouter` objects has no name to compare against the
directory listing. `app.py` does `module.router` at include time.
"""

from __future__ import annotations

from service.services.auth.presentation.routers.auth_api import auth_api
from service.services.candidate_vacancies.presentation.routers.candidate_vacancies_api import (
    candidate_vacancies_api,
)
from service.services.candidates.presentation.routers.info_candidates_api import (
    info_candidates_api,
)
from service.services.embeddings.presentation.routers.embeddings_api import embeddings_api
from service.services.files.presentation.routers.files_api import files_api
from service.services.magics.presentation.routers.magics_api import magics_api
from service.services.matching.presentation.routers.inference_api import inference_api
from service.services.personalities.presentation.routers.personalities_api import (
    personalities_api,
)

# The one import that cannot fit 100 columns: the slice name and its router package both
# repeat "personality_candidates", so the fully qualified path is 101 characters. It is
# written out rather than aliased because a reader searching for the package must find it.
from service.services.personality_candidates.presentation.routers.personality_candidates_api import (  # noqa: E501
    personality_candidates_api,
)
from service.services.personality_vacancies.presentation.routers.personality_vacancies_api import (
    personality_vacancies_api,
)
from service.services.users.presentation.routers.users_api import users_api
from service.services.vacancies.presentation.routers.vacancies_api import vacancies_api

__all__ = [
    "ROUTERS",
    "ROUTER_NAMES",
    "auth_api",
    "inference_api",
    "users_api",
    "info_candidates_api",
    "vacancies_api",
    "magics_api",
    "personalities_api",
    "personality_vacancies_api",
    "personality_candidates_api",
    "candidate_vacancies_api",
    "files_api",
    "embeddings_api",
]

#: Declaration order, which is OpenAPI order. Auth first because it is the only slice
#: with no token requirement, then the pipeline routes, then the tables in the order the
#: old module listed them.
ROUTERS = (
    auth_api,
    inference_api,
    users_api,
    info_candidates_api,
    vacancies_api,
    magics_api,
    personalities_api,
    personality_vacancies_api,
    personality_candidates_api,
    candidate_vacancies_api,
    files_api,
    embeddings_api,
)

#: The stems the structure test requires to be mounted. Derived rather than typed, so
#: adding a router above cannot leave this list stale.
ROUTER_NAMES = tuple(module.__name__.rsplit(".", 1)[-1] for module in ROUTERS)
