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
backend/src/infra/
  __init__.py        package marker, explains the "optional at runtime" contract
  cache.py           Cache facade: memory (default) | redis | noop
  storage.py         Object storage: local (default) | s3
  mail.py            DisabledMailer | FileMailer | SmtpMailer + fire-and-forget send
backend/src/workers/
  __init__.py        package marker, explains the lazy-celery contract
  celery_app.py      Celery application factory, queues, routes, time limits
  tasks.py           Task bodies (pure functions) + registry + celery binding
  beat_schedule.py   Periodic schedule as data, with env overrides
backend/src/services/
  jobs_services.py   enqueue(kind, payload) -> job_id, status(job_id) -> record
backend/requirements_platform.txt
  celery, redis, boto3 - optional, pinned, separately installed
```

## Three invariants

**1. Optional dependencies are imported lazily, inside the factory.**
`redis`, `celery` and `boto3` are imported at call time, never at module import
time. An image built from `requirements.txt` alone can still `import src.infra.storage`,
still serve uploads, and still pass the test suite. That is not a stylistic
preference: the backend container is built from `requirements.txt`, and a module-level
`import boto3` would turn an optional feature into a hard build failure.

**2. A flag that is off must reproduce today's behaviour exactly.**
`CACHE__BACKEND=memory` is a process-local dict - the same "no shared cache" semantics
the API has today. `STORAGE__BACKEND=local` writes to `DATA_PATH` with the same key
layout, the same file names and the same bytes as
`src/utils/write_file_into_server.py`. `MAIL__ENABLED=false` sends nothing.
`CELERY__ENABLED=false` runs jobs inline. Turning every flag off is not a degraded
mode; it is the previous release.

**3. A neighbour's failure is a normal state, not an incident.**
Mirroring `docs/SERVICE_CONTRACTS.md` in GPTHub: the cache, the storage backend, the
mailer and the queue each fail open or closed **explicitly**, and the choice is
documented per component below. Nothing is swallowed silently - every degradation
writes one structured log line with `status=degraded` or `status=skipped`.

## Env flag matrix

None of these keys exist in `docker/.env.example` yet; the docker owner adds them when
the flag is wired into a service. **Every one has a code default, so an absent key is
never an error** - the code reads with `_env(...)` helpers that fall back, deliberately
avoiding `backend/env.py`, whose `__getattr__` raises `AttributeError` for a missing
key. Both the nested form (`CACHE__BACKEND`, the manifest convention) and the flat
form (`CACHE_BACKEND`, what compose injects once) are accepted.

### Cache

| Key | Default | Meaning |
|---|---|---|
| `CACHE__BACKEND` | `memory` | `memory` \| `redis` \| `noop` |
| `CACHE__PREFIX` | `hiresnap` | First key segment; separates tenants in shared db 1 |
| `CACHE__TTL` | `300` | Default TTL in seconds |
| `CACHE__REDIS_URL` | `redis://localhost:6379/1` | Only read when the backend is `redis` |
| `CACHE__TIMEOUT` | `0.5` | Socket timeout in seconds |
| `CACHE__RETRY_BACKOFF` | `30` | Seconds to stop retrying after a transport failure |
| `CACHE__MAX_ENTRIES` | `2048` | LRU cap for the memory backend |

### Storage

| Key | Default | Meaning |
|---|---|---|
| `STORAGE__BACKEND` | `local` | `local` \| `s3` |
| `STORAGE__BUCKET` | `hiresnap-uploads` | Bucket name |
| `STORAGE__ENDPOINT` | *(empty)* | e.g. `http://storage:9000` (MinIO) |
| `STORAGE__ACCESS_KEY` / `STORAGE__SECRET_KEY` | *(empty)* | Credentials |
| `STORAGE__REGION` | `us-east-1` | Signing region |
| `STORAGE__SECURE` | `false` | `true` for HTTPS endpoints |
| `STORAGE__PREFIX` | *(empty)* | Optional key prefix inside the bucket |
| `STORAGE__PRESIGN_TTL` | `3600` | Presigned URL lifetime in seconds |
| `DATA_PATH` | `backend/data` | Root for the local backend; already a documented key |

### Mail

