# Runbook

Operational procedures for the HireSnap stack. Every command is written for the
superproject root (`R:\HireSnap` on Windows, or the checkout root anywhere else). If a
step fails, jump to §10 Troubleshooting before improvising — most failures have a known
cause documented there.

> Read [KNOWN_GAPS.md](KNOWN_GAPS.md) before the first production deploy. Several
> application defects are deliberately untouched, and this runbook tells you what they
> look like in operation rather than promising they are fixed.

## 1. Entry points — and why not a bare `docker compose`

The stack is driven by two shell scripts in `docker/`. They are the **only** supported
entry points.

```bash
./docker/run.sh --dev  up                # start the dev stack
./docker/run.sh --prod up                # start the prod stack
./docker/run.sh --prod down [--volumes]  # stop (--volumes also deletes named volumes)
./docker/run.sh --dev  logs [SERVICE]    # follow logs
./docker/run.sh --dev  ps                # container status
./docker/run.sh --dev  exec backend bash # shell into a service

./docker/build.sh --dev                  # backend + frontend + ml images
./docker/build.sh --prod
./docker/build.sh --prod --celery        # the backend image is reused by the workers
./docker/build.sh --prod --no-cache      # full rebuild

python docker/sync_env.py --mode dev     # create/top-up docker/.env.dev from .env.example
python docker/sync_env.py --check        # verify all three env files declare the same keys
```

Run `./docker/run.sh --help` (or `--dev --help`) for the same list: the help text is the
script's own header comment, extracted with `sed`, so it cannot drift from the code
(`docker/run.sh:57-63`).

**A bare `docker compose up` is not equivalent** (`docker/run.sh:12-14`). It skips:

| Skipped | Consequence |
|---------|-------------|
| `--env-file .env.<mode>` | Compose reads the shell environment instead of the deployment file. Values you edited in `.env.dev` are ignored. |
| `-p PROJECT_NAME` | The project name comes from the shell, so you can start dev containers inside the prod project. |
| `--profile dev` / `--profile prod` | Every service declares `profiles:`, so `up` starts **nothing at all**. |
| the sidecar `--profile` flags | `CACHE` / `STORAGE` / `CELERY` / `MAIL` from the env file are not applied (`docker/sidecar_profiles.sh`). |
| the preflight | A missing required variable fails halfway through a rollout instead of before it. |
| the GPU override | `GPU=1` in the env file never merges `docker-compose.gpu.yaml`, so the sidecar has no device. |
| the template fingerprint | An edited nginx template is never loaded until someone restarts the gateway. |

`run.sh` builds one compose command array (`docker/run.sh:189`) and reuses it for every
action, so no action can accidentally run against a different project or env file
(mirror of `R:/GPTHub/docker/run.sh:240`).

> **`make` targets.** The root `Makefile` is a thin wrapper over exactly these
> scripts: `make env`, `make build`, `make run-dev`, `make run-prod`, `make logs`,
> `make down`, `make ps`, `make smoke`, `make guards`, `make test` and
> `make config`. Every target shells into `docker/run.sh` or `docker/build.sh` rather
> than calling compose itself, so a command typed by hand and a command run through
> `make` cannot diverge. The direct script form is used throughout this runbook because
> it works on a machine with no `make`, and because it makes the flags visible.

## 2. Prerequisites

| Requirement | Why |
|-------------|-----|
| Docker Engine 24+ with Compose v2 (`docker compose`, not `docker-compose`) | The stack uses profiles and `depends_on: condition: service_healthy`. |
| `openssl` | Generates the RSA key pair the backend signs JWTs with. |
| `bash` + coreutils (`awk`, `sha256sum`, `find`) | `run.sh`, `build.sh` and the preflight are Bash. On Windows run them from Git Bash or WSL. |
| ~6 GB free disk | MySQL image, backend image, the CRA build, and the CUDA sidecar image with its torch cu116 download. |
| Free host ports | `8088` (gateway HTTP) and `8443` (gateway HTTPS, published even when TLS is off); in dev also `8080` (backend), `3000` (frontend), `9001` (MinIO console when `STORAGE=1`) and `8025` (Mailpit when `MAIL=1`). |

No local Python or Node installation is needed to *run* the stack; both live inside the
images. You need them only for `sync_env.py` (stdlib-only, Python 3.9+), the guard
scripts, and to run tests outside Docker.

```bash
docker --version && docker compose version && openssl version && python3 --version
```

## 3. First run (development)

```bash
python docker/sync_env.py --mode dev   # 1. create docker/.env.dev from .env.example
# 2. fill DB__USER, DB__PASSWORD, MYSQL_ROOT_PASSWORD, MYSQL_PASSWORD, PUBLIC_BASE_URL
./docker/run.sh --dev up               # 3. build-if-needed + start hiresnap-dev
```

`sync_env.py` is preferred over `cp` because it is **append-only and value-blind**: it
adds keys that the manifest declares but the deployment file is missing, never rewrites an
existing value, never prints a value, and writes atomically (mkstemp + fsync + replace,
mode 0600, refuses symlinks — `docker/sync_env.py`). `cp` works for the very first
creation and silently leaves you with a stale file after every manifest change.

What each step does:

1. `docker/.env.example` is committed; `docker/.env.dev` and `docker/.env.prod` are not
   (`.gitignore`). All three declare the same **89 keys**; `python docker/sync_env.py
   --check` compares the three *sets* and prints the count it compared. It does not compare
   position, and it should not: a key added to the template after your file was created is
   appended at the end under a `# --- added by sync_env.py from .env.example ---` marker
   rather than spliced into its section, so a topped-up deployment file is in section order
   only up to the first manifest growth. That append is safe today only because no value
   in these files references another key; if one ever does, the template header requires
   the source to be defined above its referrer, and a blind append can break it. Never
   edit the example in place — your local values would then look like a manifest change in
   `git diff`.
2. The four keys the compose file refuses to start without are `DB__USER`,
   `DB__PASSWORD`, `MYSQL_ROOT_PASSWORD` and `MYSQL_PASSWORD` — written in the
   required-value form ${KEY:?human message}, mirroring
   `R:/GPTHub/docker/docker-compose.yaml:124-129`; the whitelist the preflight checks is
   `docker/compose_preflight.sh:23-28`. `PUBLIC_BASE_URL` is required by the backend
service specifically (`docker/docker-compose.yaml:71`), and the two `STORAGE__*`
   credentials are required only when the `storage` profile is on.
3. `up` runs the preflight, recreates the gateway if a template changed, then
   `compose up -d`. Images are built only if they do not exist yet —
   `./docker/build.sh --dev` first if you want the build output.

Check it:

```bash
./docker/run.sh --dev ps                       # every service Up / healthy
curl -fsS http://localhost:8088/health         # {"status":"ok","service":"gateway"}
curl -fsS http://localhost:8088/server/openapi.json | head -c 200
```

`run.sh` prints the URLs on success (`docker/run.sh:345-371`) and says explicitly that
container health was **not** waited on — `up -d` returns as soon as containers are created.

## 4. Production run

```bash
python docker/sync_env.py --mode prod
# edit docker/.env.prod: DB__USER, DB__PASSWORD, MYSQL_ROOT_PASSWORD, MYSQL_PASSWORD, PUBLIC_BASE_URL
python docker/sync_env.py --check
./docker/build.sh --prod
./docker/run.sh --prod up
```

