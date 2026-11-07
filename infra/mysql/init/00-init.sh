#!/bin/bash
# HireSnap MySQL first-boot schema loader.
#
# Mounted into the mysql container's /docker-entrypoint-initdb.d/ (the compose
# file, owned by the docker teammate, must place it there and mount
# backend/HireSnap.sql read-only - see init/README.md for the exact contract).
#
# WHY the schema is not mounted into /docker-entrypoint-initdb.d directly:
# MySQL's entrypoint feeds every *.sql file straight into the client. This dump
# is NOT idempotent on its own - its CREATE TABLEs are guarded with
# IF NOT EXISTS, but every "ALTER TABLE ... ADD PRIMARY KEY/KEY/CONSTRAINT" is
# not, so a second execution aborts on duplicate key names and leaves the schema
# half-built. Applying it through this script makes the decision ("already
# initialised?" ) explicit and logged instead of implicit in client behaviour.
#
# WHY no `set -u`: MySQL's entrypoint SOURCES this file when it is not
# executable (docker_process_init_files does `. "$f"`), so shell options set
# here leak into the rest of the entrypoint, which reads plenty of optional
# variables. -e is safe and wanted; -u is not.
set -eo pipefail

SCHEMA_PATH=""
for candidate in \
    "${SCHEMA_FILE:-}" \
    /opt/hiresnap/HireSnap.sql \
    /docker-entrypoint-initdb.d/HireSnap.sql \
    /schema/HireSnap.sql \
    "/docker-entrypoint-initdb.d/${MYSQL_DATABASE:-hiresnap}.sql"
do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        SCHEMA_PATH="$candidate"
        break
    fi
done

if [ -z "$SCHEMA_PATH" ]; then
    echo "[init] FATAL: schema dump not found. Looked for:"
    echo "[init]   \${SCHEMA_FILE}, /opt/hiresnap/HireSnap.sql,"
    echo "[init]   /docker-entrypoint-initdb.d/HireSnap.sql, /schema/HireSnap.sql"
    echo "[init] The dump lives in the repository at backend/HireSnap.sql; mount it"
    echo "[init] read-only and/or set SCHEMA_FILE. See infra/mysql/init/README.md."
    exit 1
fi

DB_NAME="${MYSQL_DATABASE:-${DB__NAME:-${DB_NAME:-hiresnap}}}"

# Connection: inside the official image the entrypoint has already started a
# temporary server on a unix socket, and its root account is passwordless until
# the first boot completes. On a re-run from the host (or from a sidecar) the
# same script works over TCP by exporting MYSQL_HOST/MYSQL_PORT/MYSQL_PWD.
MYSQL_ARGS=(--default-character-set=utf8mb4)
if [ -n "${MYSQL_HOST:-}" ]; then
    MYSQL_ARGS+=(--protocol=tcp -h "$MYSQL_HOST" -P "${MYSQL_PORT:-3306}" -u "${MYSQL_USER:-root}")
else
    MYSQL_ARGS+=(--protocol=socket -S "${MYSQL_SOCKET:-/var/run/mysqld/mysqld.sock}" -u root)
fi
# The client reads the password from MYSQL_PWD; never pass it as -p<value>
# because the value is then visible in `ps` output and in shell history.
if [ -z "${MYSQL_PWD:-}" ] && [ -n "${MYSQL_ROOT_PASSWORD:-}" ]; then
    export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"
fi

SCHEMA_SHA="$(sha256sum "$SCHEMA_PATH" | cut -d ' ' -f 1)"
TABLE_COUNT="$("mysql" "${MYSQL_ARGS[@]}" --batch --skip-column-names -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB_NAME}'")"

# Sentinel: 'users' is the table every other table references (the dump's
# foreign keys all lead back to it), so its presence is the cheapest proof that
# this schema has been loaded. Counting tables would be fragile - a partial run
# would look "half initialised" and we would rather fail loudly than guess.
SENTINEL="$("mysql" "${MYSQL_ARGS[@]}" --batch --skip-column-names -e \
    "SELECT COUNT(*) FROM information_schema.tables
      WHERE table_schema='${DB_NAME}' AND table_name='users'")"

echo "[init] database: $DB_NAME"
echo "[init] schema:   $SCHEMA_PATH (sha256 $SCHEMA_SHA)"
echo "[init] tables currently in database: $TABLE_COUNT"

if [ "$SENTINEL" != "0" ]; then
    echo "[init] schema already applied (table 'users' exists); skipping the load."
    echo "[init] The dump is not idempotent (its ALTER TABLE ... ADD KEY statements"
    echo "[init] are unguarded), so re-applying it is refused by design. To rebuild"
    echo "[init] from scratch, remove the mysql data directory and start again."
else
    echo "[init] applying $SCHEMA_PATH ..."
    # No --force: the client must stop at the first failing statement, otherwise
    # a broken dump would be reported as success and the app would start against
    # a schema that is missing half its foreign keys.
    if ! "mysql" "${MYSQL_ARGS[@]}" "$DB_NAME" < "$SCHEMA_PATH"; then
        echo "[init] FATAL: schema load failed; the database is in an unknown state."
        exit 1
    fi
    echo "[init] schema applied."
fi

# Row counts, always: they are the one line of evidence a first-boot log keeps,
# and they make "the dump loaded" verifiable without opening a shell.
echo "[init] row counts:"
TABLES="$("mysql" "${MYSQL_ARGS[@]}" --batch --skip-column-names -e \
    "SELECT table_name FROM information_schema.tables
      WHERE table_schema='${DB_NAME}' AND table_type='BASE TABLE' ORDER BY table_name")"
if [ -z "$TABLES" ]; then
    echo "[init]   (no tables)"
else
    # Word splitting is intended: the query returns one table name per line and
    # every name in this schema is a simple identifier.
    for table in $TABLES; do
        rows="$("mysql" "${MYSQL_ARGS[@]}" --batch --skip-column-names -e \
            "SELECT COUNT(*) FROM \`$DB_NAME\`.\`$table\`")"
        printf '[init]   %-28s %s\n' "$table" "$rows"
    done
fi

echo "[init] ready: MySQL database $DB_NAME is initialised."
