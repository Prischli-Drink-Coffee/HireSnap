"""Job submission and status: one contract, two execution modes.

WHY this module exists: today every long operation runs inside the request
(upload -> transcode -> embed), so the browser waits and a timeout loses the
work. The platform layer turns those operations into jobs with a stable id, but
it must not force a broker on anyone: when CELERY__ENABLED is false the very
same task runs inline, synchronously, and the contract still holds - enqueue
returns an id, status returns the record. That is what makes the feature
testable on a laptop with no redis, and it is why the fallback is inline
execution rather than an error.

The contract (mirrored in backend/docs/PLATFORM_LAYER.md):

    enqueue(kind, payload) -> job_id: str
    status(job_id)         -> {"id", "kind", "state", "created_at",
                               "result_url", "error", ...} or None

State machine: queued -> running -> succeeded | failed. Nothing else. A job that
was never enqueued returns None rather than inventing a state, because "unknown
id" and "still queued" must not look identical to a polling client.

Where the state lives: the cache facade (src/infra/cache.py), namespace "jobs".
With CACHE__BACKEND=memory - the default - job state is per-process, which is
correct for the single uvicorn worker the stack runs today and honest about what
it cannot do: a job enqueued in the API process is not visible in a worker
process. Enabling redis db 1 fixes that, and the documentation says so rather
than pretending the in-memory mode is distributed.

Business data stays in MySQL. A job record is operational state: it is
disposable, it expires, and losing it costs a status lookup, never a user's data.
"""

from __future__ import annotations

import logging
import os
import time
import uuid

logger = logging.getLogger(__name__)

NAMESPACE = "jobs"
JOB_TTL_SECONDS = 86400
KEY_PREFIX = "job:"

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"

# Public kind -> (celery task name, task body). The kind is the name the API
# accepts; the task name is what the worker knows. Keeping both here means a
# rename in tasks.py cannot silently break an endpoint that enqueues "embed".
KINDS = {
    "embed_candidate": "hiresnap.embed_candidate",
    "reindex_embeddings": "hiresnap.reindex_embeddings",
    "cleanup_orphan_uploads": "hiresnap.cleanup_orphan_uploads",
    "send_email": "hiresnap.send_email",
}

# Celery's state names are its own vocabulary; mapping them here keeps the rest
# of the application speaking our four states only.
_CELERY_STATES = {
    "PENDING": STATE_QUEUED,
    "RECEIVED": STATE_QUEUED,
    "RETRY": STATE_QUEUED,
    "STARTED": STATE_RUNNING,
    "SUCCESS": STATE_SUCCEEDED,
    "FAILURE": STATE_FAILED,
    "REVOKED": STATE_FAILED,
}

MAX_ERROR_CHARS = 1000
MAX_PAYLOAD_CHARS = 2000


def _env(*names, default=""):
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cache():
    """The cache facade, imported late so this module stays import-cheap."""
    from src.infra.cache import get_cache
    return get_cache()


def _key(job_id):
    return "%s%s" % (KEY_PREFIX, job_id)