Differences from dev, and nothing else:

| | dev | prod |
|---|---|---|
| Compose file | `docker/docker-compose.dev.yaml` | `docker/docker-compose.yaml` |
| Project name (`-p`) | `hiresnap-dev` | `hiresnap-prod` |
| Published ports | gateway 8088/8443, backend 8080, frontend 3000 (`STORAGE=1` also 9001, `MAIL=1` also 8025). `ml` publishes nothing in either mode | **gateway only** (8088/8443) |
| `DEBUG` | `TRUE` (uvicorn reload is on) | `FALSE` |
| `INIT_DB` | `true` | `false` |
| Schema source | MySQL init hook, and/or `CreateSQL` when `DEBUG=TRUE` | MySQL init hook only, on a fresh volume (§8) |
| Volume suffix | `mysql_data_dev`, `redis_data_dev`, `minio_data_dev`, `ml_scratch_dev`, `hf_cache_dev` | `mysql_data`, `redis_data`, `minio_data`, `ml_scratch`, `hf_cache` |

`docker/docker-compose.dev.yaml` is **standalone, not an overlay** (:4-8): it repeats every
service. That is deliberate — an overlay makes a service's final shape the sum of two
files, and "which file sets `DEBUG`" becomes a search problem.

The project name namespaces containers, volumes and networks, so dev and prod cannot
collide (`hiresnap-prod-gateway-1`). They *do* share the host bind mounts under
`infra/`: `infra/storage` (uploads), `infra/certs` (JWT keys), `infra/models`
(weights) and `infra/logs`. Running both stacks at once against the same checkout is
therefore only safe if you accept that they write the same upload directory and the same
log file.

## 5. Building images

```bash
./docker/build.sh --dev
./docker/build.sh --prod --no-cache
./docker/build.sh --prod --celery      # also pull/refresh what the workers need
```

- **Three images, twelve services.** `backend/docker/Dockerfile` builds the API and is
  reused unchanged by the three celery workers and by `celery-beat`; the same image runs
  `python -m service.main` (`backend/docker/Dockerfile:233`) and
  `celery -A service.infrastructure.messaging.celery_app worker`, because layer 2b installs
  `backend/requirements-platform.txt` (celery, redis, boto3) alongside the application's
  own requirements. The frontend image is built from `../frontend`. The third image is the
  sidecar, built from `../ml` with `ml/docker/Dockerfile` — a CUDA base with its own pinned
  requirements, sharing no layer with the API image beyond the distro.
- **The sidecar is always built.** `docker/build.sh` has no `--ml` flag because `ml` is a
  core service: `SERVICES=(backend frontend ml)` (`docker/build.sh:154`). It is listed last
  because it is the slow one, and a failure in the cheap images is worth seeing first. The
  flags it does take add images for the optional profiles (`--cache`, `--storage`,
  `--celery`, `--mail`).
- **Two build contexts, three images.** The backend and the workers build from `..` — the
  superproject root — so the Dockerfile can copy `backend/` as a subdirectory
  (`docker/docker-compose.yaml:226-227`). The sidecar builds from `../ml`
  (`docker/docker-compose.yaml:458-459`), which is what makes it self-contained: its
  Dockerfile cannot reach `backend/` even by accident, and `ml/.dockerignore` decides what
  the daemon sees.
  Because the context is the superproject, **`docker build backend/` is not a supported
  path**: every `COPY` in that Dockerfile is written with the `backend/` prefix
  (`backend/docker/Dockerfile:145`, `:173`, `:196`, `:197`), so a build rooted at
  `backend/` fails at the first one with `failed to compute cache key: ...
  /backend/requirements.txt not found`. `backend/.dockerignore` therefore filters only a
  future standalone Dockerfile and is read by nothing today — its own header says so
  (`backend/.dockerignore:1-30`).
- **Two requirements files, two audiences.** `backend/requirements.txt` is fully pinned and
  contains **no** GPU framework — no torch, no transformers, no moviepy — only the business
  dependencies plus numpy, pandas and scikit-learn. `backend/docker/requirements.lock.txt`
  is a pinned **snapshot** of it (no hashes, no transitive closure), installed when
  `REQUIREMENTS_FILE` is overridden. The model stack lives in
  `ml/docker/requirements.txt`, pinned to `torch==1.12.1+cu116` and friends, and is the only
  place those pins exist. See [KNOWN_GAPS.md](KNOWN_GAPS.md) §4 and §15.
- The frontend build runs `npm run build` and copies `build/` into a static nginx image.
  `REACT_APP_BASE_URL` is a **build argument**, inlined at this step — rebuilding is the only
  way to change it. In dev the same variable is injected at runtime, which is why the dev
  bundle can be pointed at `http://localhost:8080/api/` without a rebuild.
- **Dev mounts the sources; prod does not.** The dev backend binds **one** directory —
  `../backend/service` onto `/app/service` (`docker/docker-compose.dev.yaml:91`) — because
  `/app/service` is the only importable package in the image and uvicorn's reloader only
  watches paths it can stat. The dev `ml` service binds `../ml/service:/app/service:ro`
  (`docker/docker-compose.dev.yaml:379`). An edit to `ml/service/app.py`, `engine.py` or
  `settings.py`, or to any module under `backend/service/`, is therefore picked up on the next
  request instead of the next image build. Earlier revisions bound `backend/src`,
  `backend/env.py` and `backend/create_sql.py` individually; all three are deleted, and the
  comment at `docker/docker-compose.dev.yaml:78-96` records why a bind mount of a path that
  no longer exists is worse than no mount at all. Prod mounts no source: what you built is
  what runs.

## 6. Logs

```bash
./docker/run.sh --dev logs                     # follow everything
./docker/run.sh --dev logs gateway backend     # two services
./docker/run.sh --dev logs --tail=200 mysql    # extra args pass through to compose
```

`run.sh logs` forwards to `docker compose logs -f`; every argument after the action is
passed through (`docker/run.sh:313-320`).

Where application logs actually go:

- **Backend** — two things configure logging, and they write the same file.
  `service/configuration/logging.py` installs the process logger (`setup_logging()`, exposed
  as `get_logger()`), pointing a `FileHandler` at `logs_dir()/app.log`
  (`backend/service/configuration/logging.py:43-46`, `:117-124`). `service/main.py` then
  loads `backend/service/configuration/logging.yaml` with `logging.config.dictConfig` before
  it starts uvicorn (`backend/service/main.py:58-69`); that YAML's file handler is the
  relative `logs/app.log` (`backend/service/configuration/logging.yaml:8-12`), which resolves
  against the process CWD `/app`. Both land on `/app/logs/app.log`. Uvicorn's own loggers go
  to the console handler only (`backend/service/configuration/logging.yaml:16-25`), so the
  container's stdout carries the access log while the file carries the application log.
  `../infra/logs:/app/logs` is the bind mount that puts that file on the host
  (`docker/docker-compose.yaml:155`). Without the mount the directory is created inside the
  container and the log dies with it.
- There is **no** `log_config=path_to_logging()` call and no uvicorn `--log-config` flag any
  more: `run_server()` passes `log_config=str(LOGGING_YAML)` to `uvicorn.run`
  (`backend/service/main.py:66`), which is the same YAML by a direct path rather than a
  helper.
