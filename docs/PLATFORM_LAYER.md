# Platform layer

The platform layer adds the GPTHub-style operational pieces HireSnap did not have:
a cache, object storage, mail, background workers with a scheduler, and a job-status
model. Every one of them is **feature-flagged with a safe fallback**, so the backend
behaves exactly as it did before unless an operator switches a flag on.

The design rule that drives everything below:

> **Business data stays in MySQL. Operational state lives in redis db 1.**

A candidate, a vacancy, an embedding row, an uploaded file record - all of that is
business data and belongs to MySQL, which is the system of record. A cache entry, a
job status, a broker message, a task result are operational state: disposable,
regenerable, expiring. They live in redis db 1 and may be lost without losing a
single user record. Keeping the two apart is what lets you flush redis during an
incident without a database restore, and it is why the broker, the result backend and
the cache all default to `/1` rather than `/0`.

## Layout

```
backend/service/infrastructure/
  __init__.py        package marker: every backend here has a local default
  cache.py           Cache facade: memory (default) | redis | noop
  storage.py         Object storage: local (default) | s3
  mail.py            DisabledMailer | FileMailer | SmtpMailer + fire-and-forget send
  uploads.py         The request-path writers: DATA_PATH/<name>/<uuid>.<ext>
  database/          connection.py and schema.py - the system of record
backend/service/infrastructure/messaging/
  __init__.py        package marker: celery is imported only inside the factory
  celery_app.py      Celery application factory, queues, routes, time limits
  tasks.py           Task bodies (pure functions) + registry + celery binding
  beat_schedule.py   Periodic schedule as data, with env overrides
  queue.py           CeleryDispatcher: the only way a rule reaches the broker
backend/service/shared/ports/__init__.py
  the five Protocols a rule may depend on: Cache, FileStorage, Mailer, JobQueue,
  MlClient - no driver behind any of them
backend/service/services/jobs/application/job_service.py
  JobService: enqueue(kind, payload) -> job_id, status(job_id) -> record
backend/requirements-platform.txt
  celery, redis, boto3 - optional, pinned, separately installed
```

`service/infrastructure/` and `service/infrastructure/messaging/` are the only two
places in the service that name a driver. Nothing above them imports `redis`,
`celery` or `boto3`: rules talk to the Protocols in
`service/shared/ports/__init__.py`, and the composition layer decides which concrete
object answers.

## Three invariants

**1. Optional dependencies are imported lazily, inside the factory.**
`redis`, `celery` and `boto3` are imported at call time, never at module import
time. An image built from `requirements.txt` alone can still
`import service.infrastructure.storage`, still serve uploads, and still pass the test
suite. That is not a stylistic preference: the backend container is built from
`requirements.txt`, and a module-level `import boto3` would turn an optional feature
into a hard build failure. `backend/tests/test_infra_fallbacks.py` and
`backend/tests/test_worker_routing.py` prove the import side of it, and
`backend/tests/test_ports_contracts.py` proves the stronger statement: importing the
ports package pulls in none of the four drivers.

**2. A flag that is off must reproduce today's behaviour exactly.**
`CACHE__BACKEND=memory` is a process-local dict - the same "no shared cache" semantics
the API has today. `STORAGE__BACKEND=local` writes to `DATA_PATH` with the same key
layout, the same file names and the same bytes as
`service/infrastructure/uploads.py`. `MAIL__ENABLED=false` sends nothing.
`CELERY__ENABLED=false` runs jobs inline. Turning every flag off is not a degraded
mode; it is the previous release.

**3. A neighbour's failure is a normal state, not an incident.**
Mirroring `docs/SERVICE_CONTRACTS.md` in GPTHub: the cache, the storage backend, the
mailer and the queue each fail open or closed **explicitly**, and the choice is
documented per component below. Nothing is swallowed silently - every degradation
writes one structured log line with `status=degraded` or `status=skipped`.

## Where the wiring lives now

The vertical-slice refactor moved the decision "which class does this flag select"
out of the facades and into `service/composition/`, one file per question:

