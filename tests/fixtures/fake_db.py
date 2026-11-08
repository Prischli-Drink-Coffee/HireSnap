"""Doubles for the persistence boundary: a fake MySQL handle and a fake repository.

WHY A FAKE DATABASE AND NOT A MOCKED REPOSITORY
-----------------------------------------------
The repository modules are the only place that knows the SQL, and SQL is exactly the
part of this tree that no other test touches: the routes are pinned by the snapshot
test, the rules by the application tests, and the statements by nothing. Mocking the
repository module (monkeypatching get_all_users) would prove that the service calls
*some* function and would leave every %s placeholder unexecuted - the shape of a test
that passes while a column name is wrong. So FakeDatabase sits one level lower, where
the db handle is, and answers the statement shapes the repositories actually issue.
The real repository code runs.

WHY IT PARSES THE SHAPE INSTEAD OF COMPARING WHOLE STATEMENTS
------------------------------------------------------------
A fake holding one expected literal per call would need updating in the same commit
as the repository, which is a second copy of the SQL rather than a check on it.
Parsing the shape (SELECT * FROM t [WHERE c=%s], INSERT INTO t (cols) VALUES (%s...),
UPDATE t SET c1=%s WHERE id=%s, DELETE FROM t WHERE id=%s) keeps the assertion about
the table and the columns, and raises UnsupportedStatement - loudly, and naming the
SQL - the day a repository issues something this fake never learned, instead of
quietly returning an empty list.

WHY THE PARAMETER BINDING IS NORMALISED
---------------------------------------
create_file and create_embedding pass a BARE STRING where the driver wants a
sequence (params = file.Url). PyMySQL accepts a str as a one-column parameter list,
so the statement works against a real server while looking wrong. FakeDatabase
normalises the same way (str/bytes become a one-element tuple, None an empty tuple)
and records the normalised tuple, so a test asserts the EFFECTIVE binding without
pretending the quirk is not there.

WHAT IS NOT FAKED: no engine, no type coercion, no foreign keys, no transactions. A
row is a dict, an insert appends to a list, an id is max+1. That is enough for the
three properties the repositories are trusted with: which table, which columns in
which order, and which values as which tuple.
"""

import importlib
import pkgutil

import pytest


class UnsupportedStatement(AssertionError):
    """A repository issued SQL this fake does not model.

    It subclasses AssertionError so the test FAILS rather than errors, and the message
    quotes the statement: the fix is either "teach the fake this shape" or "this
    repository gained a statement nobody is checking".
    """


def _bind(params):
    """The parameter list as PyMySQL would see it, as a tuple.

    A bare string is ONE value, not a sequence of characters - which is the point:
    create_file passes file.Url and the driver binds it to a single placeholder.
    """
    if params is None:
        return ()
    if isinstance(params, (str, bytes)):
        return (params,)
    if isinstance(params, (list, tuple)):
        return tuple(params)
    return (params,)


def _split_where(rest):
    """Split "table WHERE column=%s" into (table, column) or (table, None)."""
    marker = " WHERE "
    if marker not in rest:
        return rest, None
    table, clause = rest.split(marker, 1)
    if not clause.endswith("=%s"):
        raise UnsupportedStatement("unsupported WHERE clause: %r" % clause)
    return table, clause[: -len("=%s")]


def _parse_insert(query):
    """(table, column names) for an INSERT, or None when this is not a plain INSERT."""
    prefix = "INSERT INTO "
    if not query.startswith(prefix):
        return None
    rest = query[len(prefix) :]
    if " (" not in rest:
        raise UnsupportedStatement("INSERT without a column list: %r" % query)
    table, rest = rest.split(" (", 1)
    if ") VALUES (" not in rest:
        raise UnsupportedStatement("INSERT without a VALUES list: %r" % query)
    columns, values = rest.split(") VALUES (", 1)
    if not values.endswith(")"):
        raise UnsupportedStatement("INSERT with a malformed VALUES list: %r" % query)
    placeholders = [part.strip() for part in values[:-1].split(",")]
    if any(placeholder != "%s" for placeholder in placeholders):
        raise UnsupportedStatement("INSERT with a non-%s placeholder: %r" % query)
    names = [name.strip() for name in columns.split(",")]
    if len(names) != len(placeholders):
        raise UnsupportedStatement("INSERT columns and values differ in length: %r" % query)
    return table, names


