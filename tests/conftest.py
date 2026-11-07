"""Test bootstrap: make "src.*" importable and keep env flags hermetic.

WHY a conftest and not a pytest.ini in backend/: the repository has no test
configuration and adding one would change how every existing (future) test
collects. This file does the two things the platform-layer tests need:

  1. Puts backend/ on sys.path, so "import src.infra.cache" works when pytest is
     invoked from the repository root OR from backend/ (the package is imported
     as src.* everywhere in the application).
  2. Clears every platform feature flag before each test and resets the caches
     the modules hold, so a developer's backend/.env - which is loaded lazily by
     the infra modules - cannot change the result of a test. A test that depends
     on a developer's local env is a flaky test.
"""

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

PLATFORM_FLAGS = (
    "CACHE__BACKEND", "CACHE_BACKEND",
    "CACHE__PREFIX", "CACHE_PREFIX",
    "CACHE__REDIS_URL", "CACHE_REDIS_URL",
    "CACHE__TTL", "CACHE_TTL",
    "STORAGE__BACKEND", "STORAGE_BACKEND",
    "STORAGE__BUCKET", "STORAGE_BUCKET",
    "STORAGE__ENDPOINT", "STORAGE_ENDPOINT",
    "STORAGE__ACCESS_KEY", "STORAGE_ACCESS_KEY",
    "STORAGE__SECRET_KEY", "STORAGE_SECRET_KEY",
    "MAIL__ENABLED", "MAIL_ENABLED",
    "MAIL__BACKEND", "MAIL_BACKEND",
    "MAIL__HOST", "MAIL_HOST",
    "CELERY__ENABLED", "CELERY_ENABLED",
    "CELERY__BROKER_URL", "CELERY_BROKER_URL",
    "CELERY__RESULT_BACKEND", "CELERY_RESULT_BACKEND",
    "DATA_PATH", "PUBLIC_BASE_URL",
    "ML__BASE_URL", "ML_BASE_URL",
)


@pytest.fixture(autouse=True)
def _hermetic_platform_env(monkeypatch):
    """No platform flag leaks in from the environment or from backend/.env."""
    for name in PLATFORM_FLAGS:
        monkeypatch.delenv(name, raising=False)

    # The infra modules memoise backends; a stale facade from another test would
    # make the assertions depend on ordering.
    for module_name in ("src.infra.cache", "src.infra.storage", "src.workers.celery_app"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for reset in ("reset_cache", "reset_storage", "reset_celery_app"):
            getattr(module, reset, lambda: None)()
    yield