| File | The question it answers |
|---|---|
| `composition/infra.py` | Which concrete cache, storage backend, mailer and ML transport a slice talks to. Re-exports the builders from `service/infrastructure/`; nothing else may reach for `service.infrastructure.*` directly. |
| `composition/services.py` | Which application service per domain, and which persistence module it is constructed with (`SERVICE_MODULES`). |
| `composition/repositories.py` | Which persistence package per domain (`REPOSITORY_MODULES`) - repositories are modules of `%s`-parameterised statements, not classes. |
| `composition/container.py` | The lifetime. Everything is built lazily on first use, because `/server/health` must answer with the database down; a failed build is not cached. |
| `composition/state.py` | The access surface a router or a worker calls: `get_cache()`, `get_storage()`, `get_mailer()`, `get_ml_client()`, `get_dispatcher()` and the thirteen `get_<slice>_service()` accessors. |

`get_dispatcher()` is deliberately not called `get_job_queue()`: `JobQueue` is
the application port (`enqueue()` / `status()`) and `JobService` implements it,
while the dispatcher is one layer below - submit a named task, look at where it got
to, ask it to stop (`composition/state.py:116-124`).

A second, narrower mechanism sits beside the composition layer: `service/settings.py`
is a **frozen dataclass** built once by `get_settings()` (cached with `lru_cache`,
cleared by `reset_settings()`). It carries the slice-facing subset of the platform
configuration - cache backend and TTL, storage backend, mail enabled, broker URL, job
TTL, ML URL and timeouts (`backend/service/settings.py:129-161`). It does **not** try to own
every knob: `CACHE__PREFIX`, `CACHE__MAX_ENTRIES`, `STORAGE__PRESIGN_TTL`,
`MAIL__HOST` and the rest are read by the facade that uses them, through its own
`_env(*names, default)` helper. The split is the point: a rule asks
`get_settings()` for the decisions that are its business, and never sees a driver
option.

Neither mechanism uses `service/configuration/env.py`. Its `Env.__getattr__`
raises `AttributeError` for a key that is not configured, which is exactly the wrong
behaviour for an optional flag - `backend/service/settings.py:1-22` says so in its own
docstring.

## Env flag matrix

These keys **are** in `docker/.env.example` now - the manifest documents each one
where it lives, and `python docker/sync_env.py --check` keeps the three env files in
step. **Every one also has a code default, so an absent key is never an error**: the
facades read with `_env(...)` helpers that fall back, and `service/settings.py`
falls back the same way. Both the nested form (`CACHE__BACKEND`, the manifest
convention) and the flat form (`CACHE_BACKEND`, what compose injects once) are
accepted for every key.

The four 1/0 switches in section 10 of the manifest decide which optional sidecars
start, not what the code does; `docker/sidecar_profiles.sh:128-160` turns a switch
into the matching wiring export.

### Cache

| Key | Default | Meaning | Manifest |
|---|---|---|---|
| `CACHE` | `1` | Start the `cache` profile and export the redis wiring | `docker/.env.example:222` |
| `CACHE__BACKEND` | `memory` | `memory` \| `redis` \| `noop` | `:237` |
| `CACHE__PREFIX` | `hiresnap` | First key segment; separates tenants in shared db 1 | `:242` |
| `CACHE__TTL` | `300` | Default TTL in seconds | `:244` |
| `CACHE__REDIS_URL` | `redis://localhost:6379/1` | Only read when the backend is `redis` | `:240` |
| `CACHE__TIMEOUT` | `0.5` | Socket timeout in seconds | `:249` |
| `CACHE__RETRY_BACKOFF` | `30` | Seconds to stop retrying after a transport failure | `:251` |
| `CACHE__MAX_ENTRIES` | `2048` | LRU cap for the memory backend | `:246` |

### Storage