def _parse_update(query):
    """(table, column names, key column) for an UPDATE, or None."""
    prefix = "UPDATE "
    if not query.startswith(prefix):
        return None
    rest = query[len(prefix) :]
    if " SET " not in rest or " WHERE " not in rest:
        raise UnsupportedStatement("UPDATE without SET/WHERE: %r" % query)
    table, rest = rest.split(" SET ", 1)
    assignments, where = rest.split(" WHERE ", 1)
    if not where.endswith("=%s"):
        raise UnsupportedStatement("UPDATE with a malformed WHERE: %r" % query)
    key = where[: -len("=%s")]
    names = [pair.split("=")[0].strip() for pair in assignments.split(",")]
    return table, names, key


class FakeCursor(object):
    """The subset of a DictCursor the repositories read: lastrowid and rowcount."""

    def __init__(self, lastrowid=None, rowcount=0):
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def __iter__(self):
        return iter(())


class FakeDatabase(object):
    """An in-memory stand-in for service.infrastructure.database.connection.db.

    tables maps a table name to the list of rows it holds, so a test declares the
    state it wants to read (fake_db.tables["users"] = [{...}]) and inspects what the
    repository wrote afterwards. Every statement is appended to calls as
    (sql, bound_params) in order, which is what lets a test assert the exact SQL text
    AND the exact tuple handed to the driver.
    """

    def __init__(self, tables=None, next_id=None):
        self.tables = {}
        for name, rows in (tables or {}).items():
            self.tables[name] = [dict(row) for row in rows]
        self._next_id = dict(next_id or {})
        self.calls = []
        self.lastrowid = None

    # -- inspection helpers ----------------------------------------------
    @property
    def sql(self):
        """Just the statements, in order, for a readable assertion."""
        return [sql for sql, _params in self.calls]

    @property
    def params(self):
        """Just the bound parameters, in order."""
        return [params for _sql, params in self.calls]

    @property
    def last_call(self):
        return self.calls[-1] if self.calls else None

    def rows(self, table):
        return self.tables.setdefault(table, [])

    # -- id allocation ----------------------------------------------------
    def _allocate_id(self, table):
        if table in self._next_id:
            value = self._next_id[table]
            self._next_id[table] = value + 1
            return value
        existing = [row.get("id") for row in self.rows(table) if row.get("id") is not None]
        return (max(existing) + 1) if existing else 1

    # -- the driver surface ----------------------------------------------
    def execute_query(self, query, params=None):
        bound = _bind(params)
        self.calls.append((query, bound))

        parsed = _parse_insert(query)
        if parsed is not None:
            table, names = parsed
            if len(names) != len(bound):
                raise UnsupportedStatement(
                    "INSERT binds %d value(s) to %d column(s): %r" % (len(bound), len(names), query)
                )
            new_id = self._allocate_id(table)
            row = dict(zip(names, bound))
            row["id"] = new_id
            self.rows(table).append(row)
            self.lastrowid = new_id
            return FakeCursor(lastrowid=new_id, rowcount=1)

        parsed = _parse_update(query)
        if parsed is not None:
            table, names, key = parsed
            values = bound[: len(names)]
            wanted = bound[len(names)]
            changed = 0
            for row in self.rows(table):
                if row.get(key) == wanted:
                    row.update(dict(zip(names, values)))
                    changed += 1
            return FakeCursor(rowcount=changed)

        prefix = "DELETE FROM "
        if query.startswith(prefix):
            table, key = _split_where(query[len(prefix) :])
            wanted = bound[0] if bound else None
            before = len(self.rows(table))
            self.tables[table] = [row for row in self.rows(table) if row.get(key) != wanted]
            return FakeCursor(rowcount=before - len(self.tables[table]))

        raise UnsupportedStatement(
            "this fake does not model the statement: %r with params %r" % (query, bound)
        )

    def fetch_one(self, query, params=None):
        rows = self.fetch_all(query, params)
        return rows[0] if rows else None

    def fetch_all(self, query, params=None):
        bound = _bind(params)
        self.calls.append((query, bound))

        prefix = "SELECT * FROM "
        if not query.startswith(prefix):
            raise UnsupportedStatement(
                "this fake does not model the statement: %r with params %r" % (query, bound)
            )
        table, column = _split_where(query[len(prefix) :])
        if column is None:
            return [dict(row) for row in self.rows(table)]
        wanted = bound[0] if bound else None
        return [dict(row) for row in self.rows(table) if row.get(column) == wanted]


