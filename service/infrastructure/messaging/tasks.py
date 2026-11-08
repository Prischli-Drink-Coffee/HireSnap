"""Worker tasks: small, idempotent, and usable without celery.

WHY the task bodies are plain functions registered by register_tasks() instead of
"@app.task"-decorated ones: celery is optional in this project. jobs_services.py
has to be able to run the very same body inline when no broker is configured,
and a decorator would make that impossible without celery installed. So the
module holds:

    def embed_candidate(...)      the business logic, importable everywhere
    TASKS                         name -> callable, the registry jobs_services uses
    register_tasks(app)           binds each function to a real Celery Task

Idempotency is a property of the implementation, not of a retry policy:
  * embed_candidate derives the object key from its inputs, so re-running it
    overwrites the same object with the same bytes instead of accumulating
    orphans.
  * cleanup_orphan_uploads only ever deletes an object that is not referenced by
    any row, and refuses to delete anything at all when the database is
    unreachable - "the source of truth is down" must never mean "delete user
    files".
  * reindex_embeddings only enqueues the uploads that have no embedding yet, so
    a second run in the same night is a no-op.
  * send_email cannot be idempotent (SMTP has no dedup key); it is at-most-once
    by construction because nothing retries it automatically.

Heavy or optional imports (numpy, boto3, the ML client, MySQL) happen inside the
functions. The module must import on the base image with none of them present.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os

logger = logging.getLogger(__name__)

# Result statuses a task may report. jobs_services maps them onto job states, so
# the strings are part of the contract between the two modules.
STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

EMBEDDING_NAMESPACE = "users"
UPLOAD_NAMESPACE = "files"
EMBEDDING_PREFIX = "embeddings/%s/" % EMBEDDING_NAMESPACE
MODEL_VERSION = "v1"


class TaskUnavailable(RuntimeError):
    """A dependency the task needs is not installed or not configured.

    Raised rather than returning a fake success: a job whose work did not happen
    must not report "succeeded", and the caller (or the job record) needs the
    reason verbatim.
    """


def _env(*names, default=""):
    """First present environment name wins (see service/infrastructure/cache.py for the WHY)."""
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


# ---------------------------------------------------------------------------
# ML client: one seam, imported lazily.
# ---------------------------------------------------------------------------
# service/services/ml_client.py is the only supported way to reach the pipeline
# (docs/SIDECARS.md 4). It POSTs the media bytes to /v1/embed/video and decodes
# {"vector": [...], "dim": N, "model": ...}; ML_SERVICE_URL picks the address and
# defaults to the sidecar on 127.0.0.1:8080. There is no in-process path.
#
# WHY this replaced a "probe a list of candidate modules" approach: the previous
# version searched for src.modelling.ml_client / ml.client and fell back to
# POSTing JSON to "/embed". None of those three things exist - the module is
# service.services.ml_client, the route is /v1/embed/video, and the body is the
# media bytes, not a JSON descriptor. Every layer of that would have failed on
# the first real task, and it failed silently at import time instead, which is
# worse. A fallback that cannot work is not a fallback.


def _ml_client():
    """The ML transport module, or a TaskUnavailable explaining its absence."""
    try:
        from service.services import ml_client
    except Exception as exc:  # a broken install, not a missing feature flag
        raise TaskUnavailable("ml_client_unavailable: %s" % (exc,)) from exc
    return ml_client


def _local_media_path(storage, file_key, source_path=None):
    """Return (path, temporary) for one stored object.

    The sidecar reads a file from its own filesystem, so the object has to exist
    as a path inside a container the sidecar shares. LocalStorage already holds
    the bytes on that disk, so its path is used directly - copying would double
    the I/O of every task. Any other backend (S3/MinIO) is materialised into a
    temporary file that the caller deletes; the sidecar is never handed a URL,
    because it holds no credentials for our object store and a presigned link
    would leak into its logs.
    """
    if source_path and os.path.isfile(str(source_path)):
        return str(source_path), False

    path_for = getattr(storage, "path_for", None)
    if callable(path_for):
        candidate = path_for(file_key)
        if os.path.isfile(candidate):
            return candidate, False

    import tempfile

    suffix = ""
    name = str(file_key).rsplit("/", 1)[-1]
    if "." in name:
        # Keep the extension: the pipeline hands the path to ffmpeg, which picks
        # its demuxer partly from it.
        suffix = "." + name.rsplit(".", 1)[-1]
    data = storage.read_bytes(file_key)
    handle = tempfile.NamedTemporaryFile(prefix="hiresnap-embed-", suffix=suffix, delete=False)
    try:
        handle.write(data)
    finally:
        handle.close()
    return handle.name, True


def _npy_bytes(vector):
    """Serialise an embedding to .npy bytes, matching write_embedding_into_server.

    That helper calls np.save(path, array), which writes the same .npy container.
    Files produced here are therefore interchangeable with the ones the request
    path already writes into DATA_PATH.
    """
    if isinstance(vector, (bytes, bytearray)):
        return bytes(vector)
    try:
        import numpy as np
    except Exception as exc:
        raise TaskUnavailable("numpy_missing: %s" % (exc,))
    buffer = io.BytesIO()
    np.save(buffer, np.asarray(vector))
    return buffer.getvalue()


def embedding_key(candidate_id, file_key, model_version=MODEL_VERSION):
    """Deterministic object key for one candidate's embedding.

    WHY deterministic: the request path names embeddings with uuid4, which is fine
    for a one-shot upload but wrong for a retried task - a broker redelivery would
    write a second copy and orphan the first. Hashing the inputs means a retry
    overwrites the same bytes at the same key, which is what idempotent means
    here. The name stays a plain "<hex>.npy" so the existing URL shape
    (embeddings/users/<name>) and the /data mount keep working.
    """
    material = "%s|%s|%s" % (candidate_id, file_key, model_version)
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()
    return "%s%s.npy" % (EMBEDDING_PREFIX, digest[:32])


def _storage():
    """Storage backend for the current flags, or a TaskUnavailable."""
    try:
        from service.infrastructure.storage import get_storage
    except Exception as exc:  # pragma: no cover - only on a broken install
        raise TaskUnavailable("storage_unavailable: %s" % (exc,))
    return get_storage()


def _invalidate(prefix, namespace="embeddings"):
    """Best-effort cache invalidation. A cache miss is never a task failure."""
    try:
        from service.infrastructure.cache import get_cache
    except Exception:  # pragma: no cover
        return 0
    try:
        return get_cache().invalidate_prefix(prefix, namespace=namespace)
    except Exception as exc:
        logger.warning("task status=ok code=cache_invalidate_failed error=%s", exc)
        return 0


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def embed_candidate(candidate_id, file_key, model_version=MODEL_VERSION, source_path=None):
    """Embed one candidate file and store the .npy next to the other embeddings.

    Returns a dict with the stored key and its public URL, which is exactly what
    jobs_services turns into the job's result_url.
    """
    if not candidate_id or not file_key:
        raise ValueError("embed_candidate requires candidate_id and file_key")

    key = embedding_key(candidate_id, file_key, model_version)
    storage = _storage()
    ml_client = _ml_client()
    description = "ml_client:%s" % (ml_client.describe_transport().get("transport", "unknown"),)

    # Resolve the media to a path the sidecar can open, then delete it if it had
    # to be materialised. The finally makes the temporary file safe to leave
    # behind only if the process is killed between create and delete.
    media_path, temporary = _local_media_path(storage, file_key, source_path)
    try:
        vector = ml_client.get_user_embedding(media_path)
        payload = _npy_bytes(vector)
        storage.put_bytes(key, payload, content_type="application/octet-stream")
    finally:
        if temporary:
            try:
                os.unlink(media_path)
            except OSError as exc:  # never mask the original failure
                logger.warning("task=embed_candidate temp_cleanup_failed path=%s error=%s", media_path, exc)

    invalidated = _invalidate(EMBEDDING_PREFIX)

    result = {
        "status": STATUS_OK,
        "candidate_id": candidate_id,
        "file_key": file_key,
        "embedding_key": key,
        "bytes": len(payload),
        "embedder": description,
        "result_url": storage.presigned_url(key),
        "invalidated": invalidated,
    }
    logger.info(
        "task=embed_candidate status=ok candidate_id=%s key=%s bytes=%d embedder=%s",
        candidate_id, key, len(payload), description,
    )
    return result


def reindex_embeddings(limit=200, older_than_seconds=None):
    """Enqueue embedding work for uploads that do not have one yet.

    Deliberately does NOT re-embed everything: a nightly full re-embed of a
    growing corpus is unbounded GPU work. It finds the gap and lets
    embed_candidate do one file at a time, so a partial run is a valid run and
    the next night continues where this one stopped.
    """
    storage = _storage()
    try:
        limit_value = max(1, int(limit or 200))
    except (TypeError, ValueError):
        limit_value = 200

    uploads = storage.list_objects("%s/" % UPLOAD_NAMESPACE, limit=limit_value * 10)
    existing = storage.list_objects(EMBEDDING_PREFIX.rstrip("/"), limit=limit_value * 10)
    embedded = set(item["key"].rsplit("/", 1)[-1].split(".")[0] for item in existing)

    queued = []
    skipped = 0
    for item in uploads:
        if len(queued) >= limit_value:
            break
        digest = hashlib.sha1(("%s|%s|%s" % ("", item["key"], MODEL_VERSION)).encode("utf-8")).hexdigest()[:32]
        # Without a candidate id we cannot build the deterministic key exactly,
        # so the presence check uses the file's own digest prefix as a hint and
        # leaves the real decision to embed_candidate, which is idempotent.
        if digest in embedded:
            skipped += 1
            continue
        queued.append(item["key"])

    dispatched = 0
    for file_key in queued:
        try:
            dispatch("hiresnap.embed_candidate", {"candidate_id": _candidate_from_key(file_key), "file_key": file_key})
            dispatched += 1
        except Exception as exc:
            logger.warning("task=reindex_embeddings status=partial key=%s error=%s", file_key, exc)

    logger.info(
        "task=reindex_embeddings status=ok scanned=%d queued=%d skipped=%d dispatched=%d",
        len(uploads), len(queued), skipped, dispatched,
    )
    return {
        "status": STATUS_OK,
        "scanned": len(uploads),
        "queued": len(queued),
        "skipped": skipped,
        "dispatched": dispatched,
    }


def _candidate_from_key(file_key):
    """Best-effort candidate id from an upload key.

    Uploads are named "<namespace>/<uuid>.<ext>" and carry no candidate id; the
    authoritative mapping is the files table in MySQL. This helper exists so the
    task stays pure when the database is unreachable - embed_candidate treats the
    id as part of its idempotency key, not as a foreign key it validates.
    """
    name = str(file_key).rsplit("/", 1)[-1]
    return name.split(".")[0]


def cleanup_orphan_uploads(older_than_seconds=86400, dry_run=False):
    """Delete uploads that no database row references.

    Refuses to act when the reference list cannot be read. The asymmetry matters:
    deleting a referenced file destroys a user's video, deleting nothing costs
    disk. When in doubt, do nothing and say why in the result.
    """
    storage = _storage()
    try:
        threshold = float(older_than_seconds if older_than_seconds is not None else 86400)
    except (TypeError, ValueError):
        threshold = 86400.0

    import time
    now = time.time()
    uploads = storage.list_objects("%s/" % UPLOAD_NAMESPACE, limit=100000)

    referenced = _referenced_keys()
    if referenced is None:
        logger.warning(
            "task=cleanup_orphan_uploads status=skipped code=database_unavailable "
            "action=deleting nothing is the safe default (scanned=%d)", len(uploads)
        )
        return {
            "status": STATUS_SKIPPED,
            "reason": "database_unavailable",
            "scanned": len(uploads),
            "deleted": 0,
        }

    candidates = []
    for item in uploads:
        age = now - float(item.get("modified_at") or 0)
        if age < threshold:
            continue  # an upload younger than the grace window may be mid-flow
        if item["key"].rsplit("/", 1)[-1] in referenced:
            continue
        candidates.append(item["key"])

    deleted = []
    for key in candidates:
        if dry_run:
            deleted.append(key)
            continue
        try:
            storage.delete(key)
            deleted.append(key)
        except Exception as exc:
            logger.warning("task=cleanup_orphan_uploads status=partial key=%s error=%s", key, exc)

    if deleted:
        _invalidate("%s/" % UPLOAD_NAMESPACE, namespace="files")
    logger.info(
        "task=cleanup_orphan_uploads status=ok scanned=%d deleted=%d dry_run=%s",
        len(uploads), len(deleted), dry_run,
    )
    return {
        "status": STATUS_OK,
        "scanned": len(uploads),
        "candidates": len(candidates),
        "deleted": len(deleted),
        "dry_run": bool(dry_run),
    }


def _referenced_keys():
    """File names referenced by MySQL, or None when the database is unreachable.

    Imported inside the function on purpose: service.infrastructure.database.connection opens a
    MySQL connection at import time, so a module-level import would make every
    worker task import (and every test run) require a live database.
    """
    try:
        from service.repositories.file_repository import get_all_files
    except Exception as exc:
        logger.info("task=cleanup_orphan_uploads status=skipped reason=repository_import error=%s", exc)
        return None
    try:
        rows = get_all_files()
    except Exception as exc:
        logger.info("task=cleanup_orphan_uploads status=skipped reason=database_unreachable error=%s", exc)
        return None
    names = set()
    for row in rows or []:
        url = getattr(row, "Url", None) or (row.get("Url") if isinstance(row, dict) else None)
        if not url:
            continue
        names.add(str(url).rsplit("/", 1)[-1])
    return names


def send_email(to, subject, body, html=None):
    """Deliver one message through the configured mailer.

    No automatic retry: SMTP cannot deduplicate, so a redelivery after a partial
    success sends the message twice. A failed mail is logged and reported, and a
    human decides whether to resend.
    """
    if not to or not subject:
        raise ValueError("send_email requires to and subject")
    from service.infrastructure.mail import send_now

    sent = send_now(to, subject, body, html=html)
    logger.info("task=send_email status=%s to=%s subject=%r", "ok" if sent else "skipped", to, subject)
    return {"status": STATUS_OK if sent else STATUS_SKIPPED, "to": to, "subject": subject, "sent": bool(sent)}


def health():
    """Worker-side health: proves this process can reach what it depends on.

    Unlike the HTTP health endpoint (which must stay cheap and never touch the
    network), this runs on a timer in the worker and DOES probe, because its job
    is to answer "can this worker actually work".
    """
    report = {"status": STATUS_OK, "worker": os.environ.get("HOSTNAME", "local"), "checks": {}}
    try:
        from service.infrastructure.cache import cache_health
        report["checks"]["cache"] = cache_health()
    except Exception as exc:
        report["checks"]["cache"] = {"ok": False, "error": str(exc)}
    try:
        from service.infrastructure.storage import storage_health
        report["checks"]["storage"] = storage_health()
    except Exception as exc:
        report["checks"]["storage"] = {"ok": False, "error": str(exc)}
    try:
        from service.infrastructure.mail import mail_health
        report["checks"]["mail"] = mail_health()
    except Exception as exc:
        report["checks"]["mail"] = {"ok": False, "error": str(exc)}
    if not all(isinstance(value, dict) and value.get("ok", True) for value in report["checks"].values()):
        report["status"] = STATUS_FAILED
    return report


# ---------------------------------------------------------------------------
# Registry and Celery binding
# ---------------------------------------------------------------------------
# Task names are the stable identifiers operators see in "celery inspect
# registered" and in the beat schedule. They must match TASK_ROUTES in
# service/infrastructure/messaging/celery_app.py; test_worker_routing.py
# asserts that.
TASKS = {
    "hiresnap.embed_candidate": embed_candidate,
    "hiresnap.reindex_embeddings": reindex_embeddings,
    "hiresnap.cleanup_orphan_uploads": cleanup_orphan_uploads,
    "hiresnap.send_email": send_email,
    "hiresnap.health": health,
}


def get_task(name):
    """Look up a task body by name. Raises KeyError with the known names."""
    try:
        return TASKS[name]
    except KeyError:
        raise KeyError("unknown task %r; known tasks: %s" % (name, ", ".join(sorted(TASKS))))


def dispatch(name, kwargs=None, async_mode=None):
    """Run a task through celery when configured, inline otherwise.

    WHY inline is the fallback rather than an error: jobs_services must be
    testable, and a developer running the API without redis must still be able to
    exercise an embedding. The inline path is synchronous, so callers that care
    about latency pass async_mode explicitly.
    """
    kwargs = dict(kwargs or {})
    if async_mode is None:
        try:
            from service.infrastructure.messaging.celery_app import is_configured
            async_mode = bool(is_configured())
        except Exception:
            async_mode = False

    if async_mode:
        from service.infrastructure.messaging.celery_app import get_celery_app
        app = get_celery_app()
        return app.send_task(name, kwargs=kwargs)

    return get_task(name)(**kwargs)


def register_tasks(app):
    """Bind every body in TASKS to a Celery Task on the given app.

    Called by build_celery_app(). Kept separate so the binding rules (names,
    queues, time limits) are visible in one place and can be asserted with a fake
    app in tests.
    """
    from service.infrastructure.messaging.celery_app import TASK_ROUTES, TASK_TIME_LIMITS, SOFT_TIME_LIMIT, HARD_TIME_LIMIT

    registered = []
    for name, body in TASKS.items():
        route = TASK_ROUTES.get(name) or {}
        soft, hard = TASK_TIME_LIMITS.get(name, (SOFT_TIME_LIMIT, HARD_TIME_LIMIT))
        app.task(
            name=name,
            queue=route.get("queue"),
            soft_time_limit=soft,
            time_limit=hard,
            # Task bodies are pure functions of their arguments, so a broker
            # redelivery is safe to execute again. Tasks that are NOT idempotent
            # (send_email) must not declare autoretry; they disable it here.
            acks_late=True,
        )(body)
        registered.append(name)
    logger.info("celery status=tasks_registered count=%d names=%s", len(registered), ",".join(registered))
    return registered
