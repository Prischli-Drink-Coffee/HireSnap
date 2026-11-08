"""The platform ports: the four objects every slice may borrow, built on demand.

WHAT THIS FILE DECIDES: which concrete cache, storage backend, mailer and ML transport a
slice talks to. That decision is one line per port here, and nothing else in the service
is allowed to reach for `service.infrastructure.*` directly. The practical consequence is
that a deployment changes a backend by changing an environment variable, and a test changes
it by passing an object in.

WHY THE BUILDERS ARE FUNCTIONS AND NOT MODULE CONSTANTS: each of these reads the
environment, and `import service.api.app` must work with no `.env`, no broker and no MySQL.
A constant built at import time would make the import itself a configuration requirement.

WHY THE IMPORTS ARE INSIDE THE FUNCTIONS: `cache`, `storage` and `mail` import pymysql,
redis and boto3 lazily themselves, but importing the module is still the moment a typo or a
missing dependency surfaces. Deferring the import keeps the failure at the first USE, which
is the error message an operator can act on.
"""

from __future__ import annotations

from typing import Any


def build_cache() -> Any:
    """The cache facade: key/value with a TTL, memory by default."""
    from service.infrastructure.cache import get_cache

    return get_cache()


def build_storage() -> Any:
    """The file storage port: local disk by default, S3 when STORAGE__BACKEND says so."""
    from service.infrastructure.storage import get_storage

    return get_storage()


def build_mailer() -> Any:
    """The mailer: disabled unless the manifest turns it on."""
    from service.infrastructure.mail import get_mailer

    return get_mailer()


def build_ml_client() -> Any:
    """The ML transport. A module, not an instance: it holds no state, only URLs."""
    from service.infrastructure import ml_sidecar

    return ml_sidecar


def build_job_dispatcher() -> Any:
    """The broker adapter.

    WHY THE JOB SLICE ASKS FOR THIS INSTEAD OF IMPORTING CELERY: a business rule that
    names its broker cannot be unit-tested without one, and the boundary test forbids the
    import (`backend/tests/test_service_boundary.py`, rule 3). Everything the slice needs
    from the broker is five methods on the object returned here, and every one of them
    degrades when celery is not installed, which is the normal state of the API image.
    """
    from service.infrastructure.messaging.queue import CeleryDispatcher

    return CeleryDispatcher()


#: name -> builder. `container.py` iterates this, so adding a port is one entry, and
#: `describe()` can report what is available without hard-coding the same list twice.
INFRA_FACTORIES = {
    "cache": build_cache,
    "storage": build_storage,
    "mailer": build_mailer,
    "ml_client": build_ml_client,
    "dispatcher": build_job_dispatcher,
}