- **Gateway** — access log per request on stdout, with the rewritten upstream URI
  (`log_format main`, `infra/nginx/nginx.conf:50-53`; `access_log /dev/stdout` at :58).
  This is the fastest way to see whether `/api/x` was forwarded as `/server/x`.
- **MySQL** — error and slow-query log inside the container: `./docker/run.sh --prod logs mysql`.

Never paste raw compose output into a ticket without checking it: compose can echo
interpolated env values, which may include secrets. The preflight reduces a configuration
failure to a bare key name for exactly this reason — it captures both streams of
`compose config` and prints neither (`docker/compose_preflight.sh:47-55`, mirror of
`R:/GPTHub/docker/compose_preflight.sh:5-17`).

## 7. Health

**The backend does have a health route.** `GET /server/health` answers
`{"status":"ok","service":"backend","version":"<__version__>"}` — three keys, no I/O,
registered on the `/server` sub-app (`backend/service/api/app.py:136-151`) and reached through
the gateway as `/api/health`. Both images probe it: `backend/docker/Dockerfile:229-230`
(`--start-period=180s`) and the compose overrides at
`docker/docker-compose.yaml:272-282`. It replaced the `/server/openapi.json` probe because
the OpenAPI document is rebuilt from every router on each call, and a liveness probe is run
every 30 seconds for the life of the container. Probes, in this order of preference:

| Probe | Command | Green means |
|-------|---------|-------------|
| Gateway liveness | `curl -fsS http://localhost:8088/health` | nginx is up and answering **without the backend**. Served by the gateway itself (`site.conf.template:60-64`), `access_log off`, never proxied. |
| Backend liveness | `curl -fsS http://localhost:8088/server/health` | The FastAPI app imported and the router is up. The body is `{"status":"ok","service":"backend","version":"..."}`, the same shape the gateway and `ml` return. This is what the container healthchecks use. |
| Backend↔DB | `curl -s -o /dev/null -w '%{http_code}' http://localhost:8088/api/vacancies/` | Anything but `503` means the proxy path works; `401`/`403` means the backend is alive and answering. |
| Container health | `./docker/run.sh --prod ps` | Compose health status, driven by the images' `HEALTHCHECK`. |
| MySQL | `./docker/run.sh --prod exec mysql mysqladmin ping -h 127.0.0.1` | The server accepts connections. |