REPOSITORY_PACKAGES = (
    "service.services.users.persistence",
    "service.services.candidates.persistence",
    "service.services.vacancies.persistence",
    "service.services.magics.persistence",
    "service.services.personalities.persistence",
    "service.services.personality_vacancies.persistence",
    "service.services.personality_candidates.persistence",
    "service.services.candidate_vacancies.persistence",
    "service.services.files.persistence",
    "service.services.embeddings.persistence",
)


def _patch_repository_modules(monkeypatch, fake):
    """Point every persistence module at the fake handle.

    THE SUBTLETY THIS EXISTS FOR: a repository does
    from service.infrastructure.database.connection import db, so it holds its OWN
    reference. Replacing the attribute on connection alone leaves every repository
    bound to the real (socket-opening) object. So each module is imported first - one
    imported after the patch would otherwise bind the real handle - and then its db
    name is replaced.
    """
    patched = []
    for package_name in REPOSITORY_PACKAGES:
        package = importlib.import_module(package_name)
        for info in pkgutil.iter_modules(package.__path__, package_name + "."):
            module = importlib.import_module(info.name)
            if hasattr(module, "db"):
                monkeypatch.setattr(module, "db", fake, raising=True)
                patched.append(module.__name__)

    connection = importlib.import_module("service.infrastructure.database.connection")
    monkeypatch.setattr(connection, "db", fake, raising=True)
    patched.append(connection.__name__)
    return patched


def install(monkeypatch, fake=None):
    """Wire a FakeDatabase into every repository module and return it.

    Callable directly by a test that wants to set tables before the first call; the
    fake_db fixture below is the same thing, spelled as a fixture.
    """
    fake = fake if fake is not None else FakeDatabase()
    _patch_repository_modules(monkeypatch, fake)
    return fake


@pytest.fixture
def fake_db(monkeypatch):
    """A FakeDatabase installed as the db of every repository module."""
    return install(monkeypatch)


class FakeRepository(object):
    """A repository whose every method returns a declared value and records its call.

    WHY __getattr__ REFUSES UNDECLARED NAMES: a fake that answers any attribute turns
    "the service calls a method the real repository does not have" into a pass.
    Declaring the responses means the fake also documents the repository surface the
    service is allowed to use, and a renamed method fails as AttributeError.

    A declared response may be a value (returned as-is), a BaseException (raised, so a
    test can make the repository fail) or a callable (called with the same arguments).
    """

    def __init__(self, **responses):
        self.responses = dict(responses)
        self.calls = []

    def __getattr__(self, name):
        responses = self.__dict__.get("responses", {})
        if name.startswith("_") or name not in responses:
            raise AttributeError(
                "%s has no response declared for %r (declared: %s)"
                % (type(self).__name__, name, sorted(responses) or "none")
            )
        value = responses[name]

        def invoke(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if isinstance(value, BaseException):
                raise value
            if callable(value):
                return value(*args, **kwargs)
            return value

        return invoke

    def called(self, name):
        return [call for call in self.calls if call[0] == name]

    def method_names(self):
        return sorted(self.responses)


class FakeService(FakeRepository):
    """A neighbouring slice, for the link services that validate a foreign key.

    Same contract as FakeRepository, and separate for the reason a reader needs: the
    name says this stands in for another SLICE, not for a table, and a link test mixes
    both in one constructor.
    """
