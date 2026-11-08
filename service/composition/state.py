"""The access surface: what a router calls. The wiring itself lives in `container.py`.

WHY THIS MODULE STILL EXISTS AFTER THE CONTAINER WAS INTRODUCED: two different jobs were
being done by one file. Deciding WHAT is built and in which order is wiring; deciding WHO
may ask for it and WHEN is access. The first is read by a human once and belongs in
`container.py`; the second is called on every request by a router, and it is what the
boundary test reads. Keeping the accessors here means a router never imports the container,
so the container can grow without touching presentation code.

WHY THE ACCESSORS ARE FUNCTIONS AND NOT MODULE ATTRIBUTES: a router writes
`Depends(get_user_service)`. A module attribute would be built at import time, and
`import service.api.app` must work with no `.env`, no MySQL and no broker. A second call
is free because the container memoises.

WHY `SLICE_SERVICES` STILL LIVES HERE: `backend/tests/test_backend_structure.py` and
`backend/scripts/check_backend_layout.py` read it to relate a domain name to the package
that holds it. The container asks `services.py` for the class and this table for the name,
and the two are kept in step by `backend/tests/test_service_boundary.py`, which fails when
a slice on disk is missing from either.
"""

from __future__ import annotations

from typing import Any, Dict

from service.composition.container import get_container, reset_container

# Re-exported on purpose: the structure test and the layout guard relate a domain name to
# the package that holds it, and they read this module rather than `services.py` so that a
# rename has one place to land. Unused by this file, hence the noqa.
from service.composition.services import SERVICE_MODULES as SLICE_SERVICES  # noqa: F401


# --- one accessor per slice -------------------------------------------------
# Spelled out rather than generated: a router reads `get_user_service()` and a reader
# searching for it lands here, whereas `get_service("users")` hides the wiring behind
# a string that no editor can jump to.
def get_service(domain: str) -> Any:
    """Resolve one slice service by domain name. The generic form of the accessors."""
    return get_container().get_service(domain)


def get_auth_service():
    return get_service("auth")


def get_user_service():
    return get_service("users")


def get_info_candidate_service():
    return get_service("candidates")


def get_vacancy_service():
    return get_service("vacancies")


def get_magic_service():
    return get_service("magics")


def get_personality_service():
    return get_service("personalities")


def get_personality_vacancy_service():
    return get_service("personality_vacancies")


def get_personality_candidate_service():
    return get_service("personality_candidates")


def get_candidate_vacancy_service():
    return get_service("candidate_vacancies")


def get_file_service():
    return get_service("files")


def get_embedding_service():
    return get_service("embeddings")


def get_matching_service():
    return get_service("matching")


def get_job_service():
    return get_service("jobs")


# --- platform ports ---------------------------------------------------------
def get_cache():
    """The cache facade. Memory by default, so no second container is required."""
    return get_container().get_cache()


def get_storage():
    """The file storage facade. Local disk by default."""
    return get_container().get_storage()


def get_mailer():
    """The mailer. Disabled unless the manifest turns it on."""
    return get_container().get_mailer()


def get_ml_client():
    """The ML transport module itself (a module, not an instance: it is stateless)."""
    return get_container().get_ml_client()


def get_dispatcher():
    """The broker adapter the job slice talks to (never celery itself).

    WHY THIS IS NOT CALLED get_job_queue: `service/shared/ports.JobQueue` is the
    APPLICATION port - enqueue()/status() - and `JobService` implements it. This object is
    one layer below: send a named task, look at where it got to, ask it to stop. Two
    different contracts sharing one name is how a reader ends up calling the wrong one.
    """
    return get_container().get_dispatcher()


def reset_state() -> None:
    """Forget every built object, including the container. Used by tests."""
    reset_container()


def describe_state() -> Dict[str, Any]:
    """What is built right now, for a post-mortem or a diagnostic endpoint."""
    return get_container().describe()
