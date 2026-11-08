# Architecture

HireSnap is a three-tier web application — a React SPA, a FastAPI backend and MySQL — plus
one service that is not part of the browser request path at all: an inference sidecar
(`ml/`) that the backend calls over HTTP when it needs a video embedded.

The container stack runs that application **as it is**: `backend/` and `frontend/` are
standalone repositories whose code is deployed unmodified, and the stack compensates for
their known defects at the edge instead of patching them. There is exactly one structural
exception, and it is deliberate: the ML pipeline was **moved out of the backend** into
`ml/`, which is now a third repository of its own, and the API keeps only an HTTP client.
Before that move the API image carried torch, CUDA and a model cache it never needed to
serve a login. Why the boundary was redrawn, and what it cost, is
[ADR/0006-ml-sidecar-owns-inference.md](ADR/0006-ml-sidecar-owns-inference.md).

Read [KNOWN_GAPS.md](KNOWN_GAPS.md) alongside this document: the design below is shaped by
which defects are fixed at the edge, which are fixed by that one structural change, and
which are deliberately left alone.

## 1. Service graph

```
                        host (published ports)
                                   │
                     ┌─────────────▼──────────────┐
                     │  gateway  (nginx:1.27-     │   publishes
                     │  bookworm)                 │   ${NGINX_PUBLISH_HTTP:-8088} → :80
                     │                            │   ${NGINX_PUBLISH_HTTPS:-8443} → :443
                     │  /       → frontend:80     │
                     │  /api/   → backend:8080    │   (rewrite /api/ → /server/)
                     │  /data/  → backend:8080    │   (re-export of the static mount)
                     │  /health → 200, static     │   (never proxied)
                     └───┬──────────────┬─────────┘
                         │              │
        ┌────────────────▼──┐      ┌────▼──────────────────┐
        │ frontend          │      │ backend               │
        │ nginx:1.27-alpine │      │ python:3.9-slim       │
        │ static :80        │      │ uvicorn :8080         │
        │ (CRA build/)      │      │ FastAPI app           │
        └───────────────────┘      └────┬──────────────┬───┘
                                        │              │
                     ┌──────────────────▼──┐      ┌────▼─────────────────┐
                     │ mysql               │      │ bind mounts (host)   │
                     │ mysql:5.7.44 :3306  │      │ infra/storage  → DATA_PATH
                     │ (internal network)  │      │ infra/certs    → secrets/certs
                     │ datadir: named vol. │      │ infra/logs     → logs
                     └─────────────────────┘      └──────────────────────┘
                                        │
                                        │  POST /v1/embed/video  (bytes in, JSON out)
                                        │  10 s connect / 900 s read
                     ┌──────────────────▼──┐
                     │ ml   — CORE service │   the inference sidecar, port 8080
                     │ ml/docker/Dockerfile│   CUDA image; owns the pipeline and the models
                     │ internal + default  │   publishes nothing in either stack
                     └─────────────────────┘

  optional profiles — every one of them sits on the internal network only:
    cache   redis:7.2-alpine   :6379  (internal)   shared cache + celery broker (db 1)
    storage minio              :9000  (internal)   S3-style object store
    celery  same backend image x3 + beat           workers for agents/maintenance/ml
    mail    axllent/mailpit    :1025  (internal)   SMTP sink + web inbox (dev publishes it)
  GPU access is not in the base file: `docker/docker-compose.gpu.yaml` adds the device
  reservation and is applied by run.sh only when GPU=1 (§4 of that file, and RUNBOOK §16).
```

Three data paths leave the backend, and two of them cross a process boundary:

| Path | Hop | Transport | When |
|------|-----|-----------|------|
| **Data** | backend → `mysql:3306` | PyMySQL over the internal network | Always — every DB-backed route |
| **Inference** | backend → `ml:8080` | HTTP (`POST /v1/embed/video`, `/v1/embed/text`, `/v1/transcribe`) | Always — `ml` is a core service, not a profile (§3.5) |
| **Async** | backend → redis `db 1` → celery workers | celery protocol | Only under `CELERY=1`, and no route enqueues yet (§3.6) |

| Service  | Image | Internal port | Published (dev) | Published (prod) | Profile |
|----------|-------|---------------|-----------------|------------------|---------|
| `mysql`    | `mysql:5.7.44` | 3306 | — | — | always |
| `backend`  | built from `backend/docker/Dockerfile`, context `..` | 8080 | `8080` | — | `prod` / `dev` |
| `frontend` | built from `frontend/docker/Dockerfile`, context `../frontend` | 80 | `3000` | — | `prod` / `dev` |
| `gateway`  | `nginx:1.27-bookworm` | 80, 443 | `$NGINX_PUBLISH_HTTP` (`8088`), `$NGINX_PUBLISH_HTTPS` (`8443`) | same | `prod` / `dev` |
| `ml`       | built from `ml/docker/Dockerfile`, context `../ml` | 8080 | — | — | `prod` / `dev` — **core** |
| `cache`    | `redis:7.2-alpine` | 6379 | — | — | `cache` |
| `storage`  | `minio/minio` (pinned tag) | 9000 / 9001 | 9001 (console) | — | `storage` |
| `celery-worker-agents` | same backend image | — | — | — | `celery` |
| `celery-worker-maintenance` | same backend image | — | — | — | `celery` |
| `celery-worker-ml` | same backend image | — | — | — | `celery` |
| `celery-beat` | same backend image, `--schedule=/tmp/celerybeat-schedule` | — | — | — | `celery` |
| `mailpit`  | `axllent/mailpit:v1.20.5` | 1025 (SMTP), 8025 (web) | 8025 | — | `mail` |