| Key | Default | Meaning |
|---|---|---|
| `MAIL__ENABLED` | `false` | Master switch |
| `MAIL__BACKEND` | `file` | `file` \| `smtp` |
| `MAIL__FILE_PATH` | `mail_outbox.log` | Where the file backend appends |
| `MAIL__HOST` / `MAIL__PORT` | *(empty)* / `587` | SMTP endpoint |
| `MAIL__USER` / `MAIL__PASSWORD` | *(empty)* | SMTP credentials |
| `MAIL__FROM` | `no-reply@hiresnap.local` | Envelope sender |
| `MAIL__STARTTLS` | `true` | Upgrade with STARTTLS |
| `MAIL__SSL` | `false` | Use SMTP-over-SSL instead (port 465) |
| `MAIL__TIMEOUT` | `10` | Socket timeout in seconds |

### Celery

| Key | Default | Meaning |
|---|---|---|
| `CELERY__ENABLED` | `false` | `true` sends work to the broker; `false` runs inline |
| `CELERY__BROKER_URL` | `redis://localhost:6379/1` | Broker |
| `CELERY__RESULT_BACKEND` | `redis://localhost:6379/1` | Results |
| `CELERY__CLEANUP_INTERVAL_SECONDS` | `3600` | Beat cadence override |
| `CELERY__REINDEX_INTERVAL_SECONDS` | `86400` | Beat cadence override |

## Queues

Three queues, three failure domains. A queue is not a priority lane: it is the set of
work that may be delayed without delaying anything else. `docker/docker-compose.yaml`
starts one worker per queue with `--queues=<name>`.

| Queue | Tasks | Concurrency | Why it is separate |
|---|---|---|---|
| `agents` | `hiresnap.send_email` | 4 | Fast, user-visible, must never queue behind an embedding |
| `maintenance` | `hiresnap.cleanup_orphan_uploads`, `hiresnap.health` | 1 | Scans the whole storage tree; runs hourly and nobody waits on it |
| `ml` | `hiresnap.embed_candidate`, `hiresnap.reindex_embeddings` | 1-2 | Minutes of GPU per task; the only queue that may need a GPU device |

Time limits: default soft 300 s / hard 330 s; the ML tasks get 1800 s / 1860 s. The gap
between soft and hard is deliberate - the soft limit raises an exception inside the task
so it can record a failure, and the hard limit is the backstop for a task that ignores it.

`task_acks_late=True` with `task_reject_on_worker_lost=True` means a worker killed
mid-task has its message redelivered instead of silently dropped. That is only safe
because **every task body is idempotent**:

* `embed_candidate` derives its object key from `(candidate_id, file_key, model_version)`,
  so a redelivery overwrites the same bytes at the same key instead of orphaning a copy.
* `cleanup_orphan_uploads` only deletes objects that no row references, and deletes
  nothing at all when the reference list cannot be read.
* `reindex_embeddings` only enqueues uploads that have no embedding yet.
* `send_email` is the exception: SMTP cannot deduplicate, so it is at-most-once by
  construction and declares no retry.

## The job contract

```python
from src.services import jobs_services

job_id = jobs_services.enqueue("embed_candidate",
                               {"candidate_id": "c1", "file_key": "files/a.mp4"})
jobs_services.status(job_id)
# {"id": "...", "kind": "embed_candidate", "state": "succeeded",
#  "created_at": "2026-11-07T18:00:00Z", "result_url": None, "error": None, ...}
```

States: `queued -> running -> succeeded | failed`. Nothing else, and `status()` returns
`None` for an id it has never seen - "unknown" and "still queued" must not look the same
to a polling client.

With `CELERY__ENABLED=false` the task runs **inline and synchronously** inside
`enqueue()`, so the contract holds with no broker. That is what makes it testable and
what keeps a laptop runnable. With the flag on, `enqueue()` publishes to the broker and
`status()` refreshes from the task result; if the publish itself fails (celery not
installed, broker down) the job falls back to inline execution and records
`fallback_reason` - losing the work is worse than a slow response, and the log line
names the misconfiguration.

Job state is stored through the cache facade in namespace `jobs` with a 24 h TTL. With
`CACHE__BACKEND=memory` that state is **per process**: a job enqueued by the API process
is not visible to a worker process. Enable redis before enabling celery, or a job id
handed to the client will read as `None` from another worker.

## Enabling each component

### Redis cache

1. Start the `cache` profile: `./docker/run.sh --dev up` with `CACHE=1`.
2. Set `CACHE__BACKEND=redis` (and `CACHE__REDIS_URL=redis://cache:6379/1` when the
   service name differs from the default).
