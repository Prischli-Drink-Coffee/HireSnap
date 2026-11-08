"""Test bootstrap: make `service` importable and keep env flags hermetic.

WHY a conftest and not a pytest.ini: pytest already puts the rootdir on sys.path
when it collects a test file inside a package, but these tests must also work when
pytest is invoked from the repository root (`python -m pytest backend/tests`), and
the package is imported as `service.*` from inside backend/. One explicit
sys.path insert keeps both invocations identical, and unlike the old layout no
module does it implicitly.
"""

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

PLATFORM_FLAGS = (
    "CACHE__BACKEND",
    "CACHE_BACKEND",
    "CACHE__PREFIX",
    "CACHE_PREFIX",
    "CACHE__REDIS_URL",
    "CACHE_REDIS_URL",
    "CACHE__TTL",
    "CACHE_TTL",
    "STORAGE__BACKEND",
    "STORAGE_BACKEND",
    "STORAGE__BUCKET",
    "STORAGE_BUCKET",
    "STORAGE__ENDPOINT",
    "STORAGE_ENDPOINT",
    "STORAGE__ACCESS_KEY",
    "STORAGE_ACCESS_KEY",
    "STORAGE__SECRET_KEY",
    "STORAGE_SECRET_KEY",
    "MAIL__ENABLED",
    "MAIL_ENABLED",
    "MAIL__BACKEND",
    "MAIL_BACKEND",
    "MAIL__HOST",
    "MAIL_HOST",
    "CELERY__ENABLED",
    "CELERY_ENABLED",
    "CELERY__BROKER_URL",
    "CELERY_BROKER_URL",
    "CELERY__RESULT_BACKEND",
    "CELERY_RESULT_BACKEND",
    "DATA_PATH",
    "PUBLIC_BASE_URL",
    "ML__BASE_URL",
    "ML_BASE_URL",
)


def pytest_configure(config):
    """Register the markers this suite uses.

    WHY here and not in pyproject.toml: the file that carries [tool.pytest.ini_options]
    is part of the in-flight restructure, and an unregistered marker is a warning that
    CI turns into noise. Registering in a hook works with or without the ini file, so
    the marker is valid today and stays valid after the move.
    """
    config.addinivalue_line(
        "markers",
        "integration: needs the real stack (MySQL, schema, network); skipped unless "
        "HIRESNAP_INTEGRATION=1",
    )


@pytest.fixture(autouse=True)
def _hermetic_platform_env(monkeypatch):
    """No platform flag leaks in from the environment or from backend/.env."""
    for name in PLATFORM_FLAGS:
        monkeypatch.delenv(name, raising=False)

    # The infra modules memoise backends; a stale facade from another test would
    # make the assertions depend on ordering.
    for module_name in (
        "service.infrastructure.cache",
        "service.infrastructure.storage",
        "service.infrastructure.messaging.celery_app",
    ):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for reset in ("reset_cache", "reset_storage", "reset_celery_app"):
            getattr(module, reset, lambda: None)()

    # The composition container is the OTHER memo: it caches the built services and
    # the platform facades (cache, storage, mailer, ml client, dispatcher). Each of
    # those reads the environment when it is constructed, so a container built by an
    # earlier test would hand this one a backend that the flag deletion never reached.
    # Reset through the public state API, and only when the package is already
    # imported: importing it here would pull the whole service graph into a test that
    # may not need it.
    state = sys.modules.get("service.composition.state")
    if state is not None:
        state.reset_state()
    yield