| Key | Default | Meaning | Manifest |
|---|---|---|---|
| `STORAGE` | `1` | Start the `storage` profile (MinIO) | `docker/.env.example:224` |
| `STORAGE__BACKEND` | `local` | `local` \| `s3` | `:169` |
| `STORAGE__BUCKET` | `hiresnap-uploads` | Bucket name | `:157` |
| `STORAGE__ENDPOINT` | *(empty)* | e.g. `http://storage:9000` (MinIO) | `:151` |
| `STORAGE__ACCESS_KEY` / `STORAGE__SECRET_KEY` | *(empty)* | Credentials; compose requires them with a `:?` form rather than a default | `:153` / `:155` |
| `STORAGE__REGION` | `us-east-1` | Signing region | `:173` |
| `STORAGE__SECURE` | `false` | `true` for HTTPS endpoints | `:160` |
| `STORAGE__PREFIX` | *(empty)* | Optional key prefix inside the bucket | `:171` |
| `STORAGE__PRESIGN_TTL` | `3600` | Presigned URL lifetime in seconds | `:175` |
| `DATA_PATH` | `backend/data` | Root for the local backend; already a documented key | `:74` |

### Mail

| Key | Default | Meaning | Manifest |
|---|---|---|---|
| `MAIL` | `0` | Start the `mail` profile (mailpit) and export the SMTP wiring | `docker/.env.example:230` |
| `MAIL__ENABLED` | `false` | Master switch | `:294` |
| `MAIL__BACKEND` | `file` | `file` \| `smtp` | `:298` |
| `MAIL__FILE_PATH` | `mail_outbox.log` | Where the file backend appends | `:318` |
| `MAIL__HOST` / `MAIL__PORT` | *(empty)* / `587` | SMTP endpoint | `:301` / `:303` |
| `MAIL__USER` / `MAIL__PASSWORD` | *(empty)* | SMTP credentials | `:306` / `:307` |
| `MAIL__FROM` | `no-reply@hiresnap.local` | Envelope sender | `:309` |
| `MAIL__STARTTLS` | `true` | Upgrade with STARTTLS | `:312` |
| `MAIL__SSL` | `false` | Use SMTP-over-SSL instead (port 465) | `:313` |
| `MAIL__TIMEOUT` | `10` | Socket timeout in seconds | `:315` |

### Celery

| Key | Default | Meaning | Manifest |
|---|---|---|---|
| `CELERY` | `0` | Start the worker and beat services, and export `CELERY__ENABLED=true` | `docker/.env.example:227` |
| `CELERY__ENABLED` | `false` | `true` sends work to the broker; `false` runs inline | `:328` |
| `CELERY__BROKER_URL` | `redis://localhost:6379/1` | Broker | `:330` |
| `CELERY__RESULT_BACKEND` | `redis://localhost:6379/1` | Results | `:331` |
| `CELERY__CLEANUP_INTERVAL_SECONDS` | `3600` | Beat cadence override | `:333` |
| `CELERY__REINDEX_INTERVAL_SECONDS` | `86400` | Beat cadence override | `:335` |
| `CELERY_AGENTS_CONCURRENCY` | `2` | Prefork children on the `agents` queue | `:339` |
| `CELERY_MAINTENANCE_CONCURRENCY` | `1` | Prefork children on the `maintenance` queue | `:340` |
| `CELERY_ML_CONCURRENCY` | `1` | Prefork children on the `ml` queue - one GPU per child | `:341` |

## Queues

Three queues, three failure domains. A queue is not a priority lane: it is the set of
work that may be delayed without delaying anything else.
`docker/docker-compose.yaml` starts one worker per queue with
`--queues=<name>` (`:602-604`, `:648-650`, `:695-697`), and the routing table
that decides which task lands where is `TASK_ROUTES` in
`backend/service/infrastructure/messaging/celery_app.py:60-66`.

