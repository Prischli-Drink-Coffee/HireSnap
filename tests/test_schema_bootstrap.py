"""service/infrastructure/database/schema.py: the developer bootstrap that creates the schema.

WHY THIS MODULE NEEDS A TEST AT ALL: it is the only code path in the tree that executes DDL,
and it is the one thing a developer runs once on a fresh checkout. It is also the only place
that opens a connection WITHOUT a database name (so the dump can create one), and the only
place that feeds CSV seed files into MySQL. None of that is reachable from the app, so no
other test exercises it, and a silent regression here looks exactly like a working tree.

WHY THE CONNECTION IS FAKED AT pymysql.connect AND NOT AT A REPOSITORY: the subject is the
order of the statements (create database, use it, then the dump, then the seed rows) and the
error handling around them. A fake below that boundary would not observe either.

WHY THE DUMP ITSELF IS ASSERTED: read_sql() splits the SQL file on ";" with str.split, which
is only correct while the dump contains no semicolon inside a quoted value. That is true
today (measured: 50 statements, every one with balanced single quotes) and this test makes a
future dump that breaks the assumption fail here instead of halfway through a bootstrap.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pymysql
import pytest

from service.infrastructure.database import schema as schema_module
from service.infrastructure.database.schema import CreateSQL

BACKEND_ROOT = schema_module.DATABASE_DIR.parent
DUMP = BACKEND_ROOT / "database" / "HireSnap.sql"


class FakeCursor:
    """A cursor that records what it was asked to run, in the connection's event log."""

    def __init__(self, connection):
        self._connection = connection
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._closed = True
        return False

    def execute(self, statement, params=None):
        if self._connection.fail_on and self._connection.fail_on in statement:
            raise pymysql.MySQLError("statement rejected")
        self._connection.events.append(("execute", statement, params))

    def executemany(self, statement, rows):
        self._connection.events.append(("executemany", statement, list(rows)))


class FakeConnection:
    """Just enough DB-API: a cursor factory, commit, rollback, close."""

    def __init__(self, fail_on=None):
        self.events = []
        self.fail_on = fail_on
        self.closed = False
        self.cursors = []

    def cursor(self):
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        self.events.append(("rollback",))

    def close(self):
        self.closed = True
        self.events.append(("close",))

    # --- views used by the assertions -------------------------------------
    @property
    def statements(self):
        return [event[1] for event in self.events if event[0] == "execute"]

    @property
    def inserts(self):
        return [(event[1], event[2]) for event in self.events if event[0] == "executemany"]


class FakeEnv:
    """Stands in for service.configuration.env.Env: attribute reads, no .env file."""

    def __init__(self, **values):
        self._values = values

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError as exc:  # pragma: no cover - a test asked for a name it forgot
            raise AttributeError(name) from exc


def _env(tmp_path, **overrides):
    values = {
        "DB": "hiresnap",
        "DB_HOST": "db",
        "DB_PORT": "3306",
        "DB_USER": "root",
        "DB_PASSWORD": "secret",
        "DATA_PATH": str(tmp_path),
    }
    values.update(overrides)
    return FakeEnv(**values)


@pytest.fixture()
def connect_calls(monkeypatch):
    """Replace pymysql.connect inside the schema module and expose the recorded calls."""
    calls = []
    connection = FakeConnection()

    def fake_connect(**kwargs):
        calls.append(kwargs)
        return connection

    monkeypatch.setattr(schema_module.pymysql, "connect", fake_connect)
    return calls, connection


class _RecordingLog:
    """Replaces the module logger: caplog is useless here, see _record_warnings()."""

    def __init__(self):
        self.warnings = []
        self.errors = []
        self.infos = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(_render(message, args))

    def error(self, message, *args, **kwargs):
        self.errors.append(_render(message, args))

    def info(self, message, *args, **kwargs):
        self.infos.append(_render(message, args))

    def exception(self, message, *args, **kwargs):
        self.errors.append(_render(message, args))


def _render(message, args):
    try:
        return message % args if args else message
    except (TypeError, ValueError):  # pragma: no cover - a log call with odd args
        return str(message)


