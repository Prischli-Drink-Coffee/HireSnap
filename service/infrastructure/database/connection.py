"""MySQL access: one connection, opened on FIRST USE, with automatic recovery.

THE ONE BEHAVIOURAL CHANGE IN THIS MODULE, and why it is necessary: the old
`src/database/my_connector.py` opened a PyMySQL connection in `Database.__init__`
and then built a module-level `db = Database()` at the bottom of the file. The
consequence was that importing ANY repository - or anything that imports one -
required a reachable MySQL server at import time. That is why the API could not be
imported by a test, a linter or a build step without a database, and why the
platform layer (cache/storage/mail/celery) had to avoid module-level imports of the
repositories.

`db` is now a lazy proxy with the same three methods (`execute_query`,
`fetch_one`, `fetch_all`) plus the same `Database` class, so every existing
call site (`from service.infrastructure.database.connection import db` and
`db.fetch_all(query, params)`) is unchanged. The connection itself is created the
first time a statement is executed, and a connection that has gone away is
replaced instead of being pinged forever.

Nothing else about the SQL layer changed: same driver, same `utf8mb4` charset, same
`DictCursor` (the repositories index rows by column name), same commit-on-write
semantics.
"""

from __future__ import annotations

import threading
from typing import Any, Optional, Tuple

import pymysql
from pymysql.cursors import DictCursor
from pymysql.err import OperationalError

from service.configuration.env import Env
from service.configuration.logging import get_logger

log = get_logger()

#: Connection settings, read lazily: an import must not require the variables to
#: be present, and tests deliberately run with no environment at all.
_SETTINGS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB")


class Database:
    """A single MySQL connection that is opened on demand and reused.

    `connect()` is the only place that touches the network; the three query
    methods call it through `_ensure_connection()`, which re-connects when the
    previous connection died (MySQL closes idle connections after
    `wait_timeout`, 8 hours by default, and a container that has been idle longer
    than that must not answer its first request with a 500).
    """

    def __init__(self) -> None:
        self._connection: Optional[pymysql.connections.Connection] = None
        self._lock = threading.RLock()

    # -- connection management ------------------------------------------------
    def connect(self) -> pymysql.connections.Connection:
        env = Env()
        return pymysql.connect(
            host=env.__getattr__("DB_HOST"),
            db=env.__getattr__("DB"),
            port=int(env.__getattr__("DB_PORT")),
            user=env.__getattr__("DB_USER"),
            password=env.__getattr__("DB_PASSWORD"),
            charset="utf8mb4",
            cursorclass=DictCursor,
        )

    @property
    def connection(self) -> pymysql.connections.Connection:
        with self._lock:
            self._ensure_connection()
            return self._connection  # type: ignore[return-value]

    def _ensure_connection(self) -> None:
        if self._connection is None:
            self._connection = self.connect()
            return
        try:
            self._connection.ping(reconnect=False)
        except OperationalError:
            # A dead socket cannot be revived with ping(reconnect=True) when the
            # server was restarted: pymysql then re-authenticates against a
            # connection object whose session state is gone. Replacing it is the
            # unambiguous repair.
            try:
                self._connection.close()
            except Exception:  # pragma: no cover - already broken
                pass
            self._connection = self.connect()

    def check_and_reconnect(self) -> None:
        """Kept for backward compatibility with the old call sites."""
        with self._lock:
            self._ensure_connection()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                finally:
                    self._connection = None

    # -- queries --------------------------------------------------------------
    def execute_query(self, query: str, params: Optional[Tuple[Any, ...]] = None):
        with self._lock:
            self._ensure_connection()
            cursor = self._connection.cursor()  # type: ignore[union-attr]
            cursor.execute(query, params)
            self._connection.commit()  # type: ignore[union-attr]
            return cursor

    def fetch_one(self, query: str, params: Optional[Tuple[Any, ...]] = None):
        with self._lock:
            self._ensure_connection()
            with self._connection.cursor() as cursor:  # type: ignore[union-attr]
                cursor.execute(query, params)
                return cursor.fetchone()

    def fetch_all(self, query: str, params: Optional[Tuple[Any, ...]] = None):
        with self._lock:
            self._ensure_connection()
            with self._connection.cursor() as cursor:  # type: ignore[union-attr]
                cursor.execute(query, params)
                return cursor.fetchall()


#: The process-wide handle. Same object identity and same methods as before, but
#: constructing it no longer opens a socket.
db = Database()


def get_db() -> Database:
    """Explicit accessor for code that prefers a function over a module global."""
    return db
