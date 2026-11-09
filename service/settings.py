"""One frozen snapshot of the process configuration, read once.

WHY this exists next to `service/configuration/env.py`: the `Env` helper answers one
question at a time ("what is DB_HOST?") and raises an AttributeError when the answer is
absent - right for a value a request needs, wrong for a value that has a sensible
default. A slice that asks for six settings through six `Env` calls also reads the
environment six times, so two answers can disagree if something mutates it in between.

WHY a frozen dataclass rather than pydantic settings: these are process facts (paths,
limits, timeouts), not request payloads. A dataclass imports without pydantic touching
it, it is hashable, and `dataclasses.replace` gives a test a modified copy without a
second code path.

WHY `get_settings()` is cached and `reset_settings()` exists: reading the environment on
every access makes an env change mid-process half-apply - logging keeps the old level
while the cache uses the new timeout. Caching makes the snapshot explicit; the reset
hook lets a test change a variable and see the effect.

The defaults are what docker/.env.example produces with the optional profiles off:
memory cache, local storage, mail disabled, no broker, ML over HTTP on 127.0.0.1:8080.
Nothing here reaches the network at construction time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Optional

from service.configuration.flags import read_flag
from service.configuration.paths import BACKEND_ROOT, data_dir, logs_dir

DEFAULT_CACHE_BACKEND = "memory"
DEFAULT_CACHE_TTL_SECONDS = 300.0
DEFAULT_STORAGE_BACKEND = "local"
DEFAULT_JOB_TTL_SECONDS = 86400.0
DEFAULT_ML_SERVICE_URL = "http://127.0.0.1:8080"
DEFAULT_ML_CONNECT_TIMEOUT = 10.0
DEFAULT_ML_READ_TIMEOUT = 900.0


def _first(*names: str, default: str = "") -> str:
    """First *present* name wins, even when another is defined later.

    Compose translates a manifest key into whichever spelling the backend reads
    (`CACHE__BACKEND` in a container, `CACHE_BACKEND` in a developer shell) and both
    may be exported at once. Presence, not order, decides - the same rule the platform
    backends use, and disagreeing here would make the snapshot describe a different
    process than the one that runs.
    """
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _flag(*names: str, default: bool = False) -> bool:
    """A boolean flag, strict: absent means *default*, set-but-unparseable raises.

    WHY strict here and forgiving for numbers below: a malformed number degrades one
    call, while a malformed boolean silently changes which components exist - the
    process boots, health is green, and mail or the workers are simply absent.
    """
    return read_flag(*names, default=default)


def _number(*names: str, default: float) -> float:
    """A numeric setting; a malformed value falls back instead of failing the boot."""
    raw = _first(*names, default="")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _debug(*names: str) -> bool:
    """DEBUG is TRUE/FALSE in the manifest, and also 1/yes/on for a shell. Absent is False."""
    if not names:
        names = ("DEBUG",)
    return _flag(*names, default=False) or _first(*names, default="").strip().upper() == "TRUE"


@dataclass(frozen=True)
class Settings:
    """Everything the process decides once: paths, limits, backends, timeouts.

    A field is here only if more than one layer needs the same answer. A value one
    module alone reads stays in that module - otherwise this class becomes a second
    environment and nobody knows which one is authoritative.
    """

    # --- identity -----------------------------------------------------------
    service_name: str = "backend"
    version: str = "1.0.0"

    # --- paths --------------------------------------------------------------
    backend_root: str = ""
    data_path: str = ""
    logs_path: str = ""

    # --- HTTP ---------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    init_db: bool = False

    # --- platform backends --------------------------------------------------
    cache_backend: str = DEFAULT_CACHE_BACKEND
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    storage_backend: str = DEFAULT_STORAGE_BACKEND
    mail_enabled: bool = False
    broker_url: str = ""
    job_ttl_seconds: float = DEFAULT_JOB_TTL_SECONDS

    # --- ML sidecar ---------------------------------------------------------
    ml_service_url: str = DEFAULT_ML_SERVICE_URL
    ml_connect_timeout: float = DEFAULT_ML_CONNECT_TIMEOUT
    ml_read_timeout: float = DEFAULT_ML_READ_TIMEOUT

    def as_dict(self) -> dict:
        """The snapshot as plain data, for a log line or a test assertion."""
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def with_overrides(self, **changes) -> "Settings":
        """A modified copy. Used by tests; never mutates the cached instance."""
        return replace(self, **changes)


def build_settings() -> Settings:
    """Read the environment once and freeze the result."""
    return Settings(
        version=_first("SERVICE_VERSION", default="1.0.0") or "1.0.0",
        backend_root=str(BACKEND_ROOT),
        data_path=str(data_dir()),
        logs_path=str(logs_dir()),
        host=_first("HOST", default="0.0.0.0") or "0.0.0.0",
        port=int(_number("SERVER_PORT", default=8080)),
        debug=_debug(),
        init_db=_flag("INIT_DB", default=False),
        cache_backend=_first("CACHE__BACKEND", "CACHE_BACKEND", default=DEFAULT_CACHE_BACKEND),
        cache_ttl_seconds=_number("CACHE__TTL", "CACHE_TTL", default=DEFAULT_CACHE_TTL_SECONDS),
        storage_backend=_first(
            "STORAGE__BACKEND", "STORAGE_BACKEND", default=DEFAULT_STORAGE_BACKEND
        ),
        mail_enabled=_flag("MAIL__ENABLED", "MAIL_ENABLED", default=False),
        broker_url=_first("CELERY__BROKER_URL", "CELERY_BROKER_URL", default=""),
        job_ttl_seconds=_number("JOB__TTL", "JOB_TTL", default=DEFAULT_JOB_TTL_SECONDS),
        ml_service_url=_first(
            "ML__SERVICE_URL", "ML_SERVICE_URL", default=DEFAULT_ML_SERVICE_URL
        ).strip()
        or DEFAULT_ML_SERVICE_URL,
        ml_connect_timeout=_number(
            "ML__CONNECT_TIMEOUT",
            "ML_SERVICE_CONNECT_TIMEOUT",
            "ML_CONNECT_TIMEOUT",
            default=DEFAULT_ML_CONNECT_TIMEOUT,
        ),
        ml_read_timeout=_number(
            "ML__TIMEOUT", "ML_SERVICE_TIMEOUT", "ML_TIMEOUT", default=DEFAULT_ML_READ_TIMEOUT
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide snapshot. Cached, so a caller pays for one read."""
    return build_settings()


def reset_settings() -> Optional[Settings]:
    """Drop the cache; the next `get_settings()` re-reads. Returns the previous value."""
    previous: Optional[Settings] = None
    if get_settings.cache_info().currsize:
        previous = get_settings()
    get_settings.cache_clear()
    return previous
