# Sidecars and the inference service

There are two different things in this document, and the distinction is the first thing to
get right:

- **`ml/` is a core service.** It is a submodule of its own, it starts in every stack in
  both environments, and the backend always reaches it over HTTP. It is not a profile and
  there is no switch that puts inference back inside the API process
  ([ADR/0006](ADR/0006-ml-sidecar-owns-inference.md)).
- **The optional profiles are `cache`, `storage`, `celery` and `mail`.** Four flags, each
  one an operator decision, each one starting containers *and* exporting the environment
  keys the application reads, so selecting a profile is what wires it up.

| Aspect | `ml` | The four optional profiles |
|--------|------|----------------------------|
| Selected by | nothing — it is in the stack unconditionally | a flag in `docker/.env.<mode>` |
| Profile line | `profiles: ["prod"]` / `["dev"]` (the core profiles) | `profiles: ["cache"]`, `["storage"]`, `["celery"]`, `["mail"]` |
| Image | `ml/docker/Dockerfile`, build context `../ml` | `redis:7.2-alpine`, `minio/minio`, the backend image, `axllent/mailpit` |
| Wiring | none to export — `ML__SERVICE_URL=http://ml:8080` is written for the API and the workers in the compose `x-backend-env` anchor | `CACHE__BACKEND=redis`, `CELERY__ENABLED=true`, `MAIL__*` |
| If it is missing | **uploads fail** (`MLUnavailable`) | the stack keeps working, one feature slower or absent |

| Profile | Services | Image | What the flag does | Enable when |
|---------|----------|-------|--------------------|-------------|
| `cache` | `cache` | `redis:7.2-alpine` | starts Redis **and** exports `CACHE__BACKEND=redis` + `CACHE__REDIS_URL` | You run workers (celery needs a broker) or you want job state shared between containers |
| `storage` | `storage` | `minio/minio` (pinned tag) | starts MinIO **only** — `STORAGE__BACKEND` stays `local` | You outgrow the local bind mount, or a worker needs objects off the API host |
| `celery` | `celery-worker-agents`, `-maintenance`, `-ml`, `celery-beat` | the backend image | starts three workers + one beat **and** exports `CELERY__ENABLED=true` + broker/result URLs; **forces `CACHE=1`** | A request path enqueues work (nothing does today — KNOWN_GAPS §13) |
| `mail` | `mailpit` | `axllent/mailpit:v1.20.5` | starts the SMTP sink + web inbox **and** exports `MAIL__ENABLED=true`, `MAIL__BACKEND=smtp`, `MAIL__HOST=mailpit`, `MAIL__PORT=1025` | You want to see transactional mail instead of a log file |

The rule that governs the four: **a profile is enabled by an operator decision, never by
an accident of configuration.** A profile that silently turns on changes the resource
profile of the stack — `celery` without a cache has no broker, which is why that
combination is refused rather than attempted.

## 1. How the flags work

`docker/sidecar_profiles.sh` is sourced by `docker/run.sh`. It does two jobs:
`configure_sidecar_profiles` builds the `EXTRA_PROFILES` array of `--profile` arguments,
and `export_sidecar_wiring` exports the environment keys. Both are driven by the same
four flags.

```bash
# docker/sidecar_profiles.sh — the shape of both functions
configure_sidecar_profiles() {
  if [ "${CELERY:-0}" = "1" ] && [ "${CACHE:-1}" != "1" ]; then
    echo "[warn] CELERY=1 requires the redis broker; enabling the cache profile too." >&2
    CACHE=1
  fi
  if [ "${CACHE:-1}"  = "1" ]; then EXTRA_PROFILES+=(--profile cache);   fi
  if [ "${STORAGE:-1}" = "1" ]; then EXTRA_PROFILES+=(--profile storage); fi
  if [ "${CELERY:-0}" = "1" ]; then EXTRA_PROFILES+=(--profile celery);  fi
  if [ "${MAIL:-0}"   = "1" ]; then EXTRA_PROFILES+=(--profile mail);    fi
  export EXTRA_PROFILES
  export_sidecar_wiring
  configure_gpu_override
}
```

