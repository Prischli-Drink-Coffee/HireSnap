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

`_debug_flag()` (`:38-45`) is a strict string comparison: `TRUE`, `FALSE`, or a
`RuntimeError` naming the value it saw. `os_env_flag()` (`:85-93`) delegates to the
same strict parser every other boolean uses (`service/configuration/flags.py`), so it accepts
`1`/`true`/`yes`/`on`/`enabled`/`y`/`t` in any case and **raises** on a value
nobody meant instead of reading it as off - see §35 for why that changed and what it costs. The
two keys are an **AND**, so `INIT_DB=1` alone does nothing and `DEBUG=TRUE` alone does
nothing — the second key narrows the first, it cannot override it.

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

## 30. `Invalid authentication scheme.` is dead code (fixed by deletion)

**Defect, as it stood.** `JWTBearer.__call__` had three rejection branches, and the
first one compared the scheme:

```python
# backend/service/api/security.py, as it was
if credentials:
    if not credentials.scheme == "Bearer":
        ex = HTTPException(status_code=403, detail="Invalid authentication scheme.")
        log.exception(ex)
        raise ex
```

It could not run. `super().__call__(request)` is Starlette's `HTTPBearer`, which
already rejects a non-bearer scheme - with `403 Invalid authentication credentials`
when `auto_error=True`, and by returning `None` (which fell through to this
class's own `403 Invalid authorization code.`) when it was off. The
`if credentials:` guard was only true after the scheme had been accepted, so the body
was unreachable in both modes.

**Why it survived review.** It read as defence in depth, and it is the kind of code a
reviewer skims and approves precisely *because* it is checking something. The message was
also quoted in `docs/SERVICE_CONTRACTS.md` as a 403 a client could receive, which made
the branch self-documenting without ever being exercised.

**Status: fixed, by deletion.** The branch is gone. `backend/service/api/security.py:42-54`
is the whole of `__call__` now: the base class's credentials, one
`verify_jwt()` call, and a fall-through for the `auto_error=False` case. The
module docstring (`:1-19`) records the two messages that actually reach a client and
says they belong to `HTTPBearer`, so the next reader does not reintroduce a check that
cannot run: `403 Invalid authentication credentials` for a non-bearer scheme with
`auto_error=True`, and `403 Invalid authorization code.` for the same request
with it off.

Nothing a client receives changed - the branch never produced a response - which is why this
was the safe half of the pair: the fix is a delete, not a wire-contract change.

**What caught it.** `backend/tests/test_security_jwt.py:73-89`
(`test_a_non_bearer_scheme_is_rejected_by_the_superclass_not_by_this_class`) asserts
both messages the base class produces and says `D1 (KNOWN_GAPS 30), FIXED BY DELETION`
in its docstring. Nothing in the suite could have found the dead branch by accident: it had
no observable effect.

## 31. A non-admin on an admin route was reported as an expired session (fixed)

**Defect, as it stood.** The admin check raised inside `verify_jwt`'s own exception
handler, so the raise was immediately swallowed by it:

```python
# backend/service/api/security.py, as it was, condensed
try:
    payload = decode_jwt(jwtoken)
    if self.access_level == 1 and payload.get("sub") != "admin":
        raise HTTPException(status_code=401, detail="Have not access")   # :52
except Exception as ex:          # :55 - caught the line above
    payload = None               # :57
return bool(payload)             # :60 -> False
```

`False` made `__call__` render `403 Invalid token or expired token.`, so a
level-1 route answered the same 403 to a signed-in non-admin and to an expired token. There
are **17** level-1 dependency sites across nine router modules, including
`users_api.py:107`, `files_api.py:91` and `candidate_vacancies_api.py:101`.

**Why it survived review.** An expired token *should* be a 403 here, so the common case
looked right. The wrong case - a valid token with the wrong role - was indistinguishable
from it at the client, and nothing in the tree tested an admin route end to end.

**Consequence, while it lasted.** "Not an admin" could not be told apart from "session
expired". A SPA could not decide between *re-authenticate* and *you are in the wrong
account*, so it had to do the safe thing (re-authenticate) and the user saw a sign-in page
they could not get past. An operator reading the log saw the whole traceback, because
`log.exception(ex)` ran before the re-raise; the client saw nothing of it.

**Status: fixed.** The role check now sits **outside** the `try`
(`backend/service/api/security.py:70-81`): the handler covers `decode_jwt` only,
and a payload that decodes is then checked with

```python
if self.access_level == 1 and payload.get("sub") != "admin":
    ex = HTTPException(status_code=403, detail="Have not access")
    log.exception(ex)
    raise ex
```

The status is deliberately **still 403** - the level-1 semantics did not change - so a
client branches on the *detail*, not the status code. All four denials are 403 and differ
only in their body:

| Case | Body |
|------|------|
| no `Authorization` header | `Not authenticated` (Starlette) |
| non-bearer scheme | `Invalid authentication credentials` (Starlette) |
| the token does not verify, or it expired | `Invalid token or expired token.` |
| verified token, `sub` is not `admin`, `access_level=1` | `Have not access` |

The fourth row is new. [SERVICE_CONTRACTS.md §2.2](SERVICE_CONTRACTS.md#22-what-the-backend-actually-returns-partial-gap)
carries the same table for callers.

**What caught it.** Three tests in `backend/tests/test_security_jwt.py`:
`:126-140` (`test_access_level_one_rejects_a_non_admin_subject_as_a_403`) pins
the new detail; `:150-168`
(`test_the_four_denials_are_distinguishable_by_status_and_detail`) asserts the set - a
future edit that collapses two cases back into one string fails there even if every
single-case assertion was updated; `:206-229`
(`test_the_admin_denial_is_logged_before_it_is_raised`) keeps the operator's trace.
`:171-196` (`test_an_expired_token_is_rejected_as_expired_and_not_as_a_role_problem`)
closes the loop with a real HS256 expiry.

## 32. A missing dump left a half-created database and the process still exited 0 (fixed)

**Defect, as it stood.** `CreateSQL.read_sql()` wrapped its whole body in
`except Exception` and only logged:

```python
# backend/service/infrastructure/database/schema.py:128-131, as it was
except Exception as ex:
    log.warning("Error during SQL script execution", exc_info=ex)
finally:
    self.connection.close()
```

By the time that fired, two statements had already committed: `CREATE DATABASE IF NOT
EXISTS` and `USE`. A dump that was missing, unreadable or truncated therefore left
an empty database behind, plus whatever earlier statements in the dump succeeded, and the
caller had no way to know: `read_sql()` returned `None` either way,
`main()` returned 0, and uvicorn then started against a schema that was not there.

**Why it survived review.** Every statement in the dump was itself wrapped in
`except pymysql.MySQLError` on purpose - that is what lets the dump be re-applied to a
schema that already has half the tables, where "table exists" is an error on every line. The
outer handler was written in the same spirit ("the bootstrap is a convenience, do not crash
the server over it"), but the two are not the same case: a rejected DDL statement is
expected, a missing file is not.

**Consequence, while it lasted.** The first symptom was an API that booted, answered
`/server/health` with `200`, and then 500'd on the first query against a table
that was never created. The log line was at `WARNING` level among uvicorn's output,
easy to miss on a first run - and a first run is exactly when the bootstrap matters.

**Status: fixed.** `backend/service/infrastructure/database/schema.py:1-31` now
documents the order as part of the contract. `read_sql()` (`:181-205`) runs
`_load_statements()` (`:208-224`) and `_load_seed_frames()`
(`:226-255`) **before** it touches the server, so a path typo raises having executed
nothing; then `_create_database()` (`:257-266`), `_apply_dump()`
(`:268-284`), `_seed()` (`:286-309`), one `commit()` (`:197`)
and `close()` in `finally` (`:204-205`).

The two failure classes are now separate, which was the whole fix:

- `SchemaBootstrapError(RuntimeError)` (`:89-119`) carries `step`,
  `detail`, a 1-based `statement_index` into the dump and a 200-character
  `statement` excerpt (`:71-79`). It is raised for an unopenable or empty dump, a
  refused `CREATE DATABASE` or `USE`, a dead connection mid-dump (`errno`
  2006/2013/2055 - the class the per-statement tolerance must not swallow, `:50-56`)
  and any seed failure.
- A single statement MySQL rejects **while the connection is alive** is still logged and
  skipped (`:284`), because that is the re-run tolerance the dump's
  `CREATE TABLE IF NOT EXISTS` depends on. An absent `tables/` directory is still
  not an error (`:234-237`).

The seed phase is transactional in the only way MySQL allows: `begin()` once
(`:294`), `executemany` per table (`:301-302`), exactly one `commit()`
on success, and a rollback plus `SchemaBootstrapError` on any failure
(`:304-309`). The docstring states the limit plainly (`:26-30`): MySQL commits
implicitly around DDL, so the `CREATE TABLE`s are not rollback-able and only the seed
rows are.

A smaller defect in the same file went with it: `remove_column_from_csv()`
(`:147-179`) used to fall off the end into `None` on a frame it could not adapt;
it now returns the frame unchanged (`:177-179`).

**What caught it.** `backend/tests/test_schema_bootstrap.py`, 19 tests. The ones that
pin the new behaviour are
`test_the_connection_is_closed_and_nothing_is_written_when_the_dump_is_missing`
(`:315`), `test_a_dump_that_holds_no_statement_raises_before_touching_the_server`
(`:341`), `test_a_rejected_create_database_raises_instead_of_being_logged`
(`:360`), `test_a_lost_connection_mid_dump_raises_with_the_statement_index`
(`:379`), `test_one_failing_seed_table_rolls_back_the_whole_seed_phase_and_raises`
(`:277`) and `test_the_transaction_is_begun_once_and_committed_once_on_success`
(`:415`). The two that pin what deliberately did **not** change are
`test_a_dump_statement_that_fails_does_not_abort_the_rest` (`:235`) and
`test_a_seed_file_that_cannot_be_parsed_raises_before_the_dump_runs` (`:442`).

## 33. The dump was split on every `;` with `str.split` (fixed)

**Defect, as it stood.** `read_sql()` turned the SQL file into statements with a string
split:

```python
statements = [stmt.strip() for stmt in sql_script.split(";") if stmt.strip()]
```

That cannot tell a statement terminator from a semicolon inside a quoted value. A row like
`'open; part time'` in the dump would be cut in half, and the two halves would be executed
as two broken statements - the first of which fails silently under §32's per-statement
handler. Nothing in the tree could see it: the dump of record happens to contain no quoted
semicolon, so the splitter was correct only by accident of its input.

**What changed.** `backend/service/infrastructure/database/schema.py` now splits in a
module-level `split_statements(script) -> List[str]` (`:74-175`), and
`_load_statements()` calls it instead of `str.split` (`:312-328`, the call at
`:323`). It tracks the four lexical states that can hold a semicolon - single quote,
double quote, backtick identifier, block comment - plus both line-comment markers MySQL
accepts (`--` and `#`) (`:94-170`), treats a backslash escape as consuming the
next character outside a backtick and `''` / `""` as a doubled quote inside one
(`:116-141`), and only a semicolon seen outside all of them ends a statement
(`:162-168`). Double-quoted text is treated as a string, not an identifier: under
`ANSI_QUOTES` it would be an identifier, but either way its content may hold a
semicolon, which is the only thing the splitter needs to know (`:91-93`).

**Why it is still not a SQL parser.** A real grammar would be more code than the rest of the
file and would still need exactly these cases. Tracking where a quote is open is enough to
find a terminator, and it is the difference between a splitter that is right about this dump
and one that is right about the next one (`:84-89`).

**What now stops a regression.** `backend/tests/test_schema_bootstrap.py` pins both
halves, because either half alone proves nothing:

- `test_split_statements_ignores_a_semicolon_that_does_not_end_a_statement` (`:481-500`)
  - twelve cases, one per lexical state: quoted, double-quoted, backticked, `''`
  doubled, backslash-escaped, block comment, `--` and `#` line comments, a
  statement with no terminator, terminator-only input, and empty input.
- `test_the_naive_split_would_have_broken_the_case_above` (`:503-513`) - on the
  same input `str.split` disagrees with the splitter, so a splitter that returned the
  whole script as one statement would not pass.
- `test_the_shipped_dump_splits_into_the_same_statements_as_before` (`:516-525`) -
  the real file still yields the same 50 statements, so the fix did not change what the
  bootstrap executes.

**Residual risk, and where it now points.** The exposure was always a hand-edited dump
rather than the CSV seed path, which uses parameterised `executemany` and is immune.
That is still true, but the failure is no longer silent: a quoted semicolon is now handled,
and a genuinely malformed statement raises through §32's `SchemaBootstrapError` instead
of leaving a half-built schema behind.

## 34. The log path in the YAML was relative to the working directory (fixed)

**Defect, as it stood.** Two places decided where `app.log` went, and they disagreed in
form:

- `backend/service/configuration/logging.yaml:11` said `filename: 'logs/app.log'` -
  a **relative** path, resolved by `logging.FileHandler` against the process CWD.
- `backend/service/configuration/paths.py:74-76` exposed `logs_dir()` =
  `BACKEND_ROOT / "logs"`, an **absolute** path, and
  `service/configuration/logging.py:43-46` used it.

The YAML was not templated from `paths.py`, and the YAML is what actually configured the
process: `main.run_server()` loaded it with `dictConfig` and passed the same file to
`uvicorn.run(log_config=...)`.

**Why it had not bitten.** In the image `WORKDIR` is `/app` and the backend root is
also `/app`, so `logs/app.log` and `/app/logs/app.log` were the same file, and
`infra/logs` is mounted at `/app/logs`. The two definitions agreed by coincidence of
layout, not by construction.

**Consequence, while it lasted.** Started from any other directory -
`cd / && python -m service.main` or a systemd unit without `WorkingDirectory` - the
application's own log landed in `backend/logs/app.log` (via the absolute path) while
uvicorn's file handler created a second `logs/` tree under the CWD. Two files, neither
complete, and the post-mortem read half a story. A read-only CWD was also the difference
between "file logging disabled" and a working log.

**Status: fixed, by resolving the path before the config is applied.**
`backend/service/configuration/paths.py:79-90` gained `log_file()` =
`logs_dir() / "app.log"`, and `backend/service/configuration/logging.py:49-79`
gained `load_logging_config(path=None)`: it `yaml.safe_load`s the file and rewrites
`handlers.file.filename` to the absolute `log_file()` **only when the YAML spells it
relatively** (`:75-78`), so a deployment that mounts the log elsewhere can still write an
absolute path into the YAML and be left alone.

`backend/service/main.py:56-72` now applies the dict itself -
`logging.config.dictConfig(load_logging_config(LOGGING_YAML))` at `:64` - and
passes `log_config=None` to `uvicorn.run` (`:70`), so uvicorn cannot resolve
the same relative path against its own CWD. The comment at `:58-63` records why: the
reloader's child inherits no useful working directory, and one place should know where the log
file is.

The YAML still says `logs/app.log` **on purpose**
(`backend/service/configuration/logging.yaml:11`): it stays the source of truth for the
format, levels, handlers and logger tree, and one relative filename that a single function
absolutises is easier to read than a generated file. The rewrite is one-way and the fallback is
unchanged - if the directory cannot be created, `setup_logging()` still degrades to
console-only (`logging.py:92-100`).

**What caught it.** `backend/tests/test_main_entrypoint.py`:
`test_run_server_reads_the_log_config_from_the_package_not_the_working_directory`
(`:216-240`) asserts `uvicorn.run` received `log_config=None`, that
`LOGGING_YAML` is absolute, and that the configured file handler points at
`<backend>/logs/app.log`; `test_the_yaml_still_spells_the_log_file_relatively`
(`:243-255`) pins the YAML on purpose; `test_load_logging_config_makes_the_file_handler_absolute`
(`:258-269`) also pins the handler class, the encoding, the three uvicorn loggers and the
`['file','console']` root handlers; `:272-283` leaves an absolute filename alone and
`:286-293` makes a missing file handler a no-op.

## 35. Every boolean except `DEBUG` could be typo'd into "off" (fixed)

**Defect, as it stood.** `DEBUG` was parsed strictly: `service/main.py:38-45`
accepts `TRUE` or `FALSE` and raises
`RuntimeError("DEBUG must be TRUE or FALSE, got ...")` for anything else. Nothing else in
the tree was that careful. `settings.py` had the shared flag parser, and every facade had
a copy of the same three lines:

```python
# backend/service/settings.py, as it was
def _flag(*names, default=False):
    raw = _first(*names, default="")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

`INIT_DB=ture` or `MAIL__ENABLED=tru` was therefore silently `False` - the
same value as `false`, with no warning. There were six copies of that rule:
`settings.py`, `infrastructure/cache.py`, `storage.py`, `mail.py` and
`infrastructure/messaging/celery_app.py`, plus `main.os_env_flag()`, so
`CELERY__ENABLED=ture` left the workers idle while the API did the work inline.

**Where it was *not* silent.** A misspelled **backend name** warned and fell back:
`CACHE__BACKEND=redys` logged `cache status=unknown_backend value=redys action=using`
and used memory, with the same pattern in `storage.py` and `mail.py`. Numbers were
forgiving too, deliberately. Booleans were the only class that failed silently in the "off"
direction.

**Consequence, while it lasted.** The failure was invisible in the worst way: the stack
started, health was green, and one optional feature was quietly absent. `MAIL=1` with a
typo in `MAIL__ENABLED` gave a mailer that wrote nothing; `CELERY=1` with a typo
gave inline execution inside the request thread, which is exactly the latency the profile was
turned on to avoid.

**Status: fixed, in one module.** `backend/service/configuration/flags.py` (104 lines) is
now the only boolean parser in the tree:

- `TRUTHY` = `1/true/yes/on/enabled/y/t` (`:32`) and `FALSY` =
  `0/false/no/off/disabled/n/f` (`:35`), both as frozensets so the accepted
  spellings are one visible list rather than an expression.
- `FlagValueError(ValueError)` (`:38-47`) carries the variable name and the value it
  read, and says which spellings it accepts.
- `parse_flag(raw, name)` (`:63-79`) strips and lowercases, raises on an **empty**
  value (`:72-74`) and on anything in neither set (`:79`).
- `read_flag(*names, default=False)` (`:82-96`) is the rule that matters: an
  **absent** variable returns the caller's default, a **present** but unparseable one raises.
  That is what lets an opt-out flag keep working - `MAIL__STARTTLS` passes
  `default=True` (`infrastructure/mail.py:290`).

The six copies are gone. `service/settings.py:_flag` (`:58-65`) is now
`return read_flag(*names, default=default)`; `service/main.py:os_env_flag`
(`:85-93`) delegates the same way; and the facades call `read_flag` directly -
`mail.py:272` (`MAIL__ENABLED`), `:290` (`MAIL__STARTTLS`, default
true), `:292` (`MAIL__SSL`), `storage.py:477` (`STORAGE__SECURE`),
`messaging/celery_app.py:128` (`CELERY__ENABLED`). `cache.py` no longer has a
private `_truthy` at all.

**This is a deliberate behaviour change.** A stack whose env file contains
`MAIL__ENABLED=ture` now refuses to boot instead of starting with mail silently absent.
That is the trade: the failure moves from "a feature does nothing, forever, and health is green"
to "the container exits with the variable and the value in its log". The manifest is safe
because every shipped value is lowercase `true`/`false`; **numbers are still
forgiving on purpose** (`settings.py:68-76` falls back on `ValueError`), because a
port or a timeout has a sane default in a way an intent flag does not.

**What caught it.** `backend/tests/test_settings.py:255-272`
(`test_flag_raises_on_a_value_nobody_meant`, parameters `'maybe'`,
`'ture'`, `''`, `'  '`, `'true false'`) asserts the exception names
`MAIL__ENABLED` and quotes the offending value, and its docstring says
`REPORTED, NOW FIXED (KNOWN_GAPS section 35)`; `:241-252` pins the accepted
spellings and `:275-280` the absent-means-default rule.
`backend/tests/test_main_entrypoint.py:81-94`
(`test_os_env_flag_refuses_a_value_nobody_meant`) covers `INIT_DB` through the same
path, and `backend/tests/test_storage_local.py:386-397` pins
`STORAGE__SECURE` with a docstring that names the module the rule moved to.

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
`backend/tests/test_security_jwt.py:302-313` (the message names both token types in repr
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

Fifteen entries above are **closed** rather than open, and they are kept because *how* they
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
- §30 - `JWTBearer` carried its own scheme check, and Starlette had already rejected
  every other scheme before it could run. Removed rather than made reachable; nothing a client
  receives changed, which is what made it the safe half of the pair.
  `backend/tests/test_security_jwt.py:73-89` pins the two messages that were always the
  real ones.
- §31 - a verified token with the wrong subject was reported as "session expired", because the
  role check raised inside `verify_jwt`'s own `except`. The check now sits outside it
  and answers `403 Have not access` (`security.py:77-80`); the status deliberately did
  not move, so the detail is what a client branches on. `test_security_jwt.py:126-140`,
  `:150-168` and `:206-229`.
- §32 - `read_sql()` logged and continued on a missing dump, leaving a half-created
  database behind an API that answered `/server/health` with 200. It now opens the dump and
  every seed file before it touches the server and raises `SchemaBootstrapError` with the
  step and the statement index; the per-statement tolerance it exists for is unchanged
  (`schema.py:181-205`, `19` tests in `test_schema_bootstrap.py`).
- §33 - the dump was cut on every `;` with `str.split`, so a semicolon inside a
  quoted value would have split a statement in half and the first half would have failed
  silently. `split_statements()` tracks quote, identifier and comment state instead
  (`schema.py:74-175`), and the tests pin both directions: the twelve lexical cases, and the
  shipped dump still yielding the same 50 statements (`test_schema_bootstrap.py:481-525`).
- §34 - the file handler's path was relative in the YAML and absolute in the helper, so the two
  agreed only because `WORKDIR` and the backend root were both `/app`. `log_file()`
  plus `load_logging_config()` resolve it once, and `run_server()` hands
  `log_config=None` to uvicorn (`service/main.py:56-72`,
  `test_main_entrypoint.py:216-293`).
- §35 - every boolean but `DEBUG` turned a typo into "off". `service/configuration/flags.py`
  is now the single parser and a present-but-unparseable value raises `FlagValueError`
  naming the variable (`flags.py:82-96`, `test_settings.py:255-272`). This is the one
  closed entry that **changes what a bad env file does**: the stack refuses to boot instead of
  starting with a feature quietly absent.

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

Six more were documented in the same read-through that produced the ratchet and the coverage
floor, in code the refactor had moved without changing. They are the ones that have since been
**fixed**, and they are listed here rather than dropped because the fix is only trustworthy if
the defect it answers is still written down. §30, §31, §32, §33, §34 and §35 each now name the
regression test that keeps it fixed, and the entries above carry the mechanism.

Two entries remain **documented, not fixed**, and for the same reason in each case: the narrow
fix is a one-line change that alters something a client can observe - a status, a message, a
file that suddenly exists or does not - and a refactor whose claim is "the wire behaviour did not
move" was the wrong commit to change it in.

- §36 - one `shared/` module still imports `fastapi`. Pinned indirectly: the tests it
  names assert the current exception shape rather than the import.
- §37 - the employer vacancy route validates, answers, and stores nothing, honestly. It has no
  test at all - no route test posts a vacancy and checks that nothing was stored - which is part
  of why it is still open.

Everything else is documented here rather than patched, so that the next reader can tell
"the stack works around this" from "the stack is broken here".
