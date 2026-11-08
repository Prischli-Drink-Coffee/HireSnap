"""service/infrastructure/database/connection.py: the lazy, self-healing MySQL handle.

WHY THE LAZINESS IS THE TEST: the old module opened a socket in Database.__init__ and built a
module-level instance, so importing any repository required a live MySQL - that is why the app
could not be imported by a test or a build step. The property that replaced it ("constructing
or importing opens nothing") is invisible at runtime and easy to lose in a refactor, so it is
asserted explicitly rather than assumed.

WHY THE RECOVERY PATH MATTERS: MySQL closes idle connections after wait_timeout (8h by
default). A container that sits overnight must answer its first request the next morning, and
the difference between reconnecting and re-pinging a dead socket is one exception class.

WHY pymysql.connect IS THE SEAM: it is the module boundary this class owns. A fake below it
(a fake cursor) would let a broken connect() through; a fake above it would not exercise the
reconnect logic at all.
"""

from __future__ import annotations

import threading

import pymysql
import pytest
from pymysql.err import OperationalError

from service.infrastructure.database import connection as connection_module
from service.infrastructure.database.connection import Database, db, get_db


class FakeCursor:
    """A cursor that remembers the statement and answers with the configured rows."""

    def __init__(self, connection):
        self._connection = connection
        self.statement = None
        self.params = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False

    def execute(self, statement, params=None):
        self.statement = statement
        self.params = params
        self._connection.executed.append((statement, params))
        if self._connection.raises is not None:
            raise self._connection.raises

    def fetchone(self):
        return self._connection.one

    def fetchall(self):
        return self._connection.all


class FakeConnection:
    """A DB-API stand-in whose ping() can be told to fail, like a closed socket."""

    def __init__(self, name="conn", one=None, all=None, raises=None):
        self.name = name
        self.one = one
        self.all = all if all is not None else []
        self.raises = raises
        self.executed = []
        self.commits = 0
        self.closed = False
        self.pinged = 0
        self.ping_error = None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True

    def ping(self, reconnect=False):
        self.pinged += 1
        if self.ping_error is not None:
            raise self.ping_error


class FakeEnv:
    """A stand-in for service.configuration.env.Env with the database settings present."""

    def __init__(self, **values):
        self.values = {
            "DB_HOST": "db-host",
            "DB_PORT": "3307",
            "DB_USER": "app",
            "DB_PASSWORD": "secret",
            "DB": "hiresnap",
        }
        self.values.update(values)

    def __getattr__(self, name):
        try:
            return self.values[name]
        except KeyError as exc:  # pragma: no cover - a test asked for an undefined key
            raise AttributeError(name) from exc


@pytest.fixture()
def connector(monkeypatch):
    """Replace pymysql.connect inside the module; hand out connections one per call.

    Also replaces Env, because connect() reads the settings through it - a test that relied
    on the developer's .env would pass on one machine and fail on another.
    """
    made = []
    queued = []
    state = {"made": made, "queued": queued}

    def fake_connect(**kwargs):
        made.append(kwargs)
        return queued.pop(0) if queued else FakeConnection(name=f"conn{len(made)}")

    monkeypatch.setattr(connection_module.pymysql, "connect", fake_connect)
    monkeypatch.setattr(connection_module, "Env", lambda: FakeEnv())
    return state


def test_constructing_the_handle_opens_nothing():
    """The whole point of the rewrite: no socket until a statement is executed."""
    handle = Database()

    assert handle._connection is None
    assert isinstance(handle._lock, type(threading.RLock()))


def test_the_module_level_handle_is_constructed_but_disconnected():
    assert isinstance(db, Database)
    assert get_db() is db


def test_the_first_query_connects_with_the_documented_settings(monkeypatch, connector):
    handle = Database()

    handle.fetch_all("SELECT 1")

    assert connector["made"] == [
        {
            "host": "db-host",
            "db": "hiresnap",
            "port": 3307,
            "user": "app",
            "password": "secret",
            "charset": "utf8mb4",
            "cursorclass": connection_module.DictCursor,
        }
    ]


def test_the_connection_is_reused_across_queries(monkeypatch, connector):
    """One connection per process, not one per request: the pool is MySQL's job, not ours."""
    handle = Database()

    for _ in range(3):
        handle.fetch_all("SELECT 1")

    assert len(connector["made"]) == 1
    assert handle._connection.pinged == 2  # pinged before every query after the first


