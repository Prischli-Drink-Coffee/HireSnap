"""service/infrastructure/database/schema.py: the developer bootstrap that creates the schema.

WHY THIS MODULE NEEDS A TEST AT ALL: it is the only code path in the tree that executes DDL,
and it is the one thing a developer runs once on a fresh checkout. It is also the only place
that opens a connection WITHOUT a database name (so the dump can create one), and the only
place that feeds CSV seed files into MySQL. None of that is reachable from the app, so no
other test exercises it, and a silent regression here looks exactly like a working tree.

WHY THE CONNECTION IS FAKED AT pymysql.connect AND NOT AT A REPOSITORY: the subject is the
order of the statements (create database, use it, then the dump, then the seed rows) and the
error handling around them. A fake below that boundary would not observe either.

WHY THE SPLITTER IS TESTED DIRECTLY (docs/KNOWN_GAPS.md section 33): the dump used to be cut
on every ";" with str.split, which cannot tell a terminator from a semicolon inside a quoted
value. That was safe only because of what the dump happens to contain. split_statements()
now tracks the quote and comment states instead, and the tests below pin both halves: the
naive split would break these inputs, and the real dump still yields the statements it did.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pymysql
import pytest

from service.infrastructure.database import schema as schema_module
from service.infrastructure.database.schema import CreateSQL, SchemaBootstrapError

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

    def begin(self):
        self.events.append(("begin",))

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


def test_one_failing_seed_table_rolls_back_the_whole_seed_phase_and_raises(
    tmp_path, monkeypatch, connect_calls
):
    """A half-loaded seed is worse than none: the phase is one transaction and it aborts.

    The failure must also be LOUD. read_sql() used to log the insert error and carry on to
    the next table, which left the schema with rows in some tables and none in the rest, and
    returned as if the bootstrap had succeeded.
    """
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    tables = tmp_path / "tables"
    tables.mkdir()
    (tmp_path / "dump.sql").write_text("CREATE TABLE files (id INT);", encoding="utf-8")
    bootstrap.path_to_sql = str(tmp_path / "dump.sql")
    pd.DataFrame({"url": ["u.npy"]}).to_csv(tables / "embeddings.csv", index=False)
    pd.DataFrame({"url": ["a.mp4"]}).to_csv(tables / "files.csv", index=False)
    original_executemany = FakeCursor.executemany

    def explode(self, statement, rows):
        if "files" in statement:
            raise RuntimeError("duplicate key")
        return original_executemany(self, statement, rows)

    monkeypatch.setattr(FakeCursor, "executemany", explode)

    with pytest.raises(SchemaBootstrapError) as caught:
        bootstrap.read_sql()

    assert caught.value.step == "loading the seed rows"
    assert caught.value.detail.startswith("table files failed")
    assert ("begin",) in connection.events
    assert ("rollback",) in connection.events
    # Rolled back, not committed: the first table had been written before the second failed.
    assert ("commit",) not in connection.events
    assert connection.closed is True


def test_the_connection_is_closed_and_nothing_is_written_when_the_dump_is_missing(
    tmp_path, monkeypatch, connect_calls
):
    """The finally block is the only thing between a bad path and a leaked socket; the ORDER
    of the phases is what keeps the server untouched.

    This is defect D3 (docs/KNOWN_GAPS.md section 32) in one test: the dump is resolved and
    read BEFORE CREATE DATABASE, so a typo in the path cannot leave an empty database behind
    and a warning in the log. The old code did the opposite on both counts.
    """
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    (tmp_path / "tables").mkdir()
    bootstrap.path_to_sql = str(tmp_path / "absent.sql")

    with pytest.raises(SchemaBootstrapError) as caught:
        bootstrap.read_sql()

    assert caught.value.step == "opening the schema dump"
    assert "absent.sql" in caught.value.detail
    assert connection.statements == []
    assert not any(event[0] == "execute" for event in connection.events)
    assert ("commit",) not in connection.events
    assert connection.closed is True


def test_a_dump_that_holds_no_statement_raises_before_touching_the_server(
    tmp_path, monkeypatch, connect_calls
):
    """An empty dump is not an empty schema to re-run: it is a checkout missing its file."""
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    (tmp_path / "tables").mkdir()
    (tmp_path / "empty.sql").write_text(";\n;\n  \n", encoding="utf-8")
    bootstrap.path_to_sql = str(tmp_path / "empty.sql")

    with pytest.raises(SchemaBootstrapError) as caught:
        bootstrap.read_sql()

    assert caught.value.step == "reading the schema dump"
    assert "holds no SQL statement" in caught.value.detail
    assert connection.statements == []
    assert connection.closed is True


def test_a_rejected_create_database_raises_instead_of_being_logged(
    tmp_path, monkeypatch, connect_calls
):
    """The database is what the rest of the bootstrap assumes; no statement may survive it."""
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    (tmp_path / "tables").mkdir()
    (tmp_path / "dump.sql").write_text("CREATE TABLE a (id INT);", encoding="utf-8")
    bootstrap.path_to_sql = str(tmp_path / "dump.sql")
    connection.fail_on = "CREATE DATABASE"

    with pytest.raises(SchemaBootstrapError) as caught:
        bootstrap.read_sql()

    assert caught.value.step == "creating the database"
    assert "CREATE TABLE a (id INT)" not in connection.statements
    assert connection.closed is True


def test_a_lost_connection_mid_dump_raises_with_the_statement_index(
    tmp_path, monkeypatch, connect_calls
):
    """A rejected statement is skipped; a DEAD connection is not.

    MySQL error 2006 is the server not being there any more. Continuing would execute every
    remaining statement against a socket that cannot answer, and the bootstrap would return
    having applied part of the dump - the exact shape this refactor removes.
    """
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    (tmp_path / "tables").mkdir()
    (tmp_path / "dump.sql").write_text(
        "CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);\n", encoding="utf-8"
    )
    bootstrap.path_to_sql = str(tmp_path / "dump.sql")
    original_execute = FakeCursor.execute

    def gone_away(self, statement, params=None):
        if "CREATE TABLE b" in statement:
            raise pymysql.MySQLError(2006, "MySQL server has gone away")
        return original_execute(self, statement, params)

    monkeypatch.setattr(FakeCursor, "execute", gone_away)

    with pytest.raises(SchemaBootstrapError) as caught:
        bootstrap.read_sql()

    assert caught.value.step == "applying the schema dump"
    # 1-based over the DUMP, not over everything the bootstrap runs: CREATE DATABASE and
    # USE are bootstrap steps, not statements of the file, so the second CREATE TABLE is 2.
    assert caught.value.statement_index == 2
    assert "the connection died" in caught.value.detail
    assert connection.closed is True


def test_the_transaction_is_begun_once_and_committed_once_on_success(
    tmp_path, monkeypatch, connect_calls
):
    """One commit, after the seed phase, and not one per table.

    A commit per table would make the phase exactly as atomic as the old code: a failure in
    the third table leaves the first two in the database for good.
    """
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    tables = tmp_path / "tables"
    tables.mkdir()
    (tmp_path / "dump.sql").write_text("CREATE TABLE files (id INT);", encoding="utf-8")
    bootstrap.path_to_sql = str(tmp_path / "dump.sql")
    pd.DataFrame({"url": ["a.mp4"]}).to_csv(tables / "files.csv", index=False)
    pd.DataFrame({"url": ["u.npy"]}).to_csv(tables / "embeddings.csv", index=False)

    bootstrap.read_sql()

    assert [event[0] for event in connection.events].count("begin") == 1
    assert [event[0] for event in connection.events].count("commit") == 1
    assert ("rollback",) not in connection.events
    assert len(connection.inserts) == 2
    assert ("commit",) in connection.events
    assert connection.closed is True


def test_a_seed_file_that_cannot_be_parsed_raises_before_the_dump_runs(
    tmp_path, monkeypatch, connect_calls
):
    """The seed files are read in the same pre-flight as the dump, for the same reason."""
    _calls, connection = connect_calls
    bootstrap = _build(tmp_path, monkeypatch)
    tables = tmp_path / "tables"
    tables.mkdir()
    (tmp_path / "dump.sql").write_text("CREATE TABLE files (id INT);", encoding="utf-8")
    bootstrap.path_to_sql = str(tmp_path / "dump.sql")
    (tables / "files.csv").write_bytes(b"\xff\xfe\x00 not a csv")

    with pytest.raises(SchemaBootstrapError) as caught:
        bootstrap.read_sql()

    assert caught.value.step == "reading a seed file"
    assert connection.statements == []
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


# --- split_statements: the quote-aware splitter ------------------------------
@pytest.mark.parametrize(
    "script, expected",
    [
        ("SELECT 1; SELECT 2;", ["SELECT 1", "SELECT 2"]),
        ("SELECT 'a;b'; SELECT 2;", ["SELECT 'a;b'", "SELECT 2"]),
        ('INSERT INTO t VALUES ("x;y");', ['INSERT INTO t VALUES ("x;y")']),
        ("SELECT `weird;name` FROM t;", ["SELECT `weird;name` FROM t"]),
        ("SELECT 'it''s; fine';", ["SELECT 'it''s; fine'"]),
        ("SELECT 'a\\';b'; SELECT 2;", ["SELECT 'a\\';b'", "SELECT 2"]),
        ("/* a; b */ SELECT 1;", ["/* a; b */ SELECT 1"]),
        ("-- a; b\nSELECT 1;", ["-- a; b\nSELECT 1"]),
        ("# a; b\nSELECT 1;", ["# a; b\nSELECT 1"]),
        ("SELECT 1", ["SELECT 1"]),
        ("   ;;  ;  ", []),
        ("", []),
    ],
)
def test_split_statements_ignores_a_semicolon_that_does_not_end_a_statement(script, expected):
    """The cases the old str.split got wrong, each one a silent half-statement."""
    assert schema_module.split_statements(script) == expected


def test_the_naive_split_would_have_broken_the_case_above():
    """Pins WHY the splitter exists: on the same input, str.split disagrees.

    Without this, the test above proves only that the new function is self-consistent -
    and a splitter that returned the whole script as one statement would pass a weaker
    version of it.
    """
    script = "INSERT INTO t VALUES ('open; part time');"

    assert schema_module.split_statements(script) == ["INSERT INTO t VALUES ('open; part time')"]
    assert script.split(";") != schema_module.split_statements(script)


def test_the_shipped_dump_splits_into_the_same_statements_as_before():
    """The real file must not change meaning under the new splitter."""
    script = DUMP.read_text(encoding="utf-8")

    aware = schema_module.split_statements(script)
    naive = [stmt.strip() for stmt in script.split(";") if stmt.strip()]

    assert len(aware) == len(naive) >= 40
    for index, (left, right) in enumerate(zip(aware, naive)):
        assert left == right, f"statement {index} differs: the dump holds a quoted semicolon"


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