def _record_warnings(monkeypatch):
    """Capture log.warning() calls from the schema module.

    WHY NOT caplog: service.configuration.logging.setup_logging() installs a rich handler on
    the package logger and the module-level log is that logger, so records never reach the
    root handler caplog installs. Recording on a stand-in logger keeps the assertion about
    the message the operator would see, not about pytest's plumbing.
    """
    recorder = _RecordingLog()
    monkeypatch.setattr(schema_module, "log", recorder)
    return recorder.warnings


def _build(tmp_path, monkeypatch, **overrides):
    """CreateSQH with a fake env, a fake connection and a controlled seed directory."""
    monkeypatch.setattr(schema_module, "env", _env(tmp_path, **overrides))
    return CreateSQL()


def test_the_constructor_connects_without_a_database_name(tmp_path, monkeypatch, connect_calls):
    """The dump creates the database, so connecting to one first would fail on a fresh host."""
    calls, _connection = connect_calls

    _build(tmp_path, monkeypatch)

    assert len(calls) == 1
    kwargs = calls[0]
    assert "db" not in kwargs and "database" not in kwargs
    assert kwargs["host"] == "db"
    assert kwargs["port"] == 3306  # cast from the string in the env
    assert kwargs["user"] == "root"
    assert kwargs["password"] == "secret"
    assert kwargs["charset"] == "utf8mb4"
    assert kwargs["cursorclass"] is pymysql.cursors.DictCursor


def test_the_dump_is_resolved_from_the_package_not_the_working_directory(
    tmp_path, monkeypatch, connect_calls
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(schema_module, "env", _env(tmp_path))

    bootstrap = CreateSQL()

    # Compared as paths, not strings: on Windows the filesystem is case-insensitive, so
    # schema_file() finds "hiresnap.sql" before "HireSnap.sql" and both name the same file.
    assert Path(bootstrap.path_to_sql) == DUMP
    assert bootstrap.path_to_data == str(tmp_path / "tables")


def test_read_sql_creates_the_database_uses_it_then_applies_the_dump(
    tmp_path, monkeypatch, connect_calls
):
    """Order matters: CREATE DATABASE and USE must precede every statement of the dump."""
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    (tmp_path / "tables").mkdir()
    dump = tmp_path / "dump.sql"
    dump.write_text("CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);\n", encoding="utf-8")
    bootstrap.path_to_sql = str(dump)

    bootstrap.read_sql()

    statements = connection.statements
    assert statements[0] == "CREATE DATABASE IF NOT EXISTS `hiresnap`"
    assert statements[1] == "USE `hiresnap`"
    assert statements[2:] == ["CREATE TABLE a (id INT)", "CREATE TABLE b (id INT)"]
    # A trailing ";" leaves an empty tail which must not be executed as a blank statement.
    assert "" not in statements
    assert connection.events[-2:] == [("commit",), ("close",)]


def test_a_dump_statement_that_fails_does_not_abort_the_rest(tmp_path, monkeypatch, connect_calls):
    """pymysql.MySQLError is caught per statement, so one bad DDL does not stop the bootstrap.

    This is what lets the dump be re-run against a schema that already has half the tables:
    "already exists" is an error on every statement and should not be fatal.
    """
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    (tmp_path / "tables").mkdir()
    dump = tmp_path / "dump.sql"
    dump.write_text("CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);\n", encoding="utf-8")
    bootstrap.path_to_sql = str(dump)
    connection.fail_on = "CREATE TABLE a"

    bootstrap.read_sql()

    assert "CREATE TABLE b (id INT)" in connection.statements


def test_seed_csv_files_are_inserted_after_the_dump_in_one_batch_per_table(
    tmp_path, monkeypatch, connect_calls
):
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    tables = tmp_path / "tables"
    tables.mkdir()
    (tmp_path / "dump.sql").write_text("CREATE TABLE files (id INT);", encoding="utf-8")
    bootstrap.path_to_sql = str(tmp_path / "dump.sql")
    pd.DataFrame({"url": ["a.mp4", "b.mp4"]}).to_csv(tables / "files.csv", index=False)
    # Not in the whitelist: it is a leftover export, not a table this bootstrap owns.
    pd.DataFrame({"x": [1]}).to_csv(tables / "knn.csv", index=False)
    warnings = _record_warnings(monkeypatch)

    bootstrap.read_sql()

    assert len(connection.inserts) == 1
    statement, rows = connection.inserts[0]
    assert statement == "INSERT INTO files (url) VALUES (%s)"
    assert rows == [("a.mp4",), ("b.mp4",)]
    assert any("knn" in message for message in warnings)


def test_a_seed_insert_failure_is_rolled_back_and_does_not_stop_the_other_tables(
    tmp_path, monkeypatch, connect_calls
):
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    tables = tmp_path / "tables"
    tables.mkdir()
    (tmp_path / "dump.sql").write_text("CREATE TABLE files (id INT);", encoding="utf-8")
    bootstrap.path_to_sql = str(tmp_path / "dump.sql")
    pd.DataFrame({"url": ["a.mp4"]}).to_csv(tables / "files.csv", index=False)
    pd.DataFrame({"url": ["u.npy"]}).to_csv(tables / "embeddings.csv", index=False)
    original_executemany = FakeCursor.executemany

    def explode(self, statement, rows):
        if "files" in statement:
            raise RuntimeError("duplicate key")
        return original_executemany(self, statement, rows)

    monkeypatch.setattr(FakeCursor, "executemany", explode)

    bootstrap.read_sql()

    assert ("rollback",) in connection.events
    assert [statement for statement, _rows in connection.inserts] == [
        "INSERT INTO embeddings (url) VALUES (%s)"
    ]


def test_the_connection_is_closed_even_when_the_dump_is_missing(
    tmp_path, monkeypatch, connect_calls
):
    """The finally block is the only thing standing between a bad path and a leaked socket.

    DOCUMENTED, NOT ENDORSED (reported to the Lead): read_sql() swallows the missing-file
    error at schema.py:128, so the caller cannot tell a completed bootstrap from an aborted
    one and the schema is left half-created. This test pins the resource cleanup, which is
    correct, not the silence.
    """
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    (tmp_path / "tables").mkdir()
    bootstrap.path_to_sql = str(tmp_path / "absent.sql")

    bootstrap.read_sql()

    assert connection.closed is True


def test_the_shipped_dump_survives_the_naive_semicolon_split():
    """Guards the DUMP, not the splitter: a ";" inside a quoted value would split a statement.

    read_sql() uses str.split(";"), which cannot tell a terminator from a semicolon in a
    string literal. No SQL parser is worth adding for a developer convenience script, so the
    dump is kept simple instead - and this test is what keeps it simple.
    """
    script = DUMP.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in script.split(";") if stmt.strip()]

    assert len(statements) > 40
    for statement in statements:
        assert statement.count("'") % 2 == 0, f"unbalanced quotes split at: {statement[:80]!r}"
        assert "\\'" not in statement, "a backslash-escaped quote is invisible to the splitter"
    assert ";;" not in script


