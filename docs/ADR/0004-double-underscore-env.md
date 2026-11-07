# ADR 0004 — Double-underscore grouping in the env manifest, flat names in containers

- **Status:** accepted
- **Date:** 2026-11-07
- **Affects:** `docker/.env.example`, `docker/docker-compose.yaml`, `docker/docker-compose.dev.yaml`

## Context

The backend reads flat environment names. `backend/env.py` resolves them with
`Env.__getattr__` and raises `AttributeError` for anything missing
(`backend/env.py:30-36`); the names actually consumed are `HOST`, `SERVER_PORT`,
`DEBUG`, `DATA_PATH`, `PUBLIC_BASE_URL`, `ACCESS_TOKEN_EXPIRE_MINUTES`,
`REFRESH_TOKEN_EXPIRE_DAYS`, and the five database variables `DB`, `DB_HOST`,
`DB_PORT`, `DB_USER`, `DB_PASSWORD`.

One env file now has to hold, side by side:

- the application's own keys,
- the MySQL **image's** init keys (`MYSQL_ROOT_PASSWORD`, `MYSQL_DATABASE`,
  `MYSQL_USER`, `MYSQL_PASSWORD`), which look almost identical to ours and mean
  something different,
- the MinIO image's keys (`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` in the
  container, `STORAGE__ACCESS_KEY` / `STORAGE__SECRET_KEY` in the file),
- gateway and frontend keys that belong to no application at all.

The reference stack groups keys with a section prefix that ends in **two**
underscores — `PG__USER`, `MINIO__ACCESS_KEY`, `REDIS__PASSWORD`, `AGENTS__*` — and maps
each one to the third-party name the container actually reads, in the compose
`environment:` block: `POSTGRES_USER: ${PG__USER}` (`R:/GPTHub/docker/docker-compose.yaml:79-81`),
`MINIO_ROOT_USER: ${MINIO__ACCESS_KEY:?...}` (`:128-129`), `--requirepass` from
`REDIS__PASSWORD` (`:103`). The prefix marks where a name is *ours* and the target
name is somebody else's.

## Decision

**Adopt the double-underscore form for exactly one group: the database.** The container
receives flat names; the application never sees a double underscore.

```yaml
environment:
  DB_HOST: ${DB__HOST:-mysql}
  DB_PORT: ${DB__PORT:-3306}
  DB_USER: ${DB__USER:?set DB__USER in docker/.env.prod}
  DB_PASSWORD: ${DB__PASSWORD:?set DB__PASSWORD in docker/.env.prod}
  DB: ${DB__NAME:-hiresnap}
```

That is `docker/docker-compose.yaml:107-111` (the dev file carries the same block).
Every other key in `docker/.env.example` is already flat — `HOST`, `SERVER_PORT`,
`DEBUG`, `PUBLIC_BASE_URL`, `REACT_APP_BASE_URL`, `APP_DOMAIN` — and is passed
through unchanged, because a translation line that maps `SERVER_PORT` to
`SERVER_PORT` documents nothing.

## Rationale

1. **Grouping where the collision is real, not everywhere.** `DB__*` earns the prefix
   because `DB_USER` and `MYSQL_USER` sit three lines apart in the same file, mean
   different accounts, and both create a user in the same database. The prefix is what
   makes "ours" versus "the image's" visible at a glance. `SERVER_PORT` has no such
   twin, so it keeps its flat name.
2. **The mapping line is the documentation.** `DB_HOST: ${DB__HOST:-mysql}` is an
   explicit, reviewable statement that one name becomes the other, and it carries the
   default in the same place the default is used. When the application renames a variable,
   the change lands in a deployment file, in the diff, instead of only in Python.
3. **It distinguishes ownership.** `MYSQL_*` belongs to the MySQL image,
   `MINIO_*` to MinIO, `STORAGE__*` to us. Without a marker the section that owns a
   key is a matter of tribal knowledge; with one it is visible.
4. **One underscore would be ambiguous, and the application's names already use it.**
   `DB_HOST` is a real application variable, so a single-underscore convention could
   not tell "grouping prefix" from "actual name". Two underscores cannot collide with a
   name the application reads, because no application variable contains one.

## Cost, and why it is acceptable

The convention creates a translation layer that can drift in two directions:

- a key in the env file that is never mapped — the container never sees it, and the
  application fails at import with `AttributeError` (loud, at startup);
- a mapping in `environment:` referencing a key the file does not declare — the
  `:?` form aborts the compose run, the `:-` form silently injects a default.

Both are caught before runtime by `scripts/check_env_parity.py`, which fails when a
compose interpolation has no declaration in the manifest. It does **not** compare against
this page: documentation drift is a review problem, not a build problem, and a guard that
parses prose is a guard nobody trusts.

The alternative — flat names everywhere, exactly as the application expects — has no
translation layer and therefore no drift inside our own group, but produces an env file
where `DB_USER` and `MYSQL_USER` are indistinguishable at a glance. Given that
confusing those two is a documented operational trap
([RUNBOOK.md §10](../RUNBOOK.md#11-rotating-the-database-password)), the marker is worth
its cost.

## Alternatives rejected

- **Flat names only.** No drift, but the two-user confusion stays invisible in the file
  that creates both users.
- **Prefix every section (`SERVER__PORT`, `AUTH__*`, `NGINX__*`, `MYSQL__*`).**
  Rejected because a prefix is only worth its line when it disambiguates a collision. The
  reference stack prefixes the groups where *our* key and a third-party image's key would
  otherwise share a name (`PG__USER` → `POSTGRES_USER`, `MINIO__ACCESS_KEY` →
  `MINIO_ROOT_USER`). `SERVER_PORT`, `NGINX_PUBLISH_HTTP` and `APP_DOMAIN` are
  read by no third party, so prefixing them would add a translation line that maps a name
  to itself and documents nothing. Prefixing `MYSQL_*` is impossible without renaming
  the image's own variables.
- **A single `.env` file with section comments.** Comments are not enforced and do not
  group anything in a diff.
- **Renaming the application's variables to `DB__HOST`.** An application change touching
  every read site for a cosmetic gain, and it would break the backend's standalone
  deployment, which reads flat names from its own `.env` (`backend/env.py`).

## Consequences

- `docker/.env.example` is grouped by section in this order: compose/build
  (`PIP_INDEX_URL`, `NPM_REGISTRY`, `DOCKER_BUILDKIT`), project and published ports
  (`PROJECT_NAME`, `COMPOSE_MODE`, `BACKEND_PUBLISH_PORT`,
  `FRONTEND_PUBLISH_PORT`), API service (`HOST`, `SERVER_PORT`, `DEBUG`,
  `INIT_DB`, `DATA_PATH`, `PUBLIC_BASE_URL`), auth, CORS, `DB__*`, `MYSQL_*`,
  `STORAGE__*`, frontend, gateway, profile switches, ML/GPU.
- Adding a variable means: declare it in all three env files (`sync_env.py` enforces
  parity), map it in both compose files if it is a `DB__*` key, and document it in
  [ENV.md](../ENV.md). `scripts/check_env_parity.py` fails when the first two disagree.
- `docker compose config` is how you see the resolved flat names; it is the command to
  run when a variable "is set but does not arrive".
- Keys that exist only as mount targets (`JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH`)
  or as a forward contract (`STORAGE__ENDPOINT`, `STORAGE__BUCKET`,
  `STORAGE__SECURE`) are documented as such in [ENV.md](../ENV.md) rather than being
  quietly mapped — a mapping that maps nothing is worse than an honest unused key.
