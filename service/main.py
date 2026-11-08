"""Entry point: build the app, optionally bootstrap the schema, then serve.

Run it as a module - `python -m service.main` - from the backend directory (or from
/app inside the image, where PYTHONPATH=/app makes `service` importable). The old
entry point was `python src/pipeline/server.py`, which worked only because Python
put the SCRIPT's directory on sys.path; `from env import Env` then resolved against
the working directory. Nothing here relies on the working directory.

The two responsibilities of the old `__main__` block are preserved:

  1. DEBUG=TRUE loads the SQL dump and the CSV seed tables (service/infrastructure/
     database/schema.py). This is the developer convenience that lets a fresh
     checkout come up with a populated database. In the container the schema is
     normally loaded by infra/mysql/init/00-init.sh on an empty data directory, and
     `INIT_DB` gates a second attempt.
  2. uvicorn is started with the YAML log config and with `--reload` only when
     DEBUG=TRUE. The reload target is an ABSOLUTE module path
     (`service.api.app:app`): the old code passed `"server:app"`, which the
     reloader resolved by searching the current directory - a value that stops
     working the moment the process is not started from that directory.
"""

from __future__ import annotations

import logging
from typing import Optional

import uvicorn
import yaml

from service.configuration.env import Env
from service.configuration.logging import get_logger
from service.configuration.paths import LOGGING_YAML
from service.shared.debug import debug_info

log = get_logger()


def _debug_flag(env: Env) -> bool:
    """DEBUG must be exactly TRUE or FALSE; anything else is a configuration bug."""
    value = env.__getattr__("DEBUG")
    if value == "TRUE":
        return True
    if value == "FALSE":
        return False
    raise RuntimeError(f"DEBUG must be TRUE or FALSE, got {value!r}")


def bootstrap_schema(env: Env) -> None:
    """Load the SQL dump and the CSV seed rows. Only meaningful in DEBUG mode."""
    from service.infrastructure.database.schema import CreateSQL

    create_sql = CreateSQL()
    create_sql.read_sql()


def run_server() -> None:
    env = Env()
    with open(LOGGING_YAML, "r", encoding="utf-8") as handle:
        logging.config.dictConfig(yaml.safe_load(handle.read()))
    debug = _debug_flag(env)
    uvicorn.run(
        "service.api.app:app",
        host=env.__getattr__("HOST"),
        port=int(env.__getattr__("SERVER_PORT")),
        log_config=str(LOGGING_YAML),
        reload=debug,
    )


def main(argv: Optional[list] = None) -> int:
    env = Env()
    debug_info("Start create/update database")
    if _debug_flag(env) and os_env_flag("INIT_DB"):
        bootstrap_schema(env)
    log.info("Start run server")
    run_server()
    return 0


def os_env_flag(name: str) -> bool:
    """True when the variable is set to a truthy value (`1`, `true`, `yes`)."""
    import os

    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    raise SystemExit(main())
