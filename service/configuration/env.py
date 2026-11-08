"""Environment access, mirroring the old `backend/env.py` class `Env`.

Kept as a class with the same `__getattr__` contract because eight modules and
every deployment script already depend on it: a missing variable raises
`AttributeError` after logging, rather than silently returning `None` and failing
three layers deeper with "NoneType has no attribute ...".

Two defects of the original are fixed here, both of which only ever bit the
developer workflow:

  1. `__setattr__`/`__delattr__` wrote to the file name `.env` RELATIVE TO THE
     WORKING DIRECTORY, so running any command from anywhere but backend/ created
     a second, empty .env there. They now write the resolved dotenv path.
  2. The dotenv file is loaded from a path helper instead of
     `os.path.dirname(__file__)`, which after the move would have looked for
     `service/configuration/.env`.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv, set_key, unset_key

from service.configuration.logging import setup_logging
from service.configuration.paths import env_file

log = setup_logging()


class Env:
    """Environment variables with explicit failure on a missing key."""

    def __init__(self) -> None:
        load_dotenv(env_file())

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        env_vars = {k: v for k, v in os.environ.items() if not k.startswith("_")}
        log.info(env_vars)
        return str(env_vars)

    def __getattr__(self, attr: str) -> str:
        value = os.environ.get(attr)
        if value is None:
            ex = AttributeError(f"Attribute {attr} not found in environment variables")
            log.exception(ex)
            raise ex
        return value

    def __setattr__(self, attr: str, value: str) -> None:
        if not isinstance(value, str):
            ex = TypeError(f"Cannot set non-string value as environment variable: {attr}")
            log.exception(ex)
            raise ex
        os.environ[attr] = value
        set_key(str(env_file()), attr, value)

    def __delattr__(self, attr: str) -> None:
        if attr in os.environ:
            unset_key(str(env_file()), attr)
            del os.environ[attr]
        else:
            ex = AttributeError(f"Attribute {attr} not found in environment variables")
            log.exception(ex)
            raise ex


if __name__ == "__main__":  # pragma: no cover
    print(Env())