The defaults are `CACHE=1`, `STORAGE=1`, `CELERY=0`, `MAIL=0`: the two cheap stateless
sidecars start unless you say otherwise, and the ones that change request behaviour never
start by accident. That asymmetry is the point — a missing Redis yields a
slow-but-correct stack, while a mail container nobody asked for is a listener on a port.
"The defaults encode risk, not taste."

**The wiring export is the half that is easy to miss.** For each flag that is ON,
`export_sidecar_wiring` (`docker/sidecar_profiles.sh:128-160`) exports the keys the
application reads: `CACHE__BACKEND=redis`, `CELERY__ENABLED=true`, `MAIL__ENABLED=true`
and the four `MAIL__` transport values. A shell export beats `--env-file` in compose
interpolation, which is why those manifest keys ship empty or `false`: the manifest
records the *fallback* posture and the flag records the *live* one. `STORAGE` is the
exception — it starts the container and leaves `STORAGE__BACKEND=local` alone, because the
upload path writes to `DATA_PATH` directly and flipping the facade would put media in two
backends.

There is only **one** source of truth for the flags: the env file. `docker/run.sh:159-167`
reads `CACHE`, `STORAGE`, `CELERY`, `MAIL` and `GPU` out of `docker/.env.<mode>` with its
non-secret `env_value` helper (`docker/run.sh:122-132`), applies the defaults, exports them,
and only then calls `configure_sidecar_profiles` (`docker/run.sh:178`). A shell variable is
therefore **overwritten** by the file — deliberately, because "a flag that looks set in
`.env.dev` but is silently ignored is a worse bug than a flag that does not exist"
(`docker/run.sh:114-121`).

`GPU=1` is a flag that is deliberately *not* a profile. `configure_gpu_override`
(`docker/sidecar_profiles.sh:98-107`) appends `-f docker-compose.gpu.yaml` to
`GPU_OVERRIDE_FILES`, and `run.sh` splices that **before** `--profile` in the final compose
command (`docker/run.sh:189`) — order matters, because a `-f` after `--profile` can lose
the reservation. The override changes the *resources* of a service that is always present,
while `--profile` changes the *set* of services; mixing the two would mean maintaining the
sidecar block in two shapes.

To change the posture, edit the flag in the env file (or run
`python docker/sync_env.py --mode dev` once and edit the result), then start the stack:

```bash
./docker/run.sh --dev            # whatever .env.dev declares
CACHE=0 ./docker/run.sh --dev    # NOT a documented way to override: the file wins
./docker/run.sh --dev logs       # the same selection, applied to any action
```

`run.sh` prints the resolved selection on start (`describe_sidecar_profiles`), so the
first lines of output always tell you which optional services this invocation will manage,
and in dev it also prints the URLs that exist for the flags you passed
(`docker/run.sh:345-371`): the gateway, the API and SPA, `ml :` (always — the sidecar is
core and has no host port), `mail :` when `MAIL=1`, and either
`jobs : inline in the API process (CELERY=0)` or the worker notice.

## 2. `cache` — Redis

**What it is:** `redis:7.2-alpine`, attached to the **internal** network only,
command `redis-server --save 60 1 --loglevel=warning`. No published port.

**No password, deliberately.** There is no `REDIS__*` key in the manifest and none is
planned. The container publishes nothing and lives on `internal: true`, so the only
reachable peers are the other containers in this project; a password there would be
theatre unless the network boundary is already broken. The moment a Redis client is
exposed beyond the network, that judgment changes — add a `REDIS__PASSWORD` with `:?`
and a reader that uses it.

**The client exists; the callers do not.** `backend/service/infrastructure/cache.py` is a complete
facade (`get`/`set`/`get_or_set`/`invalidate_prefix`, fail-open, 30 s backoff
after an error) selected by `CACHE__BACKEND`. With the default `memory` it is a bounded
in-process dict; with `redis` it talks to this container on `db 1`. **No API route calls
it yet** ([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)),
so enabling the profile does not speed up a single request today.