> **`ml` is a core service, and that is a change.** It used to be an optional profile
> gated on `ML=1`, which meant the deployment had two inference paths: an HTTP one when
> the flag was on, and torch inside the API process when it was off. The fallback is gone
> ([ADR/0006](ADR/0006-ml-sidecar-owns-inference.md)); `ml` starts with the stack in both
> environments and the backend always talks to it over HTTP. `docker/docker-compose.gpu.yaml`
> is the only thing that is optional about it — a CPU-only host runs the same service with
> `GPU=0` and accepts that inference is slow rather than absent.

> **How the remaining profiles wire themselves.** A profile flag does two things, not one:
> it selects the containers (via `docker/sidecar_profiles.sh`, which appends `--profile ...`)
> and it exports the environment keys the application reads (`CACHE__BACKEND=redis`,
> `CELERY__ENABLED=true`, `MAIL__*`). A shell export wins over `--env-file` in compose
> interpolation, which is why those manifest keys ship empty. `STORAGE=1` is the one
> exception: it starts MinIO but leaves `STORAGE__BACKEND=local`, because the upload path
> writes to `DATA_PATH` directly and flipping the facade would split media across two
> backends. See [ENV.md §8](ENV.md#8-optional-profiles-and-the-platform-layer) for the
> key-by-key matrix.

A dash in a *published* column means the port is reachable only from inside the compose
network, never from the host. In **production the only service with a host publication is
the gateway**; `ml` publishes nothing at all, so its unauthenticated HTTP surface is
reachable only by the backend. Those internal ports sit on the no-egress `internal`
network, which has no route off the host either — see §2. Nothing routes the `ml`
container through the gateway: its only caller is the backend, over the compose network.

In **development** the stack additionally publishes what a human needs to poke at, each
only while its profile is on: `backend` 8080 and `frontend` 3000 (always), the MinIO console
9001 (`STORAGE=1`) and the Mailpit web inbox on `${MAILPIT_PUBLISH_PORT:-8025}` (`MAIL=1`).
`ml` is not in that list: it publishes **no** host port in either mode, so the way to reach
its `/v1/capabilities` is through the container (`make ml-shell`, then `curl -fsS
http://127.0.0.1:8080/v1/capabilities`) or from the backend. `./docker/run.sh --dev` prints
exactly the URLs that apply for the flags you passed.

The MySQL datadir, the Redis and MinIO data directories and the Hugging Face cache are
**named volumes** (`mysql_data`, `redis_data`, `minio_data`, `hf_cache`, with a `_dev` suffix
in the dev project). Only `infra/storage`, `infra/certs`, `infra/logs` and the read-only model
mounts are bind mounts, because a Windows bind-mounted MySQL datadir is a corruption and
performance hazard. Consequence: `docker compose down -v` destroys the database — see §4.

The gateway publishes 443 unconditionally, but nothing listens on that port until TLS is
turned on: that requires both a certificate and key in `infra/nginx/certs/` **and**
uncommenting the HTTPS server block in `infra/nginx/templates/site.conf.template` (:217-295).
The template is rendered into `conf.d/default.conf` when the gateway container starts, so
enabling TLS is a file drop plus a container recreate, not a compose edit. The gateway
healthcheck probes HTTP on purpose so that it passes either way. HTTP on
`${NGINX_PUBLISH_HTTP:-8088}` is the guaranteed path.

## 1.1 Three submodules, three branches

The superproject tracks product code as **three** gitlinks, not two. Each one is a
standalone checkout of the same remote on its own branch, and the root repository itself
lives on `dev`:

| Gitlink | Branch | Owns |
|---------|--------|------|
| `backend/` | `backend` | The FastAPI application and all business logic |
| `frontend/` | `frontend` | The React SPA |
| `ml/` | `ml` | The inference service: `service/*.py`, the Cosmos tokenizer, its own Dockerfile and tests |

`scripts/adopt-submodules.sh` records the three with `branch = <name>` in `.gitmodules`, so
`git submodule update --remote` follows the right line of development for each. A change
that spans all three services is three commits plus one pointer bump in the root — see
[AGENTS.md](../AGENTS.md#commit--pull-request-guidelines).

Two compose projects, two compose files, one service graph, and the same profile names in
both (`prod`/`dev` for the core, then `cache`, `storage`, `celery`, `mail`):

- `docker/docker-compose.yaml` — production. Project name `hiresnap-prod`. Only the
  gateway publishes a port.
- `docker/docker-compose.dev.yaml` — development. Project name `hiresnap-dev`. Same
  twelve services, plus the host publications listed above so a browser, a debugger or a
  mail client can reach them directly.

`docker/docker-compose.gpu.yaml` is neither of those: it is an **override fragment**, not a
third stack. It carries one block — the `deploy.resources.reservations.devices` reservation
for `ml` — and is appended by `run.sh` only when `GPU=1` ([docker/docker-compose.gpu.yaml:35-49](../docker/docker-compose.gpu.yaml)):
no other service asks for the card, including `celery-worker-ml`, which reaches the sidecar
over HTTP like the API does. Without the override the same images run on CPU, which is why a
machine with no NVIDIA driver can still start the stack.

Both compose files are run through `./docker/run.sh --dev` / `./docker/run.sh --prod`, which
is what supplies `--env-file`, `-p <project>`, the profile flags and the GPU override. A
bare `docker compose up` is not equivalent and is not supported.

The two files exist separately rather than as one file with overrides because the
difference is not only port publication: dev also mounts source for live reload and
runs the CRA dev server instead of the static bundle. See
[ADR/0001-two-compose-files.md](ADR/0001-two-compose-files.md).

## 1.2 Package layout

Both application checkouts are laid out the same way, because they are the same kind of
thing: a service with one importable entry point, its own dependency list, its own image
and its own tests. The shape is documented where the process can see it — the module
docstring of `backend/service/__init__.py` names each directory and what belongs in it —
and the parts a machine can check are split between two gates.
`backend/tests/test_backend_structure.py` asserts the tree: one package at the backend root,
no loose module beside it, every layer directory present, no import of the retired `src.*`
namespace, every router module on disk actually mounted by `service/api/app.py`, and no data
layer importing a business or HTTP one. `scripts/check_ml_boundary.py` asserts the GPU
boundary from outside the suite — no ML library under `backend/service/`, none named in
either requirements file, and no `modelling/` or `cosmos_tokenizer/` directory anywhere under
`backend/` — and repeats the root-file rule, which is why `make structure` can answer both
questions without Docker or a populated env file
([ADR/0007](ADR/0007-sidecar-package-layout.md)).

```
backend/
├── service/            the importable package; `python -m service.main` is the process
│   ├── __init__.py     __version__ plus the docstring that states the layout rule
│   ├── main.py         entry point: bootstrap (DEBUG and INIT_DB), then uvicorn.run(reload=DEBUG)
│   ├── api/            app.py (`create_app`, `GET /server/health`), security.py,
│   │                   routers/ — twelve modules, they validate and delegate
│   ├── configuration/  env.py, logging.py, logging.yaml, paths.py
│   ├── domain/         models.py — the value objects the layers pass to each other
│   ├── infrastructure/ cache.py, storage.py, mail.py, uploads.py;
│   │                   database/ (connection.py, schema.py), messaging/ (celery_app.py,
│   │                   tasks.py, beat_schedule.py)
│   ├── repositories/   one module per aggregate; SQL lives here and nowhere else
│   ├── services/       one module per vertical: matching.py, ml_client.py, jobs_services.py, ...
│   └── shared/         hashing, tokens, validation, urls, debug — no dependencies on the rest
├── database/           HireSnap.sql — the dump the MySQL init hook and schema.py apply
├── docker/             Dockerfile and requirements.lock.txt
├── docs/               PLATFORM_LAYER.md and images/ (the README's assets)
├── notebooks/          exploration; imported by nothing and copied into no image
├── tests/              conftest.py, manual/pipeline_smoke.py and eight test modules; the
│                       suite runs as `python -m pytest -q` from backend/
├── requirements.txt    runtime pins, GPU-free
└── requirements-platform.txt  celery, redis, boto3 — installed into the same image
```

```
frontend/
├── src/
│   ├── index.js        CRA entry
│   ├── App.js          the top-level view; Layout.jsx is the shell it renders
│   ├── app/            router.jsx (routes) and providers.jsx (Chakra, cookies, theme)
│   ├── pages/          auth/, candidate/, employer/, notFound/ — one Page.jsx per route
│   ├── shared/         api/ (apiConsts.js, instance.js, services/), auth/, components/,
│   │                   hooks/, utils/, assets/
│   ├── theme/          theme.js — the Chakra tokens every component reads
│   └── styles/         index.css, App.css
├── public/             index.html, favicon, manifest
├── jsconfig.json       compilerOptions.baseUrl = "src" — imports are absolute from src/
├── docker/Dockerfile   base / dev / builder / runtime
├── nginx.conf          /health plus the SPA fallback
└── package.json        start, build, test, lint, find:unreachable, eject
```

**The layering rule, in one direction only:** `api/` validates a request and delegates,
`services/` holds the business rules, `repositories/` holds the SQL, `infrastructure/`
holds the swappable I/O (cache, storage, mail, database, messaging). A module may import
the layer below it and nothing above; `shared/` and `domain/` are leaves that import
neither. `configuration/` is read once at the edge of the process, which is why importing
`service.api.app:app` constructs an app object without connecting to MySQL, without
reading a `.env` file and without pulling in a GPU framework — a property
`backend/tests/test_import_without_infra.py` exercises rather than hopes for, since it
does exactly that in a fresh interpreter, from a temporary working directory, with every
`DB_*` and `MYSQL_*` variable stripped, and one `scripts/check_ml_boundary.py` keeps true
for the GPU half.

Two consequences an operator notices:

- **The import root is the checkout, not the package.** `python -m service.main` from
  `backend/` (the image runs it from `/app` with `PYTHONPATH=/app`) is the only supported
  invocation. There is no `sys.path` edit anywhere, so a module that is not importable from
  the checkout root is a bug, not a missing `PYTHONPATH`.
- **The frontend has no relative climb.** `jsconfig.json` puts `src/` on the module path, so
  a component imports `shared/api/instance` and `pages/auth/SignInPage` — never
  `../../API/instance`. Moving a file is then a rename plus the imports the compiler
  names, not a hunt for `../`.

The mirror is the reference stack at `R:/GPTHub`: a sidecar there is an importable package
with thin routers, a services layer and infrastructure behind it, and its tests assert the
tree. Why this shape and what it cost — including the four alternatives that were rejected
— is [ADR/0007](ADR/0007-sidecar-package-layout.md).

## 2. Networks

Two Docker networks with deliberately different egress behavior:

| Network | Members | Egress | Purpose |
|---------|---------|--------|---------|
| `internal` | `mysql`, `cache`, `storage`, `ml`, `backend`, `gateway`, the four celery services, `mailpit` | **No** — `internal: true` | The storage tier plus the inference sidecar and the workers. A compromised database, cache or model container cannot reach the internet, and an outbound request from it fails fast instead of exfiltrating data. |
| `default` | `gateway`, `frontend`, `backend`, `ml` | Yes (NAT) | The request path, plus the one service the request path calls synchronously. Only services that must answer or serve HTTP live here. |

`backend` and `gateway` are attached to both: the API must reach MySQL (internal) and be
reachable from the gateway (default); the gateway terminates the request path and also
needs the upstreams. The database is **not** on the default network, so it has no route to
the internet at all. Rationale in [ADR/0002-two-networks.md](ADR/0002-two-networks.md).

**`ml` is on both, and that is a change from the profile-era layout.** While the sidecar was
an optional profile it was pinned to `internal` only, so the API could reach it but nothing
outside could. Now that it is a core service on the request path of an upload — the browser
waits for it — it is attached to `default` as well. It still publishes nothing, so being on
`default` gives it no route off the host: the network exists to let containers talk to each
other, and egress is NAT-ed only for the services that need it. The celery workers and
`mailpit` stay internal-only, because they consume a queue and an SMTP socket and never
answer a browser.

That leaves exactly one service with a single network attachment that a reader might expect
to be dual-homed: `frontend`. It answers the browser through the gateway and calls nothing,
so it has no reason to join `internal`.

A service that needs no outgoing traffic but is missing from `internal` is a bug: it means
the storage-tier boundary was drawn in the wrong place. A service that joins `default`
without answering or serving a request is the opposite bug.

## 3. Request flow

### 3.1 The `/api/` prefix and the `/server/` mount

This is the single most important thing to understand about the stack, because the
frontend and the backend disagree about the prefix:

- The frontend sends every request to a base URL that ends in `/api/`. One module
  resolves it, `frontend/src/shared/api/apiConsts.js:22-47`: `REACT_APP_BASE_URL` when
  set, otherwise `window.location.origin + "/api/"` on any non-loopback host, otherwise
  `http://localhost:8080/api/` (`:46`). The axios instance consumes it at
  `frontend/src/shared/api/instance.js:6-8`, and it is the only client in the app.
- The backend mounts its API app at `/server` and its static files at `/data` in one
  factory, `backend/service/api/app.py:118-141`.

```python
app.mount("/server", build_server_app())   # service/api/app.py:128
app.mount("/public", build_public_app())   # :129
app.mount("/data", StaticFiles(directory=str(uploads)))  # :139
```

So a browser request to `/api/vacancies/` would 404 against the backend unless
something rewrites it. That something is the gateway, and the mechanism is a
`rewrite` directive — not a `proxy_pass` that carries a URI:

```
GET /api/vacancies/
        │
        ▼
gateway  infra/nginx/templates/site.conf.template:77-104
        │   rewrite ^/api/(.*)$ /server/$1 break;   # :78
        │   proxy_pass $backend_upstream;             # :79
        │   # $backend_upstream = http://backend:8080  (# :43-44)
        ▼
backend receives GET /server/vacancies/
        │
        ▼
FastAPI routes it to the handler bound at @app_server.get("/vacancies/")
```

Why a `rewrite` and not the shorter `proxy_pass http://backend:8080/server/`? Because
the upstream is a **variable** (`set $backend_upstream http://backend:8080`,
site.conf.template:43-44) and nginx forbids a URI part in `proxy_pass` when the target
is a variable. The variable exists so that Docker's embedded DNS is consulted per
request instead of once at startup — `resolver 127.0.0.11 valid=10s`
(infra/nginx/nginx.conf:34) — which is what lets the gateway survive a backend
container that restarts with a new IP. The two forms are equivalent in effect; only one
of them is legal here.

The rule that matters: the matched prefix `/api/` is replaced by `/server/`, so
`/api/vacancies/` arrives as `/server/vacancies/`. A `rewrite` without `break`, or a
replacement missing its trailing slash, produces a doubled or truncated path and every
API call 404s. Why we rewrite at the edge instead of editing the frontend is in
[ADR/0003-nginx-rewrites.md](ADR/0003-nginx-rewrites.md).

### 3.2 The full request path

```
browser
  │  GET http://localhost:8088/api/vacancies/
  ▼
gateway :80
  │  1. match the /api/ location → rewrite /api/ → /server/
  │  2. proxy_set_header Host $host
  │  3. apply client_max_body_size 500m (video uploads)
  ▼
backend :8080  (uvicorn, single process, no worker pool)
  │  4. FastAPI router → service/services/*.py → service/repositories/*.py
  │  5. PyMySQL connects to mysql:3306 on the internal network
  ▼
mysql :3306
  │  6. data in the `mysql_data` named volume (see §4)
  ▼
response bubbles back up; JSON bodies are returned verbatim
```

### 3.3 Static assets and uploaded media

| Path | Rewritten to | Served by | Notes |
|------|--------------|-----------|-------|
| `/` (everything else) | — | `frontend:80` | The CRA bundle. Missing files fall back to `index.html` so client-side routes work on refresh. |
| `/api/...` | `/server/...` | `backend:8080` | API calls. |
| `/data/...` | — | `backend:8080` | Static uploads, from `StaticFiles(directory=data_dir())` at `service/api/app.py:139`. |
| `/health` | — | gateway itself | Static `{"status":"ok","service":"gateway"}` (site.conf.template:60-64). |

`/data/` is **not** rewritten: the backend already serves it at that exact prefix.
It exists in the gateway config only so that a single origin can serve API and media;
that matters because the SPA is served from the same host, and cross-origin media URLs
would otherwise need CORS.

`/health` on the gateway sits at the root, not under `/api/`, and is never proxied: it
answers `{"status":"ok","service":"gateway"}` from a static nginx location and proves
nginx is alive without touching a dependency. Three other health routes exist and each is
probed separately: the API's `GET /server/health` (reached as `/api/health`), the
sidecar's `/health`, and the SPA container's `/health`. See
[SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md#13-where-each-health-route-actually-lives) for
which process answers which, and §7 of [RUNBOOK.md](RUNBOOK.md#7-health) for the probes.

### 3.4 Why URLs in JSON bodies are absolute, and what changed

The original helper (now `backend/service/shared/urls.py:8-13`) built a URL for a stored
file from the process's own bind address:

```python
def return_url_object(url: str):
    url = url[2:]
    return (f"http://{env.__getattr__('HOST')}:{env.__getattr__('SERVER_PORT')}/"
            f"{url}")
```

Inside a container that emits `http://0.0.0.0:8080/...`, which no browser can
resolve. The shipped patch (`backend/service/shared/urls.py:16-20`) prefers a
non-empty `PUBLIC_BASE_URL`, strips its trailing slashes, and falls back to the
original `HOST:SERVER_PORT` expression when the variable is unset or empty — so a
standalone checkout that never sets it behaves exactly as before. Set
`PUBLIC_BASE_URL` in every deployment; the fallback is a compatibility path, not a
configuration. See [ENV.md](ENV.md#31-host-and-server_port-are-overloaded) and
[KNOWN_GAPS.md](KNOWN_GAPS.md#2-media-urls-are-built-from-the-bind-address).

### 3.5 The inference path (a core service, not a flag)

Embedding is the one operation that can take minutes instead of milliseconds, and it is the
one thing the API does not do itself. `ml/` owns the pipeline: frame extraction, the Cosmos
tokenizer, UNet3D, wav2vec2 and the sentence transformer all live in `ml/service/`, behind
five HTTP routes. The backend keeps `backend/service/services/ml_client.py`, which is an HTTP
client and nothing else — `ML_SERVICE_URL` unset means `http://127.0.0.1:8080`, never
"import torch and do it here".

```
upload_video()                       service/services/matching.py:202
  │  get_user_embedding(media_path)
  ▼
service/services/ml_client.py        HTTP-only: no torch, no model, no fallback
  │
  │  POST /v1/embed/video   (bytes in, JSON out)
  │  connect timeout 10 s / read timeout 900 s
  ▼
ml:8080   (core service, both stacks)  ── GPU (or CPU with GPU=0), weights, checkpoints
  │
  └── {vector, dim, model}
```

Three consequences of the boundary, each of which used to be different:

1. **There is no in-process branch.** The API cannot import the pipeline even by accident:
   `scripts/check_ml_boundary.py` fails the commit if a module under `backend/service/` imports
   `torch`, `transformers`, `sentence_transformers`, `moviepy`, `librosa`,
   `cosmos_tokenizer` or the deleted `src.modelling` package, and it fails if
   `backend/requirements.txt` names one of those distributions. Before the split the
   transport was chosen by a variable and both paths were live code.
2. **`ml` is not optional, so a missing sidecar is a failed request, not a slow one.**
   `MLUnavailable` (`ml_service_unreachable`) is the honest answer; the client does not
   retry into a local model. That is the cost of one code path, and it is the trade
   [ADR/0006](ADR/0006-ml-sidecar-owns-inference.md) accepted in exchange for deleting the
   branch that could silently pull a CUDA context into the API process.
3. **The backend image no longer installs the ML stack.** `backend/requirements.txt` keeps
   what business logic needs (`numpy`, `pandas`, `scikit-learn` for candidate-vacancy
   matching) and drops `torch`, `torchmetrics`, `torcheval`, `transformers`, `moviepy`,
   `librosa`, `opencv-python`, `faiss-cpu` and `sentence_transformers`, which are now
   `ml/`'s dependencies alone; the trees that used them (`backend/src/modelling/` and
   `backend/cosmos_tokenizer/`, both removed before the `service/` package landed) are
   deleted rather than dormant.

| Property | Before (in-process) | Now (HTTP to `ml`) |
|----------|---------------------|--------------------|
| Where torch runs | the API process | the `ml` container |
| Failure type | the pipeline's own exception | `MLUnavailable` / `MLRejected` / `MLContractError` |
| Blocks the whole API process | yes — a synchronous call inside an `async def` handler stalls the single uvicorn loop | yes — same stall; the resource moved, not the latency |
| Needs `infra/models` | yes | yes — mounted into `ml` instead |
| Needs a GPU | yes in practice, `autocast` is unconditional | no — `GPU=0` runs the same image on CPU, and `/v1/capabilities` says what is missing |
| Restart cost | reloading the API cleared the model | reloading `ml` clears the model; the API restarts in seconds |

The request still blocks while the sidecar works: moving inference into another container
moves the *resource*, not the latency. Making the upload asynchronous is the platform
layer's `jobs` contract (§3.6). That contract is delivered and its workers exist under
`CELERY=1`, but no API route calls `enqueue()` yet, so the upload stays synchronous
([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)).

The operational proof that this path is the one being used — and not a stale process
pretending — is [RUNBOOK.md §14](RUNBOOK.md#14-proving-the-sidecar-is-used): a log line on
each side of one upload, plus `describe_transport()` reporting `"transport": "http"`
([§14.1](RUNBOOK.md#141-reading-the-transport-out-of-a-running-container)).

### 3.6 The asynchronous path (`cache` / celery — delivered, wired to the workers)

The second path exists in the code and its containers ship under the `celery` profile, but
it still has **no call site in an API route**. It is documented because an operator will
turn it on:

```
backend route (does not exist yet)
  │  jobs_services.enqueue(kind, payload)
  ▼
[ cache facade ]  CACHE__BACKEND=redis ──► redis db 1   (job state, 24 h TTL)
  │
  ▼  celery broker (same redis, db 1)
celery-worker-agents | -maintenance | -ml   (profile celery)
  │  service/infrastructure/messaging/tasks.py dispatch()
  ▼
status read back through jobs_services.status(job_id)
```

Three properties decide whether this path is worth enabling:

1. **Job state lives in the cache.** With `CACHE__BACKEND=memory` (the pre-cache default) a
   job id is only meaningful to the process that issued it; a worker in another container
   reports `unknown`. Redis is a prerequisite, not an optimisation, for anything
   asynchronous — which is exactly what `CACHE=1` wires.
2. **The queue is the failure boundary.** Each task class has its own queue, its own worker
   and its own time limits (300 s soft / 330 s hard for ordinary work, 1800/1860 for ML), so
   a stuck embedding cannot starve mail. `acks_late` plus idempotent task bodies means a
   killed worker re-delivers instead of losing the job.
3. **The flags are off by default, and one flag is two switches.** With `CELERY=0` — the
   manifest default — no worker and no beat process exist and every `dispatch()` executes
   inline: the pre-platform behaviour. `CELERY=1` starts
   `celery-worker-agents`, `celery-worker-maintenance`, `celery-worker-ml` and a
   single `celery-beat` (`docker/docker-compose.yaml:579-768`) **and** exports
   `CELERY__ENABLED=true`, forcing `CACHE=1` with it because a worker without a broker
   is a retry loop, not a queue (`docker/sidecar_profiles.sh:47-51`).

The flag matrix and the failure semantics of each backend are owned by the layer:
[backend/docs/PLATFORM_LAYER.md](../backend/docs/PLATFORM_LAYER.md). The deployment view of
the sidecars is [SIDECARS.md](SIDECARS.md); the missing call sites are
[KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin).

## 4. Data paths

| Container path | Host location | Content | Backup? |
|----------------|---------------|---------|---------|
| `/var/lib/mysql` | **named volume** `mysql_data` (dev: `mysql_data_dev`) | The database. Populated from `backend/database/HireSnap.sql` only when the volume is empty — see §5. | Yes — it is the only copy, and `down -v` deletes it |
| `$DATA_PATH` (default `/app/data`) | `infra/storage/` | Uploaded videos, generated `.npy` embeddings, CSV seed tables under `tables/` | Yes |
| `/app/secrets/certs` | `infra/certs/` | `jwt-private.pem`, `jwt-public.pem` — path from `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH`, falling back to `service/certs` on a bare checkout (`backend/service/domain/models.py:37-48`) | Yes, or regenerate |
| `/app/models/weights` | `infra/models/weights/` | `Unet3D.pth`. A read-only mount in **`ml` only** | No, re-downloadable |
| `/app/models/pretrained_ckpts` | `infra/models/pretrained_ckpts/` | Cosmos tokenizer checkpoints — `Cosmos-Tokenizer-CV4x8x8/{encoder,decoder}.jit`. A read-only mount in **`ml` only** | No, re-downloadable |
| `/app/logs` | `infra/logs/` | Log files written by the application logger, plus `mail_outbox.log` when the `file` mail backend is in use | No |
| `/app/.cache/huggingface` | **named volume** `hf_cache` | Downloaded HF models, used by `ml` and retained across recreates | No |
| redis data | **named volume** `redis_data` | Cache entries, job state and the celery broker queue (`db 1`). Losing it loses in-flight job ids, not business data | No |
| minio data | **named volume** `minio_data` | Objects written through the storage facade, once a route uses it | Only if it holds uploads |
| `/app/service` | built into the image | The sidecar's own code (`ml/service/*.py`, `ml/service/cosmos_tokenizer/`); no host mount in prod | In git (`ml/` submodule) |
| `/etc/nginx/conf.d` | `infra/nginx/conf.d/` | Gateway routes (rendered from `templates/site.conf.template`) | In git |
| `/docker-entrypoint-initdb.d` | `infra/mysql/init/` | `00-init.sh`, executed **once**, on an empty datadir | In git |

`DATA_PATH` is read through `backend/service/configuration/env.py` and used by
`backend/service/infrastructure/uploads.py:15-17` for uploads and
`backend/service/infrastructure/database/schema.py:19-23` for the `tables/` CSV directory. Pointing it at a bind mount
is what keeps uploads when the container is recreated.

**The models are mounted once now, into `ml`, and nowhere else.** Before the split the same
two host directories were mounted into the API *and* into the sidecar, because both could
run the pipeline. With the fallback gone the API has no reader for either path, so
`x-backend-volumes` no longer carries `weights`, `pretrained_ckpts` or `hf_cache`; `ml`
declares its own read-only pair plus the HF cache volume. The upload directory went the
other way: it used to be mounted into `ml` so a future route could read a file by name, and
that mount is gone too — the sidecar takes bytes on the request and never reads the API's
storage.

A consequence worth stating plainly: a host with no `infra/models/` can start the whole
stack, pass every healthcheck and fail on the first upload. The mount is read-only and the
failure is lazy — `/v1/capabilities` on the sidecar names the missing file without loading
anything ([KNOWN_GAPS.md §12](KNOWN_GAPS.md#12-weights-are-not-in-the-repository)).

Only the bind mounts under `infra/` survive `docker compose down` as ordinary files.
The named volumes survive `down` too — but not `down -v`, and not
`docker volume prune`. The safe-versus-unsafe deletions are tabulated in
[AGENTS.md](../AGENTS.md#where-generated-state-lives).

## 5. Database initialization, and why it is fragile

There are two independent init mechanisms, and only one of them is reliable:

1. **MySQL image init** — the compose files mount `infra/mysql/init/` at
   `/docker-entrypoint-initdb.d` (docker-compose.yaml:195) and
   `backend/database/HireSnap.sql` at `/opt/hiresnap/HireSnap.sql`, read-only (:200; the
   dev file does the same at :137 and :139). The image runs everything in that directory **once, when the datadir volume is
   empty**: `00-init.sh` locates the dump (`/opt/hiresnap/HireSnap.sql` first) and applies
   it. On an existing volume nothing runs; that is standard MySQL behavior, not a bug.
   If the dump cannot be found the script exits non-zero and no schema is created — it
   fails loudly rather than silently.
2. **Application init** — `backend/service/main.py:72-79` runs `CreateSQL` only when
   `DEBUG` is exactly `TRUE` **and** `INIT_DB` is a truthy value (`1`, `true`, `yes`,
   `on` — `service/main.py:82-86`):

   ```python
   if _debug_flag(env) and os_env_flag("INIT_DB"):
       bootstrap_schema(env)
   ```

   `CreateSQL.read_sql()` (`backend/service/infrastructure/database/schema.py:57-124`) executes `HireSnap.sql`
   against the server and then bulk-inserts every CSV in `$DATA_PATH/tables/`.

Consequences you must plan for:

- In production `DEBUG=FALSE` (and `INIT_DB` is unset), so the application **never**
  creates or migrates its schema. The schema must come from the init script (fresh
  volume), or from a manual `python -m service.infrastructure.database.schema` /
  `mysql < backend/database/HireSnap.sql` on an existing one — see
  [RUNBOOK.md §7.3](RUNBOOK.md#83-existing-volume-nothing-happens-automatically).
- There are no migrations. Changing a table means editing `database/HireSnap.sql` and
  applying it by hand to an existing database.
- The loader connects **without** a database name and issues
  `CREATE DATABASE IF NOT EXISTS`, so the database name comes from `DB`
  (`service/infrastructure/database/schema.py:26-33`, `:80-81`).
- Re-initialising means destroying the volume (`down -v`), which also destroys the
  data. There is no in-place reset.

## 6. Configuration flow

```
docker/.env.example          committed — the single source of truth for KEY names
        │ python docker/sync_env.py --mode dev|prod
        ▼
docker/.env.dev / .env.prod  gitignored — real values, one file per environment
        │ ./docker/run.sh --dev|--prod  →  docker compose --env-file
        ▼
compose interpolation        ${DB__HOST} → DB_HOST inside the container
        │ environment:
        ▼
container env                flat KEY=VALUE (no double underscore)
        │ service/configuration/env.py load_dotenv / os.environ
        ▼
application                  Env().__getattr__("DB_HOST")
```

The double underscore is a **compose-side** convention only: `DB__HOST` groups keys by
section in the env file for human readers, and the `environment:` block maps it to the
flat `DB_HOST` the application reads. The application never sees a double underscore —
except where the key *is* the contract: the `ML__*`, `CACHE__*`, `MAIL__*` and `CELERY__*`
families keep their double-underscore spelling in the manifest and are handed to the
containers under a flat name (`ML__SERVICE_URL` → `ML_SERVICE_URL`, `ML__PORT` →
`ML_PORT`), because the sidecar and the facades read `os.environ` and nothing translates
for them. `ml/service/settings.py` reads only flat names; a key that reached it as `ML__PORT`
would be ignored. Detail in [ENV.md §8](ENV.md#8-optional-profiles-and-the-platform-layer).
Rationale and the cost of the translation layer in
[ADR/0004-double-underscore-env.md](ADR/0004-double-underscore-env.md).

Every backend variable the application reads is required — `Env.__getattr__` raises
`AttributeError` when a name is missing (`backend/service/configuration/env.py:42-48`), so a
typo fails at startup, not at request time. That rule covers the original application only.
The platform layer and the sidecar use **`os.environ` with a default for every key**
(`backend/service/infrastructure/*.py`, `ml/service/settings.py`), which is the opposite
convention and deliberately so: a missing `CACHE__REDIS_URL` must degrade to the in-memory
backend, not abort the boot. `CORS_ALLOW_ORIGINS` sits in this second camp and is read by
`_cors_settings()` (`backend/service/api/app.py:92-142`), splatted into `CORSMiddleware` at
`:175-183`; its one rule — a wildcard origin is never paired with credentials, because
browsers reject that pair — is [ENV.md §3.2](ENV.md#32-cors_allow_origins--consumed-and-the-wildcardcredentials-rule).

A manifest key that **no Python module reads** is now a short and shrinking list, and each
member is honest about why it is there:

- `JWT_ALGORITHM` — the token layer hardcodes `RS256`
  (`backend/service/domain/models.py:101`, used at `backend/service/shared/tokens.py:49`), so
  the key records the contract and nothing more. It is the only genuine candidate for
  deletion in the manifest.
- `APP_DOMAIN`, `NGINX_PUBLISH_HTTP`, `NGINX_PUBLISH_HTTPS` — interpolated by compose into
  the nginx template (`infra/nginx/templates/site.conf.template`), never into Python.

Everything that used to be on such a list has since acquired a reader: `CORS_ALLOW_ORIGINS`
by the resolver above, `STORAGE__*` by `build_storage`
(`backend/service/infrastructure/storage.py:440-454`), `WEIGHTS_DIR` and
`PRETRAINED_CKPT_DIR` and `HF_HOME` by the sidecar's settings
(`ml/service/settings.py:140`, `:157`, `:186-204`), `INIT_DB` by the schema gate
(`backend/service/main.py:75`), and `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH` by the key
resolver (`backend/service/domain/models.py:86-88`). [ENV.md](ENV.md) marks every key; the
full catalogue is there.

## 7. Failure domains

| If this dies | Effect | Mitigation |
|--------------|--------|------------|
| `mysql` | Backend raises on connect; every DB-backed route 500s. `/health` still returns 200 because it is static and served by nginx — it proves nothing about the database. | `depends_on: condition: service_healthy` on backend; check `./docker/run.sh --prod ps`. |
| `backend` | Gateway returns 502 for `/api/` and `/data/`; UI shell still loads and `/health` still answers 200. | Healthcheck on `/server/openapi.json`; restart policy. |
| `frontend` | 404/502 on `/`; API still works. | Independent container; the gateway keeps serving its other locations. |
| `gateway` | Everything unreachable. | Single point of failure by design — one published port, one place to terminate TLS. |
| `ml` | **Every upload fails**: `MLUnavailable` with `ml_service_unreachable` waiting behind it, and `service_unavailable` is what the sidecar itself answers when its weights are missing. No other route is affected — no database, no auth, no shared state. | `depends_on: ml` with `condition: service_started` keeps the API from starting into a dead upstream; the healthcheck reports `degraded`, not `down`, so `ps` alone will not tell you it is unusable — `/v1/capabilities` will ([SERVICE_CONTRACTS.md §1.4](SERVICE_CONTRACTS.md#14-the-full-inventory-and-what-each-probe-actually-runs)). |
| `ml` on a CPU-only host | The container starts and `/health` answers; the video path raises `service_unavailable` because `autocast` needs CUDA ([KNOWN_GAPS.md §7](KNOWN_GAPS.md#7-autocast-was-unconditional-fixed-the-cpu-path-is-real)). | Set `GPU=0` so the GPU override is not applied, and treat the sidecar as text-only; do not read a green healthcheck as a working pipeline. |
| `cache` | With `CACHE__BACKEND=redis` and the container down, cache reads miss (fail-open, one warning) and job state is unreachable, so `jobs_services.status()` answers `unknown`. No API route reads the cache today, so request traffic is unaffected; `CELERY=1` without a cache is the case that hurts, because the workers then have no broker. | The facade already treats a miss as a miss; keep the cache off the critical path. `CELERY=1` forces `CACHE=1` (`docker/sidecar_profiles.sh:47-51`). |
| `storage` | Once a route uses it: `StorageError` and a 500 on the upload path (fail-closed). Today no API route uses it, so a broken bucket costs nothing yet — the worker task is the one caller. | Do not enable `STORAGE__BACKEND=s3` without the pinning test; a fallback to the local disk would hide a broken bucket. |
| `celery` worker | Tasks stay `queued` (or the broker refuses the submission and `dispatch()` falls back inline with `fallback_reason`). With `CELERY=0`, the default, every dispatch runs inline and no worker exists. | `celery -A service.infrastructure.messaging.celery_app inspect active` and `... inspect ping`; see [RUNBOOK.md §15](RUNBOOK.md#15-operating-the-optional-profiles). |
| Missing `infra/certs` | **Every** authenticated route fails, but not at import: `AuthJWT` resolves the pair from `JWT_*_KEY_PATH` with `service/certs` as the packaged fallback (`backend/service/domain/models.py:37-48`, `:85-88`), and `decode_jwt` cannot verify what the missing key would have signed. | `make env` generates them; see [SECRETS.md](SECRETS.md). |
| Missing `infra/models` | The embedding path breaks **at first use**, in `ml` — the only container that mounts it now. Startup and `/health` are unaffected. | Mount is read-only; `/v1/capabilities` names the missing file. See [KNOWN_GAPS.md §12](KNOWN_GAPS.md#12-weights-are-not-in-the-repository). |

The last two rows are the ones that surprise people: both failures are **lazy**. A stack
can start clean, pass every healthcheck, serve the UI and list vacancies while being
unable to embed a single video.

## 8. What this stack deliberately does not do

- No reverse-proxy TLS termination is configured out of the box. `infra/nginx/certs/`
  exists for a host-provided certificate; until a certificate and key are there **and**
  the HTTPS server block in the template is uncommented, the gateway answers plain HTTP
  on `8088` while the `443` publication stays idle. Both ports are published by
  design (see §1), so adding TLS is a file drop plus a container recreate.
- **No GPU requirement on the host.** The base compose files request no device; the
  reservation lives in `docker/docker-compose.gpu.yaml` and is applied only when `GPU=1`.
  A machine without `nvidia-container-toolkit` therefore starts the whole stack, and the
  sidecar degrades to CPU or to a `service_unavailable` on the video route. The cost is
  that "it started" says nothing about whether it can infer, which is why
  `/v1/capabilities` exists.
- No horizontal scaling. The backend runs a single uvicorn process and the sidecar runs
  `uvicorn.run(..., workers=1)` (`ml/service/app.py`) with one `threading.RLock` around model
  use: one process, one copy of the models, **one request at a time**. Two concurrent
  uploads queue behind each other, and that is a deliberate ceiling rather than a
  missing feature — the alternative is N copies of a multi-gigabyte model on one GPU.
- No asynchronous upload. The `ml` transport moves inference to another container but the
  HTTP request still waits for the vector (§3.5). The `jobs` contract that would make it
  asynchronous is shipped and dormant ([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)).
- No application-level migration. See [KNOWN_GAPS.md](KNOWN_GAPS.md#5-no-migrations).
- **No shared build for the three services.** Each submodule still builds independently
  from its own Dockerfile against its own requirements file; the superproject records one
  commit per service and nothing more. The one structural change made here is inside
  `backend/` — `src/` became the `service/` package and the frontend dropped 86
  unreachable files ([ADR/0007](ADR/0007-sidecar-package-layout.md)) — and the one removal
  is the ML pipeline, which left for its own repository because keeping it here forced
  every deployment to install a CUDA stack to serve a REST API
  ([ADR/0006](ADR/0006-ml-sidecar-owns-inference.md)).
- No routing of `ml` through the gateway. The sidecar has no authentication and
  `infra/nginx/templates/site.conf.template` has no location block for it, so it is never
  reachable at `/` or `/api/`, and it publishes no host port in any mode — not even in
  development. A developer who wants to curl `/v1/capabilities` goes through the
  container (`make ml-shell`). Exposing it as a public route would mean adding both an
  auth check and a location block, and nothing yet needs it.
- **No second copy of the inference code.** The invariant that makes the split stay split
  is enforced, not documented: no Python module under `backend/service/` may import a GPU
  framework (`torch`, `torchvision`, `torchmetrics`, `torcheval`, `transformers`,
  `sentence_transformers`, `moviepy`, `librosa`, `cosmos_tokenizer`) or the deleted
  `src.modelling` package, and `backend/requirements.txt` may not name those
  distributions. `scripts/check_ml_boundary.py` fails the commit on either, which is the
  only reason the boundary survives the first convenient afternoon.
- No scheduler in the request path. `celery-beat` exists only under `CELERY=1` and its
  schedule is a code constant, not a database row — see §3.6.
