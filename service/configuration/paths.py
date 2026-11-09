"""Every filesystem path the service uses, resolved from `__file__`.

WHY this module exists: the old layout computed paths in four different
`__init__.py` files (`src/`, `src/pipeline/`, `src/utils/`, `src/repository/`
each defined its own `path_to_project`) and two of them also mutated `sys.path` on
import. A path helper that edits the import path is how a project ends up unable to
say where it is. Here paths are pure functions of `__file__`, they never touch
`sys.path`, and they are correct regardless of the working directory - which
matters because the image runs with WORKDIR /app while uvicorn's reloader can
re-import this module from a temporary directory.

Container layout this assumes (backend/docker/Dockerfile):
    /app/service/configuration/paths.py   this file
    /app/service/...                      the package
    /app/database/HireSnap.sql            the schema dump
    /app/data                             DATA_PATH bind mount
    /app/logs                             log bind mount
"""

from __future__ import annotations

import os
from pathlib import Path

# service/configuration/paths.py -> service/configuration -> service -> backend root
SERVICE_DIR: Path = Path(__file__).resolve().parent.parent
BACKEND_ROOT: Path = SERVICE_DIR.parent

#: Where the YAML that configures the process lives. Inside the package on
#: purpose: it is code, it is versioned with the code, and the image COPYs the
#: package, so there is no second thing to mount.
#:
#: Only logging.yaml exists. The old tree also carried src/config.yaml, read by a
#: ConfigParser helper that decoded latin1 and re-decoded utf-8 to survive Cyrillic
#: values; both the file and the helper were dead (nothing imported the helper) and
#: were deleted in the same move. Do not add a CONFIG_YAML constant back for a file
#: that is not there - a path constant nobody reads is a claim nobody checks.
CONFIGURATION_DIR: Path = SERVICE_DIR / "configuration"
LOGGING_YAML: Path = CONFIGURATION_DIR / "logging.yaml"

#: The RS256 key pair: the BARE-CHECKOUT fallback, not the deployed location.
#: In a container the pair is mounted at /app/secrets/certs and both compose files
#: set JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH, which service/domain/models.py
#: reads FIRST; this constant is only what a developer’s checkout uses when
#: those variables are unset. Keep it a constant because a key path that is
#: computed from a variable at import time is a key path that can silently point
#: at nothing.
CERTS_DIR: Path = SERVICE_DIR / "certs"

#: The schema dump the DEBUG-mode bootstrap loads.
DATABASE_DIR: Path = BACKEND_ROOT / "database"


def env_file() -> Path:
    """The dotenv file for a bare-metal run (never present in the image)."""
    return BACKEND_ROOT / ".env"


def data_dir() -> Path:
    """Uploads and embeddings.

    Read from `DATA_PATH` because the deployment mounts a host directory there
    and both the application and the ml sidecar are told the same absolute path.
    A relative value is resolved against the backend root so that a developer who
    writes `DATA_PATH=data` gets `backend/data` and not `<cwd>/data`.
    """
    raw = os.environ.get("DATA_PATH") or "data"
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = BACKEND_ROOT / candidate
    return candidate


def logs_dir() -> Path:
    """Directory the logging config writes into."""
    return BACKEND_ROOT / "logs"


def log_file() -> Path:
    """The one log file: the bind-mounted /app/logs/app.log in the image.

    Absolute on purpose. The YAML names the handler's file relatively
    (`logs/app.log`), which only resolves when the process happens to run from a
    directory that has a `logs/` beside it - true inside the image because WORKDIR
    is /app, false for the reloader's child, false for a developer who started the
    server from anywhere else, and false for every test. `load_logging_config()`
    in service/configuration/logging.py rewrites the relative name through this
    function, so the file the process writes is decided here and nowhere else.
    """
    return logs_dir() / "app.log"


def schema_file(database_name: str) -> Path:
    """The SQL dump for a database name.

    Kept for the DEBUG-mode loader (service/infrastructure/database/schema.py),
    which used to resolve `<DB>.sql` next to itself. It now resolves it in
    `backend/database/` and accepts the historical mixed-case name: the dump in
    this repository is `HireSnap.sql` while the default database name is
    `hiresnap`, and a case-sensitive filesystem (the Linux container) would
    otherwise look for a file that does not exist. The candidate list is tried in
    order and the first existing file wins.
    """
    candidates = [
        DATABASE_DIR / f"{database_name}.sql",
        DATABASE_DIR / "HireSnap.sql",
        DATABASE_DIR / "hiresnap.sql",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]