The gateway's `/health` is at the **root**; the API's is under the prefix. `/api/health`
is rewritten to `/server/health` by the unconditional rule
(`infra/nginx/templates/site.conf.template:77-79`), so it reaches the route; the gateway's
own `/health` is an exact-match location (:60-64) and never proxies. Two different probes,
two different meanings — see
[SERVICE_CONTRACTS.md §1.3](SERVICE_CONTRACTS.md#13-where-each-health-route-actually-lives).

The contract these probes *should* satisfy — `{"status","service","version"}` and the error
envelope — is specified in [SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md). The gateway's
`/health` returns `{"status":"ok","service":"gateway"}`: two of the three fields. It proves
nginx is alive and **nothing about the backend**; that separation is intentional, because a
liveness probe that fails when a dependency is down restarts the wrong container.

Container healthchecks, for reference (`docker/docker-compose.yaml`):

| Service | Probe | interval / timeout / retries / start_period | Needs MySQL? |
|---------|-------|---------------------------------------------|--------------|
| backend | `curl -fsS http://127.0.0.1:$SERVER_PORT/server/health` | 30s / 10s / 5 / 180s (:272-282) | **No** — the route does no I/O, so it answers green with MySQL down |
| frontend | `wget -qO- http://localhost` | 30s / 5s / 5, no start period (:315-319) | No — static nginx |
| gateway | `curl -fsS http://localhost/health` | 30s / 5s / 5, no start period (:354-362) | No — static nginx location |
| mysql | `mysqladmin ping -h 127.0.0.1 -u$MYSQL_USER -p$MYSQL_PASSWORD --silent` | 10s / 5s / 10 / 60s (:210-215) | It **is** MySQL |
| cache | `redis-cli ping` | 10s / 5s / 5, no start period (:375-379) | No — Redis only |
| storage | `mc ready local` | 30s / 10s / 3, no start period (:405-409) | No — MinIO only |
| ml | `curl -fsS http://127.0.0.1:${ML__PORT:-8080}/health` | 30s / 10s / 5 / **300s** (:538-546) | No — the sidecar has no database at all |
| celery-worker-agents | `celery -A service.infrastructure.messaging.celery_app inspect ping -d celery@$HOSTNAME --timeout 10` | 60s / 20s / 3 / 60s (:617-622) | No — the probe is a broker round trip, never a query |
| celery-worker-maintenance | same `inspect ping` | 60s / 20s / 3 / 60s (:659-664) | No |
| celery-worker-ml | same `inspect ping` | 60s / 20s / 3 / 60s (:712-717) | No |
| celery-beat | `test -s /tmp/celerybeat.pid && kill -0 $(cat /tmp/celerybeat.pid)` — no broker round trip, the pid file is the proof | 60s / 10s / 3 / 60s (:755-760) | No |
| mailpit | `/mailpit readyz` — answers only when the SMTP and HTTP listeners are both bound | 15s / 10s / 5 / 10s (:792-799) | No |

Eleven of the twelve probes are **DB-free**: only `mysql`'s own probe touches MySQL, and
`backend`'s is deliberately built not to (§8.2 below). That is what makes the
`INIT_DB` asymmetry worth knowing: the *container* can be reconfigured to need MySQL at
startup, but nothing else in the stack does.

> Two kinds of dollar sign appear in those probes, and they are not interchangeable.
> `$${SERVER_PORT}` / `$${HOSTNAME}` are written doubled **so compose leaves them alone**:
> one `$` is consumed by compose, a literal `${SERVER_PORT}` reaches the container, and the
> container's shell expands its own variable. The `ml` probe is the opposite case —
> `${ML__PORT:-8080}` is interpolated **by compose, from the manifest**, because the probe
> string is assembled before the container exists. Do not "fix" either; doubling the ml one
> would hand the container a variable it never sets, and halving a `$$` would freeze a
> host-side value that is only correct on one machine.

**The slow starter is the sidecar, not the API.** `ml` gets 300 s because a first start on a
cold cache downloads Hugging Face weights into `hf_cache` and then builds a CUDA context
(`docker/docker-compose.yaml:538-546`); a shorter start period reports a healthy first boot
as a crash loop. The API's 180 s is now headroom rather than necessity: the backend image
contains no torch (the model stack lives in `ml/docker/requirements.txt`, ADR/0006), so its
import cost is FastAPI, PyMySQL, numpy and scikit-learn plus, when `DEBUG=TRUE`, the schema
check — seconds, not minutes. The compose comment beside it still says otherwise; treat the
number as conservative, not as a measurement of a torch import.

**Measured, not assumed** (Windows host, the refactor virtualenv, no `.env` in the
working directory, six runs): importing `service.api.app` and calling `create_app()`
took 2.1 s, 2.3 s, 3.1 s and 3.6 s on four of them, with two outliers at 6.9 s and 18.7 s
that a fresh temporary directory and a scanning antivirus explain better than the
application does. `create_app()` accounts for about 0.2 s of any run; the rest is FastAPI,
PyMySQL, numpy, pandas and scikit-learn. The app it returns has
seven routes at the root — the three mounts (`/server`, `/public`, `/data`) plus FastAPI's
own `/openapi.json`, `/docs`, `/docs/oauth2-redirect` and `/redoc`. The working directory
afterwards contained the probe script and **nothing else**: no `setup.log`, no `.env`
read, no MySQL connection. That is the property
`backend/tests/test_import_without_infra.py` pins, measured once by hand as well.

`ml` also carries the shortest grace period in the stack, 30 s (`:554`): a shutdown during
an in-flight `/v1/embed/video` kills that one request, the caller gets a transport error and
retries, which is acceptable because the sidecar holds no state. Waiting out the 900 s client
read timeout instead would turn every deploy into a fifteen-minute stall. The workers are the
mirror image — `stop_grace_period: 360s` for the two short-queue workers (`:632`, `:670`) and
`1860s` for `celery-worker-ml` (`:724`), each waiting out its own soft time limit so a task
is not redelivered and run twice.

## 8. Database initialization

There are two independent mechanisms. Know which one applies.

### 8.1 Fresh named volume (first ever boot)

`infra/mysql/init/00-init.sh` is mounted at `/docker-entrypoint-initdb.d` (direct
children only — the image ignores subdirectories, `docker/docker-compose.yaml:195`) and
`backend/database/HireSnap.sql` is mounted read-only at `/opt/hiresnap/HireSnap.sql` (:200). The
script probes for the dump in a fixed order — `$SCHEMA_FILE`, `/opt/hiresnap/HireSnap.sql`,
`/docker-entrypoint-initdb.d/HireSnap.sql`, `/schema/HireSnap.sql`, then a file named
after `$MYSQL_DATABASE` — and **exits non-zero** when none exists rather than starting an
empty database (`infra/mysql/init/00-init.sh`, `infra/mysql/init/README.md`).

The MySQL image executes that directory **only when the data directory is empty**, i.e. on
the very first start of a new volume. This is the only reliable schema path in production.

```bash
./docker/run.sh --prod up
./docker/run.sh --prod logs mysql | grep -i "init"
```

`run.sh` prints this same warning after every `up` (`docker/run.sh:339-343`): the
schema is loaded **only** on a fresh volume.

### 8.2 Development: the application can create the schema

`service/main.py` bootstraps the schema when **both** switches are on: `DEBUG=TRUE` **and**
`INIT_DB` truthy (`backend/service/main.py:72-79`, gate at `:75`). It then calls
`bootstrap_schema()`, which lazily imports `CreateSQL`
(`backend/service/infrastructure/database/schema.py`) and runs `read_sql()`: that connects
without a database name, issues `CREATE DATABASE IF NOT EXISTS`, executes
`backend/database/HireSnap.sql` (resolved by `paths.schema_file()`, which also accepts the
mixed-case name, `backend/service/configuration/paths.py:74-93`), then bulk-inserts the CSVs
in `$DATA_PATH/tables/` for `embeddings`, `files`, `personalities`, `users`,
`info_candidates` and `vacancies`
(`backend/service/infrastructure/database/schema.py:22-23`, `:99-101`).

**The asymmetry that bites: with `INIT_DB` truthy the API needs MySQL to boot, and with
it falsy it does not.** `bootstrap_schema()` runs *before* `run_server()`, and `CreateSQL()`
opens a `pymysql.connect` in its constructor
(`backend/service/infrastructure/database/schema.py:26-33`). So with `DEBUG=TRUE` and
`INIT_DB` truthy and MySQL down, the process raises and exits **before uvicorn binds the
port** (`backend/service/main.py:75-76`: the bootstrap call precedes the `run_server()` at
`:78`). The container never becomes healthy and the healthcheck reports a crash loop, which
is the correct signal: the operator asked for a schema to be loaded.

Set `INIT_DB=false` and the same stack answers: `GET /server/health` returns 200 with MySQL
down, because the route does no I/O — it reports that *the process* is up, not that its
database is. That distinction is deliberate (see [SERVICE_CONTRACTS.md §1](SERVICE_CONTRACTS.md#1-health)),
and it is why `INIT_DB` is a developer convenience rather than a startup step: leaving it
on turns a database outage into an API that will not start at all.

Convenient in dev, and **production must not rely on it**: with `DEBUG=FALSE` it never runs,
and with `INIT_DB` unset or `false` it never runs either — the two are an AND, not an
override. Note what `DEBUG=TRUE` also does: `run_server()` passes `reload=debug` to
`uvicorn.run` (`backend/service/main.py:66-69`), so production with `DEBUG=TRUE` gets a
reloader as well. See [KNOWN_GAPS.md](KNOWN_GAPS.md#6-schema-creation-only-happens-when-debugtrue).

### 8.3 Existing volume: nothing happens automatically

On an existing `mysql_data` volume — a **named volume**, not a directory under `infra/` —
the init directory is not re-run, and `MYSQL_DATABASE`, `MYSQL_USER` and
`MYSQL_PASSWORD` are ignored. To apply a schema change you must do it by hand:

```bash
./docker/run.sh --prod exec -T mysql sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"' \
  < backend/database/HireSnap.sql
```

Check what the volume actually contains:

```bash
docker volume ls | grep hiresnap           # mysql_data  /  mysql_data_dev
./docker/run.sh --prod exec mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e 'SHOW TABLES' hiresnap
```

### 8.4 Resetting a development database

Destroys all data. Development only.

```bash
./docker/run.sh --dev down --volumes     # deletes mysql_data_dev and friends
./docker/run.sh --dev up                 # fresh volume => init hook runs
```

`--volumes` is passed straight through to `compose down`, and `down` does **not**
remove volumes by default (`docker/run.sh:300-311`): dropping the database is a deliberate
act, never a side effect of stopping a stack.

## 9. Mounted state: certs, weights, storage, logs

| Mount | Host path | Container path | Needed by | Failure mode if missing |
|-------|-----------|----------------|-----------|-------------------------|
| JWT keys | `infra/certs/` | `/app/secrets/certs` (ro) | every authenticated route | The pair is resolved from `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH`, which the manifest points at `/app/secrets/certs/*.pem` (`docker/.env.example:92`, `:94`), read before the packaged fallback (the `_key_path()` helper in `backend/service/models/auth_models.py`). Missing keys are not an import failure — signing and verifying fail, so login and every guarded route answer 500. |
| Uploads and embeddings | `infra/storage/` | `$DATA_PATH` (`/app/data`) | upload, embedding, media | Uploads 500; `/data/` returns 404. |
| Model weights | `infra/models/weights/` | `/app/models/weights` (ro) | **`ml` only** | `Unet3D.pth` is missing: `/health` still answers but `/v1/embed/video` returns 503 and names the path. |
| Pretrained checkpoints | `infra/models/pretrained_ckpts/` | `/app/models/pretrained_ckpts` (ro) | **`ml` only** | The Cosmos tokenizer will not load; the first embedding returns 503. |
| Request scratch | named volume `ml_scratch` (`ml_scratch_dev`) | `/app/ml-scratch` | `ml` only | The sidecar falls back to `/tmp` inside the container, so a 300 MB upload lands in the writable layer and disappears on recreate. |
| Backend log | `infra/logs/` | `/app/logs` | `service/configuration/logging.yaml`, `MAIL__FILE_PATH`, worker and sidecar logs | Log dies with the container; no post-mortem, and `mail_outbox.log` is lost on recreate. |
| HF cache | named volume `hf_cache` (`hf_cache_dev`) | `/app/.cache/huggingface` | **`ml` only** | Re-downloaded on every recreate — minutes of cold start, not a failure. |
| MySQL data | named volume `mysql_data` (`mysql_data_dev`) | `/var/lib/mysql` | mysql | **Data loss** if the volume is removed. |
| Redis data | named volume `redis_data` (`redis_data_dev`) | `/data` | cache, celery broker and result backend | Queue and job state are lost; `CELERY=1` with an empty broker is a normal cold start, not a fault. |
| MinIO data | named volume `minio_data` (`minio_data_dev`) | `/data` | storage | Objects are lost; the `storage` profile re-creates the bucket but not the contents. |
| Gateway config | `infra/nginx/` | `/etc/nginx/` (ro; templates rendered at start) | gateway | Gateway fails to start. |

The volume/bind split is deliberate: a MySQL data directory needs real POSIX semantics,
and a Windows bind mount gives it NTFS-over-9p semantics — a documented corruption risk
for InnoDB (`docker/docker-compose.yaml:178-184`). Uploads, by contrast, are files a human
must be able to see, so they are a bind mount.

Generate the JWT pair (also done by the Makefile's `env` target when it is installed):

```bash
mkdir -p infra/certs
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
  -out infra/certs/jwt-private.pem
openssl rsa -pubout -in infra/certs/jwt-private.pem \
  -out infra/certs/jwt-public.pem
chmod 600 infra/certs/jwt-private.pem
```

The filenames are fixed by the application (`backend/service/models/auth_models.py`):
`jwt-private.pem` and `jwt-public.pem`, algorithm `RS256`
(the model's `algoritm` field). A differently named file is simply not found
unless the matching `JWT_*_KEY_PATH` variable points at it. Never overwrite an existing
pair: replacing it invalidates every token already issued. Full key handling is in [SECRETS.md](SECRETS.md).

Weights are needed by the **`ml` sidecar only** — the API never opens a model file, so a
deployment without them still serves every non-ML route and fails one call at a time with
`MLUnavailable`. Inside the sidecar the Cosmos checkpoint resolves through
`settings.cosmos_encoder_path()` — `pretrained_ckpt_dir()/Cosmos-Tokenizer-CV4x8x8/encoder.jit`
(`ml/service/settings.py:148-172`), one function so the readiness probe and the loader
cannot disagree about the artifact — so the host directory must be
`infra/models/pretrained_ckpts/Cosmos-Tokenizer-CV4x8x8/`, and the names in the CLI
examples (`CosmosCV_f4x8x8`, `CosmosCI_f8x8`) are not what the pipeline loads.
`infra/models/weights/Unet3D.pth` is the other required file. Both directories reach the
container through `WEIGHTS_DIR` and `PRETRAINED_CKPT_DIR`, which are **host** paths relative
to `docker/` (the manifest values are `../infra/models/weights` and
`../infra/models/pretrained_ckpts`); the mount targets
`/app/models/weights` and `/app/models/pretrained_ckpts` are literals in the `ml` service's
`volumes:` block (`docker/docker-compose.yaml:520-521`).

Hugging Face models (`facebook/wav2vec2-large-960h-lv60-self` and
`dima806/facial_emotions_image_detection`) are fetched on first use into the `hf_cache`
volume. In an offline environment, pre-seed that volume; see
[SIDECARS.md](SIDECARS.md#4-ml--inference-sidecar).

`infra/certs/`, `infra/models/`, `infra/logs/`, `infra/storage/` and
`infra/mysql_data/` are all gitignored (`.gitignore:50-56`), each for a reason stated
there: key material, multi-gigabyte licence-bound weights, log files that can quote request
data, user uploads, and a reserved mount point in case someone ever switches the database
off a named volume.

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `./docker/run.sh --dev up` aborts with `required variable DB__PASSWORD is missing a value` | The required-value guard fired — by design (`docker/docker-compose.yaml:69`). | Set the key in `docker/.env.dev` (or `python docker/sync_env.py --mode dev`), re-run. The preflight prints the key name, never the value. |
| `environment file not found: docker/.env.dev` | The deployment file does not exist yet. | `python docker/sync_env.py --mode dev` (`docker/run.sh:108-112`). |
| `unknown option: --prd`, exit 2 | Typo in the mode flag. | `./docker/run.sh --help`. An unknown flag exits 2 rather than starting with defaults (`docker/run.sh:83-87`). |
| Every `/api/` request 404s, but `/server/` on the published backend port works | The rewrite is not applied — usually because the vhost was edited back to the `proxy_pass http://backend:8080/server/` form. | The rewrite must be `rewrite ^/api/(.*)$ /server/$1 break;` followed by `proxy_pass $backend_upstream;` with **no** URI (`site.conf.template:77-104`). Recreate the gateway; the template fingerprint only fires when the template file changes (`docker/run.sh:264-294`). |
| `/api/` returns 503 `service_unavailable` | Backend down, still starting, crashed on import, or genuinely slow. | `./docker/run.sh --prod logs backend`; a missing env key raises `AttributeError` at import. Check `./docker/run.sh --prod ps` for health. |
| Backend exits immediately with `AttributeError: Attribute X not found` | A required env key is not in the container env — usually a compose `environment:` mapping missing, not just an env-file key. | Add the mapping. `env_file` alone does not rename keys; `DB__HOST` → `DB_HOST` happens only in `environment:` (`docker/docker-compose.yaml:66`). |
| `RuntimeError: DEBUG must be TRUE or FALSE, got 'true'` | `DEBUG` is neither `"TRUE"` nor `"FALSE"`; the check is a string comparison in `_debug_flag()` (`backend/service/main.py:40-47`). | Use the exact uppercase strings; `true` fails. The old `Exception: Not init debug mode in env file` text is gone — the message now names the value it saw. |
| Login returns 500 | `infra/certs/jwt-*.pem` missing, misnamed, or unreadable by the container user. | Generate the pair (§9); confirm the mount with `./docker/run.sh --prod exec backend ls -l /app/secrets/certs`. |
| Media URLs are `http://0.0.0.0:8080/...` | The URL helper falls back to `HOST`/`SERVER_PORT` when `PUBLIC_BASE_URL` is empty (`backend/service/shared/urls.py:16-20`). | Set `PUBLIC_BASE_URL` in the env file. See [KNOWN_GAPS.md](KNOWN_GAPS.md#2-media-urls-are-built-from-the-bind-address). |
| `Access denied for user 'hiresnap'@'...'` | `DB__PASSWORD` changed but the MySQL user still has the old password (`MYSQL_*` is init-only). | §11. |
| Uploads return 413 | Body larger than `client_max_body_size 500m` (`infra/nginx/nginx.conf:71`). | Raise the value in `infra/nginx/nginx.conf` and recreate the gateway. There is **no** env key for it. |
| Tables missing in prod, backend 500s on first query | `DEBUG=FALSE` (or `INIT_DB` false) means `CreateSQL` never ran, and the init hook was skipped because the volume already existed. | §8.1 / §8.3. |
| `Address already in use` on 8088 | Another stack (or the other project) holds the port. | Set `NGINX_PUBLISH_HTTP=8089` in the env file, or `./docker/run.sh --prod down`. |
| Containers start as `hiresnap-prod-*` when you wanted dev | A shell `COMPOSE_PROJECT_NAME` overrode the file, or `PROJECT_NAME` in the env file is wrong. | `unset COMPOSE_PROJECT_NAME`; `run.sh` always passes `-p` with the `PROJECT_NAME` read from the file, so the file is authoritative (`docker/run.sh:134-138`). |
| `run.sh` warns `COMPOSE_MODE=prod but --dev was requested` | A `.env.prod` was copied to `.env.dev`. | Fix `COMPOSE_MODE` in the file; the warning exists because the visible symptom is "my code changes are ignored" (`docker/run.sh:140-147`). |
| `run.sh` warns the env file is missing keys | The deployment file predates a manifest change. | `python docker/sync_env.py --mode dev` (append-only, never rotates existing values). |
| `make guards` prints `SKIP scripts/check_...: no usable python3/python on PATH` | The first `python.exe` or `python3.exe` on `PATH` cannot start — on Windows it is usually the Microsoft Store stub, which resolves as a file and then exits 49 without running the script. | Nothing was checked, so do not read it as a pass: pass a real interpreter (`make guards PYTHON=python3.13`) or install one. `make lint` resolves the same problem through `scripts/run_guard.sh`, which is why the hook prints a skip instead of a false `Failed` (`scripts/run_guard.sh:1-39`). |
| Edited nginx template has no effect | nginx reads `/etc/nginx/conf.d` at start only. | `run.sh` recreates the gateway when the template fingerprint changes; if you edited after the last `up`, just `up` again (`docker/run.sh:264-294`). |
| `/data/<file>` 404s although the file exists | `DATA_PATH` points somewhere other than the mount, or the object lives under a different `name_object` subdirectory (`backend/service/infrastructure/uploads.py:15-17`). | Compare `DATA_PATH` in the container env with the mount target `/app/data`. |

## 11. Rotating the database password

The trap: `MYSQL_USER` / `MYSQL_PASSWORD` are **image init variables**, honored only when
`/var/lib/mysql` is empty. Editing them in `.env.prod` on a live stack changes nothing,
and because `DB__PASSWORD` *does* change, the application starts failing authentication
while `mysql` still reports healthy.

Correct procedure, no downtime beyond a backend restart:

```bash
# 1. Change the password inside MySQL first.
./docker/run.sh --prod exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" \
  -e "ALTER USER 'hiresnap'@'%' IDENTIFIED BY 'new-secret'; FLUSH PRIVILEGES;"

# 2. Only then update the env file (both keys, so a re-init stays consistent).
#    DB__PASSWORD=new-secret
#    MYSQL_PASSWORD=new-secret

# 3. Recreate the backend so it picks up the new value.
docker compose --env-file docker/.env.prod -p hiresnap-prod \
  -f docker/docker-compose.yaml --profile prod up -d --force-recreate backend
```

Order matters. Doing step 2 before step 1 leaves the backend retrying against a password
the server no longer accepts, and the failure surfaces as generic 500s rather than an
authentication error.

## 12. Backup and restore

The database lives in the `mysql_data` **named volume**, uploads in `infra/storage/`, and
the JWT pair in `infra/certs/`. Only the first two are irreplaceable; regenerating the keys
invalidates every issued token. Logically back up the database rather than copying a live
data directory:

```bash
./docker/run.sh --prod exec -T mysql sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --databases hiresnap' \
  > backup-$(date +%F).sql
tar -czf storage-$(date +%F).tar.gz infra/storage infra/certs
```

The database name is `hiresnap` — lowercase. It is `DB__NAME`'s default
(`docker/.env.example`, `docker/docker-compose.yaml:162`, default at `:167`) and the name MySQL creates on
first boot. `--databases HireSnap` dumps nothing and exits 0, which is the worst possible
failure mode for a backup.

Capture the volume list too — it is the part a file-level backup misses:

```bash
docker volume ls | grep hiresnap | tee volumes-$(date +%F).txt
```

Restore:

```bash
./docker/run.sh --prod down
./docker/run.sh --prod up            # fresh volume => init hook creates the schema
./docker/run.sh --prod exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" \
  < backup-2026-01-01.sql
tar -xzf storage-2026-01-01.tar.gz
```

## 13. Stopping and cleaning

```bash
./docker/run.sh --prod down              # stop and remove containers; volumes and host data survive
./docker/run.sh --prod down --volumes    # ALSO deletes mysql_data, redis_data, minio_data, hf_cache
```

`down` alone is always safe: containers, networks and the compose project go away, the
named volumes and every bind mount under `infra/` stay. `down --volumes` is the
destructive one — it deletes the database, and it is therefore never a default
(`docker/run.sh:300-311`).

The database is a **named volume**, not a directory under `infra/`. Deleting `infra/`
does not delete it, and "the data is in `infra/mysql_data`" is not a statement about this
stack — the `.gitignore` rule for that path is a reserved guard in case someone later
switches the datadir to a bind mount (`.gitignore:51`). The safe-versus-unsafe deletions
are tabulated in [AGENTS.md](../AGENTS.md#where-generated-state-lives).

To reclaim disk without losing data: `docker image prune` and `docker builder prune`.
Never `docker volume prune` on a host that runs this stack: it removes `mysql_data`
along with everything else unused.

## 14. Proving the sidecar is used

The sidecar is not optional and has no switch: both compose files give `ml` the stack's own
profile (`prod` / `dev`), and the API has no in-process inference path left to fall back on
([ADR/0006](ADR/0006-ml-sidecar-owns-inference.md)). So the question is no longer "is it
enabled" but "is the API dialling the right name, and did the request actually cross the
network". `ML__SERVICE_URL` ships **non-empty** in the manifest — `http://ml:8080`
(`docker/.env.example:267`) — because an empty value would fall through to the client's own
default `http://127.0.0.1:8080` (`backend/service/infrastructure/ml_sidecar.py:65`), where nothing is
listening inside the API container.

The decisive observation is a pair of log lines on both sides of one upload (§14.1 is the
same question answered by the client itself), and that is step 3 below.

```bash
# 1. Is the container up, and did it start as a sidecar?
./docker/run.sh --dev ps                       # ml should be listed and (healthy)
docker compose -f docker/docker-compose.dev.yaml --env-file docker/.env.dev \
  -p hiresnap-dev logs --tail 20 ml            # expect: 'ml settings: ...' then app.py on :8080

# 2. What URL did the API resolve? This is the switch that matters.
./docker/run.sh --dev exec backend printenv | grep -i 'ML_SERVICE'
#    ML_SERVICE_URL=http://ml:8080   <- the compose name; correct in every mode
#    (no output)                     <- the client falls back to http://127.0.0.1:8080,
#                                       which inside the API container is the API itself

# 3. Does the sidecar think it can serve? degraded is not a failure, but the flags name
#    the missing capability (weights, checkpoints, cuda). Probe it from INSIDE: the ml
#    service publishes no host port in either mode (docker/run.sh:168-171).
./docker/run.sh --dev exec ml curl -fsS http://127.0.0.1:8080/v1/capabilities
```

Note the port in that probe: the sidecar listens on `ML_PORT` (`8080`, `docker/.env.example:260`)
and both compose files map it literally, so a wrong number inside the container is a
connection refusal, not a timeout. `make ml-shell` gives you a shell in the same container.

The decisive evidence is the logs, on both sides of one upload — both lines are literal
strings from the code, so they are greppable:

```bash
./docker/run.sh --dev logs backend | grep -i 'ml sidecar'
#   INFO  ml sidecar /v1/embed/video answered for abc.mp4 (4194304 bytes)
#         ^ backend/service/infrastructure/ml_sidecar.py:278
./docker/run.sh --dev logs ml | grep -i 'embed/video'
#   INFO  embed/video begin name=abc.mp4 bytes=4194304
#   INFO  embed/video done name=abc.mp4 dim=384
#         ^ ml/service/app.py:400-405 (uvicorn's own access line, POST /v1/embed/video 200,
#           sits next to them)
```

One line on each side means the request crossed the network. A `ml sidecar` line on the API
side with **nothing** on the `ml` side means the URL points somewhere else — re-read step 2,
and remember that one sidecar container can serve several API containers while only the ones
with the right variable reach it. No line on the API side at all means the upload never got
that far: look for `ml_service_unreachable` in the same log.

Three failure shapes worth recognising:

| Symptom | Means |
|---------|-------|
| `503 service_unavailable` from the sidecar | The sidecar is reachable and honest: a weight or checkpoint file is missing. The `detail` names it. See [§9](#9-mounted-state-certs-weights-storage-logs). |
| `MLUnavailable ... ml_service_unreachable` in the API log | Nothing answers at that URL. Check `ML__SERVICE_URL` is `http://ml:8080` and **not** `http://127.0.0.1:8080` (inside the API container that is the API), that `ml` is on both the `default` and `internal` networks, and that the container is up. |
| `MLContractError` | Both are up but disagree about the payload — the media routes take raw bytes, so a JSON body is a 415/422. |

There is no fallback to return to. The only lever is *which* sidecar the API points at:
point `ML__SERVICE_URL` at another host running the same image, or empty it and the client
dials `127.0.0.1:8080` inside its own container, where nothing listens — every upload then
fails with `ml_service_unreachable` rather than silently recomputing embeddings in-process.

### 14.1 Reading the transport out of a running container

Steps 1-3 inspect the container from outside. The client itself can report what it will do
on the next request, which is the value to quote in an incident note:

```bash
./docker/run.sh --dev exec backend python -c \
  "import json;from service.infrastructure.ml_sidecar import describe_transport;print(json.dumps(describe_transport()))"
#   {"transport": "http", "service_url": "http://ml:8080", "timeouts": [10.0, 900.0]}
```

`describe_transport()` (`backend/service/infrastructure/ml_sidecar.py:414-431`) reports what
`service_url()` (`:101-120`) resolved, and it strips any userinfo from the URL before
printing it, so the output is safe to paste. The `service_url` field is the part that can
still be wrong: `transport` is always `http`, because the second transport no longer exists.

That is why `http://ml:8080` and `http://127.0.0.1:8080` both print a healthy-looking
result — only the second one cannot work from inside the API container. The seam accepts
both spellings, `ML_SERVICE_URL` and `ML__SERVICE_URL`, and re-reads the variable on every
call rather than memoising it, so setting it and restarting the API is enough; no rebuild is
in the path. It also never prints userinfo, so a credentialed proxy URL does not leak into
an incident note.

The outside checks in §14 and this one disagree in one instructive way: `ps` can show a
healthy `ml` container while `describe_transport()` names a URL nothing resolves. The
container existing is not the claim; the pair of log lines is.

## 15. Operating the optional profiles

Four flags live in the env file — `CACHE`, `STORAGE`, `CELERY`, `MAIL` — and `docker/run.sh`
reads them out of the file (`docker/run.sh:159-162`) before calling
`configure_sidecar_profiles`. A shell variable of the same name is **overwritten** by the
file. Turning a flag on does two things: it adds the `--profile`, and it exports the
environment keys the application reads (`docker/sidecar_profiles.sh:128-160`; the profile
list itself is built at `:48-85`).

The stack's own services are not in this table: `mysql`, `backend`, `frontend`, `gateway`
and `ml` are core and are always in the invocation. `ml` in particular has no flag — see
[§14](#14-proving-the-sidecar-is-used).

| Want | Do | Verify |
|------|-----|--------|
| cache on | `CACHE=1` in `docker/.env.dev` (default) | `./docker/run.sh --dev ps` lists `cache`; `exec backend printenv` shows `CACHE__BACKEND=redis` |
| cache off | `CACHE=0`, then `down` and `up` | the service disappears from `ps`; `CELERY=1` would force it back on with a warning |
| storage on | `STORAGE=1` (default) | `ps` lists `storage`; console at `:9001` in dev only. Wiring does **not** flip: `STORAGE__BACKEND` stays `local` |
| storage off | `STORAGE=0` | — |
| celery on | `CELERY=1` | `ps` lists four containers; `inspect ping` below; `CACHE` is forced on |
| celery off | `CELERY=0` (default) | `run.sh` prints `jobs : inline in the API process` |
| mail on | `MAIL=1` | inbox at `http://localhost:8025` (dev); `exec backend printenv` shows `MAIL__BACKEND=smtp` |
| mail off | `MAIL=0` (default) | mail goes to `MAIL__FILE_PATH` if you enable it in code, or nowhere |
| gpu on | `GPU=1` | `run.sh` prints `gpu file : docker-compose.gpu.yaml`; `exec ml nvidia-smi` sees the device |
| gpu off | `GPU=0` (default) | the sidecar still runs on CPU and `/v1/capabilities` reports `cuda: false` — models load in float32, which is the degraded-but-working path |

`GPU` is deliberately not a fifth profile above: a device reservation is a **host**
requirement, not a set of containers. `GPU=1` merges one override file
(`docker/docker-compose.gpu.yaml`) through `sidecar_profiles.sh:98-107`, and the `-f` is
spliced in **before** `--profile` so compose does not drop the reservation when it filters
by profile. A host without the NVIDIA driver must leave it at `0`; the reservation then has
nothing to satisfy and `up` fails.

A profile started with a flag and then restarted without it leaves the container running:
`--profile` filters what compose *manages* in that invocation, not what is already up.
`down` removes all profiles of the project.

`run.sh` prints what it resolved on every `up` (`docker/run.sh:240-252`): mode, project,
compose file, env file, the sidecar list, the compute line and — only when the override is
in play — `gpu file : docker-compose.gpu.yaml`. The dev summary afterwards
(`docker/run.sh:345-371`) adds the published URLs, a reminder that `ml` has none, the Mailpit
inbox when `MAIL=1`, and `jobs : inline in the API process (CELERY=0)` when celery is off.
Those lines come from the same variables compose used, so they are a faithful summary of the
invocation.

### 15.1 Is a worker consuming?

The service names are `celery-worker-agents`, `celery-worker-maintenance`,
`celery-worker-ml` and `celery-beat` (`docker/docker-compose.yaml:579-768`). Ask them
directly rather than guessing from `ps`:

```bash
# one worker by name; -d takes the nodename celery printed on startup
./docker/run.sh --dev exec celery-worker-agents \
  celery -A service.infrastructure.messaging.celery_app inspect ping -d celery@$HOSTNAME

# what is it running, and what does it know how to run?
./docker/run.sh --dev exec celery-worker-agents \
  celery -A service.infrastructure.messaging.celery_app inspect active
./docker/run.sh --dev exec celery-worker-agents \
  celery -A service.infrastructure.messaging.celery_app inspect registered
#   -> hiresnap.embed_candidate, hiresnap.reindex_embeddings,
#      hiresnap.cleanup_orphan_uploads, hiresnap.send_email, hiresnap.health
```

`inspect active` prints `{}` when the worker is alive and idle, and hangs or exits non-zero
when it cannot reach the broker. An empty `registered` list means the worker started without
importing `service.infrastructure.messaging.tasks` — check that the working directory is `/app` and that
`PYTHONPATH` includes it. The container healthcheck is exactly the `inspect ping` above
(`docker/docker-compose.yaml:617`), so a container that is `(healthy)` has already
answered this question.

The worker must also reach Redis on **db 1**, the same database celery uses as broker and
result backend:

```bash
./docker/run.sh --dev exec celery-worker-agents python -c \
  "import os,redis;r=redis.from_url(os.environ.get('CELERY__BROKER_URL','redis://cache:6379/1'));print(r.ping())"
#   True
```

### 15.2 Reading a job through the application

The `jobs` contract is `queued -> running -> succeeded|failed` and it lives in the `jobs`
slice's application layer
(`backend/service/services/jobs/application/job_service.py`). Nothing in the request path
enqueues today, so the honest way to exercise it is a one-off in the API container:

```bash
./docker/run.sh --dev exec backend python -c "
from service.composition import state
service = state.get_job_service()
job = service.enqueue('send_email', {'to': 'a@example.com', 'subject': 's', 'body': 'b'})
print('job', job)
print(service.status(job))
"
```

The four job kinds are `embed_candidate`, `reindex_embeddings`, `cleanup_orphan_uploads`
and `send_email` (`job_service.py:64-69`; `supported_kinds()` at `job_service.py:122`
returns the same list). `hiresnap.send_email` is a *task* name a worker registers, not a
job kind — passing it to `enqueue()` raises `ValueError: unknown job kind
'hiresnap.send_email'; supported: ...` (`job_service.py:212-215`). The module-level
functions `enqueue()` and `status()` remain and are what the slice's own tests call, so
`from service.services.jobs.application.job_service import enqueue, status` works too;
the composition accessor is the form a route would use.

With `CELERY=0` the call runs inline and `status()` answers immediately from the in-process
cache. With `CELERY=1` the id goes to the broker and `status()` refreshes from the worker
through `infrastructure/messaging/queue.py` (`CeleryDispatcher.state()` at `queue.py:72-85`);
`None` means "unknown or expired" and is a real answer — with the `memory` cache backend a
job is only visible to the process that created it. When the broker refuses the submission the
record keeps a `fallback_reason` and the work runs inline instead (`job_service.py:234-242`).

### 15.3 The mail inbox

With `MAIL=1` the stack ships `mailpit` (`axllent/mailpit:v1.20.5`) and the profile
exports `MAIL__ENABLED=true`, `MAIL__BACKEND=smtp`, `MAIL__HOST=mailpit`,
`MAIL__PORT=1025`, `MAIL__STARTTLS=false`, `MAIL__SSL=false`. Open the inbox:

- dev: <http://localhost:8025> (`${MAILPIT_PUBLISH_PORT:-8025}`)
- prod: no port is published; read it with
  `./docker/run.sh --prod logs mailpit` or exec into the container

`MP_MAX_MESSAGES` (5000) bounds the mailbox; when it fills, the oldest message is dropped.
Without the profile, `MAIL__BACKEND=file` appends to `MAIL__FILE_PATH` —
`/app/logs/mail_outbox.log` by default, i.e. the bind-mounted `infra/logs/`:

```bash
./docker/run.sh --dev exec backend tail -n 40 /app/logs/mail_outbox.log
./docker/run.sh --dev exec backend python -c \
  "from service.infrastructure.mail import send_now; print(send_now('ops@example.com','probe','hello'))"
```

Nothing in the API calls the mailer yet ([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)),
so an empty outbox is the expected state, not a broken configuration. A send failure is
swallowed by design: mail is never allowed to fail a request.

## 16. The backend's own gates, and the contract snapshot

Two commands answer "is the backend tree still what the docs say it is", and neither needs
Docker, an env file or a running stack:

```bash
cd backend
python -m pytest -q                      # the suite, including the layout and boundary tests
python scripts/check_backend_layout.py   # the same layout rules without pytest
```

`python scripts/check_backend_layout.py` is stdlib-only on purpose: it imports nothing from
the application, so it runs before `pip install -r requirements.txt` has finished and on a
machine whose interpreter has no pytest. It asserts eight rules
(`backend/scripts/check_backend_layout.py:1-63`): `service/` is the only package at the
backend root, no loose script or dump sits beside `pyproject.toml`, no flat module survives
under `service/services/`, a directory under it that has any layer has `application/` and
`presentation/` (`jobs` is the documented exception), every router module is named in the
`ROUTERS` tuple, the retired `service/domain/`, `service/repositories/` and
`service/api/routers/` stay gone, `models/` and `shared/` import no upward layer, and there is
exactly one pytest configuration. The GPU boundary is the other guard, not this one. From the
superproject root `make structure` runs both in one command (`Makefile:280-282`). That target is
honest about absence in two different ways, and the difference matters: the superproject half
errors when **no** layout guard is present (`scripts/check_ml_boundary.py` missing), while the
submodule half prints `SKIP` for a `backend/scripts/check_backend_layout.py` that is not in this
checkout — a checkout made without `--recursive` lacks the file, and a missing file is a missing
prerequisite, not a broken tree.

Use the repository's own interpreter if you have one — the tests need the application's
dependencies, and a bare `python` on Windows is often the Microsoft Store stub that exits 49
(§10). On this workstation that is
`R:/HireSnap/.venv-refactor/Scripts/python.exe -m pytest -q` from `backend/`.

### 16.1 Regenerating the OpenAPI contract snapshot

The public shape of the API is recorded in
`backend/tests/snapshots/critical_endpoint_openapi.json`: the paths, the methods, the
status codes and the response models of the endpoints a client is allowed to depend on. A
test compares the running app's document against that file, so a refactor that moves a route
or renames a field fails locally instead of in a client.

Regenerate it **only** when the contract change is intended, and read the diff before you
commit it:

```bash
cd backend
python -m tests.snapshot_tool --write     # rewrite the snapshot from the current app
python -m tests.snapshot_tool             # check without writing: what CI and the test do
```

The snapshot is deliberately partial — it covers the endpoints the SPA calls and the
health route, not the whole generated document — because a full dump changes on every
description edit and would be re-accepted reflexively. [SERVICE_CONTRACTS.md §5](SERVICE_CONTRACTS.md#5-versioning)
states the same rule from the client's side.
