# Known gaps

The defects this stack **deliberately does not fix**, what they look like in operation,
and what the workaround is. Read this before a production deploy.

Why a stack would ship with known defects: HireSnap is an existing application with two
independent repositories. Rewriting its internals is a different project with a
different risk profile. The deployment layer's job is to run the application **as it
is**, make its behavior predictable, and put every sharp edge in writing — so the person
paged at 03:00 is not the person who discovers the edge.

Each entry below states the defect, its evidence in the source, the operational
consequence, and the mitigation this stack applies.

---

## 1. API prefix drift: `/api/` versus `/server/`

**Defect.** The frontend requests `/api/...`; the backend serves `/server/...`.

- `frontend/src/shared/api/apiConsts.js:46` — the last-resort fallback,
  `return "http://localhost:8080/api/";` (still the value in effect on plain localhost)
- `backend/service/api/app.py:178` — `app.mount("/server", build_server_app())`

**Consequence.** Pointed at each other directly, every API call 404s. The bug is
invisible in local development because the frontend's hardcoded URL points at
`localhost:8080`, and a developer running both halves on the host may never notice
which prefix the browser actually used.

**Mitigation.** The gateway rewrites the prefix (`infra/nginx/templates/site.conf.template:77-104`):

```nginx
location /api/ {
    rewrite ^/api/(.*)$ /server/$1 break;
    proxy_pass $backend_upstream;   # http://backend:8080
}
```

The `rewrite` performs the substitution; `proxy_pass` cannot carry the `/server/` URI
because the upstream is a variable (see
[ADR/0003-nginx-rewrites.md](ADR/0003-nginx-rewrites.md) for why it is a variable). The
alternative that was rejected — editing either application — is in the same ADR.

**Residual risk.** Anything that bypasses the gateway — a direct `curl` to `:8080`, a
script that reads the hardcoded URL — still sees the mismatch. In dev the backend port is
published for exactly that reason, and it responds at `/server/`.

## 2. Media URLs are built from the bind address

**Defect.** `return_url_object` builds absolute URLs from `HOST` and `SERVER_PORT` —
the same variables uvicorn binds with.

```python
def return_url_object(url: str):
    url = url[2:]
    return f"http://{env.__getattr__('HOST')}:{env.__getattr__('SERVER_PORT')}/{url}"
```

**Consequence.** In a container `HOST=0.0.0.0` is the only sane bind address, so every
returned media URL is `http://0.0.0.0:8080/...` — an address no browser can resolve.
The video still exists and `/data/...` still serves it; only the URL in the JSON is
wrong. That is worse than a hard failure, because the upload appears to succeed.

**Mitigation.** `PUBLIC_BASE_URL` is set in the env file and the two-repository patch
(`backend/service/shared/urls.py`, backward compatible: it falls back to the old behavior
when the variable is absent) uses it. **Without the patch the fallback is
still broken in a container**, which is why `PUBLIC_BASE_URL` is documented as
required-in-prod in [ENV.md](ENV.md) even though the unpatched code ignores it.

**Residual risk.** Any code path that builds a URL itself instead of calling the helper
still emits `0.0.0.0`. Grep for `SERVER_PORT` before adding a new one.

## 3. `env.py` wrote `.env` relative to the working directory (closed)

**Defect, as it stood.** `Env.__setattr__` and `Env.__delattr__` called
`dotenv.set_key('.env', ...)` / `unset_key('.env', ...)` — a **relative** path. Reading was
always safe, because `load_dotenv` was given a path anchored to the module
(`backend/env.py:23`); writing was not. Any process started from a directory other than
`backend/` created a second, empty `.env` there and then never read it.

**Status: fixed in the refactor.** The two writers now resolve the same path the reader
uses: `backend/service/configuration/env.py:50-65` calls `set_key(str(env_file()), ...)` and
`unset_key(str(env_file()), ...)`, and `env_file()`
(`backend/service/configuration/paths.py:49-51`) is the one helper both paths share. The
module docstring states the old behaviour as the first of the two defects the move fixed.
That is why `backend/tests/test_import_without_infra.py:88-93` can assert the stronger
property: importing the application writes **no file at all** into the working directory,
from any CWD.

**Consequence today.** Nothing in the request path calls the setters, so the fix is a trap
closed rather than a feature used: container environment variables remain the only
configuration channel, and compose injects them. The superproject `.gitignore` still ignores
`.env` and `.env.*` at every level, so a stray file could not be committed even if a future
change reintroduced one.

**What would have caught it.** Nothing did — the relative write was legal Python, produced a
readable file, and only went wrong when the process ran from an unexpected directory. The
test above pins the whole property rather than the two call sites.

## 4. No dependency lock

**Defect.** Neither dependency list is a lock, and they cover different runtimes.
`backend/requirements.txt` pins the HTTP and data block and leaves a tail floating;
`backend/docker/requirements.lock.txt` is a pinned **snapshot** of it (no hashes, no
transitive closure); and the GPU stack that used to float in the first file now lives in
`ml/docker/requirements.txt`, where `torch==1.12.1+cu116` is pinned against the CUDA 11.6
wheel index.

**Consequence.** Two builds of the same commit can install different versions of the
floating tail. A rebuild months later picks up a new `PyYAML` or `rich` and the difference
is invisible until something breaks. There is no `pip freeze` artifact to compare against,
so the drift is found by a failing request rather than by a diff.

**Mitigation.** Build once per release and keep the image. The pinned blocks cover what
the deployment layer depends on: the web framework (`fastapi==0.111.0`,
`uvicorn==0.30.1`, `PyMySQL==1.1.1`, `PyJWT==2.8.0`) and the data stack
(`numpy==1.26.4`, `pandas==2.2.2`, `scikit-learn==1.5.1`).

`backend/docker/requirements.lock.txt` exists and is the smallest honest step: it is
`requirements.txt` verbatim with a pinned version for every package `requirements.txt`
leaves floating, selected to satisfy three hard constraints (Python 3.9 wheels,
`numpy<2`, `pandas` 2.x). It is **not** a resolved lock: no hashes, no transitive
dependencies, no resolution order, so `pip` still resolves the tree at install time. It is
also not the default - `backend/docker/Dockerfile:52` keeps
`ARG REQUIREMENTS_FILE=requirements.txt`, so passing
`--build-arg REQUIREMENTS_FILE=requirements.lock.txt` is a deliberate operator decision,
and the file drifts the moment `requirements.txt` changes without it.

`backend/requirements-platform.txt` (celery 5.4.0, redis 5.0.8, boto3 1.35.24) is a
**third** list that neither of the other two mentions. An image that runs a worker or
talks to redis/MinIO needs `pip install -r requirements.txt -r requirements-platform.txt`,
and nothing in the lock snapshot records that pair, so a reproducible build of a worker
image is not yet expressible with the artifacts in the repository.

One dependency is neither list names, and it does not need to be: `python-multipart`
arrives transitively because `fastapi==0.111.0` declares it as a hard requirement. The `auth`
slice's router — `service/services/auth/presentation/routers/auth_api/` — declares `Form(...)`
parameters, and defining such a route without the package raises
`RuntimeError: Form data requires "python-multipart" to be installed`. The image is therefore
correct today and fragile tomorrow: narrowing `requirements.txt` to an explicit audited list
must add `python-multipart` back, or the container dies at import - before it can answer a
healthcheck - in a way no gate here models.

## 5. No migrations

**Defect.** The schema exists as `backend/database/HireSnap.sql`, applied by `CreateSQL`
(`backend/service/infrastructure/database/schema.py`) or by the MySQL image's init
directory. There is no Alembic, no version table, no downgrade path.

**Consequence.** A schema change is a hand-edited SQL file plus a manual `ALTER` on every
existing database. Two environments drift and nothing detects it. Rolling back a deploy
does not roll back a schema.