**What the flag does change.** `CACHE=1` starts the container *and* exports
`CACHE__BACKEND=redis` plus `CACHE__REDIS_URL`
(`docker/sidecar_profiles.sh:129-134`), so the facade the workers already use switches
from a per-process dict to shared state. That is the difference between a job id that only
the process that created it can read and one any container can read — see
[RUNBOOK.md §15.1](RUNBOOK.md#151-is-a-worker-consuming) and `jobs_services.status()`.

**It is also the celery broker and result backend** (`CELERY__BROKER_URL` defaults to
`redis://cache:6379/1` in the manifest, `docker/.env.example:236`). That is why
`CELERY=1` forces `CACHE=1` with a warning rather than starting workers with no broker
(`docker/sidecar_profiles.sh:52-59`).

**Enable when:**

- You wire a route to the cache facade (personality catalogues and vacancy lists are the
  obvious candidates — they are re-queried per request).
- You need job state to survive a restart (`jobs_services.status` with the memory backend
  only knows about jobs enqueued in the same process).
- You run more than one `backend` container and need shared rate-limit counters; an
  in-process counter cannot work across processes.

**Failure behavior to design for:** the API must degrade to "slower" when Redis is
unreachable, never to "500". The facade already treats an error as a miss; keep it that way.

## 3. `storage` — MinIO

**What it is:** an S3-compatible object server, pinned by tag, on the internal network.
In dev it also publishes its console on `9001`; in prod nothing is published.

**Why it exists:** today uploads land in `${DATA_PATH}/<name_object>/<uuid>.<ext>`
(`backend/service/infrastructure/uploads.py:15-17`) on a bind mount under
`infra/storage/`. That is fine for one host and terrible for two: a bind mount does not
follow a container to another machine, and the backend returns URLs built from its own
env, so a second host would serve half the media.

**The client exists; the callers do not.** `backend/service/infrastructure/storage.py` implements
`put_bytes`/`put_file`/`read_bytes`/`exists`/`delete`/`list_objects`/`presigned_url`
behind `STORAGE__BACKEND` (`local` by default, `DATA_PATH` under the hood; `s3`
for this container). The worker task `hiresnap.embed_candidate` already reads and writes
through it, but no API route does ([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)).
**What the flag does and does not change.** `STORAGE=1` starts the container and
exports nothing: `STORAGE__BACKEND` stays `local` (`docker/sidecar_profiles.sh` has no
storage branch on purpose, and the default is pinned in
`docker/docker-compose.yaml:94` with the reason inline at `:85-94`). This is the one
profile whose container and whose wiring are deliberately separate: the upload path writes
through `service/infrastructure/uploads.py` to `DATA_PATH`, while the workers read the facade, so
flipping the backend would leave half the media in a bucket and half on the bind mount.
MinIO is therefore infrastructure you enable ahead of time, and the day the upload path
moves behind the facade, `STORAGE__BACKEND=s3` becomes a one-line env change.
The remaining keys are already in the manifest so that switch needs no code
(`docker/.env.example` section 7): `STORAGE__REGION`, `STORAGE__PREFIX` and
`STORAGE__PRESIGN_TTL`.


**Storage fails closed on purpose.** A cache outage is a miss; a storage outage is
`StorageError` and a 500. The alternative — silently writing to the local disk while the
bucket is unreachable — splits the media across two backends and is discovered weeks later.

**Enable when:**

- You run the stack on more than one host, or behind a load balancer.
- `infra/storage/` grows past what a bind mount comfortably handles, or media should
  live on separate hardware from the database.
- You need lifecycle rules, versioning or replication.

**Credentials:** `STORAGE__ACCESS_KEY` and `STORAGE__SECRET_KEY` in the env file,
mapped by the compose file onto `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`. Both use
`:?` so a missing value aborts the start instead of shipping `minioadmin`. The same
pair is what the S3 client reads (`STORAGE_ACCESS_KEY`/`STORAGE_SECRET_KEY` in the
container). See [SECRETS.md](SECRETS.md#5-object-storage-credentials).

## 4. `ml` — inference sidecar

**What it is:** a standalone FastAPI service in the `ml/` submodule that serves the
video/audio pipeline over HTTP. It is a *sidecar to the backend*, not a second copy of the
API: it has no database connection, no auth, no upload path, and it cannot answer
`/server/*`. It is also **not optional** — the API has no in-process inference path left
([ADR/0006](ADR/0006-ml-sidecar-owns-inference.md)), so a stack without this container can
serve logins and uploads but cannot produce a single embedding.

| Property | Value | Evidence |
|----------|-------|----------|
| Build | `context: ../ml`, `dockerfile: docker/Dockerfile` | `docker/docker-compose.yaml:458-459`, `docker/docker-compose.dev.yaml:348-349` |
| Image | `nvidia/cuda:11.6.2-cudnn8-runtime-ubuntu20.04` + Python 3.9 from deadsnakes | `ml/docker/Dockerfile:14`, `:39-47` |
| Command | `CMD ["python3", "app.py"]` — never `uvicorn app:app` | `ml/docker/Dockerfile:130` (why: `:124-129`) |
| Workdir | `/app/service`, `PYTHONPATH=/app/service` — the modules are flat, there is no package | `ml/docker/Dockerfile:74`, `:112` |
| User | `app` (uid/gid 999) | `ml/docker/Dockerfile:88-89`, `:113` |
| Port | `8080` inside the network (`ML_PORT`); `EXPOSE 8080`; **published nowhere** | `ml/docker/Dockerfile:115`, `docker/.env.example:256` |
| State | **stateless**; request scratch in `/app/ml-scratch`, nothing else to persist | `ml/service/app.py:197-215` |
| Profile | `profiles: ["prod"]` / `["dev"]` — the core profile, not a flag | `docker/docker-compose.yaml:444`, `docker/docker-compose.dev.yaml:340` |
| Healthcheck | image: `curl -fsS http://127.0.0.1:${ML_PORT:-8080}/health`, 30s / 10s / **start period 60s** / 5 (`ml/docker/Dockerfile:121-122`); both compose files raise the start period to **300s** | `docker/docker-compose.yaml:538-546`, `docker/docker-compose.dev.yaml:399-409` |

> **Compose wiring.** Both stacks build the sidecar from `../ml` with
> `docker/Dockerfile`, which is what makes it self-contained: its Dockerfile cannot reach
> `backend/` even by accident, and `ml/.dockerignore` decides what the daemon sees. The
> service is given the flat `ML_*` names from [ENV.md §8.3](ENV.md#83-ml) plus the four
> sidecar-owned keys, no `DB_*`, and **no `depends_on: mysql`**. The API and all four
> celery services get `ML_SERVICE_URL: ${ML__SERVICE_URL:-http://ml:8080}` from the
> `x-backend-env` anchor (`docker/docker-compose.yaml:104`) and `depends_on: ml` with
> `condition: service_started`. Neither stack publishes a host port for it — the
> canonical way in is `make ml-shell`.

### 4.1 Contract

Five routes, from the module docstring at `ml/service/app.py:10-16`. The full
request/response schemas, error registry and examples live with the service:
[ml/README.md](../ml/README.md) is the canonical contract table, and this section is the
deployment view of it.

| Route | Method | Returns |
|-------|--------|---------|
| `/health` | GET | `{"status", "service", "version"}` — `service` is `ml`, `version` `1.0.0` |
| `/v1/capabilities` | GET | `{"video_embedding", "text_embedding", "transcription", "cuda", "models"}` |
| `/v1/embed/video` | POST | `{"vector", "dim", "model"}` — 384-dim float list |
| `/v1/embed/text` | POST | `{"vector", "dim", "model"}` — JSON body `{"text": ...}` |
| `/v1/transcribe` | POST | `{"text", "model"}` |

Three properties of that contract are load-bearing for the deployment:

1. **`/health` is always 200 and `degraded` is not a failure.** On a CPU host with no
   weights the route returns `status: "degraded"` inside a 200 (`ml/service/app.py:355-369`);
   the watchdog never reports `down`. The container healthcheck therefore passes, which is
   correct: the process is up, and a capability it does not have is what
   `/v1/capabilities` is for. That is also the probe to alert on.
2. **The media routes take bytes, not a path.** `POST /v1/embed/video` and
   `POST /v1/transcribe` read the body as `application/octet-stream` (a multipart `file`
   field is also accepted), because the sidecar has no access to the backend's storage
   mount and must not be given one. Filename metadata travels in `X-Filename`
   (`backend/service/services/ml_client.py:265-268`).
3. **Errors are `{"error", "detail"}` with a stable code.** The registry is closed
   (`ml/service/app.py:64-76`): `invalid_request` 422, `empty_body` 400,
   `request_too_large` 413, `unsupported_media_type` 415, `not_found` 404,
   `method_not_allowed` 405, `service_unavailable` 503, `inference_failed` 500,
   `internal` 500. **503 is the expected failure**: weights and checkpoints live outside
   the image, so a container that started fine can still be unable to serve.

No interactive docs: `/docs` and `/redoc` are 404 by design (`ml/tests/test_contract.py`
asserts it). A closed surface is the point — the sidecar is reachable only from the
compose network and has no authentication of its own.

### 4.2 How the backend reaches it

`backend/service/services/ml_client.py` is **HTTP-only**. There is no second transport and no
import of the pipeline anywhere in `backend/`:

| `ML_SERVICE_URL` (manifest: `ML__SERVICE_URL`) | Behavior |
|------------------------------------------------|----------|
| set — the manifest ships `http://ml:8080` | POST to the sidecar |
| unset, empty, or hand-emptied | falls back to the client default `http://127.0.0.1:8080` (`backend/service/services/ml_client.py:65`), which **inside the API container is the API itself** — every upload then fails with `ml_service_unreachable` |

| Property | Value | Evidence |
|----------|-------|----------|
| Variable | `ML__SERVICE_URL` → `ML_SERVICE_URL`, read per call | `backend/service/services/ml_client.py:101-120` |
| Connect / read timeout | 10 s / 900 s | `backend/service/services/ml_client.py:59-60`, `:134-146` |
| Errors | `MLUnavailable` / `MLRejected` / `MLContractError` | `backend/service/services/ml_client.py:149-161` |
| Transport report | always `"http"`; the URL can still be wrong | `backend/service/services/ml_client.py:418-434` |
| Only wired call site | `upload_video()` → `get_user_embedding()` | `service/services/matching.py:202` |

The 900-second read timeout is not generosity: a long video embedding runs for minutes, and
the gateway's `proxy_read_timeout 86400s` (`infra/nginx/nginx.conf:120-122`) is sized for
the same reason. The client timeout is deliberately shorter so a hung sidecar raises
`MLUnavailable` instead of the gateway severing a request the backend still believes in.

**What this costs.** `ml` is now the only inference path, so while it is down or still
starting, uploads fail — a 503 `service_unavailable` when it is up but degraded, a 503
`ml_service_unreachable` when it cannot be reached at all. Every other route keeps working.
See [KNOWN_GAPS.md §23](KNOWN_GAPS.md#23-the-sidecar-is-the-only-inference-path-and-it-serialises-gpu-work).

### 4.3 Mounts and configuration

| Mount | At | Why |
|-------|----|-----|
| `${WEIGHTS_DIR:-../infra/models/weights}` | `/app/models/weights:ro` | `Unet3D.pth`; read-only, ml only |
| `${PRETRAINED_CKPT_DIR:-../infra/models/pretrained_ckpts}` | `/app/models/pretrained_ckpts:ro` | Cosmos tokenizer `.jit` files |
| `../infra/logs` | `/app/logs` | One log destination per host, same as the API |
| `ml_scratch` (volume) | `/app/ml-scratch` | request scratch; a 300 MB upload must not land in the writable layer |
| `hf_cache` (volume) | `/app/.cache/huggingface` | Hugging Face downloads survive recreation |

The **host** side of the two model mounts is the manifest key (`WEIGHTS_DIR`,
`PRETRAINED_CKPT_DIR`, both relative to `docker/`); the container side is a literal in the
`volumes:` block (`docker/docker-compose.yaml:520-521`). Nothing is mounted at `/app/data`
in prod: the sidecar receives bytes, it never reads the upload directory.

Nothing from `backend/` is copied into the image: no `env.py`, no dotenv file. The
sidecar reads `os.environ` directly through `ml/service/settings.py`, and a dotenv file
inside a container is how a stale local value overrides the manifest.

Read-only is deliberate. A sidecar that can write its own weights is one bad path join
away from corrupting files the API container shares.

### 4.4 Enabling, GPU, and proving which path is live

**There is nothing to enable.** The service is in both stacks under the core profile. The
only switch nearby is `GPU`:

| Flag | Effect |
|------|--------|
| `GPU=1` | `run.sh` merges `docker/docker-compose.gpu.yaml` (`sidecar_profiles.sh:98-107`), which adds one `deploy.resources.reservations.devices` block asking for `driver: nvidia`, `count: all`, `capabilities: [gpu]`. The `-f` is spliced **before** `--profile` in the compose command (`docker/run.sh:189`) so profile filtering cannot drop it. |
| `GPU=0` (default) | no reservation. The sidecar still starts and serves; models load in float32 on CPU and `/v1/capabilities` reports `cuda: false`. That is the degraded-but-working path. |

A host without the NVIDIA runtime must leave `GPU=0`: a reservation with nothing to
satisfy makes `up` fail. `nvidia-container-toolkit` is the requirement, and
`docker run --rm --gpus all nvidia/cuda:11.6-base nvidia-smi` is the check.

**Nothing in the API can infer in-process**, so the interesting question is not *whether*
the sidecar is used but *which URL the client dials*. `describe_transport()` inside the API
container answers it (`backend/service/services/ml_client.py:418-434`):

```bash
./docker/run.sh --dev exec backend python -c \
  "import json;from service.services.ml_client import describe_transport;print(json.dumps(describe_transport()))"
#   {"transport": "http", "service_url": "http://ml:8080", "timeouts": [10.0, 900.0]}
```

`transport` is always `http`; `service_url` is the field that can still be wrong. The
decisive proof is a pair of log lines on both sides of one upload — see
[RUNBOOK.md §14](RUNBOOK.md#14-proving-the-sidecar-is-used).

**Weights:** the checkpoints are not in git (`.gitignore` blocks `infra/models/`).
`infra/models/weights/Unet3D.pth` and
`infra/models/pretrained_ckpts/Cosmos-Tokenizer-CV4x8x8/{encoder,decoder}.jit` must exist
before the video path works; `/v1/capabilities` reports exactly which of them is missing
without loading anything.

## 5. Selecting profiles in each environment

| Profile | Flag | dev | prod | Why |
|---------|------|-----|------|-----|
| `cache` | `CACHE` | `1` | `1` | ~10 MB, and it is the celery broker. Idles harmlessly while no route reads it. Set `CACHE=0` to drop it — but then `CELERY=1` will re-enable it with a warning. |
| `storage` | `STORAGE` | `1` | `1` | MinIO idles without S3 clients and the console is handy in dev. Set `STORAGE=0` to drop it. |
| `celery` | `CELERY` | `0` | `0` | Four containers for queues that nothing enqueues to yet ([KNOWN_GAPS.md §13](KNOWN_GAPS.md#13-the-platform-layer-is-delivered-but-its-call-sites-are-thin)). Forces `CACHE=1`. |
| `mail` | `MAIL` | `0` | `0` | One container; worth it in dev to read real mail, pointless in prod unless mail is exercised. |

Not in the table, on purpose: `ml` is core (above) and `GPU` is a host capability, not a
set of containers. Everything else — `mysql`, `backend`, `frontend`, `gateway`, `ml` —
carries a `prod`/`dev` profile and is always part of the stack. There is no
`--profile none` mode: the base services are what the project *is*.

The combinations that come up in practice:

| You want | Flags | What changes |
|----------|-------|--------------|
| Default | none | `cache` and `storage` run because their flags default to `1`; nothing else is added |
| Use the GPU | `GPU=1` | the same containers, with a device reservation on `ml` |
| Async work, same host | `CELERY=1` | `cache` (forced), three workers, one beat |
| Full local stack | `GPU=1 CELERY=1 MAIL=1` | device reservation, `cache`, `storage`, four celery containers, `mailpit` |
| Read the transactional mail | `MAIL=1` | `mailpit` only; `MAIL__BACKEND` flips to `smtp` |

`run.sh` prints the resolved set on every start (`docker/run.sh:240-252`) and, in dev, the
URLs that exist (`docker/run.sh:345-371`): the Mailpit inbox when `MAIL=1`, and a line
saying `ml` has **no host port** with the `exec ml curl` probe to use instead.
