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
- `backend/service/api/app.py:128` — `app.mount("/server", app_server)`

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
arrives transitively because `fastapi==0.111.0` declares it as a hard requirement, and
`backend/service/api/routers/auth.py:20-23` declares `Form(...)` parameters that raise
`RuntimeError: Form data requires "python-multipart" to be installed` at route definition
when it is absent. The image is therefore correct today and fragile tomorrow: narrowing
`requirements.txt` to an explicit audited list must add `python-multipart` back, or the
container dies at import - before it can answer a healthcheck - in a way no gate here
models.

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
machine-readable error code.

**Consequence.** Clients must string-match `detail` to distinguish outcomes, and the
strings are sentences (`"Invalid token or expired token."`) that may be reworded.
Full inventory in
[SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md#22-what-the-backend-actually-returns-partial-gap).

**Mitigation.** The target contract (`{"error","detail"}`) is specified, and the gateway
normalizes its *own* errors to it. Application errors are left as-is; adding one
exception handler would fix them, and that is an application change.

## 10. No health endpoint (closed: `GET /server/health` exists)

**Defect, as it stood.** The backend exposed no `/health`, `/ping` or `/status` route, so
container health checks had to use `/server/openapi.json` - a document rebuilt from every
router on every call, run every 30 seconds for the life of the container - and the
gateway's own `/health` proved only that nginx was alive.

**Status: fixed.** `build_server_app()` now registers
`GET /server/health` on the `/server` sub-app (`backend/service/api/app.py:88-100`,
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

**Status: fixed.** `_cors_settings()` (`backend/service/api/app.py:92-142`) resolves the
key and `create_app()` splats the result into `CORSMiddleware`
(`backend/service/api/app.py:175-183`, `**_cors_settings()` at `:182`).

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
(`docker/.env.example:112`).

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
`backend/service/infrastructure/messaging/` (celery app, tasks, beat schedule) and
`backend/service/services/jobs_services.py` exist and are tested, and the containers that
host them now ship. **The API still does not call them.** Every request is answered from
MySQL and the local filesystem, exactly as before the layer landed.

- `backend/service/api/app.py` mounts twelve routers and none of them is a `/server/jobs`
  route; grepping the package for `send_now_or_later`, `get_cache` and `get_storage` finds
  the facades and their tests, not a caller.
- Signup (`backend/service/api/routers/auth.py:19-33`) calls `auth_services.signup` and
  returns; no welcome mail is sent, because no caller reaches
  `mail.send_now_or_later`.
- The listing endpoints query MySQL on every request; the cache facade is never consulted.

**Mitigation.** Document the dormancy instead of advertising the feature (this entry and
[SIDECARS.md](SIDECARS.md)). The layer's own tests
(`backend/tests/test_infra_fallbacks.py`, `test_jobs_services.py`,
`test_worker_routing.py`) prove the facades work; they cannot prove a route uses them.
The wiring is an application change with its own review: add the route, then the flag, then
the test that fails when the neighbour is down.

**Residual risk.** The failure semantics are the inverse of what a reader expects in two
places. Cache fails **open** (a miss), but job state lives in the cache too: with
`CACHE__BACKEND=memory` — the pre-cache default — `jobs_services.status()` only knows
about jobs enqueued in the **same process**, so a job id handed to a different worker, or
read after a restart, reports `unknown`. `CACHE=1` fixes that; a deployment that runs
celery without it does not. Storage fails **closed** (`StorageError`), so an S3 outage is
a 500 on the upload path once uploads use it, not a silent write to the local disk.

## 14. The sidecar is the only inference path

**Defect, from the other side.** The API has no in-process inference path. There used to be
one: `backend/service/services/ml_client.py` imported the ML pipeline in-process when
`ML__SERVICE_URL` was unset and POSTed to the sidecar when it was set. That branch is
gone - the module is HTTP-only, with a docstring at the top saying why
(`ml_client.py:1-43`).

- `service_url()` (`ml_client.py:101-120`) returns the first non-empty of
  `ML_SERVICE_URL` / `ML__SERVICE_URL`, and otherwise `DEFAULT_SERVICE_URL` =
  `http://127.0.0.1:8080` (`:65`). Nothing branches on "is it set"; `is_remote()`
  (`:123-131`) returns `True` unconditionally.
- `backend/service/services/matching.py:22` imports `get_user_embedding` from the client
  and `upload_video()` calls it at `:202` - the only wired call site.
- The four celery services take the same variable and `depends_on: ml`
  (`docker/docker-compose.yaml:104`, `:256-263`).

That default is not a fallback worth having. Inside the API container,
`127.0.0.1:8080` is the API itself, which serves no `/v1/*` route, so an emptied key
turns every upload into `ml_service_unreachable` rather than into local inference.
`ML__SERVICE_URL` ships **non-empty** (`http://ml:8080`, `docker/.env.example:263`) for
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

**Status: fixed.** The task now goes through `backend/service/services/ml_client.py` only —
the same transport the request path uses, so there is one place that knows the sidecar's
contract:

- `_ml_client()` lazily imports `service.services.ml_client` and wraps a failure in
  `TaskUnavailable("ml_client_unavailable: ...")`. A task cannot silently fall back to
  a transport that does not exist.
- `_local_media_path(storage, file_key, source_path=None)` returns `(path,
  temporary)`: an existing `source_path` as-is; else `storage.path_for(key)` when
  that is a file (a local backend already holds the bytes, so no copy); else
  `storage.read_bytes(key)` materialised into a `NamedTemporaryFile` with the
  **original extension**, because ffmpeg picks its demuxer from the filename.
- `embed_candidate()` calls `ml_client.get_user_embedding(media_path)`, stores the
  `.npy` through the storage facade, and deletes the temporary file in a `finally`
  that logs `temp_cleanup_failed` instead of masking the original error. The reported
  embedder is `ml_client:<transport>`, so a job record says which path ran.

The sidecar is never handed a URL or a credential: it receives bytes that the worker
already had, which is why the object store stays private.

**Coverage.** `backend/tests/test_worker_ml_seam.py` exercises the seam with no torch, no
sidecar and no weights: a real path is not copied, a local path is not copied, an
object-store object is materialised **with its extension**, the temporary file is removed on
success **and** on failure, the key and the bytes are deterministic, the module actually
imported is `service.services.ml_client`, and a blocked import raises `TaskUnavailable`.
The backend suite is 109 passed.

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
the manifest (`docker/.env.example:131`), in the MySQL image (`MYSQL_DATABASE`) and
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

**Status: fixed in the package move.** The two functions live at
`backend/service/services/matching.py:261-280` (`get_all_vacancies`) and `:283-292`
(`get_all_candidates`), and both now sort by an explicit key:

```python
user_vacancies.sort(key=lambda row: row.get("distance") or 0, reverse=True)
vacancy_candidates.sort(key=lambda row: row.get("distance") or 0, reverse=True)
```

`distance` is the similarity score the matching matrix writes (`decimal(6,6)` in
`backend/database/HireSnap.sql`), so the ordering is "closest first" and the intent is
stated rather than inferred from a comparison. The misspelled loop variable is renamed, and
the reason is recorded in the code comment at `:266-268`.

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

- `backend/service/repositories/candidate_vacancy_repository.py`
- `backend/service/repositories/personality_candidate_repository.py`
- `backend/service/repositories/personality_vacancy_repository.py`

**Status: fixed.** Each module now has `from fastapi import HTTPException, status`
(line 5 in all three) with a comment saying why the import exists, so an empty
`update_*()` payload returns 400 with the module's own message. The note stays here
because the shape of the failure is instructive: a guard branch that no test exercised
looked correct in review for as long as it existed.

**Consequence if it regresses.** The endpoint answers 500 to a client mistake, which reads
as a server outage in every dashboard and hides the real cause (an empty form).

**Mitigation.** The import is a one-line change per module and is recorded in
[ADR/0005-source-patches.md](ADR/0005-source-patches.md) with the other source-level
additions. Nothing else about these modules changed.

## 20. The HTTP contract is narrower than the old function signatures

**Defect.** `backend/service/services/ml_client.py` keeps the signatures the in-process
implementations had, and the HTTP transport cannot honour every keyword. The differences
are silent - the call succeeds and returns a vector - which is worse than an error:

| Knob | Old in-process meaning | Over HTTP | Evidence |
|------|------------------------|-----------|----------|
| `num_frames` | honoured (default 16) | **ignored**; the sidecar runs its fixed 16-frame path | the client warns when it is not 16 |
| `frames_temp_folder`, `audio_temp_folder` | honoured | **ignored**; the sidecar spools into its own scratch dir | the client debug-logs it |
| `unet_weights_path` | honoured | **ignored**; `WEIGHTS_DIR` inside the sidecar decides | dropped on purpose: a backend-relative path means nothing in another container |
| `output_embedding_path` | writes the `.npy` | **still works** - the client decodes the response and saves it | `ml_client.py` |
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
is the one that reads the file into memory before posting (`ml_client.py`
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
`backend/service/services/ml_client.py` - provably ran **in-process** in every deployed
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

**23.1 No second path.** `backend/service/services/ml_client.py` is HTTP-only
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
(`docker/.env.example:192`).

**What survives, and where.** The token itself survives **only in `frontend`'s git history**,
in every clone and every fork of the remote; cleaning the working tree does not reach it. The
plumbing survives on purpose and is wired end to end: the manifest key
(`docker/.env.example:192`) is passed as a build argument
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

## What is *not* on this list

Three changes to application source ship with this stack, and they are deliberately excluded
from the list above because they are fixed. All three are enumerated in
[ADR/0005-source-patches.md](ADR/0005-source-patches.md), and that list is closed:

1. `frontend/src/shared/api/apiConsts.js` - the base URL becomes configurable.
2. `backend/service/shared/urls.py` - `PUBLIC_BASE_URL` is honored, with the old
   behavior as fallback.
3. `backend/service/repositories/{candidate_vacancy,personality_candidate,personality_vacancy}_repository.py`
   - the missing `fastapi` import that turned a 400 into a 500.

All three are backwards compatible: with no new configuration they behave exactly as before.

Eight entries above are **closed** rather than open, and they are kept because *how* they
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
  (`backend/service/api/app.py:92-142`) resolves it and turns credentials off for the
  wildcard; `backend/tests/test_cors_settings.py` pins the rule and the middleware wiring.
- §16 - the celery ML client posted to a route the sidecar does not serve. Now routed
  through `service.services.ml_client` and covered by `backend/tests/test_worker_ml_seam.py`.
- §18 - `get_all_candidates()` sorted dictionaries and raised `TypeError`; it sorts on the
  distance key the way `get_all_vacancies()` already did.
- §19 - a missing `fastapi` import that turned a 400 into a 500.
- §22 - the `ml` service built the API image and published no URL, so the sidecar was never
  used in any deployed configuration. Now enforced by
  `scripts/check_compose_parity.py` rule 6.

Two entries are new in this revision and are about the *shape* of the deployment rather than
a bug: §23 (one inference path, serialised GPU work) and §24 (a credential rotated at the
issuer, still in history).

Everything else is documented here rather than patched, so that the next reader can tell
"the stack works around this" from "the stack is broken here".