def test_a_dead_connection_is_replaced_instead_of_pinged_forever(monkeypatch, connector):
    """The 8-hour idle case: ping raises OperationalError, so a NEW connection is opened."""
    dead = FakeConnection(name="dead")
    dead.ping_error = OperationalError(2006, "MySQL server has gone away")
    connector["queued"].append(dead)
    alive = FakeConnection(name="alive")
    connector["queued"].append(alive)
    handle = Database()

    handle.fetch_all("SELECT 1")
    handle.fetch_all("SELECT 1")

    assert len(connector["made"]) == 2
    assert dead.closed is True  # the broken socket is closed, not leaked
    assert handle._connection is alive


def test_a_failure_to_close_the_dead_connection_still_reconnects(monkeypatch, connector):
    """The inner close() is best-effort; a raise from it must not mask the reconnect."""
    dead = FakeConnection(name="dead")
    dead.ping_error = OperationalError(2006, "gone")

    def refuse():
        raise RuntimeError("socket already broken")

    dead.close = refuse
    connector["queued"].append(dead)
    alive = FakeConnection(name="alive")
    connector["queued"].append(alive)
    handle = Database()

    handle.fetch_all("SELECT 1")
    handle.fetch_all("SELECT 1")

    assert handle._connection is alive


def test_execute_query_commits_and_returns_the_cursor(monkeypatch, connector):
    """Callers read lastrowid off the returned cursor, so the commit must happen first."""
    handle = Database()

    cursor = handle.execute_query("INSERT INTO users (email) VALUES (%s)", ("a@b.c",))

    connection = handle._connection
    assert connection.commits == 1
    assert cursor.statement == "INSERT INTO users (email) VALUES (%s)"
    assert cursor.params == ("a@b.c",)
    # Not a context manager: the cursor is handed back still open, exactly as before.
    assert cursor.closed is False


def test_fetch_one_and_fetch_all_close_their_cursor(monkeypatch, connector):
    handle = Database()
    handle._connection = FakeConnection(name="ready", one={"id": 1}, all=[{"id": 1}, {"id": 2}])

    assert handle.fetch_one("SELECT 1") == {"id": 1}
    assert handle.fetch_all("SELECT 1") == [{"id": 1}, {"id": 2}]


def test_a_query_error_propagates_and_leaves_the_connection_usable(monkeypatch, connector):
    """The transaction is not committed on failure, and the handle still works afterwards."""
    handle = Database()
    connection = FakeConnection(name="ready")
    connection.raises = pymysql.err.ProgrammingError(1064, "bad SQL")
    handle._connection = connection

    with pytest.raises(pymysql.err.ProgrammingError):
        handle.fetch_all("SELECT nope")

    assert connection.commits == 0

    connection.raises = None
    handle.fetch_all("SELECT 1")


def test_close_is_idempotent_and_forgets_the_connection(monkeypatch, connector):
    handle = Database()
    connection = FakeConnection(name="ready")
    handle._connection = connection

    handle.close()
    handle.close()

    assert connection.closed is True
    assert handle._connection is None


def test_check_and_reconnect_is_the_documented_alias(monkeypatch, connector):
    """The old call sites use this name; it must open a connection like a query would."""
    handle = Database()

    handle.check_and_reconnect()

    assert handle._connection is not None
    assert len(connector["made"]) == 1


def test_the_handle_is_usable_from_several_threads(monkeypatch, connector):
    """The lock is what keeps two request threads off one cursor."""
    handle = Database()
    errors = []

    def worker():
        try:
            for _ in range(20):
                handle.fetch_all("SELECT 1")
        except Exception as exc:  # pragma: no cover - only on a real race
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(connector["made"]) == 1


def test_connect_reads_the_environment_through_env_not_module_constants(monkeypatch):
    """A changed variable must be visible on the NEXT connect (a container restart, a test)."""
    seen = []

    env = FakeEnv(DB_HOST="first")
    monkeypatch.setattr(connection_module, "Env", lambda: env)
    monkeypatch.setattr(
        connection_module.pymysql,
        "connect",
        lambda **kwargs: seen.append(kwargs) or FakeConnection(),
    )

    Database().connect()
    env.values["DB_HOST"] = "second"
    Database().connect()

    assert [call["host"] for call in seen] == ["first", "second"]