**Mitigation.** Fresh environments get the schema from the MySQL init hook:
`infra/mysql/init/00-init.sh` is mounted at `/docker-entrypoint-initdb.d` and applies
`backend/database/HireSnap.sql` (mounted at `/opt/hiresnap/HireSnap.sql`) on the first boot
of an empty `mysql_data` named volume. Existing volumes are handled by the documented manual
procedure in
[RUNBOOK.md §8.3](RUNBOOK.md#83-existing-volume-nothing-happens-automatically). Back up
before any schema change; see [RUNBOOK.md §12](RUNBOOK.md#12-backup-and-restore).

## 6. Schema creation only happens when `DEBUG=TRUE`

**Defect.** The application creates its own schema from a **mode flag**. The gate now lives
in `service/main.py`, and it reads two keys, but the flag is still the reason it can run in
a deployment at all:

```python
# backend/service/main.py:75
if _debug_flag(env) and os_env_flag("INIT_DB"):
    bootstrap_schema(env)          # lazily imports CreateSQL and calls read_sql()
```

`_debug_flag()` (`:40-47`) is a strict string comparison: `TRUE`, `FALSE`, or a
`RuntimeError` naming the value it saw. `os_env_flag()` (`:82-86`) accepts
`1`/`true`/`yes`/`on` in any case. The two are an **AND**, so `INIT_DB=1` alone does
nothing and `DEBUG=TRUE` alone does nothing — the second key narrows the first, it cannot
override it.

**Consequence.** `CreateSQL.read_sql()` creates the database, executes `HireSnap.sql`,
and bulk-loads the CSVs in the `tables/` directory under `DATA_PATH` for `embeddings`,
`files`, `personalities`, `users`, `info_candidates` and `vacancies`. With
`DEBUG=FALSE` - i.e. in production - **none of that runs**. A production database that
was never seeded by `infra/mysql/init/` starts empty and the first query 500s.

**Mitigation.** `infra/mysql/init/00-init.sh` + `backend/database/HireSnap.sql` is the
production path, and it runs on a fresh volume. `DEBUG` is a mode flag, **not** a schema
switch; using it to create tables is the defect, and shipping `DEBUG=TRUE` to production to
get tables would also enable uvicorn's reloader and turn a typo into a restart loop.

## 7. `autocast` was unconditional (fixed; the CPU path is real)

**Defect, as it stood.** Two call sites opened an autocast context unconditionally -
line 195 of `get_video_embedding.py` and line 46 of `user_embedding.py` in the (since
deleted) `backend/src/modelling/` tree - a path that no longer exists, because the ML code
now lives at `ml/service/` - both `with torch.cuda.amp.autocast():`. On a host without a
CUDA device that raises, so the embedding path failed instead of falling back to full
precision.

**Status: fixed, and the fix moved with the code.** `autocast_context(device)` in
`ml/service/video_embedding.py:170-181` is the single decision point: it returns
`torch.autocast(device_type="cuda", dtype=torch.float16)` only when the device name starts
with `cuda` **and** `torch.cuda.is_available()`, and a null context otherwise. The call
sites go through it (`ml/service/video_embedding.py:233`, `:256`, `:280` and
`ml/service/user_embedding.py:111`, `:125`), and the tensor is moved with
`.to(torch.float32)` so the CPU path is genuine float32 rather than a half-precision
emulation.

**Consequence today.** A CPU-only host is **degraded, not equivalent**: the container
starts, `/health` answers, `/v1/capabilities` reports `cuda: false` and the pipeline runs
in float32 - minutes instead of seconds per video. That is a capacity statement, not an
error, and it is why `GPU=0` is a supported mode rather than a broken one.

**What is still true.** Nothing in this repository exercises the CUDA branch: no test, no
CI job and no image build touches a GPU, so "it worked on my card" remains the only
evidence for it. See §8.

## 8. `torch` 1.12.1 + CUDA 11.6 is the only supported combination

**Defect.** The GPU stack is pinned in exactly one place, `ml/docker/requirements.txt`:
`--extra-index-url https://download.pytorch.org/whl/cu116` (`:18`), then
`torch==1.12.1+cu116` (`:21`), `torchvision==0.13.1+cu116` (`:22`) and
`torchaudio==0.12.1+cu116` (`:23`). The base image is
`nvidia/cuda:11.6.2-cudnn8-runtime-ubuntu20.04` (`ml/docker/Dockerfile:14`) because those
three wheels were built against that toolkit line. `backend/requirements.txt` names none of
them, and `scripts/check_ml_boundary.py` fails a commit that puts one back.

**Consequence.** The sidecar needs a CUDA 11.6-era driver on the host and a matching base
image. Newer GPUs and newer drivers usually work, but nothing here tests that, so the
support statement is "what the pinned wheels were built for". The image is several
gigabytes and is not portable across accelerator generations without a rebuild.

**Mitigation.** Keep the base image and the wheel trio in one file, which they now are,
and treat a torch upgrade as an application project rather than a deployment tweak.

## 9. Errors have no stable code

**Defect.** The API returns FastAPI's default envelope, `{"detail": "..."}`, with no
machine-readable error code. The envelope is now produced in one place -
`ServiceError` and its four subclasses in `service/shared/errors.py`, rendered by the single
handler on the `/server` sub-app - but it is still a `detail` string and nothing else.

**Consequence.** Clients must string-match `detail` to distinguish outcomes, and the
strings are sentences (`"Invalid token or expired token."`) that may be reworded.
Full inventory in
[SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md#22-what-the-backend-actually-returns-partial-gap).

**Mitigation.** The target contract (`{"error","detail"}`) is specified, and the gateway
normalizes its *own* errors to it. Application errors are left as-is, but the refactor left
the cost of fixing them at one file: every rule now raises a `ServiceError` subclass from
`service/shared/errors.py`, and **one** handler on the `/server` sub-app renders it
([SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md#22-what-the-backend-actually-returns-partial-gap)).
Adding the code is a change to that handler and to the four exception classes, not a sweep
over the tree.

**A 404 that stays wrong on purpose.** `Email already exist` - the duplicate-signup failure -
has always answered 404 with a message that reads like a 409. The refactor keeps both, because
changing the status would break every client that already special-cases it and this pass was
supposed to change no path, no method and no response. If the wire contract is ever allowed a
breaking change, that is where a `ConflictError` belongs - and `ConflictError.status_code` is
**404** for exactly that reason (`service/shared/errors.py:58-61`), not because anyone thinks a
duplicate is a missing resource.

## 10. No health endpoint (closed: `GET /server/health` exists)

**Defect, as it stood.** The backend exposed no `/health`, `/ping` or `/status` route, so
container health checks had to use `/server/openapi.json` - a document rebuilt from every
router on every call, run every 30 seconds for the life of the container - and the
gateway's own `/health` proved only that nginx was alive.

**Status: fixed.** `build_server_app()` now registers
`GET /server/health` on the `/server` sub-app (`backend/service/api/app.py:136-151`,
decorator at `:93`), returning `{"status":"ok","service":"backend","version":__version__}`
(`:96`): three keys, no I/O, the same shape the gateway and the `ml` sidecar answer with.
Both images probe it - `backend/docker/Dockerfile:229-230` and the compose override at
`docker/docker-compose.yaml:272-282` - and through the gateway it is `/api/health`, because
the `/api/` location rewrites the prefix (`infra/nginx/templates/site.conf.template:77-79`).

**What still differs.** The gateway's `GET /health` is an exact-match location at the root
(:60-64) with a static `{"status":"ok","service":"gateway"}` body; it answers even when
the backend is down, which is the point, and it is not a substitute for the API's probe.
See [RUNBOOK.md §7](RUNBOOK.md#7-health) and
[SERVICE_CONTRACTS.md §1](SERVICE_CONTRACTS.md#1-health).

## 11. CORS is open by default, and the wildcard cannot carry credentials

**Defect, as it stood.** `create_app()` hardcoded `allow_origins=["*"]` **with**
`allow_credentials=True`, while `CORS_ALLOW_ORIGINS` sat in the manifest
(`docker/.env.example`, section 4) and was read by nothing. Two separate failures: the
deployment had no way to narrow the origin list, and the pair the middleware emitted -
`Access-Control-Allow-Origin: *` together with `Access-Control-Allow-Credentials: true` -
is one browsers **reject**, so a credentialed cross-origin request never worked anyway.

**Status: fixed.** `_cors_settings()` (`backend/service/api/app.py:67-117`) resolves the
key and `create_app()` splats the result into `CORSMiddleware`
(`backend/service/api/app.py:169-177`, `**_cors_settings()` at `:176`).

| `CORS_ALLOW_ORIGINS` | `allow_origins` | `allow_credentials` |
|----------------------|-----------------|---------------------|
| unset, blank, `*`, `["*"]`, `[]`, malformed JSON (with a warning) | `["*"]` | `False` |
| JSON array or comma list of origins | exactly those, trimmed | `True` |

**The rule that matters.** A wildcard origin is never paired with credentials, because
the browser refuses that combination; an explicit list is, because that pairing is legal.
This is not a tightening of the default - unset still means `*` - it is a fix for an
illegal header pair that could never have worked.

**Consequence.** Any origin can still call the API from a browser when the key is unset or
`*`. That is survivable only because the SPA is served same-origin through the gateway, so
CORS is not what protects the API - the Bearer token is. An explicit list is the
configuration a real deployment should use, and the manifest ships one
(`docker/.env.example:116`).

**What would have caught it.** Nothing did: the old value was legal Python, the API
answered same-origin requests perfectly, and the failure needed a *cross-origin credentialed*
request to appear. `backend/tests/test_cors_settings.py` now pins the resolution table,
including an end-to-end assertion that `create_app()` hands the resolved value to the
middleware rather than merely computing it.

See [ENV.md §3.2](ENV.md#32-cors_allow_origins--consumed-and-the-wildcardcredentials-rule).

## 12. Weights are not in the repository

**Defect.** The checkpoints are host files, gitignored, and resolved through one function:
`settings.cosmos_encoder_path()` = `pretrained_ckpt_dir()` / `Cosmos-Tokenizer-CV4x8x8` /
`encoder.jit` (`ml/service/settings.py:148-172`, directory constant at `:162`). That
single function is what closed the older defect, where the pipeline opened the literal
`pretrained_ckpts/Cosmos-Tokenizer-CV4x8x8/encoder.jit` **relative to the working
directory** while the CLI examples under `ml/service/cosmos_tokenizer/` use different names
(`CosmosCV_f4x8x8` / `CosmosCI_f8x8`), so copying a tree out of those examples left the
loader unable to find it. `Unet3D.pth` is the second required file, and two Hugging Face
models are downloaded on first use. `backend/.gitignore` still carries a rule for the
retired `/src/weights/` path - a leftover from the deleted tree - and the superproject
ignores `infra/models/`.

**Consequence.** A fresh clone cannot produce a video embedding. The failure is at
runtime, on the first request that needs a tokenizer, not at startup: `/health` is green
either way, which is why `/v1/capabilities` is the probe to alert on.

**Mitigation.** `infra/models/weights/` and `infra/models/pretrained_ckpts/` are host
directories bind-mounted read-only into the **`ml` container only**, at
`/app/models/weights` and `/app/models/pretrained_ckpts`
(`docker/docker-compose.yaml:520-521`). The API never opens a model file. For an offline
deployment, pre-seed the Hugging Face cache (`hf_cache` volume); see
[RUNBOOK.md §9](RUNBOOK.md#9-mounted-state-certs-weights-storage-logs) and
[SIDECARS.md §4.3](SIDECARS.md#43-mounts-and-configuration).

## 13. The platform layer is delivered but its call sites are thin

**Defect.** `backend/service/infrastructure/` (cache, storage, mail),
`backend/service/infrastructure/messaging/` (celery app, tasks, beat schedule) and the `jobs`
slice (`service/services/jobs/application/job_service.py`, the module that used to be
`jobs_services.py`) all exist and are tested, and the containers that host them ship. **No
HTTP route calls them.** Every request is still answered from MySQL and the local filesystem,
exactly as before the layer landed.

- `backend/service/api/app.py` mounts twelve routers and none of them is a `/server/jobs`
  route. The jobs slice does reach the cache - `job_service.py:111` resolves it through
  `state.get_cache()` - and that is the sharpest way to see the shape of the gap: the one
  consumer of the platform layer is a subsystem no route can reach, so `send_now_or_later`,
  `get_storage` and the rest still have the facades and their tests as their only callers.
- Signup (`service/services/auth/presentation/routers/auth_api/`) calls the auth
  application service's `signup()` and returns; no welcome mail is sent, because no caller
  reaches `mail.send_now_or_later`.
- The listing endpoints query MySQL on every request; the cache facade is never consulted.

**Mitigation.** Document the dormancy instead of advertising the feature (this entry and
[SIDECARS.md](SIDECARS.md)). The layer's own tests
(`backend/tests/test_infra_fallbacks.py`, `test_jobs_services.py`, `test_worker_routing.py`,
`test_ports_contracts.py`) prove the facades work; they cannot prove a route uses them.
The wiring is an application change with its own review: add the route, then the flag, then
the test that fails when the neighbour is down.

**Residual risk.** The failure semantics are the inverse of what a reader expects in two
places. Cache fails **open** (a miss), but job state lives in the cache too: with
`CACHE__BACKEND=memory` — the pre-cache default — `JobService.status()` only knows
about jobs enqueued in the **same process**, so a job id handed to a different worker, or
read after a restart, reports `unknown`. `CACHE=1` fixes that; a deployment that runs
celery without it does not. Storage fails **closed** (`StorageError`), so an S3 outage is
a 500 on the upload path once uploads use it, not a silent write to the local disk.

## 14. The sidecar is the only inference path

**Defect, from the other side.** The API has no in-process inference path. There used to be
one: `backend/service/infrastructure/ml_sidecar.py` imported the ML pipeline in-process when
`ML__SERVICE_URL` was unset and POSTed to the sidecar when it was set. That branch is
gone - the module is HTTP-only, with a docstring at the top saying why
(`ml_sidecar.py:1-43`).

- `service_url()` (`ml_sidecar.py:101-120`) returns the first non-empty of
  `ML_SERVICE_URL` / `ML__SERVICE_URL`, and otherwise `DEFAULT_SERVICE_URL` =
  `http://127.0.0.1:8080` (`:65`). Nothing branches on "is it set"; `is_remote()`
  (`:123-131`) returns `True` unconditionally.
- The `matching` slice's application service imports `get_user_embedding` from the client
  and `upload_video()` calls it - the only wired call site.
- The four celery services take the same variable and `depends_on: ml`
  (`docker/docker-compose.yaml:104`, `:256-263`).

That default is not a fallback worth having. Inside the API container,
`127.0.0.1:8080` is the API itself, which serves no `/v1/*` route, so an emptied key
turns every upload into `ml_service_unreachable` rather than into local inference.
`ML__SERVICE_URL` ships **non-empty** (`http://ml:8080`, `docker/.env.example:267`) for
exactly that reason.

**Consequence, and it is the accepted cost of the split.** While `ml` is down, still
starting, or degraded, an upload fails: `503 service_unavailable` when the sidecar is up
but cannot load a model, `MLUnavailable`/`ml_service_unreachable` when nothing answers at
that URL. Every other route keeps working, because no other route touches a model. That
is the trade the split makes - inference availability becomes a service dependency
instead of a per-container capability - and it is recorded in
[ADR/0006](ADR/0006-ml-sidecar-owns-inference.md) rather than treated as a defect to
route around.

**What replaces the old mitigation.** The question is no longer *which transport* but
*which URL*. [RUNBOOK.md §14](RUNBOOK.md#14-proving-the-sidecar-is-used) gives the paired
log evidence, and
[§14.1](RUNBOOK.md#141-reading-the-transport-out-of-a-running-container) asks the client
itself via `describe_transport()`, which always reports `"transport": "http"` and names
the URL it would dial.

**Residual risk.** The failure shapes are now uniform (the client's own exceptions, not a
mixture of pipeline and HTTP errors), but a start-order race remains: `depends_on: ml` uses
`condition: service_started`, not `service_healthy`, because the sidecar's 300-second start
period would otherwise block the API's bootstrap. A first upload that arrives while the
sidecar is still loading models gets `503` and succeeds on retry. Queue it rather than
retrying in a tight loop.

## 15. Three dependency lists, one of them the sidecar's own

**Defect.** There is no single install set for the deployment. Three files each describe a
part of it, and nothing reconciles them:

- `backend/requirements.txt` - the API. Pinned HTTP and data block, floating tail.
- `backend/requirements-platform.txt` (celery 5.4.0, redis 5.0.8, boto3 1.35.24) - a
  *separate* list installed only in images that run a worker or talk to Redis/MinIO.
  `backend/docker/Dockerfile:52` keeps `ARG REQUIREMENTS_FILE=requirements.txt`, so the
  platform trio is an extra `pip install` and never part of the lock snapshot.
- `ml/docker/requirements.txt` (49 lines) - the sidecar, and **its own file** rather than
  a shared one. It pins the CUDA wheel index (`:18`) and `torch==1.12.1+cu116` (`:21`),
  `transformers==4.30.2` (`:24`), `sentence-transformers==2.2.2` (`:25`), `moviepy==1.0.3`
  (`:26`), `librosa==0.10.1` (`:29`) and the rest through `requests==2.32.3` (`:48`).
  `pandas` is deliberately absent (`:31-33`): nothing in the sidecar imports it.

**Consequence.** The API image and the sidecar image resolve different trees from different
files, so "the deployment's dependencies" is three answers. A version that works for the API
can be incompatible with the sidecar's torch pin, and nothing detects that until a request
crosses the boundary.

**Mitigation.** Keep the boundary explicit instead of merging the files: the sidecar's
requirements are part of the sidecar image contract ([SIDECARS.md §4](SIDECARS.md#4-ml--inference-sidecar)),
and `ml/docker/requirements.txt` is what the image installs (`ml/docker/Dockerfile:94`,
`:96-97`). A shared file was rejected because the CUDA index and the torch pin would then
be a property of the API image too - see [ADR/0006](ADR/0006-ml-sidecar-owns-inference.md).

**Residual risk.** None of the three carries hashes, so a compromised index is not detected
at install time. See §4.

## 16. The celery ML client spoke to an endpoint the sidecar does not serve (closed)

**Defect (historical).** The background task that warms an embedding posted to a route, a
body and a response shape that the sidecar does not implement, so it could never succeed
against it:

- The background task posted to `base_url.rstrip("/") + "/embed"` (the module is
  `service/infrastructure/messaging/tasks.py` today); the sidecar serves
  `POST /v1/embed/video`, `/v1/embed/text` and `/v1/transcribe`
  (`ml/service/app.py:395-427`). There is no `/embed` route, so the sidecar answered 404.
- The body was JSON `{"file_key", "path"}`; the media routes take the **bytes** as
  the request body (`application/octet-stream`, with `multipart/form-data` field
  `file` also accepted).
- The response was read as `body["embedding"]`; the sidecar returns
  `{"vector", "dim", "model"}`.
- The module probe looked for `modelling.ml_client` and `ml.client`, neither of
  which exists, and fell back to `ML__BASE_URL` instead of `ML_SERVICE_URL`.

**Status: fixed.** The task now goes through the ML client only — the same transport the
request path uses, so there is one place that knows the sidecar's contract. (The module was
`service/services/ml_client.py` when this was fixed; [ADR/0008](ADR/0008-vertical-slice-services.md)
has since moved it to `service/infrastructure/ml_sidecar.py`, which is why the names below
read as they did at the time.)

- `_ml_client()` lazily imports `service.infrastructure.ml_sidecar` and wraps an
  import failure in `TaskUnavailable("ml_sidecar_unavailable: ...")`
  (`tasks.py:85-91`). A task cannot silently fall back to a transport that does not
  exist. The helper keeps its old name; the module it returns does not.
- `_local_media_path(storage, file_key, source_path=None)` returns `(path,
  temporary)`: an existing `source_path` as-is; else `storage.path_for(key)` when
  that is a file (a local backend already holds the bytes, so no copy); else
  `storage.read_bytes(key)` materialised into a `NamedTemporaryFile` with the
  **original extension**, because ffmpeg picks its demuxer from the filename.
- `embed_candidate()` calls `get_user_embedding(media_path)` on that client, stores the
  `.npy` through the storage facade, and deletes the temporary file in a `finally`
  that logs `temp_cleanup_failed` instead of masking the original error. The reported
  embedder is `<transport>`, so a job record says which path ran.

The sidecar is never handed a URL or a credential: it receives bytes that the worker
already had, which is why the object store stays private.

**Coverage.** `backend/tests/test_worker_ml_seam.py` exercises the seam with no torch, no
sidecar and no weights: a real path is not copied, a local path is not copied, an
object-store object is materialised **with its extension**, the temporary file is removed on
success **and** on failure, the key and the bytes are deterministic, the module actually
imported is `service.infrastructure.ml_sidecar` (`test_worker_ml_seam.py:154-159`), and a
blocked import raises `TaskUnavailable` with `ml_sidecar_unavailable` in the message
(`:161-175`). No pass count is quoted here on purpose: the suite is being extended in the same
change and a number in prose is stale the day after.

**Residual risk.** The task is still only reachable from a code path nothing calls
([§13](#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)), so the fix is proven by tests
rather than by traffic. The consumer now exists — `CELERY=1` starts
`celery-worker-agents`, `celery-worker-maintenance`, `celery-worker-ml` and `celery-beat`
(`docker/docker-compose.yaml:579-768`) and exports `CELERY__ENABLED=true` — but
`CELERY__ENABLED=true` *without* the profile still queues work with nothing to consume it,
which is why the flag and the profile are derived from one another in
`docker/sidecar_profiles.sh` and why the compose env anchor keeps the enabled key at
`false` by default.

## 17. The schema dump is table-only; adding CREATE DATABASE would split it in two

**Defect.** `backend/database/HireSnap.sql` contains **no** `CREATE DATABASE` and **no**
`USE` statement. All ten tables are declared `CREATE TABLE IF NOT EXISTS`, and the
only occurrence of `HireSnap` in the file is the phpMyAdmin comment on line 21 (a
database name that MySQL never reads). The loader passes the database name explicitly —
`infra/mysql/init/00-init.sh:89` runs
`mysql ... "$DB_NAME" < "$SCHEMA_PATH"` — so the tables land in `DB__NAME`
(`hiresnap`, lowercase) no matter what the comment says.

**Consequence.** None today, and the case mismatch in the comment is a red herring. The
trap is the export dialog: phpMyAdmin offers "Add CREATE DATABASE / USE statement", and
re-exporting this database with that box ticked produces a dump that creates and selects a
schema named `HireSnap`. The loader would then create a **second**, empty-looking schema
beside `hiresnap` while the application kept connecting to `hiresnap` (populated by
an earlier boot), and the two would drift silently — one of them collecting tables from
half the schema.

**Mitigation.** Keep the table-only export. If the dump is ever regenerated, verify it
before committing:

```bash
grep -nE '^(CREATE DATABASE|USE )' backend/database/HireSnap.sql   # must print nothing
```

and confirm the loader still passes `DB_NAME` as an argument rather than relying on the
file (`infra/mysql/init/00-init.sh:89`). The default database name is `hiresnap` in
the manifest (`docker/.env.example:135`), in the MySQL image (`MYSQL_DATABASE`) and
in the init script's fallback chain.

**Residual risk.** `lower_case_table_names` differs between a Linux container and a
Windows host, so a mixed workflow that inspects the volume with a host client can see the
tables under a differently-cased name and conclude the load failed. It did not: the
container is the reference.

## 18. `get_all_candidates()` sorted dictionaries and raised `TypeError` (closed)

**Defect, as it stood.** Two ranking functions sorted a list of dicts with no key function:

```python
vacancy_candidates.append(sample.model_dump())   # a dict
vacancy_candidates.sort(reverse=True)            # the bug
```

`dict` objects are not orderable in Python 3, so the call raised
`TypeError: '<' not supported between instances of 'dict' and 'dict'` as soon as the list
held **two or more** entries. With zero or one candidate the sort was a no-op and the
function returned normally, which is why it survived casual testing. Both functions were
written together and never exercised with real data; the loop variable in
`get_all_vacancies()` was even spelled `smaple` while the body read `sample.`, so that
branch raised `NameError` before it could sort anything.

**Status: fixed in the package move.** The two functions live in the `matching` slice's
application service (`backend/service/services/matching/application/matching_service.py`,
`get_all_vacancies()` at `:356` and `get_all_candidates()` at `:370`), and both now
sort by an explicit key:

```python
rows.sort(key=lambda row: row.get("distance") or 0, reverse=True)   # matching_service.py:367
rows.sort(key=lambda row: row.get("distance") or 0, reverse=True)   # matching_service.py:382
```

`distance` is the similarity score the matching matrix writes (`decimal(6,6)` in
`backend/database/HireSnap.sql`), so the ordering is "closest first" and the intent is
stated rather than inferred from a comparison. The misspelled loop variable is renamed, and
the reason is recorded in the docstring of each function (`matching_service.py:357-363` and
`:371-376`), not in a comment.

**Why no gate caught it, and why that has not changed.** The ruff configuration runs a
narrow rule set; the rules that are enabled (undefined names, unused imports) are
**syntactic**. `sort(reverse=True)` on a list is legal Python for any element type - the
failure is a runtime type error inside a library method, which no linter detects and no
type checker sees either, because `model_dump()` is annotated to return `dict` and
`list.sort` accepts `Iterable[Any]`. mypy reports nothing here for the same reason. There is
still **no test with two rows**, which is the only thing that would have caught it; the
fix is asserted by the code, not by a gate.

**Why the entry stays.** It is the clearest example in this file of a defect that is
invisible to every check the repository runs and fatal to real data: a stack that passed
every healthcheck could still 500 on the candidate list. The lesson is not "sort with a
key" - it is that a shape-only test suite reads as coverage.

## 19. Legacy repositories raised `NameError` instead of a 400 (fixed)

**Defect (historical).** Three repository modules used `HTTPException` and
`status` in their `update_*()` functions without importing either name, so the
guard that is supposed to reject an empty payload raised `NameError` — a 500 — instead
of the intended 400:

- `service/services/candidate_vacancies/persistence/candidate_vacancy_repository.py`
- `service/services/personality_candidates/persistence/personality_candidate_repository.py`
- `service/services/personality_vacancies/persistence/personality_vacancy_repository.py`

**Status: fixed.** Each module now has `from fastapi import HTTPException, status`
(**line 6** in all three; the import moved down one line when the slice moved) with a
comment at `:3-5` saying why it exists, so an empty `update_*()` payload returns 400 with
the module's own message. The note stays here
because the shape of the failure is instructive: a guard branch that no test exercised
looked correct in review for as long as it existed.

**Consequence if it regresses.** The endpoint answers 500 to a client mistake, which reads
as a server outage in every dashboard and hides the real cause (an empty form).

**Mitigation.** The import is a one-line change per module and is recorded in
[ADR/0005-source-patches.md](ADR/0005-source-patches.md) with the other source-level
additions. Nothing else about these modules changed.

## 20. The HTTP contract is narrower than the old function signatures

**Defect.** `backend/service/infrastructure/ml_sidecar.py` keeps the signatures the in-process
implementations had, and the HTTP transport cannot honour every keyword. The differences
are silent - the call succeeds and returns a vector - which is worse than an error:

| Knob | Old in-process meaning | Over HTTP | Evidence |
|------|------------------------|-----------|----------|
| `num_frames` | honoured (default 16) | **ignored**; the sidecar runs its fixed 16-frame path | the client warns when it is not 16 |
| `frames_temp_folder`, `audio_temp_folder` | honoured | **ignored**; the sidecar spools into its own scratch dir | the client debug-logs it |
| `unet_weights_path` | honoured | **ignored**; `WEIGHTS_DIR` inside the sidecar decides | dropped on purpose: a backend-relative path means nothing in another container |
| `output_embedding_path` | writes the `.npy` | **still works** - the client decodes the response and saves it | `ml_sidecar.py` |
| per-word speech CSV (`path_to_table_speech`) | could be written | **not reachable**: `audio2text` is always called with `save_table=False` | `ml/service/engine.py` transcribe path |

**Consequence.** A caller that tuned an embedding - a shorter clip, a custom weight file, a
CSV of word timings - gets a different result and no error. The one that bites is the
speech table: nothing consumes it today, but a transcript-with-timestamps feature would
work in-process and return `None` here.

**Mitigation.** The client logs every dropped knob, so the behaviour is visible in the API
log. Do not add a caller that depends on these arguments until the sidecar accepts them:
the transport boundary is the contract, and a parameter that exists on one side only is a
bug waiting for its second caller. Two of the four are cheap to close - `num_frames` and
the scratch dirs are already request-independent on the sidecar side and could become body
fields without a pipeline change.

**Residual risk.** Error shapes are now uniform (`MLUnavailable`, `MLRejected`,
`MLContractError`, all subclasses of `MLClientError` with `code`, `detail` and `status`),
but the *causes* are not: a 503 from the sidecar means a missing model, while a 503 from
the gateway in front of the API means something else entirely. The client's exceptions name
the sidecar, not the hop.

## 21. Two ceilings you will hit on the sidecar path, and one you will not

**Defect / behaviour.** Three independent limits apply to one upload, and they fail with
different errors from different components:

| Limit | Where | Symptom |
|-------|-------|---------|
| `client_max_body_size 500m` | gateway (`infra/nginx/nginx.conf:71`) | nginx answers **413** before the request reaches the API |
| `MAX_UPLOAD_MB` (default 512) | the sidecar (`ml/service/app.py:228-272`) | sidecar answers **413** `{"error":"request_too_large"}`; the check is incremental, so it fires before the whole body is spooled |
| `ML_SERVICE_TIMEOUT` (default 900 s) | the API client | `MLUnavailable` with a `requests` timeout inside the message; the sidecar keeps working on a request nobody is waiting for |

**What does *not* happen:** the sidecar does **not** buffer the body in memory. Both media
routes spool to a file under `DATA_PATH/ml-incoming`, falling back to the system temp dir
(`ml/service/app.py:197-215`), so a 300 MB video streams to disk. The **backend client**
is the one that reads the file into memory before posting (`ml_sidecar.py`
`media.read_bytes()`), so the API container's memory ceiling must fit the largest video
even though the sidecar's does not. The multipart branch does read the part into memory
(`ml/service/app.py:305-320`) - one more reason the raw-bytes form is the documented one.

**Consequence.** A video between 500 MB and 512 MB is accepted by the gateway and refused
by the sidecar; one larger than 512 MB is refused by both, with two different bodies. Above
`ML_SERVICE_TIMEOUT` the client gives up while the GPU keeps working, and the next request
queues behind that work because the engine serialises model access.

**Mitigation.** Size `MAX_UPLOAD_MB` **below** the gateway ceiling so the failure has one
shape, and raise `ML_SERVICE_TIMEOUT` rather than retrying: the timeout stops the waiting,
not the work. GPU work is serialised behind one model cache and a `threading.RLock`
(`ml/service/engine.py:133`) in a single uvicorn worker (`ml/service/app.py:450-459`), so
concurrency buys nothing - plan a queue, not a retry storm. That serialisation is recorded
in §23.

**Residual risk.** The timeout is per call, not per job: a project that embeds a 40-minute
video is inside 900 s today and will not always be. Watch the p99 of `/v1/embed/video`
before raising the ceiling in both places.

## 22. The `ml` service built the API image and published no URL (closed)

**The defect, as it stood.** `ml/Dockerfile` existed, built and was pinned
(`scripts/check_dockerfile_pins.py` saw it) and `ml/service/app.py` (then `ml/app.py`)
served the five routes, but neither compose file used that image. Both declared the
service as:

```yaml
  ml:
    profiles: ["ml"]
    build:
      context: ..
      dockerfile: ./backend/docker/Dockerfile      # not ./ml/Dockerfile
    env_file: ./.env.prod
    environment:
      DB_HOST: ...  DB_PORT: ...  DB_USER: ...  DB_PASSWORD: ...
```

Three consequences followed, and they are why this entry is kept rather than deleted:

1. `ML=1` started a **second copy of the FastAPI app**, not the inference service. It
   answered `/server/*`, mounted the same volumes and competed for the same database.
   Nothing routed inference traffic to it, and nothing could.
2. No `ML__SERVICE_URL` was exported anywhere, so the API kept running inference
   **in process** even with the flag on.
3. The Dockerfile's own env contract (`ML_PORT`, `ML_HOST`, `WEIGHTS_DIR` ...) was never
   applied, so the keys in [ENV.md §8.3](ENV.md#83-ml) were code defaults and nothing else.

An earlier version of this entry understated it, calling `ml` a mis-provisioned container.
The sharper statement is that the service built the same backend image **and** published no
`ML__SERVICE_URL`, so the seam the sidecar exists for -
the ML client, now `backend/service/infrastructure/ml_sidecar.py` - provably ran **in-process** in every deployed
configuration, profile on or off.

**What closed it.** The compose integration landed, and the shape is now:

- `context: ../ml`, `dockerfile: docker/Dockerfile` in both files
  (`docker/docker-compose.yaml:458-459`, `docker/docker-compose.dev.yaml:348-349`). The
  build context is the **submodule**, so the image cannot reach `backend/` even by accident,
  and `ml/.dockerignore` decides what the daemon sees.
- The service is **core**, not a profile: `profiles: ["prod"]` / `["dev"]`, exactly like
  the API. There is no `ml` profile, no `ML` flag and no `ML_PUBLISH_PORT`.
- The `DB_*` block is gone and there is no `depends_on: mysql`; the sidecar is stateless
  and joins `default` and `internal` only (`docker/docker-compose.yaml:562-563`).
- Port `8080` inside the container and **no host port in either mode**
  (`docker/run.sh:168-171`); the way in is `make ml-shell`.
- The API and all four celery services receive
  `ML_SERVICE_URL: ${ML__SERVICE_URL:-http://ml:8080}` from the `x-backend-env` anchor
  (`docker/docker-compose.yaml:104`) and `depends_on: ml` with
  `condition: service_started` (`:253-263`), so there is one service to start, not a flag
  plus a variable.
- Weights and checkpoints are mounted read-only into **this container only**, at
  `/app/models/weights` and `/app/models/pretrained_ckpts`
  (`docker/docker-compose.yaml:520-521`); the API mounts neither.

**What would have caught it.** `scripts/check_compose_parity.py` compares the two stacks to
each other, so two identical wrong blocks passed. Two changes close that:

1. The **core** profile is now identified by which profile owns a `gateway` service rather
   than by service count, so the comparison stays honest as optional profiles are added.
2. It now asserts that the `ml` service builds with a context **inside the ml submodule**
   and that no other service does (`scripts/check_compose_parity.py:373-492`, rule 6). The
   assertion is deliberately narrow - proving the image actually answers `/v1/embed/video`
   needs a built image and a GPU, and this guard has to stay offline and sub-second.
   `scripts/check_ml_boundary.py` is the companion rule on the source side.

Unlike the other closed entries this one is enforced by a **path comparison**, not by a
behavioural test, so a regression is caught the moment a compose file points `ml` back at
the root. The narrower Dockerfile-path rule it replaced was proven by regression at the
time (pointing `ml` at `./backend/docker/Dockerfile` produced
`[FAIL] compose-parity: prod service 'ml' builds with context R:\HireSnap instead of the ml
submodule` with `failures=1`).

## 23. The sidecar is the only inference path, and it serialises GPU work

**Two facts that belong together, because they fail in the same scenario.**

**23.1 No second path.** `backend/service/infrastructure/ml_sidecar.py` is HTTP-only
(§14). The consequence is availability coupling that did not exist before the split: while
`ml` is down, starting, or degraded, an upload fails - `503 service_unavailable` when the
sidecar is up but cannot load a model, `MLUnavailable`/`ml_service_unreachable` when
nothing answers at the configured URL. Every other route keeps working, because no other
route touches a model.

This is the accepted cost of the architecture, not a defect to route around: the
alternative - keeping a second inference implementation in the API - is exactly what
[ADR/0006](ADR/0006-ml-sidecar-owns-inference.md) rejects, because two implementations
drift and the drift is silent. The operating consequence is that `ml` belongs in the same
availability conversation as MySQL, and its 300-second start period
(`docker/docker-compose.yaml:538-546`) is the window in which an upload can fail.

`depends_on: ml` uses `condition: service_started`, not `service_healthy` - deliberate,
because the health condition would block the API's own bootstrap for those 300 seconds. A
first upload that arrives during the sidecar's model load gets `503` and succeeds on retry.

**23.2 Serialised GPU work.** The engine holds one model cache behind a
`threading.RLock` (`ml/service/engine.py:133`) inside a single uvicorn worker
(`ml/service/app.py:450-459`, `workers=1` on purpose). Two concurrent video embeddings do
not run in parallel; the second waits for the first. A 40-minute video therefore occupies
the sidecar for the duration, and every other upload queues behind it.

**Consequence.** Throughput is a function of card count and video length, not of request
concurrency. Raising `ML_SERVICE_TIMEOUT` does not create capacity: the timeout stops the
client waiting, not the GPU working (§21).

**Mitigation.** Scale by adding GPU replicas and a queue in front of the sidecar - which is
what the `celery-worker-ml` service exists for - rather than by raising worker counts. Do
not put two `ml` replicas behind one GPU: they would each load their own copy of the
models and contend for the same memory, which is worse than queueing.

**Residual risk.** Nothing measures queue depth. `/v1/capabilities` reports what is loaded,
not how long the queue is, so the first symptom of saturation is a client timeout.

## 24. A credential left the working tree but is still in git history

**Defect.** The dadata.ru client token used by the supplier requisites form was committed as
a literal. **The literal is gone from the working tree**, and so is the form: the supplier
tree was unreachable from `src/index.js` (86 of the frontend's 121 files were, see
[ADR/0007](ADR/0007-sidecar-package-layout.md)) and was deleted outright rather than
rewritten. Measured after that deletion, `grep -rn process.env frontend/src` has exactly one
hit - `frontend/src/shared/api/apiConsts.js:16`, reading `REACT_APP_BASE_URL` - so **no code
in the tree reads the dadata key today**, and its value is empty in the manifest
(`docker/.env.example:196`).

**What survives, and where.** The token itself survives **only in `frontend`'s git history**,
in every clone and every fork of the remote; cleaning the working tree does not reach it. The
plumbing survives on purpose and is wired end to end: the manifest key
(`docker/.env.example:196`) is passed as a build argument
(`docker/docker-compose.yaml:312`, and as a runtime variable in dev at
`docker/docker-compose.dev.yaml:223`) into `frontend/docker/Dockerfile:102-103`, which
declares `ARG REACT_APP_DADATA_TOKEN=` and re-exports it as `ENV`. The Dockerfile comment states the
position exactly: an empty default, a build that must never fail for a missing token, and
wiring kept so that adding the lookup later needs no image change.

**Consequence.** Anyone who can read the repository history has the old key. Rewriting
history does not fix it on its own: the remote has it, any clone has it, and dadata has
already issued it.

**Mitigation.** Rotate the token AT dadata, and put the new value in `docker/.env.dev` /
`docker/.env.prod` (never in the manifest). Rotation is the only fix that invalidates the
leaked value; scrubbing history is optional hygiene on top of it. The same reasoning applies
to any credential found in a submodule's history, which is why
[SECRETS.md](SECRETS.md) treats a leaked key as rotated-or-not rather than as
committed-or-not.

**Residual risk.** Until the rotation happens the old value is still valid at the issuer. And
if the lookup is ever revived, remember what `REACT_APP_*` means: CRA inlines it into the
browser bundle, so the value is visible to every visitor by construction. It is a
rate-limited public client key, which bounds the damage to quota theft rather than to
disclosure - which is also why the empty default is the normal state and not a
misconfiguration.

## 25. The vertical slices are a package boundary, not a domain model

[ADR/0008](ADR/0008-vertical-slice-services.md) moves the backend from three sibling layer
directories into thirteen directories, one per bounded context. It is worth being explicit about
what that does **not** buy, because "we have vertical slices now" invites three assumptions the
tree does not support.

**25.1 The repository returns rows, not aggregates.** Each
`persistence/<domain>_repository.py` still holds raw SQL and hands back dictionaries or
model rows - the same SQL that lived in `service/repositories/` before the move, with a new
import path. There is no aggregate root, no identity map and no unit of work: the slice groups
the code that changes together, it does not define a consistency boundary. Consequence: a rule
that spans two tables in one slice is enforced by the application service, in Python, and
nothing prevents a second caller from reaching the repository directly.

**25.2 There is no service-level transaction boundary.** A connection is opened per call
(`service/infrastructure/database/connection.py`) and every query commits on its own, so an
operation that writes twice is not atomic. This was true before the refactor and is unchanged by
it - the slices did not introduce the problem and do not fix it. It is cheap to revisit
precisely because the writes now sit in one `persistence/` module per domain: wrapping them
in an explicit transaction is a change to that file plus the service that calls it, not a
search across the tree.

**25.3 The verticals share their entities.** `service/models/db_models.py` is one module for
every domain; `candidates` does not own its row model. So the *behaviour* is sliced while
the *data shape* is not, and a change to a shared model still touches every slice that uses it.
That is a deliberate stopping point: thirteen copies of a schema that must agree is worse than one
file that is obviously shared, and splitting it is only worth doing when a domain's model
genuinely stops being shared.

**Why these are recorded rather than fixed.** All three are invisible to the gates and would
each be a multi-day refactor with no user-visible change. Recording them keeps the honest
reading of the layout - the slice is the unit of *ownership*, not the unit of *consistency* -
and it tells the next reader which questions the tree already answers (where does a vacancies
change go?) and which it does not (is this write atomic?).

**What would change the answer.** A domain that grows a real invariant across tables earns a
transaction boundary (25.2) and possibly its own model module (25.3). A domain whose repository
grows past a few queries earns an aggregate (25.1). None of that should be imposed uniformly:
see the second rejected alternative in [ADR/0008](ADR/0008-vertical-slice-services.md), where
full hexagonal ports and protocols for a 40-line repository are rejected for the same reason.

## 26. The personality-vacancy link is written against the wrong personality

**Defect, preserved on purpose.** `similarity_for_user()` scores one candidate against every
personality and every vacancy, then writes two link tables. The second loop is wrong:

```python
for j, personality in enumerate(personalities):        # j ends at len(personalities) - 1
    ...create_personality_candidate(personality_id=personality.ID, ...)

for similarity, vacancy in zip(P[0, :], vacancies):
    ...create_personality_vacancy(personality_id=personalities[j].ID, ...)   # j is stale
```

`personalities[j]` reads the index left over from the **previous** loop instead of the
personality the `C`/`P` row actually belongs to
(`backend/service/services/matching/application/matching_service.py:247-266`, `j` reused at
`:262`). `P` is vacancy-vs-personality (`:72`, built at `:78` inside `build_matrix()`), so the
score is right and the personality it is attributed to is arbitrary - whichever one the first
loop happened to stop on.

**Why it is not fixed here.** Correcting it changes rows that are already stored: the
`personality_vacancies` table would need a backfill, which is a migration, not a refactor.
The docstring says so in the code itself (`matching_service.py:222-227`: *"KNOWN DEFECT,
PRESERVED ON PURPOSE ... Fixing it changes stored data, which is a migration"*), so a reader
who finds the loop is told why it is still there rather than assuming it was missed.

**Why it is invisible in production.** No route calls the function. It has no route of its
own - the four `inference` routes call `upload_video`, `upload_vacancy`, `get_all_vacancies`
and `get_all_candidates` - so this deployment never wrote a wrong row; the defect is a trap
for whoever wires the pipeline back up, which is why it is recorded here rather than silently
fixed or silently dropped. Outside `tests/`, a tree-wide search finds exactly one occurrence
of the name: its own `def` at `matching_service.py:216`.

**Mitigation.** Fix it in the same change that gives the function a caller, together with
the backfill for rows already written by the old `main_services.py`: the pair is one
migration and one loop. Until then the honest statement is that the link table cannot be
trusted if it has any rows in it, and a fresh database has none.

**Pinned by a test, not merely described.**
`backend/tests/test_slices_application.py::test_similarity_stores_the_personality_the_score_belongs_to`
(`:861-921`) states the correct expectation - a vacancy that scores against OCEAN column 0
must be stored against `personalities[0]` - and is marked `xfail(strict=True)` with a reason
naming this section (`:861-869`). Two consequences worth knowing: the suite is green **on** the
bug rather than blind to it, and `strict=True` turns an unexpected pass into a failure, so
fixing the loop and deleting the marker have to happen in the same commit.

**What would have caught it originally.** Nothing in the tree did. It is not a syntax error,
it is not a type error (`j` is an `int` and `personalities[j]` type-checks), and until the
slice move no test exercised the function at all. ruff and mypy are both clean on it, which is
the same lesson as §18 from the other side: a shape-only suite reads as coverage. What changed
is that the gap is now asserted from the outside - an xfail is a recorded expectation, not a
green tick.

## 27. The `jobs` slice has no HTTP route, on purpose

**Not a defect, an omission with a reason.** `jobs` is the one slice with no `presentation/`
layer: `service/services/jobs/` holds `application/job_service.py` and nothing else
(`job_service.py:37-39`: *"WHY THERE IS NO presentation/ LAYER: no route exposes a job
today ... adding a `/server/jobs` endpoint is a contract change that needs the snapshot
regenerated, so it is a separate decision rather than a hole in this slice"*).

The layout guard knows the exception by name rather than by accident:
`SLICES_WITHOUT_PRESENTATION = {"jobs"}`
(`backend/scripts/check_backend_layout.py:121`), so a slice that loses its router fails the
build while `jobs` stays valid.

**Consequence.** The job machinery is reachable only from Python: `enqueue()` and `status()`
are called by nothing in the request path, which is the dormancy recorded in §13. Two
different facts are being stated and it is worth keeping them apart - §13 says the platform
layer has no call sites; this entry says one slice will never have one until the contract
changes.

**What adding it costs.** A new route under `/server/jobs` is a public contract change: the
OpenAPI snapshot (`backend/tests/snapshots/critical_endpoint_openapi.json`) has to be
regenerated with `python -m tests.snapshot_tool --write`, `ml/README.md` and
[SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md) grow a section, and the router has to be listed
in `service/api/registry.py`. That is the whole reason it is a decision instead of a
20-line patch.

## 28. The four inference routes raised `NameError` on every call (closed)

**Defect, as it stood.** The inference handlers called `main_services.upload_video(...)` and
its three siblings. `main_services` was the module that held those functions
(`service/services/main_services.py`, now the `matching` slice's application service), and the
router never imported it, so every one of the four routes answered 500 with
`NameError: name 'main_services' is not defined` the first time it was called - not on a bad
payload, on any payload.

**Why it survived review.** The name looked imported, because the same module name was
imported in the neighbouring routers, and because nothing exercised these routes: there is no
test that calls `/upload_video/inference/{user_id}`, and the API-prefix contract guard checks
paths and methods against the gateway, not the inside of a handler body. A path can be
correct and the handler still unable to run.

**Status: fixed.** Each handler takes its collaborator as a `Depends` parameter instead of
touching a module-level name - `service=Depends(get_matching_service)` from
`service/composition/state.py` - so the name is resolved by the framework at request time and
a missing wiring is an import error at collection, not a 500 in production
(`backend/service/services/matching/presentation/routers/inference_api/inference_api.py:46-59`,
`:71-85`, `:94-102`, `:111-123`). The history is recorded in the module itself
(`inference_api.py:27-30`), including what caught it: *"ruff's F821 found it, not a test, which
is why the api-prefix contract alone was not enough."*

**Consequence if it regresses.** The four `inference` routes are the product - upload a video,
get a profile; add a vacancy; list matches - so a recurrence is a total outage of the feature
behind a 500, invisible to every healthcheck because `/server/health` does no I/O.

**Mitigation.** The `Depends` shape makes the regression loud at import time, and the
`matching` slice's application service is exercised by
`backend/tests/test_service_boundary.py` for import direction rather than behaviour. An
end-to-end test of these four routes still needs MySQL and the sidecar, so it belongs to the
integration suite (`backend/tests/integration/pipeline_smoke.py`, behind
`HIRESNAP_INTEGRATION=1`), not to the unit run.

## 29. Stored notebook output, and a schema sketch that lied

**Two pieces of repository weight, removed in the same change.** Neither was a bug in the
running system; both made the checkout say something that is not true.

### 29.1 The notebooks carried their outputs, and one of them looked like a checkpoint

All seven notebooks under `backend/notebooks/` were committed with their cell outputs
attached. `KNN.ipynb` was 252,841 bytes - the single largest Python-adjacent file in the
submodule - and the overwhelming majority of that was base64 PNG output: cluster scatter
plots from a run against a database that no longer exists. A reader scanning the tree for
`torch`-sized blobs found that file and had to open it to learn it was a picture.

Every stored output and every `execution_count` was cleared. Source cells are untouched.
Measured: 336,307 bytes -> 40,909 bytes in total, and `KNN.ipynb` 252,841 -> 13,152.
The seven files now parse with zero outputs and zero execution counts.

**Why it matters beyond the bytes.** An output is a snapshot of one machine's run - it is
stale the day after it is committed, it cannot be reviewed as a diff, and it is the one part
of a notebook that a reviewer learns to skip. A notebook whose outputs are cleared is a
document; one with outputs is a screenshot of a terminal nobody can reproduce. The cost is
that a reader no longer sees the expected result inline, which is why the same numbers
belong in prose (this file, the docs under `backend/docs/`) rather than in a cell's
`display_data`.

**What would have caught it.** Nothing did. No linter opens a `.ipynb`, the large-file hook
in `.pre-commit-config.yaml` caps at 512 KB so a 253 KB notebook passes, and `git` stores it
as one opaque blob. The habit that catches it is checking `git status` after opening a
notebook in Jupyter at all - merely executing and saving rewrites the outputs.

### 29.2 `backend/docs/images/SQL.txt` was a schema sketch that contradicted the schema

`backend/docs/images/SQL.txt` (2,543 bytes, in the same directory as the README's
presentation assets) held a hand-written sketch of the tables, and it was **wrong**: it
declared a single-value `ENUM('candidate, employer')` - one string, not two values - where
`backend/database/HireSnap.sql:134` has the two-value `enum('candidate','employer')` the code
actually writes. A reader who
trusted the sketch over the dump would conclude the column cannot hold two roles.

It was referenced by no file in the repository, in any extension. Deleted rather than
corrected: `backend/database/HireSnap.sql` is the schema of record, it is the file the MySQL
init mount reads (`docker/docker-compose.yaml:200`), and a second, unreferenced description
of the same tables can only drift. The nine remaining files in `backend/docs/images/` are the
README's presentation assets (PDF, PPTX, SVG, PNG) and they stay.

### 29.3 The 14 MB is a deliberate, documented cost

`backend/docs/images/` holds ~15.2 MiB in nine files, of which `HireSnap.pdf` is 10.3 MB and
`HireSnap.pptx` 3.4 MB. They live **inside a gitlink**, so every clone of the superproject
that runs `git submodule update` downloads them, and they are duplicated in every fork. That
is recorded here as a known cost rather than an oversight: they are the product's
presentation material, they are the images `backend/README.md` embeds, and moving them to an
external host would break the README in an offline checkout. The alternative - Git LFS - was
not taken because it turns a `git clone` into a two-step operation for every contributor and
adds a server-side dependency the project does not otherwise have.

**If this ever needs to shrink**, the order is: move the two large binaries to a release
asset and link them, keep the SVGs (they are ~1.1 MB together and render in the README), and
leave the rest. Do not reach for history rewriting to reclaim the space: the blobs stay in
every existing clone regardless.

## 30. `Invalid authentication scheme.` is dead code

**Defect, as it stands.** `JWTBearer.__call__` has three rejection branches
(`backend/service/api/security.py:25-44`). The first one compares the scheme:

```python
# backend/service/api/security.py:32-36
if credentials:
    if not credentials.scheme == "Bearer":
        ex = HTTPException(status_code=403, detail="Invalid authentication scheme.")
        log.exception(ex)
        raise ex
```

It cannot run. `super().__call__(request)` on line 31 is Starlette's
`HTTPBearer`, which already rejects a non-bearer scheme - with
`403 Invalid authentication credentials` when `auto_error=True`, and by
returning `None` (which falls through to this class's own
`403 Invalid authorization code.`) when it is off. Line 32's `if credentials:` is
only true after the scheme has been accepted, so the body of the branch is unreachable in
both modes.

**Why it survives review.** It reads as defence in depth, and it is the kind of code a
reviewer skims and approves precisely *because* it is checking something. The message was
also quoted in `docs/SERVICE_CONTRACTS.md` as a 403 a client can receive - it said so until
this pass corrected it - which made the branch self-documenting without ever being exercised.

**Status: documented, not fixed.** Changing it means either deleting a branch (a no-op for
every client) or reordering the scheme check ahead of `super()`, which would change the
403 body a non-bearer request gets today. The second is a wire-contract change, so it is a
decision rather than a tidy-up - the same rule as `ConflictError` being a 404 (§9).

**What caught it.** `backend/tests/test_security_jwt.py:71-86` asserts the message a
client *actually* receives in both modes and says `REPORTED, NOT FIXED` in its docstring.
Nothing in the suite could have found it by accident: the branch has no observable effect.

## 31. A non-admin on an admin route gets `403`, not `401`

**Defect, as it stands.** The admin check raises inside the function's own exception
handler, so the raise is immediately swallowed by it:

```python
# backend/service/api/security.py:46-60, condensed
try:
    payload = decode_jwt(jwtoken)
    if self.access_level == 1 and payload.get("sub") != "admin":
        raise HTTPException(status_code=401, detail="Have not access")   # :52
except Exception as ex:          # :55 - catches the line above
    payload = None               # :57
return bool(payload)             # :60 -> False
```

`False` makes `__call__` render `403 Invalid token or expired token.`
(`:37-40`), so a level-1 route answers `403` to a logged-in non-admin and to an
expired token alike. There are **17** level-1 dependency sites across nine router modules
(measured by grepping `JWTBearer(access_level=1)`), including
`users_api.py:107`, `files_api.py:91` and `candidate_vacancies_api.py:101`.

**Why it survives review.** An expired token *should* be a 403 here, so the common case
looks right. The wrong case - a valid token with the wrong role - is indistinguishable from
it at the client, and nothing in the tree tests an admin route end to end.

**Consequence for clients.** "Not an admin" cannot be told apart from "session expired". A
SPA cannot decide between *re-authenticate* and *you are in the wrong account*, so it has to
do the safe thing (re-authenticate) and the user sees a sign-in page they cannot get past.
An operator reading the log sees the whole traceback, because `log.exception(ex)` runs
before the re-raise; the client sees nothing of it.

**Why it is not fixed here.** The narrow fix is one line - move the admin check after the
`try`, or catch `HTTPException` and re-raise. Both change the status a client
receives for a whole class of callers, which is a contract change.

**Pinned as-is.** `backend/tests/test_security_jwt.py:123-137` asserts the 403 and its
docstring records why the 401 is unreachable. Fixing the code has to change that test in the
same commit, which is the point.

## 32. A missing dump leaves a half-created database and the process still exits 0

**Defect, as it stands.** `CreateSQL.read_sql()` wraps its whole body in
`except Exception` and only logs:

```python
# backend/service/infrastructure/database/schema.py:128-131
except Exception as ex:
    log.warning("Error during SQL script execution", exc_info=ex)
finally:
    self.connection.close()
```

By the time that fires, two statements have already committed: `CREATE DATABASE IF NOT
EXISTS` (`:87`) and `USE` (`:88`). A dump that is missing, unreadable or
truncated therefore leaves an empty database behind, plus whatever earlier statements in the
dump succeeded, and the caller has no way to know: `read_sql()` returns `None` either
way, `main()` returns 0, and uvicorn then starts against a schema that is not there.

**Why it survives review.** Every statement in the dump is itself wrapped in
`except pymysql.MySQLError` (`:99-104`) on purpose - that is what lets the dump be
re-applied to a schema that already has half the tables, where "table exists" is an error on
every line. The outer handler was written in the same spirit ("the bootstrap is a convenience,
do not crash the server over it"), but the two are not the same case: a rejected DDL statement
is expected, a missing file is not.

**Consequence.** The first symptom is an API that boots, answers `/server/health` with
`200`, and then 500s on the first query against a table that was never created. The log line
is at `WARNING` level among uvicorn's output, so it is easy to miss on a first run - and
a first run is exactly when the bootstrap matters.

**What would change it.** Distinguish the two failures: let the file-open error propagate (or
exit non-zero), keep the per-statement tolerance. The bootstrap is the one code path in the
tree whose job is to fail loudly, and it is the one that swallows.

**Pinned.** `backend/tests/test_schema_bootstrap.py:302-320` asserts what is *correct*
about this path - the connection is closed even when the dump is missing - and says
"DOCUMENTED, NOT ENDORSED" in its docstring rather than treating the silence as behaviour.

## 33. The dump is split on `;` with `str.split`

**Defect, as it stands.** `read_sql()` turns the SQL file into statements with a string
split (`backend/service/infrastructure/database/schema.py:95`):

```python
statements = [stmt.strip() for stmt in sql_script.split(";") if stmt.strip()]
```

That cannot tell a statement terminator from a semicolon inside a quoted value. A row like
`'open; part time'` in the dump would be cut in half, and the two halves would be executed
as two broken statements - the first of which fails silently under §32's per-statement
handler.

**Measured today: safe.** `backend/database/HireSnap.sql` splits into exactly 50
statements, none contains an escaped quote (`\'`), none contains `;;`, and every one
has balanced single quotes. The full `ENUM('candidate','employer')` form is used rather
than a comma-joined string (`:134`), which is one reason the dump has stayed
splitter-friendly.

**Why the fix is not a SQL parser.** A real splitter is a parser, and a parser in a developer
convenience script would be more code than the rest of the file. The cheaper invariant is that
the dump stays simple, so the guard is a test on the *dump* rather than on the splitter:
`backend/tests/test_schema_bootstrap.py:322-337` asserts the statement count, the balanced
quotes and the absence of `\'` and `;;`. A future dump that breaks the assumption
fails there instead of halfway through a bootstrap.

**Residual risk.** The invariant is on the schema of record, not on the CSV seed path, which
uses parameterised `executemany` (`:107-124`) and is immune. So the exposure is only
ever a hand-edited dump.

## 34. The log path in the YAML is relative to the working directory

**Defect, as it stands.** Two places decide where `app.log` goes, and they disagree in
form:

- `backend/service/configuration/logging.yaml:11` says `filename: 'logs/app.log'` -
  a **relative** path, resolved by `logging.FileHandler` against the process CWD.
- `backend/service/configuration/paths.py:74-76` exposes `logs_dir()` =
  `BACKEND_ROOT / "logs"`, an **absolute** path, and
  `service/configuration/logging.py:43-46` uses it.

The YAML is not templated from `paths.py`, and the YAML is what actually configures the
process: `main.run_server()` loads it with `dictConfig` (`service/main.py:59-60`) and
passes the same file to `uvicorn.run(log_config=...)` (`:66`).

**Why it has not bitten.** In the image `WORKDIR` is `/app` and the backend root is also
`/app`, so `logs/app.log` and `/app/logs/app.log` are the same file, and
`infra/logs` is mounted at `/app/logs`. The two definitions agree by coincidence of
layout, not by construction.

**Consequence.** Start the server from any other directory - `cd / && python -m service.main`
or a systemd unit without `WorkingDirectory` - and the application's own log lands in
`backend/logs/app.log` (via the absolute path) while uvicorn's file handler creates a second
`logs/` tree under the CWD. Two files, neither complete, and the post-mortem reads half a
story. A read-only CWD is also the difference between "file logging disabled" and a working
log.

**What would change it.** One templated placeholder, or dropping the file handler from the YAML
and letting `setup_logging()` own the file - the second is a code change and would
re-open whether uvicorn's own records reach the file at all.

## 35. Every boolean except `DEBUG` can be typo'd into "off"

**Defect, as it stands.** `DEBUG` is parsed strictly: `service/main.py:39-46` accepts
`TRUE` or `FALSE` and raises `RuntimeError("DEBUG must be TRUE or FALSE, got ...")`
for anything else. Nothing else in the tree is that careful. `settings.py:57-62` is the
shared flag parser:

```python
# backend/service/settings.py:57-62
def _flag(*names, default=False):
    raw = _first(*names, default="")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

`INIT_DB=ture` or `MAIL__ENABLED=tru` is therefore silently `False` - the same value
as `false`, with no warning. The platform facades clone the same set
(`infrastructure/cache.py:49`, `storage.py:44`, `mail.py:46`, and a fifth copy in
`infrastructure/messaging/celery_app.py:75`), so `CELERY__ENABLED=ture` leaves the
workers idle while the API does the work inline.

**Where it is *not* silent.** A misspelled **backend name** does warn and fall back:
`CACHE__BACKEND=redys` logs `cache status=unknown_backend value=redys action=using` and
uses memory (`cache.py:483-486`), with the same pattern in `storage.py:471-473` and
`mail.py:296-298`. Numbers are forgiving too, deliberately (`settings.py:65-73` falls
back on `ValueError`). Booleans are the only class that fails silently in the "off"
direction.

**Consequence.** The failure is invisible in the worst way: the stack starts, health is green,
and one optional feature is quietly absent. `MAIL=1` with a typo in `MAIL__ENABLED` gives
a mailer that writes nothing; `CELERY=1` with a typo gives inline execution inside the
request thread, which is exactly the latency the profile was turned on to avoid.

**What would change it.** Treat an unparseable boolean as an error - the `DEBUG` rule
applied to the rest - or log a warning when a non-empty value does not match the truthy set.
Either is a behaviour change for a stack that is expected to start, which is why the strict
parser was only ever applied to the one key whose ambiguity is dangerous.

## 36. `fastapi` is still imported from `service/shared/`

**Defect, as it stands.** [ADR/0008](ADR/0008-vertical-slice-services.md) moved the HTTP mapping
to the edge: application and domain modules raise `ServiceError` and no framework reaches
the middle of the tree. `service/shared/validation.py` is clean - its docstring says so
explicitly ("WHY no fastapi here any more") and it raises `ConflictError` at `:41` and
`:54`. One shared module is not:

```python
# backend/service/shared/tokens.py:23-24, :114-126
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer

http_bearer = HTTPBearer()
...
raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token type ...")
```

Three legacy repositories do the same, and those are recorded as source patches in
[ADR/0005](ADR/0005-source-patches.md): `candidate_vacancy_repository.py:3-6`,
`personality_candidate_repository.py:3-6` and `personality_vacancy_repository.py:3-6` -
each imports `HTTPException` to raise a 400 where the old code did, so the layer keeps a
framework dependency its siblings do not.

**Why no gate catches it.** The boundary suite asserts the framework-free rule for a
slice's `presentation/` (rule 2), for `application/` (rule 3) and for a `domain/` that exists
(rule 4), and `backend/scripts/check_backend_layout.py` rule 7 asserts that `models/` and
`shared/` do not import *upward* into `service.api` or `service.services`. Neither says
anything about a third-party framework: `fastapi` is not an upward import, so the shape
checks are silent and the module keeps a dependency its siblings do not have. `tokens.py` is
a token *codec*, and a codec that cannot be imported without starlette cannot be reused by a
worker, a CLI or a test that does not install the API stack.

**Why it is not fixed here.** `validate_token_type()` raising `HTTPException` is how the
sign-in flow produces its 401 detail, and two tests assert that shape directly:
`backend/tests/test_security_jwt.py:216-227` (the message names both token types in repr
form) and `backend/tests/test_slices_application.py:750-762` (an application-layer test
catching a `fastapi` exception, and saying in its docstring that this is deliberate).
Moving it to `UnauthorizedError` changes which handler renders it - correct, but a
contract-adjacent change that belongs with a route test rather than a docs pass.

## 37. The employer vacancy route accepts a vacancy and stores nothing

**Not a stub a refactor introduced - the feature is half-built and honest about it.**
`POST /upload_vacancy/inference/employer` validates four form fields and then returns a fixed
body:

```python
# backend/service/services/matching/application/matching_service.py:343-353
def upload_vacancy(self, name, description, salary, skill):
    """... the form posts text and the pipeline needs an embedding the
    employer has not produced ..."""
    log.info("vacancy received but not persisted: name=%r salary=%r", name, salary)
    return {"message": "test"}
```

**What that means for a user.** The form succeeds, the toast says "request sent", and the
vacancy does not appear in `GET /vacancies/`. The frontend says so in two places rather
than hiding it: `frontend/src/shared/api/services/vacancyService.js:22-31` warns that the
route is a stub and explicitly says *not* to "fix" it by switching to `POST /vacancies/`
(that would skip the embedding the recommendation routes need), and
`frontend/src/features/vacancies/components/VacancyCreateForm.jsx:8-16` explains why the
success toast is worded "request sent" rather than "vacancy added".

**What implementing it needs.** The bridge already exists and has no caller:
`backend/service/infrastructure/ml_sidecar.py:383-398` can embed a string through the
sidecar's `/v1/embed/text`, and its docstring says as much - *"No call site uses this yet
... the first caller that needs text embeddings (the vacancy side of the ranking) should not
have to re-derive it."* So the work is: embed the vacancy text, persist the vacancy and its
vector in one transaction, and give the ranking a vacancy embedding to compare against.

**Why it is recorded rather than done.** It is a feature, not a defect in the move: the old
module returned the same literal. Doing it here would mean a new call path, a schema question
(where does a vacancy embedding live) and a contract change, in a refactor whose whole claim
is that the wire behaviour did not move. The honest state is written down so the next reader
does not mistake it for a regression.

## What is *not* on this list

Three changes to application source ship with this stack, and they are deliberately excluded
from the list above because they are fixed. All three are enumerated in
[ADR/0005-source-patches.md](ADR/0005-source-patches.md), and that list is closed:

1. `frontend/src/shared/api/apiConsts.js` - the base URL becomes configurable.
2. `backend/service/shared/urls.py` - `PUBLIC_BASE_URL` is honored, with the old
   behavior as fallback.
3. the three vacancy/personality `persistence/` repositories — `candidate_vacancies`,
   `personality_candidates` and `personality_vacancies` under
   `backend/service/services/` — the missing `fastapi` import that turned a 400 into a 500.

All three are backwards compatible: with no new configuration they behave exactly as before.

Nine entries above are **closed** rather than open, and they are kept because *how* they
failed is the useful part - each was invisible to the gates and would have surfaced first in
production:

- §3 - `Env` wrote `.env` relative to the working directory. Both writers now resolve the
  same path as the reader (`service/configuration/env.py:50-65`), and
  `backend/tests/test_import_without_infra.py:88-93` pins the property that matters: an
  import writes no file at all, from any CWD.
- §7 - unconditional autocast. Fixed in `ml/service/video_embedding.py`, and the fix moved
  with the code, which is why the CPU path is now a supported mode rather than an error.
- §10 - the API had no probe of its own. `GET /server/health` exists now, composes probes it,
  and `backend/tests/test_import_without_infra.py:74-85` asserts the exact body.
- §11 - CORS was hardcoded to `['*']` **with** credentials, an illegal header pair, while
  `CORS_ALLOW_ORIGINS` was read by nothing. `_cors_settings()`
  (`backend/service/api/app.py:67-117`) resolves it and turns credentials off for the
  wildcard; `backend/tests/test_cors_settings.py` pins the rule and the middleware wiring.
- §16 - the celery ML client posted to a route the sidecar does not serve. Now routed
  through `service.infrastructure.ml_sidecar` and covered by `backend/tests/test_worker_ml_seam.py`.
- §18 - `get_all_candidates()` sorted dictionaries and raised `TypeError`; it sorts on the
  distance key the way `get_all_vacancies()` already did.
- §19 - a missing `fastapi` import that turned a 400 into a 500.
- §22 - the `ml` service built the API image and published no URL, so the sidecar was never
  used in any deployed configuration. Now enforced by
  `scripts/check_compose_parity.py` rule 6.
- §28 - the four `inference` routes called a module they never imported, so every one of them
  answered `NameError`. ruff's F821 found it; the `Depends` wiring makes a recurrence an
  import error rather than a 500.

Six entries are new since the slice refactor. §23 (one inference path, serialised GPU work),
§24 (a credential rotated at the issuer, still in history) and §25 (the vertical slices are a
package boundary, not a domain model - raw-SQL repositories, no transaction boundary, shared
entity models) are about the *shape* of the tree rather than a bug. §26 (the personality-vacancy
link is written against the stale index) and §27 (`jobs` has no route, on purpose) are the
two the slice move made worth writing down: one is a real defect preserved because fixing it is
a migration, the other is a deliberate absence the layout guard names at
`backend/scripts/check_backend_layout.py:121`. §29 is a third kind again: notebook output that
looked like a checkpoint and a schema sketch that contradicted the schema, both removed from the
checkout rather than from the running system. None of the six is a bug a test could turn green:
they are the deliberate stopping point of the refactor that
[ADR/0008](ADR/0008-vertical-slice-services.md) records.

Eight more were documented in the same read-through that produced the ratchet and the coverage
floor, and they are a different kind again - each is a real defect in code that the refactor
moved without changing, so the behaviour is exactly what it was before the move:

- §30 and §31 are the two auth quirks. A dead branch that reads like protection, and a 401 that
  a client can never receive because the function's own handler swallows it.
- §32, §33 and §34 are the bootstrap and its logging: a swallowed dump error, a splitter that
  assumes no semicolons in string literals, and a log path that is relative in the YAML and
  absolute in the helper.
- §35 is the flag parser: every boolean except `DEBUG` turns a typo into "off" silently.
- §36 is the one `shared/` module that still imports `fastapi`, plus the three legacy
  repositories that do the same as recorded source patches.
- §37 is the employer vacancy route: it validates, answers, and stores nothing - honestly, and
  with the frontend saying so.

All eight are *documented, not fixed*, and in every case for the same reason: the narrow fix is
a one-line change that alters something a client can observe - a status, a message, a file
that suddenly exists or does not - and a refactor whose claim is "the wire behaviour did not
move" is the wrong commit to change it in. Six of the eight are pinned by a test that asserts
the behaviour as it is and says so in its docstring: `test_security_jwt.py:71-86` (§30),
`test_security_jwt.py:123-137` (§31), `test_schema_bootstrap.py:302-320` (§32),
`test_schema_bootstrap.py:322-337` (§33, and only on the input - the dump - not on the
outcome), `test_main_entrypoint.py:219-233` (§34) and `test_settings.py:255-265` (§35).
§36 is pinned indirectly: the tests it names assert the current exception shape rather than the
import. §37 is the one with no test at all - no route test posts a vacancy and checks that
nothing was stored - which is part of why it is still open.

Everything else is documented here rather than patched, so that the next reader can tell
"the stack works around this" from "the stack is broken here".