def _truncate(value, limit):
    text = str(value if value is not None else "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def supported_kinds():
    """Kinds accepted by enqueue(), in a stable order (for an API endpoint)."""
    return sorted(KINDS)


def _serialise_payload(payload):
    """Store a bounded copy of the payload: enough to debug, not a data leak.

    A payload can hold a candidate's name or phone number. Job state is
    operational and lives in redis, so only the shape and a clipped preview are
    kept - never the whole record.
    """
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return {"_repr": _truncate(repr(payload), 200)}
    preview = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            preview[key] = _truncate(value, 200)
        else:
            preview[key] = _truncate(repr(value), 100)
    return {"keys": sorted(payload.keys()), "preview": _truncate(preview, MAX_PAYLOAD_CHARS)}


def _record(job_id, kind, state, **extra):
    record = {
        "id": job_id,
        "kind": kind,
        "state": state,
        "created_at": extra.pop("created_at", None) or _utc_now(),
        "updated_at": _utc_now(),
        "result_url": extra.pop("result_url", None),
        "error": extra.pop("error", None),
    }
    record.update(extra)
    return record


def _save(record):
    try:
        _cache().set(_key(record["id"]), record, ttl=JOB_TTL_SECONDS, namespace=NAMESPACE)
    except Exception as exc:  # the facade already swallows; this is belt and braces
        logger.warning("jobs status=degraded code=save_failed job_id=%s error=%s", record.get("id"), exc)
    return record


def _load(job_id):
    try:
        return _cache().get(_key(job_id), None, namespace=NAMESPACE)
    except Exception as exc:
        logger.warning("jobs status=degraded code=load_failed job_id=%s error=%s", job_id, exc)
        return None


def celery_enabled():
    """True when a broker is configured; False means inline execution."""
    try:
        from src.workers.celery_app import is_configured
    except Exception:
        return False
    try:
        return bool(is_configured())
    except Exception:
        return False


def enqueue(kind, payload=None, run_inline=None):
    """Submit a job and return its id.

    run_inline overrides the mode: True forces synchronous execution (tests,
    one-off scripts, an operator debugging a broken worker), False forces the
    broker. By default the mode follows CELERY__ENABLED.

    An unknown kind raises ValueError: that is a programming error in the caller,
    not an operational condition, and silently queueing nothing would hide it.
    A task that fails, by contrast, is recorded as a failed job - the submitter
    already received an id and must be able to see the outcome.
    """
    if kind not in KINDS:
        raise ValueError("unknown job kind %r; supported: %s" % (kind, ", ".join(supported_kinds())))
    payload = dict(payload or {})

    job_id = uuid.uuid4().hex
    record = _record(job_id, kind, STATE_QUEUED, payload_summary=_serialise_payload(payload))
    _save(record)

    inline = (not celery_enabled()) if run_inline is None else bool(run_inline)
    if inline:
        return _run_inline(record, payload)

    try:
        from src.workers.celery_app import get_celery_app
        async_result = get_celery_app().send_task(KINDS[kind], kwargs=payload)
        record["task_id"] = getattr(async_result, "id", None)
        record["mode"] = "celery"
        _save(record)
        logger.info("jobs status=queued job_id=%s kind=%s task_id=%s", job_id, kind, record["task_id"])
    except Exception as exc:
        # The broker refused or celery is missing despite the flag. Do not lose
        # the work: run it here, and record why the queue path was skipped so the
        # operator sees a misconfiguration rather than a mystery.
        logger.warning(
            "jobs status=degraded job_id=%s kind=%s code=enqueue_failed error=%s fallback=inline",
            job_id, kind, exc,
        )
        record["fallback_reason"] = _truncate(exc, 300)
        return _run_inline(record, payload)
    return job_id


def _run_inline(record, payload):
    """Execute the task in this process and record the outcome."""
    from src.workers.tasks import get_task

    record["state"] = STATE_RUNNING
    record["mode"] = "inline"
    _save(record)
    logger.info("jobs status=running job_id=%s kind=%s mode=inline", record["id"], record["kind"])
    try:
        result = get_task(KINDS[record["kind"]])(**payload)
    except Exception as exc:
        record["state"] = STATE_FAILED
        record["error"] = _truncate("%s: %s" % (type(exc).__name__, exc), MAX_ERROR_CHARS)
        _save(record)
        logger.warning("jobs status=failed job_id=%s kind=%s error=%s", record["id"], record["kind"], record["error"])
        return record["id"]

    record["state"] = STATE_SUCCEEDED
    record["result"] = _summarise_result(result)
    record["result_url"] = _extract_result_url(result)
    _save(record)
    logger.info("jobs status=succeeded job_id=%s kind=%s", record["id"], record["kind"])
    return record["id"]


def _summarise_result(result):
    """Keep a small, JSON-safe summary; the full result may hold a numpy array."""
    if isinstance(result, dict):
        return {key: value for key, value in result.items()
                if isinstance(value, (str, int, float, bool)) or value is None}
    if isinstance(result, (str, int, float, bool)) or result is None:
        return {"value": result}
    return {"type": type(result).__name__}


def _extract_result_url(result):
    if isinstance(result, dict):
        for key in ("result_url", "url", "public_url"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def status(job_id):
    """Return the job record, or None when the id is unknown or expired.

    "Unknown" is a real answer: with the in-memory backend a job enqueued by
    another process is invisible here, and returning a fabricated "queued" would
    make a polling client wait forever.
    """
    if not job_id:
        return None
    record = _load(job_id)
    if record is None:
        return None
    if not celery_enabled() or not record.get("task_id"):
        return record
    # Broker mode: the authoritative state is the worker's, not the row we wrote
    # at submission time. Refreshing keeps the record accurate for a client that
    # polls after the worker already finished.
    refreshed = _refresh_from_celery(record)
    return refreshed


def _refresh_from_celery(record):
    try:
        from celery.result import AsyncResult
        from src.workers.celery_app import get_celery_app
        async_result = AsyncResult(record["task_id"], app=get_celery_app())
        raw_state = str(async_result.state or "").upper()
    except Exception as exc:
        logger.debug("jobs status=degraded code=celery_state_unavailable error=%s", exc)
        return record

    mapped = _CELERY_STATES.get(raw_state)
    if mapped is None or mapped == record.get("state"):
        return record

    record["state"] = mapped
    record["updated_at"] = _utc_now()
    if mapped == STATE_SUCCEEDED:
        try:
            record["result"] = _summarise_result(async_result.result)
            record["result_url"] = _extract_result_url(async_result.result)
        except Exception:
            pass
    elif mapped == STATE_FAILED:
        try:
            record["error"] = _truncate("%s" % (async_result.result,), MAX_ERROR_CHARS)
        except Exception:
            record["error"] = "task failed"
    _save(record)
    return record


def cancel(job_id):
    """Best-effort revoke. Returns True when a cancel was requested.

    Inline jobs are already finished by the time enqueue returns, so there is
    nothing to revoke and this returns False - a truthful answer beats a fake
    success.
    """
    record = _load(job_id)
    if record is None:
        return False
    if record.get("mode") != "celery" or not record.get("task_id"):
        return False
    try:
        from src.workers.celery_app import get_celery_app
        get_celery_app().control.revoke(record["task_id"], terminate=False)
    except Exception as exc:
        logger.warning("jobs status=degraded job_id=%s code=revoke_failed error=%s", job_id, exc)
        return False
    record["state"] = STATE_FAILED
    record["error"] = "cancelled"
    _save(record)
    return True


def jobs_health():
    """Health payload for the job subsystem: reports the mode it is actually in."""
    inline_only = not celery_enabled()
    return {
        "mode": "inline" if inline_only else "celery",
        "store": getattr(_cache(), "backend_name", "unknown"),
        "ok": True,
        "kinds": supported_kinds(),
    }
