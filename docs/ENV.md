# Environment reference

> **`docker/.env.example` is the only manifest that exists.** This document explains
> *why* each key exists and *what reads it*; the example file is the authoritative list
> of key names. When the two disagree, the file wins and this page is the bug — the
> `scripts/check_env_parity.py` guard exists precisely to catch that drift in CI, and
> `scripts/check_compose_parity.py` checks that both compose files interpolate only keys
> the manifest declares. Both run in `make guards`, and again inside `make lint`,
> which reaches them through `scripts/run_guard.sh` so a Windows host without a real
> `python` on `PATH` reports a skip instead of a false failure.

```
docker/.env.example ──sync_env.py --mode dev|prod──▶ docker/.env.dev | .env.prod (gitignored)
                                        │
   ./docker/run.sh --dev|--prod  ─▶  docker compose --env-file .env.<mode> \
                                       -p ${PROJECT_NAME} -f <compose file> [profiles]
                                        │
                        compose interpolation of ${...} references
                                        │
                                  environment:
                                    DB_HOST: ${DB__HOST}          ← flat name
                                        │
                                     container env
                                        │
              service/configuration/env.py  load_dotenv + os.environ
                                        │
                             Env().__getattr__("DB_HOST")
```

`docker/run.sh` is the only supported entry point (`./docker/run.sh --dev`,
`--prod`). A bare `docker compose up` skips the `--env-file`, the profile selection and
the preflight, so it starts a stack with the wrong values or none at all. This is the
same reason the reference stack routes everything through `R:\GPTHub\docker\run.sh`.

Three rules follow from that chain:

1. **Double underscore groups, it does not nest.** `DB__HOST` is a human-readable
   section prefix in the env file. The container receives the flat `DB_HOST` the
   application actually calls. Arguments for this are in
   [ADR/0004-double-underscore-env.md](ADR/0004-double-underscore-env.md).
2. **Secrets fail closed, everything else has a default.**
   `${VAR:?human message}` aborts `docker compose up` with your message when `VAR` is
   unset; `${VAR:-default}` substitutes a default. A password or key must never use
   the `:-` form — a defaulted secret is a secret that silently deploys as `changeme`.
   Reference: `R:\GPTHub\docker\compose_preflight.sh:10-15`, which additionally
   refuses to echo raw compose diagnostics because they can contain dotenv values.
3. **The application has no defaults of its own.** `Env.__getattr__`
   (`backend/service/configuration/env.py:42-48`) raises `AttributeError` for any missing name. A missing
   variable is a startup crash, not a runtime surprise — which is the behavior you
   want, but it means every key below marked *required* must be present even if the
   feature is unused. **The platform layer and the sidecar are the exception, and
   deliberately so**: `backend/service/infrastructure/*.py` and
   `ml/service/settings.py` read `os.environ` with a default for every key, because a
   missing `CACHE__REDIS_URL` must degrade to the in-memory backend rather than abort
   the boot. No key in that layer is ever mandatory, and §8 marks which is which.

## 2. Shared / compose-level

| Key | Meaning | Required | Default | Consumed by |
|-----|---------|----------|---------|-------------|
| `PROJECT_NAME` | Compose project namespace: `hiresnap-prod` or `hiresnap-dev`. `run.sh` passes it as `-p`, so the containers, network and volumes of the two stacks never collide. | yes | per file | every `docker compose` call |
| `COMPOSE_MODE` | `dev` or `prod`. `run.sh` cross-checks it against the compose file it was asked to run and warns on a mismatch; it is a guard, not a selector. | yes | `prod` | `docker/run.sh` |
| `PIP_INDEX_URL` / `NPM_REGISTRY` | Package index used by the **image builds** (build ARGs). Set them when the default is slow or blocked. | no | pypi.org / registry.npmjs.org | `backend`, `frontend` build stages |
| `DOCKER_BUILDKIT` | Must be `1`: both Dockerfiles use `--mount=type=cache` for pip/npm. | yes | `1` | `docker build` |
| `NGINX_PUBLISH_HTTP` / `NGINX_PUBLISH_HTTPS` | Host ports the gateway publishes on 80 / 443. | no | `8088` / `8443` | `gateway` |
| `BACKEND_PUBLISH_PORT` | Host port for the backend. **Dev only** — in prod the gateway is the only publisher. | dev | `8080` | `backend` (dev compose) |
| `FRONTEND_PUBLISH_PORT` | Host port for the frontend. **Dev only.** | dev | `3000` | `frontend` (dev compose) |
| `MAILPIT_PUBLISH_PORT` | Host port for the Mailpit web inbox. **Dev only**, and only when `MAIL=1`. | dev | `8025` | `mailpit` (dev compose) |

> **There is no host port for `ml` in any mode**, so the manifest has no `ML_PUBLISH_PORT`
> key: the sidecar is reachable only on the compose network, and `ml` publishes nothing
> even in dev. To curl `/v1/capabilities` yourself, open a shell in the container
> (`make ml-shell`) and request `http://127.0.0.1:8080` from there.

`8088` rather than `80` for the gateway is deliberate: binding port 80 needs root or
`CAP_NET_BIND_SERVICE`, and a developer laptop usually already has something on 80.
Changing `NGINX_PUBLISH_HTTP` changes only the host side; the container always listens
on 80.

## 3. Backend — core

These names are identical in the env file and in the container — the application reads
them flat, so the manifest declares them flat too. Every one is **required** at container
start unless the Notes column says otherwise.

