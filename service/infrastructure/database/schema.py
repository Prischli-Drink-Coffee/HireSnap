"""The DEBUG-mode schema bootstrap: create the database, apply the dump, load the seed rows.

WHY THIS MODULE IS WRITTEN THE WAY IT IS (defect D3, docs/KNOWN_GAPS.md section 32):

  It used to wrap the whole of read_sql() in a bare except Exception that logged a warning
  and returned, and it issued CREATE DATABASE IF NOT EXISTS before it opened the dump. The
  two together made the worst possible failure shape: a checkout with a missing or
  unreadable backend/database/HireSnap.sql got an EMPTY database, a warning nobody reads, an
  exit code of 0, and a next boot that found a database which existed and had no tables. A
  caller cannot correct what it is not told about, so this module raises.

WHAT RAISES AND WHAT STILL LOGS:

  * Raises SchemaBootstrapError: the dump cannot be opened or holds no statement, the server
    refuses CREATE DATABASE or USE, the connection dies mid-dump, a seed file cannot be
    parsed, or a seed INSERT fails. None of those leaves a usable schema behind.
  * Logs a warning and continues: ONE statement that MySQL rejects. That tolerance is
    deliberate - the dump is written with CREATE TABLE IF NOT EXISTS and re-running it
    against an existing schema is a supported operation, where "table already exists" is an
    error on every statement and none of them is fatal.

THE ORDER IS PART OF THE CONTRACT: the dump is read, and the seed files are read, BEFORE any
statement runs. A path typo therefore produces an exception and no side effect, instead of an
empty database with a warning in the log.

TRANSACTIONS: MySQL commits implicitly around DDL, so the CREATE TABLE statements cannot be
rolled back and this module does not pretend they can. What IS wrapped in one transaction is
the seed phase: a failing INSERT rolls the whole phase back, so the tables are never left with
rows in some of them and none in the rest. That is the only part a transaction can protect,
and it is the part that was leaking before.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import pymysql
from pandas import read_csv

from service.configuration.env import Env
from service.configuration.logging import setup_logging
from service.configuration.paths import DATABASE_DIR, schema_file

env = Env()
log = setup_logging()

#: MySQL client error numbers that mean the CONNECTION is gone rather than the server
#: rejecting one statement: 2006 "MySQL server has gone away", 2013 "Lost connection to
#: MySQL server during query", 2055 "Lost connection to MySQL server at ...". These are the
#: one class of error the per-statement tolerance must NOT swallow - after them every
#: remaining statement fails for the same reason, so the bootstrap would return with most of
#: the dump unapplied and a half-built schema.
CONNECTION_ERROR_CODES = frozenset({2006, 2013, 2055})

#: The tables the seed CSVs under DATA_PATH/tables are allowed to fill. Anything else in that
#: directory is an export somebody left behind; inserting into a table this bootstrap does not
#: own is worse than skipping it loudly.
SEED_TABLES = (
    "embeddings",
    "files",
    "personalities",
    "users",
    "info_candidates",
    "vacancies",
)

#: How much of a rejected statement travels in an exception message and a CI log line.
EXCERPT_LIMIT = 200


def split_statements(script: str) -> List[str]:
    """Split a SQL script on the semicolons that actually END a statement.

    WHY NOT `script.split(";")` (docs/KNOWN_GAPS.md section 33): that cannot tell a
    terminator from a semicolon inside a string literal, an identifier or a comment. One
    row holding 'open; part time' was cut in half and executed as two broken statements,
    and under this module's deliberate per-statement tolerance the first half failed
    quietly. The dump is what keeps the old splitter working today; the failure mode is a
    hand-edited dump, which is exactly the case nobody tests by hand.

    WHY THIS IS NOT A PARSER: it tracks the four lexical states that can contain a
    semicolon - single quote, double quote, backtick identifier, block comment - plus the
    two line-comment markers MySQL accepts. It does not understand SQL, it only knows
    where a quote is open, which is all that is needed to find a terminator. A real
    grammar would be more code than the rest of this module and would still need exactly
    these cases.

    Double-quoted strings are ambiguous (ANSI_QUOTES makes them identifiers) and both are
    handled the same way here: either way the content may hold a semicolon.
    """
    statements: List[str] = []
    buffer: List[str] = []
    single = False
    double = False
    backtick = False
    in_block_comment = False
    index = 0
    length = len(script)
    while index < length:
        char = script[index]
        following = script[index + 1] if index + 1 < length else ""

        if in_block_comment:
            buffer.append(char)
            if char == "*" and following == "/":
                buffer.append(following)
                index += 2
                in_block_comment = False
                continue
            index += 1
            continue

        if single or double or backtick:
            buffer.append(char)
            # A backslash escape is only meaningful outside backticks; inside them MySQL
            # treats it literally, but appending the next character either way keeps the
            # scanner from reading an escaped quote as a terminator of the string.
            if char == "\\" and not backtick and following:
                buffer.append(following)
                index += 2
                continue
            if char == "'" and single and following == "'":
                # '' is an escaped quote inside a single-quoted string.
                buffer.append(following)
                index += 2
                continue
            if char == '"' and double and following == '"':
                buffer.append(following)
                index += 2
                continue
            if char == "'" and single:
                single = False
            elif char == '"' and double:
                double = False
            elif char == "`" and backtick:
                backtick = False
            index += 1
            continue

        # Outside every literal and comment.
        if char == "/" and following == "*":
            buffer.append(char)
            buffer.append(following)
            index += 2
            in_block_comment = True
            continue
        if (char == "-" and following == "-") or char == "#":
            # Line comment: copy it through to the end of the line, semicolons included.
            while index < length and script[index] not in "\r\n":
                buffer.append(script[index])
                index += 1
            continue
        if char == "'":
            single = True
        elif char == '"':
            double = True
        elif char == "`":
            backtick = True
        elif char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _excerpt(statement: str, limit: int = EXCERPT_LIMIT) -> str:
    """One bounded line of a statement, safe to put in a message."""
    collapsed = " ".join(str(statement).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _mysql_error_code(exc: BaseException) -> Optional[int]:
    """The errno pymysql packs into args[0], or None when the error carries no code."""
    if exc.args and isinstance(exc.args[0], int):
        return exc.args[0]
    return None


class SchemaBootstrapError(RuntimeError):
    """The bootstrap could not produce a usable schema, and it says exactly where.

    WHY AN EXCEPTION AND NOT A LOG LINE: the caller of read_sql() is
    service/main.py:bootstrap_schema(), which runs only when DEBUG and INIT_DB are both on.
    A container booting with a broken dump must crash and be restarted, because a crash-loop
    is visible in docker compose ps while an empty database is not - the API simply starts
    and answers 500 to every request that touches a table.

    The statement index is 1-based over the statements of the dump, i.e. the n-th
    semicolon-delimited statement, so the operator can open the file and find the line.
    """

    def __init__(
        self,
        step: str,
        detail: str,
        statement_index: Optional[int] = None,
        statement: Optional[str] = None,
    ):
        self.step = step
        self.detail = detail
        self.statement_index = statement_index
        self.statement = _excerpt(statement) if statement is not None else None
        message = "schema bootstrap failed while %s: %s" % (step, detail)
        if statement_index is not None:
            message += " [statement %d" % statement_index
            if self.statement:
                message += ": %r" % self.statement
            message += "]"
        super().__init__(message)


class CreateSQL:
    """Create the database named by DB, apply the dump, then load the seed rows."""

    def __init__(self):
        # Определение пути к SQL-файлу
        # Resolved through service.configuration.paths so the dump is found in
        # backend/database/ regardless of the working directory and regardless of
        # the file name case (HireSnap.sql vs a lowercase database name).
        self.path = str(DATABASE_DIR)
        self.path_to_sql = str(schema_file(env.__getattr__("DB")))
        self.path_to_data = os.path.join(env.__getattr__("DATA_PATH"), "tables")

        # Установка соединения с базой данных
        # No database name: the dump creates it, so connecting to one first would fail on a
        # fresh host. A connection failure is not caught here - the pymysql error is the
        # right one to raise, and it names the host and the user.
        self.connection = pymysql.connect(
            host=env.__getattr__("DB_HOST"),
            port=int(env.__getattr__("DB_PORT")),
            user=env.__getattr__("DB_USER"),
            password=env.__getattr__("DB_PASSWORD"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )

    @staticmethod
    def remove_column_from_csv(df):
        """Drop the three unnamed index columns pandas writes into a re-exported CSV.

        Returns the frame UNCHANGED on failure. It used to fall off the end of the except
        clause and return None, and the caller then formatted None into column names
        ("TypeError: NoneType object is not iterable") - a failure reported a second time,
        one frame later, with the real cause already lost.
        """
        # Проходим по всем файлам в указанной директории
        try:
            # Чтение CSV файла в DataFrame
            df = df.replace({pd.NA: None, np.nan: None})
            column_name1 = "Unnamed: 0.1"
            column_name2 = "Unnamed: 0"
            column_name3 = "Unnamed: 0.2"
            # Проверяем, существует ли столбец с нужным названием
            if column_name1 in df.columns:
                if column_name2 in df.columns:
                    if column_name3 in df.columns:
                        # Удаляем столбец
                        df = df.drop(columns=[column_name1])
                        df = df.drop(columns=[column_name2])
                        df = df.drop(columns=[column_name3])
                # Сохраняем обновленный DataFrame обратно в CSV
                print("Столбец удален из датафрейма")
                return df
            else:
                log.error("Столбец не найден в датафрейме")
                return df
        except Exception as e:
            log.exception(f"Ошибка при обработке датафрейме: {e}")
            return df

    def read_sql(self) -> None:
        """Create the database, apply the dump and load the seed rows, or raise.

        The connection is always closed. On every path that raises, nothing has been written
        to the server, because the dump and the seed files are read first.
        """
        try:
            statements = self._load_statements()
            seeds = self._load_seed_frames()
            with self.connection.cursor() as cursor:
                self._create_database(cursor)
                self._apply_dump(cursor, statements)
            self._seed(seeds)
            # Exactly one commit on the success path: the DDL is committed by MySQL itself, the
            # seed phase ends here. A failure never reaches this line - it raises from _seed
            # after rolling back - so 'committed once' is a property of the control flow.
            self.connection.commit()
            log.info(
                "Database %s is ready: %d statement(s) from %s",
                env.__getattr__("DB"),
                len(statements),
                self.path_to_sql,
            )
        finally:
            self.connection.close()

    # --- the phases -------------------------------------------------------
    def _load_statements(self) -> List[str]:
        """Read and split the dump. Runs BEFORE anything is executed against the server."""
        try:
            with open(self.path_to_sql, "r", encoding="utf-8") as handle:
                script = handle.read()
        except OSError as exc:
            raise SchemaBootstrapError(
                "opening the schema dump",
                "%s (%s)" % (self.path_to_sql, exc.strerror or exc),
            ) from exc

        statements = split_statements(script)
        if not statements:
            raise SchemaBootstrapError(
                "reading the schema dump", "%s holds no SQL statement" % self.path_to_sql
            )
        return statements

    def _load_seed_frames(self) -> List[Tuple[str, pd.DataFrame]]:
        """Read the seed CSVs, before any DDL, so a bad file cannot leave a half schema.

        A MISSING seed directory is not an error: the schema is what this bootstrap is for and
        the rows are a convenience, so an absent DATA_PATH/tables is applied as "schema only".
        A seed file that EXISTS but cannot be parsed is an error, because the operator put it
        there expecting it to load.
        """
        directory = self.path_to_data
        if not os.path.isdir(directory):
            log.info("No seed directory at %s - applying the schema without seed rows", directory)
            return []

        seeds: List[Tuple[str, pd.DataFrame]] = []
        for file in sorted(os.listdir(directory)):
            if not file.endswith(".csv"):
                continue
            table_name = file.split(".")[0]
            if table_name not in SEED_TABLES:
                log.warning("Не найдена таблица %s", table_name)
                continue
            try:
                frame = read_csv(os.path.join(directory, file))
            except Exception as exc:
                raise SchemaBootstrapError(
                    "reading a seed file", "%s (%s)" % (os.path.join(directory, file), exc)
                ) from exc
            log.info("Найдена таблица %s", table_name)
            seeds.append((table_name, frame))
        return seeds

    def _create_database(self, cursor) -> None:
        """CREATE DATABASE then USE. A failure here has no tolerable form."""
        database = env.__getattr__("DB")
        try:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            cursor.execute(f"USE `{database}`")
        except pymysql.MySQLError as exc:
            raise SchemaBootstrapError(
                "creating the database", "`%s` was refused (%s)" % (database, exc)
            ) from exc

    def _apply_dump(self, cursor, statements: List[str]) -> None:
        """Execute the dump, skipping statements MySQL rejects but not a dead connection."""
        for index, statement in enumerate(statements, start=1):
            try:
                cursor.execute(statement)
                log.info("Executed SQL statement: %s", statement)
            except pymysql.MySQLError as exc:
                if _mysql_error_code(exc) in CONNECTION_ERROR_CODES:
                    raise SchemaBootstrapError(
                        "applying the schema dump",
                        "the connection died (%s); %d of %d statement(s) had been applied"
                        % (exc, index - 1, len(statements)),
                        statement_index=index,
                        statement=statement,
                    ) from exc
                # Логирование неудачных запросов может помочь в отладке
                log.warning("Skipped statement %d, MySQL rejected it: %s", index, exc)

    def _seed(self, seeds: List[Tuple[str, pd.DataFrame]]) -> None:
        """Insert the seed rows in ONE transaction, so a failure loads none of them."""
        if not seeds:
            return
        failed_table = seeds[0][0]
        try:
            # Explicit: pymysql opens a transaction implicitly, and saying so is what makes
            # the rollback below a property of the code instead of a property of the driver.
            self.connection.begin()
            for failed_table, frame in seeds:
                frame = self.remove_column_from_csv(frame)
                columns = ", ".join(frame.columns)
                placeholders = ", ".join(["%s"] * len(frame.columns))
                sql = f"INSERT INTO {failed_table} ({columns}) VALUES ({placeholders})"
                data_tuples = [tuple(row) for row in frame.to_numpy()]
                with self.connection.cursor() as cursor:
                    cursor.executemany(sql, data_tuples)
                log.info("Inserted %d row(s) into %s", len(data_tuples), failed_table)
        except Exception as exc:
            self._rollback_quietly()
            raise SchemaBootstrapError(
                "loading the seed rows",
                "table %s failed (%s); the whole seed phase was rolled back" % (failed_table, exc),
            ) from exc

    def _rollback_quietly(self) -> None:
        """Roll back, and never let a rollback error hide the insert error that caused it."""
        try:
            self.connection.rollback()
        except Exception as rollback_error:  # pragma: no cover - needs a broken driver
            log.warning("Rollback of the seed phase failed: %s", rollback_error)


if __name__ == "__main__":
    create_sql = CreateSQL()
    create_sql.read_sql()
