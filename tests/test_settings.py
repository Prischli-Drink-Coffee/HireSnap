"""service/settings.py: the one snapshot of the process configuration.

WHY THIS DESERVES ITS OWN TEST: every deployment module reads its configuration through
this class, so a drifted default does not fail - it just behaves differently at runtime
(a cache TTL of zero, an empty ML URL, DEBUG quietly on). The class is also the only place
that decides the two-spelling question (CACHE__BACKEND for compose, CACHE_BACKEND for a
developer shell), and getting that wrong means the snapshot describes a different process
than the one that runs.

WHY A LOCAL autouse FIXTURE CLEARS THE ENVIRONMENT: conftest.py only clears the platform
flags. A settings test that read the developer's own HOST/SERVER_PORT/DEBUG would pass on
CI and fail on a machine with a .env loaded, which is the definition of a test that
reports the environment instead of the code.
"""

from __future__ import annotations

import dataclasses

import pytest

from service import settings as settings_module
from service.configuration.paths import BACKEND_ROOT
from service.settings import Settings, build_settings, get_settings, reset_settings

#: Every name build_settings() reads. Cleared for the duration of each test so a value
#: exported in the developer's shell (or loaded from backend/.env by an earlier module)
#: cannot decide an assertion.
SETTINGS_ENV = (
    "SERVICE_VERSION",
    "HOST",
    "SERVER_PORT",
    "DEBUG",
    "INIT_DB",
    "DATA_PATH",
    "CACHE__BACKEND",
    "CACHE_BACKEND",
    "CACHE__TTL",
    "CACHE_TTL",
    "STORAGE__BACKEND",
    "STORAGE_BACKEND",
    "MAIL__ENABLED",
    "MAIL_ENABLED",
    "CELERY__BROKER_URL",
    "CELERY_BROKER_URL",
    "JOB__TTL",
    "JOB_TTL",
    "ML__SERVICE_URL",
    "ML_SERVICE_URL",
    "ML__CONNECT_TIMEOUT",
    "ML_SERVICE_CONNECT_TIMEOUT",
    "ML_CONNECT_TIMEOUT",
    "ML__TIMEOUT",
    "ML_SERVICE_TIMEOUT",
    "ML_TIMEOUT",
)


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch):
    """No settings variable leaks in, and the lru_cache never survives a test."""
    for name in SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    reset_settings()
    yield
    reset_settings()


def test_the_bare_defaults_are_the_manifest_defaults_off_profile():
    """Settings() with no environment is what docker/.env.example produces with no profiles.

    Pinned as one dict rather than field by field so a changed default is one visible diff
    in one place, next to the comment that says why it matters.
    """
    snapshot = Settings()

    assert snapshot.service_name == "backend"
    assert snapshot.version == "1.0.0"
    assert snapshot.host == "0.0.0.0"
    assert snapshot.port == 8080
    assert snapshot.debug is False
    assert snapshot.init_db is False
    assert snapshot.cache_backend == "memory"
    assert snapshot.cache_ttl_seconds == 300.0
    assert snapshot.storage_backend == "local"
    assert snapshot.mail_enabled is False
    assert snapshot.broker_url == ""
    assert snapshot.job_ttl_seconds == 86400.0
    assert snapshot.ml_service_url == "http://127.0.0.1:8080"
    assert snapshot.ml_connect_timeout == 10.0
    assert snapshot.ml_read_timeout == 900.0