# --- remove_column_from_csv ------------------------------------------------
def _frame(columns):
    return pd.DataFrame({column: [None, None] for column in columns})


def test_the_three_unnamed_columns_are_dropped_together():
    """The drops are nested, so a file with only one of them keeps all of its columns."""
    frame = _frame(["Unnamed: 0.1", "Unnamed: 0", "Unnamed: 0.2", "url"])

    cleaned = CreateSQL.remove_column_from_csv(frame)

    assert list(cleaned.columns) == ["url"]


@pytest.mark.parametrize(
    "columns",
    [
        ["Unnamed: 0", "url"],
        ["Unnamed: 0.1", "url"],
        ["Unnamed: 0.2", "url"],
        ["Unnamed: 0.1", "Unnamed: 0", "url"],
    ],
)
def test_a_partial_set_of_unnamed_columns_is_left_alone(columns):
    """Measured consequence: an ordinary index column ("Unnamed: 0" alone) is NOT removed.

    pandas only writes the dotted names when a file already had an unnamed index, so a CSV
    exported with a plain index keeps its index column and the INSERT will then be short one
    placeholder. Pinned as-is because changing it would change what the bootstrap writes.
    """
    frame = _frame(columns)

    cleaned = CreateSQL.remove_column_from_csv(frame)

    assert list(cleaned.columns) == columns


def test_missing_nan_values_are_replaced_with_none():
    """pymysql writes NULL for None and the literal string "nan" for a float nan."""
    frame = pd.DataFrame({"url": ["a.mp4", np.nan]})

    cleaned = CreateSQL.remove_column_from_csv(frame)

    assert cleaned["url"].tolist() == ["a.mp4", None]