| Queue | Tasks | Concurrency | Why it is separate |
|---|---|---|---|
| `agents` | `hiresnap.send_email` | `CELERY_AGENTS_CONCURRENCY` (default 2) | Fast, user-visible, must never queue behind an embedding |
| `maintenance` | `hiresnap.cleanup_orphan_uploads`, `hiresnap.health` | `CELERY_MAINTENANCE_CONCURRENCY` (default 1) | Scans the whole storage tree; runs hourly and nobody waits on it |
| `ml` | `hiresnap.embed_candidate`, `hiresnap.reindex_embeddings` | `CELERY_ML_CONCURRENCY` (default 1) | Minutes of GPU per task; the only queue that may need a GPU device |

Time limits: default soft 300 s / hard 330 s; the ML tasks get 1800 s / 1860 s
(`celery_app.py:50-55`, applied per task by `TASK_TIME_LIMITS` at `:70-73`).
The gap between soft and hard is deliberate - the soft limit raises an exception
inside the task so it can record a failure, and the hard limit is the backstop for a
task that ignores it.

`task_acks_late=True` with `task_reject_on_worker_lost=True` means a worker killed
mid-task has its message redelivered instead of silently dropped
(`celery_app.py:167-171`). That is only safe because **every task body is
idempotent**:

* `embed_candidate` derives its object key from `(candidate_id, file_key, model_version)`,
  so a redelivery overwrites the same bytes at the same key instead of orphaning a copy.
* `cleanup_orphan_uploads` only deletes objects that no row references, and deletes
  nothing at all when the reference list cannot be read
  (`backend/tests/test_jobs_services.py:126-136` pins that refusal).
* `reindex_embeddings` only enqueues uploads that have no embedding yet.
* `send_email` is the exception: SMTP cannot deduplicate, so it is at-most-once by
  construction and declares no retry.

## The job contract

The application surface is a slice, not a module: `JobService` in
`service/services/jobs/application/job_service.py`, reached through the composition
accessor.

```python
from service.composition.state import get_job_service

service = get_job_service()

job_id = service.enqueue("embed_candidate",
                         {"candidate_id": "c1", "file_key": "files/a.mp4"})
service.status(job_id)
# {"id": "...", "kind": "embed_candidate", "state": "succeeded",
#  "created_at": "2026-11-07T18:00:00Z", "updated_at": "...",
#  "result_url": None, "error": None,
#  "payload_summary": {"keys": [...], "preview": "..."}}
```

The four kinds are `embed_candidate`, `reindex_embeddings`,
`cleanup_orphan_uploads` and `send_email` (`job_service.py:64-69`; ask
`supported_kinds()` for the same list at runtime). An unknown kind is a **caller
error** - `enqueue()` raises `ValueError`, it does not record a failed job.

States: `queued -> running -> succeeded | failed`. Nothing else, and `status()`
returns `None` for an id it has never seen - "unknown" and "still queued" must not
look the same to a polling client.

With `CELERY__ENABLED=false` the task runs **inline and synchronously** inside
`enqueue()`, so the contract holds with no broker. That is what makes it testable and
what keeps a laptop runnable. With the flag on, `enqueue()` publishes to the broker and
`status()` refreshes from the task result; if the publish itself fails (celery not
installed, broker down) the job falls back to inline execution and records
`fallback_reason` - losing the work is worse than a slow response, and the log line
names the misconfiguration (`job_service.py:235-245`).

The broker itself is reached through `service/infrastructure/messaging/queue.py`, not
by the rule: `JobService` may not name celery (rule 3 of
`backend/tests/test_service_boundary.py`), so it asks
`service.composition.state.get_dispatcher()` for an object with
`configured()` / `submit()` / `run()` / `state()` / `revoke()`.

Job state is stored through the cache facade in namespace `jobs` with a 24 h TTL.
With `CACHE__BACKEND=memory` that state is **per process**: a job enqueued by the
API process is not visible to a worker process. Enable redis before enabling celery,
or a job id handed to the client will read as `None` from another worker.