def test_build_settings_reads_the_environment_into_the_snapshot():
    import os

    os.environ.update(
        {
            "SERVICE_VERSION": "2.5.0",
            "HOST": "127.0.0.1",
            "SERVER_PORT": "9000",
            "DEBUG": "TRUE",
            "INIT_DB": "1",
            "CACHE__BACKEND": "redis",
            "CACHE__TTL": "45.5",
            "STORAGE__BACKEND": "s3",
            "MAIL__ENABLED": "yes",
            "CELERY__BROKER_URL": "redis://cache:6379/1",
            "JOB__TTL": "60",
            "ML__SERVICE_URL": " http://ml:8080 ",
            "ML__CONNECT_TIMEOUT": "2.5",
            "ML__TIMEOUT": "120",
        }
    )

    snapshot = build_settings()

    assert snapshot.version == "2.5.0"
    assert snapshot.host == "127.0.0.1"
    assert snapshot.port == 9000
    assert snapshot.debug is True
    assert snapshot.init_db is True
    assert snapshot.cache_backend == "redis"
    assert snapshot.cache_ttl_seconds == 45.5
    assert snapshot.storage_backend == "s3"
    assert snapshot.mail_enabled is True
    assert snapshot.broker_url == "redis://cache:6379/1"
    assert snapshot.job_ttl_seconds == 60.0
    # Stripped: a manifest value pasted with a trailing space must not become a URL that
    # fails only when a request is made.
    assert snapshot.ml_service_url == "http://ml:8080"
    assert snapshot.ml_connect_timeout == 2.5
    assert snapshot.ml_read_timeout == 120.0


def test_paths_are_resolved_from_the_package_not_the_working_directory(monkeypatch, tmp_path):
    """A relative DATA_PATH is joined to backend/, and cwd never enters the answer.

    The image runs with WORKDIR /app while the reloader can re-import from a temporary
    directory; a path that followed the working directory would put uploads somewhere the
    /data mount does not see.
    """
    monkeypatch.chdir(tmp_path)

    snapshot = build_settings()

    assert snapshot.backend_root == str(BACKEND_ROOT)
    assert snapshot.logs_path == str(BACKEND_ROOT / "logs")
    assert snapshot.data_path == str(BACKEND_ROOT / "data")


def test_data_path_absolute_value_is_used_verbatim(monkeypatch, tmp_path):
    """An absolute DATA_PATH is not re-rooted under backend/ - only a relative one is."""
    monkeypatch.setenv("DATA_PATH", str(tmp_path))

    assert build_settings().data_path == str(tmp_path)


# --- the two spellings ------------------------------------------------------
# Compose writes CACHE__BACKEND; a developer shell writes CACHE_BACKEND. Both spellings
# are read, and when both are present the double underscore decides - the same rule the
# platform backends implement, and disagreeing here would produce a snapshot that
# describes a different process than the one that runs.
#
# The table names the FIELD each variable feeds, because MAIL__ENABLED is a flag: a test
# that compared its rendered value with the raw string would assert the harness.
def _cache_backend(snapshot):
    return snapshot.cache_backend


def _storage_backend(snapshot):
    return snapshot.storage_backend


def _mail_enabled(snapshot):
    return snapshot.mail_enabled


def _broker_url(snapshot):
    return snapshot.broker_url


#: (double-underscore name, single-underscore name, field reader, value for each spelling,
#: expected value for each spelling). Two DIFFERENT values per row, so "the variable was
#: read at all" and "the right one won" cannot be confused.
SPELLING_CASES = [
    ("CACHE__BACKEND", "CACHE_BACKEND", _cache_backend, "redis", "memory", "redis", "memory"),
    ("STORAGE__BACKEND", "STORAGE_BACKEND", _storage_backend, "s3", "local", "s3", "local"),
    ("MAIL__ENABLED", "MAIL_ENABLED", _mail_enabled, "true", "false", True, False),
    (
        "CELERY__BROKER_URL",
        "CELERY_BROKER_URL",
        _broker_url,
        "redis://double:6379/1",
        "redis://single:6379/1",
        "redis://double:6379/1",
        "redis://single:6379/1",
    ),
]


@pytest.mark.parametrize(
    "double, single, read, double_raw, single_raw, expected, ignored", SPELLING_CASES
)
def test_the_double_underscore_spelling_wins_when_both_are_set(
    monkeypatch, double, single, read, double_raw, single_raw, expected, ignored
):
    monkeypatch.setenv(double, double_raw)
    monkeypatch.setenv(single, single_raw)

    assert read(build_settings()) == expected


@pytest.mark.parametrize(
    "double, single, read, double_raw, single_raw, ignored, expected", SPELLING_CASES
)
def test_the_single_underscore_spelling_is_read_when_the_double_one_is_absent(
    monkeypatch, double, single, read, double_raw, single_raw, ignored, expected
):
    monkeypatch.setenv(single, single_raw)

    assert read(build_settings()) == expected