| Key | Meaning | Dev | Prod | Notes |
|-----|---------|-----|------|-------|
| `HOST` | Bind address for uvicorn, and the host half of absolute media URLs built by the URL helper. | `0.0.0.0` | `0.0.0.0` | Read in `run_server()` (`backend/service/main.py:58-69`) and passed to `uvicorn.run`, which is why it must not be `127.0.0.1` — see §3.1. |
| `SERVER_PORT` | In-container port for uvicorn **and** the port half of those URLs. | `8080` | `8080` | Keep `8080`: the gateway upstream and the image healthcheck hardcode it. Changing the host-side publish does not change this. |
| `DEBUG` | `"TRUE"` or `"FALSE"`, upper case, compared as a string. | `TRUE` | `FALSE` | Any other value raises `RuntimeError: DEBUG must be TRUE or FALSE, got ...` in `_debug_flag()` (`backend/service/main.py:38-45`). `TRUE` enables uvicorn reload **and** is one of the two gates on the schema-creating `CreateSQL` run. |
| `INIT_DB` | Operator intent: "initialise the schema on start". | `false` | `false` | Read as a strict boolean (`os_env_flag()`, `backend/service/main.py:85-93`, which delegates to `service/configuration/flags.py`: `1`/`true`/`yes`/`on`/`enabled`/`y`/`t` in any case, and a value nobody meant **raises** rather than reading as off). It is only an **AND** with `DEBUG`: the bootstrap runs when `DEBUG=TRUE` **and** `INIT_DB` is truthy (`backend/service/main.py:75-82`). It cannot override the `DEBUG` gate. See [KNOWN_GAPS.md](KNOWN_GAPS.md#6-schema-creation-only-happens-when-debugtrue). |
| `DATA_PATH` | Absolute directory for uploads, embeddings and `tables/` CSVs. | `/app/data` | `/app/data` | Bind-mounted from `infra/storage`. The two must agree. Used at `backend/service/infrastructure/uploads.py:15` (also `:31-35`) and `backend/service/infrastructure/database/schema.py:23`. |
| `PUBLIC_BASE_URL` | Externally reachable origin used for media URLs by the patched URL helper (`backend/service/shared/urls.py`). | `http://localhost:8088` | `https://<real host>` | `${PUBLIC_BASE_URL:?...}` — required at compose level. Optional to the *code*: when it is absent the helper falls back to `http://${HOST}:${SERVER_PORT}`. |

### 3.1 `HOST` and `SERVER_PORT` are overloaded

The unpatched helper (now `backend/service/shared/urls.py:8-9`) builds
media URLs from the same two variables uvicorn binds with. Setting `HOST=0.0.0.0` — the
only sane bind address in a container — makes every returned media URL point at
`http://0.0.0.0:8080/...`, which a browser cannot resolve. The stack does not paper over
this by binding to a public address; it sets `PUBLIC_BASE_URL`, which the patched helper
prefers (`backend/service/shared/urls.py:16-20`), and keeps the unpatched
`HOST:SERVER_PORT` fallback intact so a standalone checkout behaves exactly as before. This is recorded in
[KNOWN_GAPS.md](KNOWN_GAPS.md#2-media-urls-are-built-from-the-bind-address).

### 3.2 `CORS_ALLOW_ORIGINS` — consumed, and the wildcard/credentials rule

`CORS_ALLOW_ORIGINS` is the one place a browser origin allow-list can be set without
touching code. It is read by `_cors_settings()`
(`backend/service/api/app.py:67-117`), whose return value is splatted into
`CORSMiddleware` from `create_app()` at `backend/service/api/app.py:169-177`
(`**_cors_settings()` at `:182`). The gateway proxies; it does not filter by `Origin`, so
this key is the enforcement point.

| Value | `allow_origins` | `allow_credentials` |
|-------|-----------------|---------------------|
| unset, blank, `*`, `["*"]` or `[]` | `["*"]` | **`False`** |
| JSON array, e.g. `["https://a.example","https://b.example"]` | those two | `True` |
| comma list, e.g. `https://a.example, https://b.example` | same, trimmed, empty parts dropped | `True` |
| malformed JSON, e.g. `[not json` | `["*"]` after a `WARNING` | `False` |

Two consequences follow, and both are deliberate:

1. **A wildcard never carries credentials.** A browser rejects
   `Access-Control-Allow-Origin: *` together with `Access-Control-Allow-Credentials: true`,
   and Starlette emits both if asked. The pre-refactor code shipped exactly that illegal
   pair, so a credentialed cross-origin request never actually worked; the resolver stops
   pretending rather than widening anything. This application authenticates with a Bearer
   header rather than a cookie, so the wildcard branch is survivable — it is just not the
   branch to deploy.
2. **An explicit list is the configuration to use.** It keeps the legal credentialed
   pairing and it is what the manifest ships
   (`CORS_ALLOW_ORIGINS=["https://hiresnap.example.com"]`, `docker/.env.example:116`).

A malformed value degrades to the wildcard with a warning instead of raising: a typo in a
variable must not make the API unimportable. The whole table is pinned by
`backend/tests/test_cors_settings.py` (nine tests, including an end-to-end assertion that
`create_app()` really hands the resolved value to `CORSMiddleware`).

The manifest comment block (`docker/.env.example:103-115`) says the same thing in the place
an operator will read it first. Background in
[ARCHITECTURE.md §6](ARCHITECTURE.md#6-configuration-flow) and
[KNOWN_GAPS.md §11](KNOWN_GAPS.md#11-cors-is-open-by-default-and-the-wildcard-cannot-carry-credentials).

## 4. Backend — database

| Key | Env-file key | Meaning | Required | Dev default | Prod |
|-----|--------------|---------|----------|-------------|------|
| `DB_HOST` | `DB__HOST` | MySQL hostname: the compose service name. | yes | `mysql` | `mysql` |
| `DB_PORT` | `DB__PORT` | MySQL port **inside the network**. Never published to the host. | yes | `3306` | `3306` |
| `DB` | `DB__NAME` | Database name. The application reads `DB`, not `DB_NAME` — the unusual name is the existing contract. | yes | `hiresnap` | `hiresnap` |
| `DB_USER` | `DB__USER` | Application account. | yes — `${DB__USER:?...}` | `hiresnap` | **must be set** |
| `DB_PASSWORD` | `DB__PASSWORD` | Application password. | yes — `${DB__PASSWORD:?...}` | template value | **must be set** |

`DB__NAME` is the **only** nesting translation that matters here, and it is the reason
[ADR/0004-double-underscore-env.md](ADR/0004-double-underscore-env.md) exists: the env
file groups (`DB__`), the container receives flat names (`DB_HOST`), and the compose
`environment:` block is the single place where the two meet.

Read at `backend/service/infrastructure/database/connection.py:58-65` and
`backend/service/infrastructure/database/schema.py:22-33`.

MySQL **server-side** variables, consumed by the `mysql` service rather than the app:

| Key | Meaning | Required | Notes |
|-----|---------|----------|-------|
| `MYSQL_ROOT_PASSWORD` | Root password for the MySQL container. | yes — `${...:?}` | Never used by the application; only for administration and `infra/mysql/init/`. |
| `MYSQL_DATABASE` | Database created on the first boot of an empty datadir. | no | Must equal `DB__NAME`, otherwise the first `CREATE DATABASE` is the only thing that makes them agree. |
| `MYSQL_USER` / `MYSQL_PASSWORD` | The application account created on first boot. | no | Must equal `DB__USER` / `DB__PASSWORD`. |

**Why the duplication is dangerous:** `MYSQL_*` variables are only honored on the first
boot of an **empty `mysql_data` named volume**; the datadir is a Docker volume, not a
host directory ([ARCHITECTURE.md §4](ARCHITECTURE.md#4-data-paths)). On an existing
volume they are ignored entirely, so changing `DB__PASSWORD` without also running
`ALTER USER` leaves the app unable to authenticate while the container looks healthy.
See [RUNBOOK.md §10](RUNBOOK.md#11-rotating-the-database-password).

## 5. Backend — authentication

| Key | Meaning | Required | Dev | Prod |
|-----|---------|----------|-----|------|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Lifetime of an access token, minutes. Parsed as `int` at import, in the settings model of `backend/service/models/auth_models.py`. | yes | `15` | `15` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Lifetime of a refresh token, days. `int` at import, from the same settings model. | yes | `7` | `7` |
| `JWT_ALGORITHM` | Algorithm name documented next to the keys. **No code reads it today**: the token layer hardcodes `RS256` (the model's `algoritm: str = "RS256"` field, used at `backend/service/shared/tokens.py:49` and `:73`), so an HS256 value here changes nothing. It records the contract and is a candidate for deletion. | yes | `RS256` | `RS256` |
| `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` | Mount targets inside the container, not host paths. These two keys **win** when set and non-empty (resolved in `backend/service/models/auth_models.py`, the `_key_path()` helper); the fallback when both are unset is `backend/service/certs/`, which is what a bare checkout uses. | yes | `/app/secrets/certs/…` | same |

Both are read at **import time**, not per request: an invalid value prevents the process
from starting at all, and a change requires a container restart.

The signing keys are **not** environment variables — they are files:

- `infra/certs/jwt-private.pem` → `/app/secrets/certs/jwt-private.pem`
- `infra/certs/jwt-public.pem` → `/app/secrets/certs/jwt-public.pem`

Paths are resolved in `backend/service/models/auth_models.py`; the algorithm is fixed to
`RS256` in the token layer. Generation and rotation are covered in
[SECRETS.md](SECRETS.md).

## 6. Frontend

| Key | Meaning | Required | Dev | Prod |
|-----|---------|----------|-----|------|
| `REACT_APP_BASE_URL` | API base URL baked into the bundle at **build** time. | no | `/api/` | `/api/` |
| `DADATA_TOKEN` | Reserved client token for the company-lookup autocomplete. Handed to the image as `REACT_APP_DADATA_TOKEN`. **Read by no code today** — the supplier form that used it was deleted with the dead-UI cleanup — but the plumbing is kept wired. | no | empty | empty |

This one deserves an explicit warning. CRA inlines `REACT_APP_*` into the bundle **at
build time**, so the value is frozen into the JavaScript — changing it requires a rebuild,
not a restart. A leftover `http://localhost:8080` in a production bundle would send every
user's browser to *their own machine*: the classic "works for me, broken for everyone"
failure. That is why the manifest ships `/api/` for both modes.

The patch now lives in `frontend/src/shared/api/apiConsts.js:22-47`, which resolves the
base URL in three steps, first match wins:

1. `process.env.REACT_APP_BASE_URL`, trimmed and forced to end in exactly one `/`;
2. `window.location.origin + "/api/"` whenever the bundle is served from a non-loopback
   host — this is what makes the gateway deployment work with no rebuild;
3. the original hardcoded `http://localhost:8080/api/` as the local-development fallback.

So the key is live but never mandatory: an empty value falls through to the same-origin
path. See [ADR/0003-nginx-rewrites.md](ADR/0003-nginx-rewrites.md).

`DADATA_TOKEN` is the opposite case: **reserved and wired end to end, read by nothing.**
The chain is complete — `docker/.env.example:196` declares it, the prod compose file passes
it as the build argument `REACT_APP_DADATA_TOKEN: ${DADATA_TOKEN:-}`
(`docker/docker-compose.yaml:312`; dev injects it as a runtime variable at
`docker/docker-compose.dev.yaml:223`), and `frontend/docker/Dockerfile:102-103` declares
`ARG REACT_APP_DADATA_TOKEN=` with an empty default and re-exports it as `ENV` (the
rationale sits above it at `frontend/docker/Dockerfile:90-101`) — but no
module reads `process.env.REACT_APP_DADATA_TOKEN`. Measured after the frontend restructure,
`grep -rn process.env frontend/src` returns one hit, `REACT_APP_BASE_URL` in
`frontend/src/shared/api/apiConsts.js:16`; the supplier requisites form that consumed the
token was unreachable dead code and was deleted (`docs/KNOWN_GAPS.md` §24 keeps the history,
because the old value is still valid at the issuer until it is rotated). The plumbing is kept
deliberately, so reviving the lookup needs no Dockerfile or compose change; **empty is the
normal value**, and the build must never fail for a missing token.

## 7. Gateway (nginx)

| Key | Meaning | Required | Default |
|-----|---------|----------|---------|
| `APP_DOMAIN` | Public hostname: the vhost's `server_name` and the base for redirects. | yes | — |
| `NGINX_PUBLISH_HTTP` | Host port published to container port 80. | no | `8088` |
| `NGINX_PUBLISH_HTTPS` | Host port published to container port 443. The **port** is always published; whether 443 answers TLS depends on `infra/nginx/certs/` having a certificate and key. HTTP is the guaranteed path. | no | `8443` |

The upload ceiling lives in the mounted vhost (`infra/nginx/nginx.conf`), not in the env
file: `client_max_body_size 500m` (`infra/nginx/nginx.conf:71`) is four times the reference
stack's original 200 MB value (`R:/GPTHub/infra/nginx/nginx.conf:30`) because the primary input
here is an uploaded video, not a form post. Raising it is a reviewed configuration-file
change, not a per-deployment override: an env key would let one deployment silently
accept bodies that the proxy timeouts (`proxy_read_timeout 86400s`, `infra/nginx/nginx.conf:120-122`)
and the container memory limit were never sized for.

The gateway also needs to know where to forward, but those targets are **container
names**, not configuration: `backend:8080` and `frontend:80` are resolved by Docker's
embedded DNS at `127.0.0.11` (the `resolver` line in
`infra/nginx/nginx.conf:34`, mirroring the reference stack's
`R:/GPTHub/infra/nginx/nginx.conf:12`), so the gateway never pins an IP address. The
upstreams live in the mounted template (`infra/nginx/templates/site.conf.template:43-44`)
and are used through variables so a recreated container is picked up without a config
reload — see [ADR/0003-nginx-rewrites.md](ADR/0003-nginx-rewrites.md).

## 8. Optional profiles and the platform layer

Two different things live behind these keys and the distinction matters when something
does not work:

- **Compose profiles** are *containers*. **Four** boolean flags in the manifest
  (`CACHE`, `STORAGE`, `CELERY`, `MAIL`) are translated into `--profile` flags by
  `docker/run.sh`, and turning one ON also **exports the keys the application reads**, so
  the flag wires the app instead of only starting a neighbour
  (`docker/sidecar_profiles.sh:128-160`). `ml` is **not** one of them: the sidecar is a
  core service of both stacks and starts with the API that calls it.
- **`GPU` is not a profile either.** It selects no container, it adds a device
   reservation: `GPU=1` merges `docker/docker-compose.gpu.yaml` through
   `configure_gpu_override` (`docker/sidecar_profiles.sh:98-107`). A reservation is a
   **host** requirement — a machine without `nvidia-container-toolkit` fails to start the
   container instead of starting it degraded — which is why it is opt-in and not a profile.
- **Platform flags** are *code paths inside the API image*. They are read with
   `os.environ.get(NAME, default)` by `backend/service/infrastructure/*.py` and
   `ml/service/settings.py`, always accept **both** the
  nested (`CACHE__BACKEND`) and the flat (`CACHE_BACKEND`) spelling, and every one of them
  is **optional**: a missing key falls back to a default instead of raising, which is the
  opposite of the rule in §1.

How far the wiring reaches is not uniform, and pretending otherwise is how a runbook
starts lying:

| Layer | Wired to the request path? |
|-------|----------------------------|
| ML transport (`backend/service/infrastructure/ml_sidecar.py`) | **Yes, always** — `upload_video()` calls `get_user_embedding` (the `matching` slice's application service,
`backend/service/services/matching/`), and the client is HTTP-only, so there is no flag to get wrong and no in-process branch to fall back to |
| cache, storage, mail and jobs facades (`backend/service/infrastructure/*` and the `jobs` slice under `service/services/jobs/`) | **No** — complete and tested, used by the worker tasks, but no route calls them yet ([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)) |

> **Where these keys live today.** All of them are manifest keys, in sections 11-15 of
> `docker/.env.example`: **89 keys in total, the four profile switches included** (there is
> no fifth). `python docker/sync_env.py --check` verifies that the template and both
> deployment files declare the same set. Most *wiring* keys ship in their off posture
> (`CACHE__BACKEND=` empty, `MAIL__ENABLED=false`, `CELERY__ENABLED=false`) and
> `sidecar_profiles.sh` overrides them at run time when the matching flag is ON. A shell
> export wins over `--env-file` in compose interpolation, which is what makes that work; it
> also means a value hard-coded in `.env.dev` is replaced the moment the flag is on. The
> exception is `ML__SERVICE_URL`, which ships **non-empty** (`http://ml:8080`): it is not a
> fallback switch any more, and an empty value would point the client at
> `http://127.0.0.1:8080` inside the API container, where nothing listens.
> `scripts/check_env_parity.py` can only see a key that a compose file interpolates, so an
> application-only key is verified by reading `backend/docs/PLATFORM_LAYER.md`, not by the
> guard.

The authoritative matrix (every key, every default, every failure mode) is
[backend/docs/PLATFORM_LAYER.md](../backend/docs/PLATFORM_LAYER.md). This section records
only the deployment view: which key to set in the env file, and what breaks without it.

### 8.0 Profile switches

| Key | Default | Container it starts (profile) | Wiring it exports when ON |
|-----|---------|-------------------------------|---------------------------|
| `CACHE` | `1` | `cache` — `redis:7.2-alpine` | `CACHE__BACKEND=redis`, `CACHE__REDIS_URL` (default `redis://cache:6379/1`) |
| `STORAGE` | `1` | `storage` — `minio/minio` | **none** — `STORAGE__BACKEND` stays `local` (§8.2) |
| `CELERY` | `0` | `celery` — three workers plus `celery-beat` | `CELERY__ENABLED=true`, `CELERY__BROKER_URL`, `CELERY__RESULT_BACKEND` |
| `MAIL` | `0` | `mail` — `mailpit` | `MAIL__ENABLED=true`, `MAIL__BACKEND=smtp`, `MAIL__HOST=mailpit`, `MAIL__PORT=1025`, `MAIL__STARTTLS=false`, `MAIL__SSL=false` |

Four switches, four containers, and nothing else. `docker/run.sh:159-162` reads them **out
of the env file**, overwriting anything exported in the shell, so the file is the only
source of truth for which containers the next invocation manages; `GPU` is read on the next
line (`:163-167`). The wiring exports happen afterwards, inside
`configure_sidecar_profiles` (`docker/sidecar_profiles.sh:48-85` for the profiles,
`:128-160` for the exports, `:98-107` for the GPU override).

One dependency crosses those flags: **`CELERY=1` forces `CACHE=1`.** A worker without
a broker would retry a connection no container answers, so the cache profile is turned on
and a warning is printed (`docker/sidecar_profiles.sh:56-60`). Nothing else is coupled,
and there is no `ML` switch at all: inference holds no shared state and has nothing to wire.
See [SIDECARS.md §1](SIDECARS.md#1-how-the-flags-work).

### 8.1 `cache`

The `redis:7.2-alpine` container itself needs **no keys**: no password, no published
port, `--save 60 1 --loglevel=warning`, on the internal (no-egress) network only.

The *client* side is configured inside the API image and inside every worker, because both
inherit the same YAML anchor (`x-backend-env`, `docker/docker-compose.yaml:64-125`):

| Key | Default | Effect when set |
|-----|---------|-----------------|
| `CACHE__BACKEND` | *(empty)*, i.e. `memory` | `redis` switches the facade to Redis; `CACHE=1` exports it. Anything else stays in-process |
| `CACHE__REDIS_URL` | `redis://cache:6379/1` | Must name the `cache` service inside compose (`localhost` is the API container itself) |
| `CACHE__PREFIX` | `hiresnap` | Key namespace; two stacks sharing one Redis must differ |
| `CACHE__TTL` | `300` | Default entry lifetime in seconds |
| `CACHE__MAX_ENTRIES` | `2048` | Bound for the in-memory backend only |
| `CACHE__TIMEOUT` | `0.5` | Socket timeout; a slow cache must lose to a fast miss |
| `CACHE__RETRY_BACKOFF` | `30` | Seconds to stop retrying after a failure (fail-open) |

Redis db **1**, not 0, because the celery broker lives in the same instance; a debugging
`FLUSHALL` against db 0 must not be able to erase job state.

**What breaks without it:** nothing. `CACHE__BACKEND=memory` is a complete
implementation — but it is **per process**, which is why job state
(`JobService.status()`, or the `status()` function on the jobs application module) only
knows about jobs enqueued in the same process until the redis backend is in use. That is the
one place where the cache backend is not merely an optimisation.

### 8.2 `storage`

The manifest declares an S3-shaped contract; the compose file renames two of its keys in
the `storage` service because the MinIO image has its own names:

| Manifest key | Container variable | Meaning |
|--------------|--------------------|---------|
| `STORAGE__ACCESS_KEY` | `MINIO_ROOT_USER` | Root user **and** S3 access key; compose requires $STORAGE__ACCESS_KEY with `:?` |
| `STORAGE__SECRET_KEY` | `MINIO_ROOT_PASSWORD` | Root password **and** S3 secret key; likewise required |
| `STORAGE__ENDPOINT` | also `STORAGE_ENDPOINT` in the API | `minio:9000`; the endpoint the S3 backend uses |
| `STORAGE__BUCKET` | also `STORAGE_BUCKET` in the API | Bucket name reserved for uploads |
| `STORAGE__SECURE` | also `STORAGE_SECURE` in the API | `false` inside the network |

The facade keys:

| Key | Default | Effect when set |
|-----|---------|-----------------|
| `STORAGE__BACKEND` | `local` | `s3` switches the facade to MinIO/S3; `local` writes under `DATA_PATH` |
| `STORAGE__REGION` | `us-east-1` | Required by the S3 signature even for MinIO |
| `STORAGE__PREFIX` | `hiresnap` | Key prefix inside the bucket |
| `STORAGE__PRESIGN_TTL` | `3600` | Lifetime of a presigned URL in seconds |

**`STORAGE=1` starts MinIO but does not flip `STORAGE__BACKEND`, and that asymmetry
is deliberate.** The upload path still writes through
`backend/service/infrastructure/uploads.py` into `DATA_PATH`, while the worker side reads
through this facade. Flipping the backend to `s3` while the upload path stays local
would write uploads to a bind mount and then look for them in MinIO — a split brain that
fails one task at a time, days later. The compose anchor says so at
`docker/docker-compose.yaml:94`. Switching it is a one-line change, a separate
decision from starting the container.

**What breaks without it:** nothing today — no API route calls the facade. Once one does,
`STORAGE__BACKEND=s3` without a reachable MinIO is a **hard** failure
(`StorageError`, a 500): storage fails closed on purpose, because a silent fallback to
the local disk would split the media across two backends.

The `minioadmin` placeholder pair is acceptable for a local dev stack **precisely
because nothing is published**, and unacceptable in prod — which is why the compose file
uses `:?` there. Keeping the odd keys (bucket, region, prefix) in the manifest is what
makes `docker/sync_env.py --check` fail the day a deployment file loses them.

### 8.3 `ml`

The sidecar is a **core service**, not an optional one: it starts with both stacks
(`profiles: ["prod"]` / `["dev"]`) and is built from its own image,
`ml/docker/Dockerfile`, on port `8080`, with no database, no dependency on MySQL and no
auth. It is stateless: bytes in, vectors out. `ml` is its own git submodule and the only
place the model stack lives ([ADR/0006](ADR/0006-ml-sidecar-owns-inference.md)).

The keys split in two, and the split is the point: the manifest keeps the **double
underscore** nesting spelling, and the compose `environment:` block of the `ml` service is
the single translation point to the **flat** names `ml/service/settings.py` reads
(`docker/docker-compose.yaml:464-506`). A key that reached the sidecar as `ML__PORT` would
be ignored, and the image would bind its default.

| Key | Default | Meaning |
|-----|---------|---------|
| `ML__SERVICE_URL` | `http://ml:8080` | What the **API** reads, flat as `ML_SERVICE_URL`. Not a switch: the client is HTTP-only, and an empty value falls back to `http://127.0.0.1:8080`, where nothing listens |
| `ML__HOST` | `0.0.0.0` | Bind address inside the container; `127.0.0.1` would make the healthcheck and the API both miss |
| `ML__PORT` | `8080` | In-container port; the image healthcheck, the compose healthcheck and the client default all assume it |
| `ML__CONNECT_TIMEOUT` | `10` | Client connect timeout, exported to the API as `ML_SERVICE_CONNECT_TIMEOUT` |
| `ML__TIMEOUT` | `900` | Client read timeout for one inference call, exported as `ML_SERVICE_TIMEOUT`. A cold GPU load takes minutes |
| `ML_DEVICE` | *(auto)* | Flattened to `DEVICE`: `cuda` or `cpu`; empty decides at first model load |
| `ML_MAX_UPLOAD_MB` | `512` | Flattened to `MAX_UPLOAD_MB`, the per-request media ceiling (413 above it) |
| `ML_MAX_TEXT_CHARS` | `20000` | Flattened to `MAX_TEXT_CHARS`, the text ceiling for `/v1/embed/text` |
| `ML_MODEL_IDLE_UNLOAD_SECONDS` | `0` | Flattened to `MODEL_IDLE_UNLOAD_SECONDS`: free VRAM after this many idle seconds; `0` means never |
| `NVIDIA_VISIBLE_DEVICES` | `all` | GPUs the runtime exposes: `all` or a list such as `0,1` |
| `NVIDIA_DRIVER_CAPABILITIES` | `compute,utility` | Driver capabilities the CUDA stack needs |
| `WEIGHTS_DIR` | `../infra/models/weights` | **Host** directory (relative to `docker/`) mounted read-only at `/app/models/weights`, where `Unet3D.pth` is expected |
| `PRETRAINED_CKPT_DIR` | `../infra/models/pretrained_ckpts` | **Host** directory mounted read-only at `/app/models/pretrained_ckpts` |
| `HF_HOME` | `/app/.cache/huggingface` | Container path of the Hugging Face cache, a named volume, so downloads survive recreation |

`WEIGHTS_DIR` and `PRETRAINED_CKPT_DIR` are **host** paths used by the compose `volumes:`
block; the container-internal keys of the same name hold the mount targets and are
literals in the `ml` service's `environment:` block (`docker/docker-compose.yaml:487-490`).
Passing the host's relative path into the container would resolve it against the WORKDIR and
load nothing, which is why the two spellings exist and why the mount is the only place the
host path appears.

`GPU` is not in this table because it is not read here: it is a compose switch (§8.0) that
adds `docker/docker-compose.gpu.yaml`, whose only content is the `nvidia` device
reservation. `GPU=0` on a CPU-only host is a working, slow deployment, not a broken one.

**What breaks without it:** every upload. `ml` is the only inference path, so with the
container down `upload_video()` (the `matching` slice's application service) fails with `MLUnavailable`
(`ml_service_unreachable`, a 503) instead of falling back to a local model. That is the
accepted cost of the split, recorded in [KNOWN_GAPS.md §23](KNOWN_GAPS.md#23-the-sidecar-is-the-only-inference-path-and-it-serialises-gpu-work).
The proof that a request really crossed the network is
[RUNBOOK.md §14](RUNBOOK.md#14-proving-the-sidecar-is-used);
the contract is [SIDECARS.md §4](SIDECARS.md#4-ml--inference-sidecar).

### 8.4 `mail`

Mail is a **code path plus an optional container**. The code path is
`backend/service/infrastructure/mail.py`; the container is `mailpit` (an SMTP sink with a web
inbox), which starts only under the `mail` profile. With `MAIL=0` the facade stays
on the `file` backend, so an unconfigured stack needs no SMTP server at all.

| Key | Default | Meaning |
|-----|---------|---------|
| `MAIL` | `0` | Start `mailpit` **and** export the six `MAIL__*` keys below (profile switch, §8.0) |
| `MAIL__ENABLED` | `false` | Nothing is sent unless this is true |
| `MAIL__BACKEND` | `file` | `file` appends to `MAIL__FILE_PATH`; `smtp` talks to `MAIL__HOST` |
| `MAIL__HOST` | *(empty)* | SMTP host; `smtp` without it degrades to the disabled mailer with a warning. `MAIL=1` exports `mailpit` |
| `MAIL__PORT` | *(empty)*, backend default | SMTP port. `MAIL=1` exports `1025`, mailpit's plain port |
| `MAIL__USER`, `MAIL__PASSWORD` | *(empty)* / placeholder | SMTP credentials; an empty user means no AUTH is attempted |
| `MAIL__FROM` | `no-reply@hiresnap.local` | Envelope sender |
| `MAIL__STARTTLS` | `true` | STARTTLS on the plain port. `MAIL=1` forces `false`, because mailpit offers none |
| `MAIL__SSL` | `false` | Implicit TLS on connect (mutually exclusive with STARTTLS in practice) |
| `MAIL__TIMEOUT` | `10` | SMTP socket timeout in seconds |
| `MAIL__FILE_PATH` | `/app/logs/mail_outbox.log` | Where the `file` backend appends; `/app/logs` is the `infra/logs` bind mount, so the outbox is readable on the host |
| `MP_MAX_MESSAGES` | `5000` | Mailpit inbox bound; a catcher that grows forever is a memory leak with a nice UI |
| `MAILPIT_PUBLISH_PORT` | `8025` | **Dev only** host port for the inbox, i.e. http://localhost:8025 |

Every `__`-prefixed switch in this section - `MAIL__ENABLED`, `MAIL__STARTTLS`, `MAIL__SSL`, `STORAGE__SECURE`, `CACHE__*` - goes through the same strict parser
(`backend/service/configuration/flags.py:63-96`): absent means the default, and a present value
nobody meant raises `FlagValueError` instead of quietly reading as off. That is deliberate; see
[KNOWN_GAPS.md §35](KNOWN_GAPS.md#35-every-boolean-except-debug-could-be-typod-into-off-fixed).

**What breaks without it:** nothing; with `MAIL__ENABLED=false` the disabled mailer is a
no-op. Request-path sends use `send_now_or_later`, which swallows every failure on
purpose — a dead SMTP relay must not fail a signup. No API route sends mail today
([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)), so the
inbox is a way to exercise the facade after a code change, not a feature you will see
traffic in.

### 8.5 `celery` (workers and beat)

Celery is a code path **and** four containers. Under the `celery` profile compose starts
`celery-worker-agents`, `celery-worker-maintenance`, `celery-worker-ml` and a
single `celery-beat` (`docker/docker-compose.yaml:579-768`). The workers run the same
image as the API — the YAML anchor gives them the same environment and the same mounts, so
a task reads the same uploads and certificates as the process that queued it — and they
differ only in `--queues` and `--concurrency`. **No worker takes a GPU reservation**, not
even `celery-worker-ml`: that worker calls the sidecar over HTTP like the API does, which
is why the `celery` profile works on a CPU-only host.

| Key | Default | Meaning |
|-----|---------|---------|
| `CELERY` | `0` | Start the three workers and beat **and** export `CELERY__ENABLED=true` (profile switch, §8.0) |
| `CELERY__ENABLED` | `false` | `false` runs every `dispatch()` inline; `true` sends to the broker |
| `CELERY__BROKER_URL` | `redis://cache:6379/1` | The broker. Needs the `cache` container, which `CELERY=1` guarantees |
| `CELERY__RESULT_BACKEND` | `redis://cache:6379/1` | Same instance, db `1` — operational state, never business data |
| `CELERY__CLEANUP_INTERVAL_SECONDS` | `3600` | Beat interval for orphan-upload cleanup |
| `CELERY__REINDEX_INTERVAL_SECONDS` | `86400` | Beat interval for embedding reindex |
| `CELERY_AGENTS_CONCURRENCY` | `2` | Prefork children of the `agents` worker (`--queues=agents`) |
| `CELERY_MAINTENANCE_CONCURRENCY` | `1` | Children of the `maintenance` worker; its work is I/O bound |
| `CELERY_ML_CONCURRENCY` | `1` | Children of the `ml` worker; each child can hold a GPU |

The concurrency numbers differ per queue because **a queue boundary is a failure domain,
not a priority lane** — `backend/service/infrastructure/messaging/celery_app.py`
documents the queue split: a maintenance sweep that walks the storage tree must never sit
in front of a user's upload, and an embedding job that holds a GPU for minutes must not
delay either. The `ml` worker keeps `1` child for the same reason the sidecar runs
`workers=1` — one GPU, one queue of work.

**What breaks without it:** nothing, and enabling `CELERY__ENABLED=true` with no worker
makes work *worse*, not better — tasks are queued and never consumed. That is why the
switch is one key and one profile, exported together: `CELERY=1` starts the workers and
`CELERY__ENABLED=true` in the same decision. Operate it with
[RUNBOOK.md §15.1](RUNBOOK.md#151-is-a-worker-consuming).

## 9. Dev vs prod at a glance

| Concern | `docker/.env.dev` | `docker/.env.prod` |
|---------|--------------------|---------------------|
| `COMPOSE_MODE` | `dev` | `prod` |
| Compose file it pairs with | `docker/docker-compose.dev.yaml` | `docker/docker-compose.yaml` |
| `DEBUG` | `TRUE` — reload, and the schema is created on start | `FALSE` — never touches the schema |
| Published ports | gateway 8088/8443, backend 8080, frontend 3000, minio console 9001 (`STORAGE=1`), `mailpit` 8025 (`MAIL=1`). `ml` publishes nothing in either mode | gateway only |
| Secrets | template placeholders are tolerated | every secret uses `${VAR:?...}` |
| `PUBLIC_BASE_URL` | `http://localhost:8088` | the real public origin |
| Profiles on by default | `cache`, `storage` (see caveat below) | `cache`, `storage` |
| Profiles off unless asked | `celery`, `mail` | `celery`, `mail` |
| Not a profile at all | `ml` (core service of both stacks) and `GPU` (a one-block override file) | the same |
| Wiring exported by the flags | `CACHE__BACKEND=redis`; `CELERY__ENABLED`, `CELERY__BROKER_URL`, `CELERY__RESULT_BACKEND`, `MAIL__*` when their flag is on. `ML__SERVICE_URL` is not exported by anything: it ships set | the same, so the flags behave identically in both modes |

The three files declare the **same 89 keys** — that is the set
`python docker/sync_env.py --check` compares, and it is why a key can be documented once
here instead of per mode. It compares the *set*, not the position: a key added to the
template after a deployment file was created is appended at the end by `--mode` under an
`# --- added by sync_env.py from .env.example ---` marker, so a topped-up file keeps
section order only up to the first manifest growth. That is harmless while no value
references another key (none does today); the moment one does, the template header's
define-above rule applies and a blind append is not automatically safe. All three files are
text templates except the two deployment files, which are gitignored and are the only place
real values belong.

Note the profile defaults: `docker/sidecar_profiles.sh:48-85` reads `${CACHE:-1}`,
`${STORAGE:-1}`, `${CELERY:-0}` and `${MAIL:-0}`, so `cache` and `storage` come up unless
you export `CACHE=0` / `STORAGE=0`; `celery` and `mail` stay off until asked for, and
`GPU` defaults to `0`. The defaults encode risk, not taste: the two cheap stateless
sidecars are on because forgetting them yields a slow-but-correct stack, while a missing
GPU yields a failed container start. `.env.example` carries the same four decisions as
`CACHE=1`, `STORAGE=1`, `CELERY=0`, `MAIL=0`, plus `GPU=0`.

One dependency crosses those flags and is the single fact the docs must not contradict:
**`CELERY=1` forces `CACHE=1`.** A worker without a broker would sit in a retry loop
forever, so `configure_sidecar_profiles` turns the cache profile on and says so
(`docker/sidecar_profiles.sh:56-60`). Nothing else is coupled, and no flag controls
inference: the sidecar is always there. See
[SIDECARS.md](SIDECARS.md#1-how-the-flags-work).

### 9.1 The 89 keys, as a set

The manifest is the contract, so here is the whole of it in one place, grouped exactly as
`docker/.env.example` groups it. The counts add up to **89** — the number
`python docker/sync_env.py --check` prints for each of the three files.

| Group (§ in `.env.example`) | Count | Keys |
|-----------------------------|-------|------|
| build and compose (0, 1) | 7 | `PIP_INDEX_URL`, `NPM_REGISTRY`, `DOCKER_BUILDKIT`, `PROJECT_NAME`, `COMPOSE_MODE`, `BACKEND_PUBLISH_PORT`, `FRONTEND_PUBLISH_PORT` |
| API (2) | 6 | `HOST`, `SERVER_PORT`, `DEBUG`, `INIT_DB`, `DATA_PATH`, `PUBLIC_BASE_URL` |
| auth (3) | 5 | `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`, `JWT_ALGORITHM`, `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH` |
| CORS (4) | 1 | `CORS_ALLOW_ORIGINS` |
| database (5, 6) | 9 | `DB__HOST`, `DB__PORT`, `DB__USER`, `DB__PASSWORD`, `DB__NAME`, `MYSQL_ROOT_PASSWORD`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD` |
| storage facade and MinIO (7) | 9 | `STORAGE__ENDPOINT`, `STORAGE__ACCESS_KEY`, `STORAGE__SECRET_KEY`, `STORAGE__BUCKET`, `STORAGE__SECURE`, `STORAGE__BACKEND`, `STORAGE__PREFIX`, `STORAGE__REGION`, `STORAGE__PRESIGN_TTL` |
| frontend (8) | 2 | `REACT_APP_BASE_URL`, `DADATA_TOKEN` (reserved: wired manifest → compose build arg → `frontend/docker/Dockerfile:102-103`, but read by no code today; empty is normal — see §6 and [KNOWN_GAPS.md §24](KNOWN_GAPS.md#24-a-credential-left-the-working-tree-but-is-still-in-git-history)) |
| gateway (9) | 3 | `APP_DOMAIN`, `NGINX_PUBLISH_HTTP`, `NGINX_PUBLISH_HTTPS` |
| profile switches (10) | 4 | `CACHE`, `STORAGE`, `CELERY`, `MAIL` |
| cache facade (11) | 7 | `CACHE__BACKEND`, `CACHE__REDIS_URL`, `CACHE__PREFIX`, `CACHE__TTL`, `CACHE__MAX_ENTRIES`, `CACHE__TIMEOUT`, `CACHE__RETRY_BACKOFF` |
| ml sidecar (12) | 9 | `ML__HOST`, `ML__PORT`, `ML__SERVICE_URL`, `ML__CONNECT_TIMEOUT`, `ML__TIMEOUT`, `ML_DEVICE`, `ML_MAX_UPLOAD_MB`, `ML_MAX_TEXT_CHARS`, `ML_MODEL_IDLE_UNLOAD_SECONDS` |
| mail (13) | 13 | `MAIL__ENABLED`, `MAIL__BACKEND`, `MAIL__HOST`, `MAIL__PORT`, `MAIL__USER`, `MAIL__PASSWORD`, `MAIL__FROM`, `MAIL__STARTTLS`, `MAIL__SSL`, `MAIL__TIMEOUT`, `MAIL__FILE_PATH`, `MP_MAX_MESSAGES`, `MAILPIT_PUBLISH_PORT` |
| celery (14) | 8 | `CELERY__ENABLED`, `CELERY__BROKER_URL`, `CELERY__RESULT_BACKEND`, `CELERY__CLEANUP_INTERVAL_SECONDS`, `CELERY__REINDEX_INTERVAL_SECONDS`, `CELERY_AGENTS_CONCURRENCY`, `CELERY_MAINTENANCE_CONCURRENCY`, `CELERY_ML_CONCURRENCY` |
| GPU and weights (15) | 6 | `GPU`, `NVIDIA_VISIBLE_DEVICES`, `NVIDIA_DRIVER_CAPABILITIES`, `WEIGHTS_DIR`, `PRETRAINED_CKPT_DIR`, `HF_HOME` |

Two rules bite when you edit that file, and neither is enforced by a guard:

1. **A dotenv file is expanded left to right.** A line may reference a key defined earlier
   in the same file (`${ML__PORT}`), never one defined below it — the reference resolves to
   empty and compose interpolates the empty value, silently. Keep a key above every line
   that mentions it. This is why appending a block at the end of the file (which is what
   `sync_env.py --mode` does when the template grew) is safe today and only today: no value
   references another key yet.
2. **The double underscore stops at the compose boundary for the keys that were flattened.**
   `DB__HOST` becomes `DB_HOST` and `ML__PORT` becomes `ML_PORT`; `CACHE__BACKEND`,
   `STORAGE__*` and `MAIL__*` are handed over **unchanged**, because the facades'
   `_first_env` helper looks up several spellings (`CACHE__BACKEND` first, then
   `CACHE_BACKEND`) and compose passing the double-underscore form as a plain environment
   variable is already valid. The single point where any of this is decided is
   `docker/docker-compose.yaml` (`x-backend-env` at `:64-125` for the API and the workers,
   and the `ml` service's own block at `:464-506`, which does flatten). No Python module
   rewrites a name, and `ml/service/settings.py` reads flat names only. Adding a key means
   adding it in both the manifest and the block that passes it, or the container never sees it.

Almost every key in that table now has a reader. The short list that does **not** is:

- `JWT_ALGORITHM` — the token layer hardcodes `RS256`
  (`backend/service/shared/tokens.py:49`);
  the key documents the contract and nothing reads it.
- `APP_DOMAIN`, `NGINX_PUBLISH_HTTP`, `NGINX_PUBLISH_HTTPS` — consumed by compose when it
  interpolates `infra/nginx/templates/site.conf.template` and the published ports, never by
  Python.

Everything that used to sit on that list has acquired a reader, and this is the part that
had rotted: `CORS_ALLOW_ORIGINS` is read by `_cors_settings()` (§3.2), `STORAGE__*` by
`backend/service/infrastructure/storage.py:440-454`, `WEIGHTS_DIR` / `PRETRAINED_CKPT_DIR` /
`HF_HOME` by the sidecar (§8.3), `INIT_DB` by the startup gate
(`backend/service/main.py:75`), and `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` by the
`_key_path()` helper in `backend/service/models/auth_models.py`. Every section above marks its own keys.

## 10. Validating your env file

```bash
# 1. Create or top up the file from the template (append-only, never rewrites a value).
python docker/sync_env.py --mode dev      # or: make env
# 2. Prove the three files still declare the same key set.
python docker/sync_env.py --check
# 3. Prove the file interpolates against the real compose file.
cd docker && docker compose --env-file .env.dev \
  -f docker-compose.dev.yaml config --quiet
```

Step 3 is the one that catches a missing `${VAR:?...}`. It prints nothing on success.
On failure, **do not paste its raw output into a ticket** — compose can echo interpolated
values, which is exactly why the reference preflight script collapses the diagnostic to a
key name (`R:\GPTHub\docker\compose_preflight.sh:5-17`), and why the local
`docker/compose_preflight.sh:23-28` keeps a whitelist of four key names
(`DB__USER`, `DB__PASSWORD`, `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`) and reports
anything else as `code=invalid_configuration`. `docker/run.sh` sources that preflight
and runs it before every `up`, so the sanctioned way to see the bounded diagnostic is
`./docker/run.sh --dev up` (or `make run-dev`): it fails before creating a container and
prints only the key name. There is no separate `--check` action on `run.sh`; the static
key-set and interpolation checks are `python scripts/check_env_parity.py` (also `make guards`).