No HTTP route exposes any of this yet, on purpose
(`job_service.py:37-39`; [KNOWN_GAPS.md](../../docs/KNOWN_GAPS.md#27-the-jobs-slice-has-no-http-route-on-purpose)).

## Enabling each component

### Redis cache

1. Start the `cache` profile: `./docker/run.sh --dev up` with `CACHE=1`.
2. Set `CACHE__BACKEND=redis` (and `CACHE__REDIS_URL=redis://cache:6379/1` when the
   service name differs from the default). `CACHE=1` exports both for you - see
   `docker/sidecar_profiles.sh:129-134`.
3. Verify: ask a worker to run the health task (below) and read
   `checks.cache.ok`.

The compose file deliberately does **not** make the backend depend on `cache`, so
`CACHE=0` stays startable. If redis is unreachable the facade degrades: every read is a
miss, every write returns `False`, one warning is logged, and the API keeps answering.

### S3 / MinIO object storage

1. Start the `storage` profile (`STORAGE=1`) and set `STORAGE__ACCESS_KEY` /
   `STORAGE__SECRET_KEY` - the compose file uses a required-value form rather than a
   default, because a defaulted MinIO password publishes an object store with a known
   credential.
2. Set `STORAGE__BACKEND=s3`, `STORAGE__ENDPOINT=http://storage:9000`,
   `STORAGE__BUCKET=hiresnap-uploads`.
3. Install `backend/requirements-platform.txt` in the image that runs with S3. Without
   boto3 the backend logs `storage backend=s3 status=unavailable code=boto3_missing
   fallback=local` and **keeps storing uploads on disk** rather than failing them
   (`backend/service/infrastructure/storage.py:487-494`) - which is a silent split-brain, so
   check the log line after the first deploy.
4. Create the bucket once: `get_storage().ensure_bucket()` on startup or `mc mb` in
   the runbook.

Local semantics are byte-identical to today: `<name>/<uuid>.<ext>` and
`embeddings/<namespace>/<uuid>.npy` under `DATA_PATH`, served by the `/data` static
mount. Switch back with `STORAGE__BACKEND=local` and the existing URLs keep working.

### Mail

Set `MAIL__ENABLED=true` and leave `MAIL__BACKEND=file` for development: messages
are appended to `mail_outbox.log` where a developer can read the reset link. For real
delivery set `MAIL__BACKEND=smtp` plus `MAIL__HOST`; with GPTHub's mailpit service
that is `mailpit:1025`, no credentials and `MAIL__STARTTLS=false` - asking for
STARTTLS against a server that does not offer it fails the handshake
(`docker/sidecar_profiles.sh:145-157` exports exactly that set).

Request paths must call `send_now_or_later(...)`, never `send_now(...)`. The helper
runs the send in a daemon thread and swallows every error: a user must not get a 500
for an action that already succeeded because the mail relay is down.

### Celery workers and beat

Only after redis is enabled:

1. Install `requirements-platform.txt` in the image that runs workers.
2. Set `CELERY__ENABLED=true` and `CELERY__BROKER_URL=redis://cache:6379/1`.
3. Start one worker per queue, each with a `stop_grace_period` well above Docker's
   default 10 s: `task_acks_late` only helps if SIGTERM arrives before SIGKILL.
   Compose uses 360 s for the two short queues and 1860 s for `ml`
   (`docker/docker-compose.yaml:632`, `:670`, `:724`).
4. Start beat - exactly one instance. Two beats double every periodic run.

```sh
celery -A service.infrastructure.messaging.celery_app worker --loglevel=info --queues=agents
celery -A service.infrastructure.messaging.celery_app worker --loglevel=info --queues=maintenance
celery -A service.infrastructure.messaging.celery_app worker --loglevel=info --queues=ml
celery -A service.infrastructure.messaging.celery_app beat   --loglevel=info
```

That module path is the one both compose files run, seven times between them
(`docker/docker-compose.yaml:602-604`, `:648-650`, `:695-697`, `:758`).
`service/infrastructure/messaging/celery_app.py` exposes the application through a
module-level `__getattr__`, so `-A service.infrastructure.messaging.celery_app` works
without an import-time `Celery(...)` call.

The health task is registered and routed but **not on the beat schedule** - beat runs
the cleanup sweep and the reindex only (`beat_schedule.py:60-86`). Ask for the
health report explicitly:

```sh
celery -A service.infrastructure.messaging.celery_app call hiresnap.health
```

## Failure semantics, component by component

| Component | Flag off | Dependency missing | Dependency down | Raises into a request? |
|---|---|---|---|---|
| Cache | memory dict | unknown backend name -> memory | reads miss, writes return `False`, 30 s backoff | never |
| Storage | local disk | `s3` without boto3 -> local, one error log | `StorageError` on the operation | yes - user data must not be lost silently |
| Mail | no-op, logs | `smtp` without host -> disabled | `MailError` from `send_now`; swallowed by `send_now_or_later` | only if a caller uses `send_now` directly |
| Celery | inline execution | publish fails -> inline + `fallback_reason` | same | never - the job still runs |
| Jobs | inline, per process | n/a | `status()` returns `None` for an unknown id | never |

The asymmetry between cache and storage is intentional. A cache is an optimisation: if
it is broken, recompute. Storage holds the user's file: if a write fails and the caller
is told it succeeded, the video is gone and the database row points at nothing. So the
cache fails open and storage fails closed, and both say so in the log. The port
docstrings in `backend/service/shared/ports/__init__.py:12-21` are the canonical statement of
the four directions.

## Health

* The API's `GET /server/health` is three keys and no I/O -
  `{"status": "ok", "service": "backend", "version": "..."}`
  (`backend/service/api/app.py:144-147`). It does **not** report the platform backends and
  performs **no network round trip**: a gateway polls it on a timer, and probing S3
  there would make a storage hiccup look like a dead API.
* The worker's `hiresnap.health` task is the one that probes. It reports
  `checks.{cache,storage,mail}` and flips `status` to `failed` when any of them is
  not ok (`backend/service/infrastructure/messaging/tasks.py:438-468`). Each check sits in
  its own `try/except`, so a broken storage client does not hide the cache result.
* `cache_health()`, `storage_health()` and `mail_health()` are all cheap by
  contract: `cache_health()` only pings when the backend is redis
  (`cache.py:528-535`), `storage_health()` never performs a round trip
  (`storage.py:512-529`), `mail_health()` never sends (`mail.py:340-345`).

## Tests

```sh
cd backend && python -m pytest -q
```

`tests/test_infra_fallbacks.py` proves the degradation paths for all three facades (33
tests: the memory LRU, the redis backoff, url redaction, the local key layout, the
traversal guard, the boto3-missing fallback and the fire-and-forget mail path).
`tests/test_jobs_services.py` proves the job contract including the inline and
degraded-enqueue branches (15 tests). `tests/test_worker_routing.py` proves the queue
topology, the beat schedule and that importing the modules needs neither celery nor a
broker (21 tests). `tests/test_ports_contracts.py` proves the ports package imports no
driver (17 tests). The counts are the measured `--collect-only` numbers, not estimates:
they are here to tell you a file was skipped, not to be quoted as a contract.

## Why the schedule lives in code, not in the database

`service/infrastructure/messaging/beat_schedule.py` returns a plain dict. A periodic
job is part of the program, exactly like a route: if it were a row in MySQL, every
environment would need the row inserted by hand, a renamed task would leave a stale row
pointing at nothing, and rolling back the image would not roll back the schedule. Code
keeps it reviewable in the same diff as the task it triggers and identical in dev and
prod. The "operators want to change the interval without a deploy" objection is
answered by the `CELERY__CLEANUP_INTERVAL_SECONDS` /
`CELERY__REINDEX_INTERVAL_SECONDS` env overrides - read through `_seconds()`, which
falls back to the default and warns on a malformed value rather than stopping beat
(`beat_schedule.py:35-48`).

Two jobs are scheduled today: `cleanup-orphan-uploads-hourly` on `maintenance` and
`reindex-embeddings-nightly` on `ml` (`beat_schedule.py:60-86`), each with its own
`expires` so a beat restart cannot pile up a backlog.