def test_an_empty_double_underscore_value_shadows_the_single_one(monkeypatch):
    """Presence, not non-emptiness, selects the variable - and that is deliberate.

    compose renders `CACHE__BACKEND: ${CACHE_BACKEND:-}`, so an unset manifest key arrives
    as an empty string in the container while the developer's shell may still carry
    CACHE_BACKEND. Falling through to the second spelling would make one process read two
    different caches depending on where it was started. The ML URL below is the documented
    exception: it strips and falls back to its default because a URL is used as a URL.
    """
    monkeypatch.setenv("CACHE__BACKEND", "")
    monkeypatch.setenv("CACHE_BACKEND", "redis")

    assert build_settings().cache_backend == ""


# --- flags and numbers ------------------------------------------------------
@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_flag_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv("MAIL__ENABLED", raw)

    assert build_settings().mail_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off", "", "  "])
def test_flag_falsy_spellings(monkeypatch, raw):
    monkeypatch.setenv("MAIL__ENABLED", raw)

    assert build_settings().mail_enabled is False


def test_flag_does_not_raise_on_an_unrecognised_value(monkeypatch):
    """REPORTED, NOT FIXED: settings._flag returns False for "maybe" instead of raising.

    The strict TRUE/FALSE guard the manifest documents lives one layer up, in
    service.main._debug_flag, which raises RuntimeError for DEBUG. Here an unknown flag
    silently means "off" - the safe direction for a feature flag, and what the code does
    today, so this test pins the behaviour rather than wishing it different.
    """
    monkeypatch.setenv("MAIL__ENABLED", "maybe")

    assert build_settings().mail_enabled is False


def test_number_falls_back_to_the_default_instead_of_failing_the_boot(monkeypatch):
    monkeypatch.setenv("CACHE__TTL", "not-a-number")
    monkeypatch.setenv("SERVER_PORT", "")

    snapshot = build_settings()

    assert snapshot.cache_ttl_seconds == 300.0
    assert snapshot.port == 8080


def test_debug_accepts_the_manifest_spelling_and_shell_spellings(monkeypatch):
    for raw, expected in (("TRUE", True), ("FALSE", False), ("1", True), ("yes", True)):
        monkeypatch.setenv("DEBUG", raw)
        assert build_settings().debug is expected, raw


def test_an_empty_ml_url_falls_back_to_the_documented_default(monkeypatch):
    monkeypatch.setenv("ML__SERVICE_URL", "   ")

    assert build_settings().ml_service_url == settings_module.DEFAULT_ML_SERVICE_URL


# --- the cache and the copy -------------------------------------------------
def test_get_settings_returns_the_same_object_twice(monkeypatch):
    monkeypatch.setenv("HOST", "10.0.0.1")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.host == "10.0.0.1"


def test_reset_settings_drops_the_cache_and_returns_the_previous_snapshot(monkeypatch):
    """The reset is what lets a test (or a config reload) see a changed variable."""
    monkeypatch.setenv("HOST", "10.0.0.1")
    before = get_settings()

    monkeypatch.setenv("HOST", "10.0.0.2")
    previous = reset_settings()
    after = get_settings()

    assert previous is before
    assert after is not before
    assert after.host == "10.0.0.2"


def test_reset_settings_on_a_cold_cache_returns_none():
    reset_settings()

    assert reset_settings() is None


def test_with_overrides_returns_a_new_snapshot_and_leaves_the_original_alone():
    original = Settings()

    copy = original.with_overrides(debug=True, port=9000)

    assert copy is not original
    assert (copy.debug, copy.port) == (True, 9000)
    assert (original.debug, original.port) == (False, 8080)


def test_the_snapshot_is_frozen():
    """Frozen is the contract: a module that needs a different value builds a copy."""
    snapshot = Settings()

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.port = 9000  # type: ignore[misc]


def test_as_dict_covers_every_field_and_is_a_copy():
    snapshot = Settings()
    data = snapshot.as_dict()

    assert sorted(data) == sorted(snapshot.__dataclass_fields__)
    assert data["port"] == snapshot.port

    data["port"] = 1
    assert snapshot.port == 8080