3. Verify: the worker `hiresnap.health` task reports `checks.cache.ok`.

The compose file deliberately does **not** make the backend depend on `cache`, so
`CACHE=0` stays startable. If redis is unreachable the facade degrades: every read is a
miss, every write returns `False`, one warning is logged, and the API keeps answering.

### S3 / MinIO object storage

1. Start the `storage` profile (`STORAGE=1`) and set `STORAGE__ACCESS_KEY` /
   `STORAGE__SECRET_KEY` - the compose file uses `:${VAR:?}` rather than a default,
   because a defaulted MinIO password publishes an object store with a known credential.
2. Set `STORAGE__BACKEND=s3`, `STORAGE__ENDPOINT=http://storage:9000`,
   `STORAGE__BUCKET=hiresnap-uploads`.
3. Install `backend/requirements_platform.txt` in the image that runs with S3.
4. Create the bucket once: `get_storage().ensure_bucket()` on startup or
   `mc mb` in the runbook.

Local semantics are byte-identical to today: `files/<uuid>.<ext>` and
`embeddings/<namespace>/<uuid>.npy` under `DATA_PATH`, served by the `/data` static
mount. Switch back with `STORAGE__BACKEND=local` and the existing URLs keep working.

### Mail

Set `MAIL__ENABLED=true` and leave `MAIL__BACKEND=file` for development: messages are
appended to `mail_outbox.log` where a developer can read the reset link. For real
delivery set `MAIL__BACKEND=smtp` plus `MAIL__HOST`; with GPTHub's mailpit service that
is `mailpit:1025` and no credentials.

Request paths must call `send_now_or_later(...)`, never `send_now(...)`. The helper runs
the send in a daemon thread and swallows every error: a user must not get a 500 for an
action that already succeeded because the mail relay is down.

### Celery workers and beat

Only after redis is enabled:

1. Install `requirements_platform.txt` in the image that runs workers.
2. Set `CELERY__ENABLED=true` and `CELERY__BROKER_URL=redis://cache:6379/1`.
3. Start one worker per queue (compose profiles), each with a `stop_grace_period`
   of at least 90 s: `task_acks_late` only helps if SIGTERM arrives before Docker's
   default 10 s stop timeout escalates to SIGKILL.
4. Start beat - exactly one instance. Two beats double every periodic run.

```sh
celery -A src.workers.celery_app worker --loglevel=info --concurrency=4 --queues=agents
celery -A src.workers.celery_app worker --loglevel=info --concurrency=1 --queues=maintenance
celery -A src.workers.celery_app worker --loglevel=info --concurrency=1 --queues=ml
celery -A src.workers.celery_app beat   --loglevel=info
```

`src/workers/celery_app.py` exposes the application through a module-level
`__getattr__`, so `-A src.workers.celery_app` works without an import-time
`Celery(...)` call.

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
cache fails open and storage fails closed, and both say so in the log.

## Health

* The API's `/health` stays cheap: it reports the configured backends from
  `cache_health()`, `storage_health()` and `mail_health()` and performs **no network
  round trip**. A gateway polls it on a timer; probing S3 there would make a storage
  hiccup look like a dead API.
* The worker's `hiresnap.health` task does probe for real, because its job is to answer
  "can this worker actually work". It reports `checks.{cache,storage,mail}` and flips
  `status` to `failed` when any of them is not ok.

## Tests

```sh
cd backend && python -m pytest tests -q
```

`tests/test_infra_fallbacks.py` proves the degradation paths for all three facades;
`tests/test_jobs_services.py` proves the job contract including the inline and
degraded-enqueue paths; `tests/test_worker_routing.py` proves the queue topology, the
beat schedule and that importing the modules needs neither celery nor a broker. None of
them touch the network, redis, MySQL or torch.

## Why the schedule lives in code, not in the database

`src/workers/beat_schedule.py` returns a plain dict. A periodic job is part of the
program, exactly like a route: if it were a row in MySQL, every environment would need
the row inserted by hand, a renamed task would leave a stale row pointing at nothing, and
rolling back the image would not roll back the schedule. Code keeps it reviewable in the
same diff as the task it triggers and identical in dev and prod. The "operators want to
change the interval without a deploy" objection is answered by the
`CELERY__CLEANUP_INTERVAL_SECONDS` / `CELERY__REINDEX_INTERVAL_SECONDS` env overrides.
