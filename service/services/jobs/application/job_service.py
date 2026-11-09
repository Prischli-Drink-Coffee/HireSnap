"""Job submission and status: one contract, two execution modes.

WHAT MOVED: these functions used to live at `service/services/jobs_services.py`, a flat
module beside the other thirteen, and a caller reached them by module name. They are
methods of `JobService` now - one object the composition layer hands out - so a test can
substitute a fake queue instead of patching a module global. The functions themselves
are unchanged; only their home and their calling convention are.

WHY this module exists at all: today every long operation runs inside the request
(upload -> transcode -> embed), so the browser waits and a timeout loses the work. The
platform layer turns those operations into jobs with a stable id, but it must not force a
broker on anyone: when CELERY__ENABLED is false the very same task runs inline,
synchronously, and the contract still holds - enqueue returns an id, status returns the
record. That is what makes the feature testable on a laptop with no redis, and it is why
the fallback is inline execution rather than an error.

The contract (mirrored in docs/SERVICE_CONTRACTS.md):

    enqueue(kind, payload) -> job_id: str
    status(job_id)         -> {"id", "kind", "state", "created_at",
                               "result_url", "error", ...} or None

State machine: queued -> running -> succeeded | failed. Nothing else. A job that was
never enqueued returns None rather than inventing a state, because "unknown id" and
"still queued" must not look identical to a polling client.

Where the state lives: the cache facade (`service/infrastructure/cache.py`), namespace
"jobs". With CACHE__BACKEND=memory - the default - job state is per process, which is
correct for the single uvicorn worker the stack runs today and honest about what it
cannot do: a job enqueued in the API process is not visible in a worker process.
Enabling redis db 1 fixes that, and the documentation says so rather than pretending the
in-memory mode is distributed.

Business data stays in MySQL. A job record is operational state: it is disposable, it
expires, and losing it costs a status lookup, never a user's data.

WHY THERE IS A presentation/ LAYER NOW: there was not one, on purpose, until the job
machinery had a caller worth exposing (docs/KNOWN_GAPS.md section 27 kept the decision on
the record). `service/services/jobs/presentation/routers/jobs_api/` exposes submit, poll,
cancel and describe. The module-level functions below are unchanged for Python callers;
the two spellings and why they differ are documented on `JobService`.
"""

from __future__ import annotations

import logging
import os
import time
import uuid

from service.shared.errors import InvalidPayloadError, NotFoundError

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
    """
    The cache facade, resolved through the wiring root and imported late.

    WHY through service.composition.state rather than importing get_cache from
    service.infrastructure.cache directly: the composition layer is the one place that
    decides which object a caller gets, so a test replaces the store by resetting that
    state instead of monkeypatching this module. The import stays INSIDE the function
    because a module-level one would drag the cache and its environment reads into
    every importer of this module.
    """
    from service.composition import state

    return state.get_cache()


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
        logger.warning(
            "jobs status=degraded code=save_failed job_id=%s error=%s", record.get("id"), exc
        )
    return record


def _load(job_id):
    try:
        return _cache().get(_key(job_id), None, namespace=NAMESPACE)
    except Exception as exc:
        logger.warning("jobs status=degraded code=load_failed job_id=%s error=%s", job_id, exc)
        return None


def _queue():
    """The broker adapter, resolved through the composition layer.

    WHY NOT IMPORT CELERY HERE: the application layer may not name the broker
    (`backend/tests/test_service_boundary.py`, rule 3) - a rule that knows its queue
    cannot be tested without one. The adapter answers the same questions and degrades
    when celery is absent, which is the normal state of the API image.
    """
    from service.composition.state import get_dispatcher

    return get_dispatcher()


def celery_enabled():
    """True when a broker is configured; False means inline execution."""
    try:
        return bool(_queue().configured())
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
        raise ValueError(
            "unknown job kind %r; supported: %s" % (kind, ", ".join(supported_kinds()))
        )
    payload = dict(payload or {})

    job_id = uuid.uuid4().hex
    record = _record(job_id, kind, STATE_QUEUED, payload_summary=_serialise_payload(payload))
    _save(record)

    inline = (not celery_enabled()) if run_inline is None else bool(run_inline)
    if inline:
        return _run_inline(record, payload)

    accepted, task_id, reason = _queue().submit(KINDS[kind], payload)
    if accepted:
        record["task_id"] = task_id
        record["mode"] = "celery"
        _save(record)
        logger.info("jobs status=queued job_id=%s kind=%s task_id=%s", job_id, kind, task_id)
        return job_id

    # The broker refused or celery is missing despite the flag. Do not lose the work:
    # run it here, and record why the queue path was skipped so the operator sees a
    # misconfiguration rather than a mystery.
    logger.warning(
        "jobs status=degraded job_id=%s kind=%s code=enqueue_failed error=%s fallback=inline",
        job_id,
        kind,
        reason,
    )
    record["fallback_reason"] = _truncate(reason or "broker unavailable", 300)
    return _run_inline(record, payload)


def _run_inline(record, payload):
    """Execute the task in this process and record the outcome."""
    record["state"] = STATE_RUNNING
    record["mode"] = "inline"
    _save(record)
    logger.info("jobs status=running job_id=%s kind=%s mode=inline", record["id"], record["kind"])
    try:
        result = _queue().run(KINDS[record["kind"]], payload)
    except Exception as exc:
        record["state"] = STATE_FAILED
        record["error"] = _truncate("%s: %s" % (type(exc).__name__, exc), MAX_ERROR_CHARS)
        _save(record)
        logger.warning(
            "jobs status=failed job_id=%s kind=%s error=%s",
            record["id"],
            record["kind"],
            record["error"],
        )
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
        return {
            key: value
            for key, value in result.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
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
        observed = _queue().state(record["task_id"])
    except Exception as exc:
        logger.debug("jobs status=degraded code=celery_state_unavailable error=%s", exc)
        return record
    if observed is None:
        return record
    raw_state, result = observed

    mapped = _CELERY_STATES.get(raw_state)
    if mapped is None or mapped == record.get("state"):
        return record

    record["state"] = mapped
    record["updated_at"] = _utc_now()
    if mapped == STATE_SUCCEEDED:
        try:
            record["result"] = _summarise_result(result)
            record["result_url"] = _extract_result_url(result)
        except Exception:
            pass
    elif mapped == STATE_FAILED:
        try:
            record["error"] = _truncate("%s" % (result,), MAX_ERROR_CHARS)
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
        revoked = _queue().revoke(record["task_id"])
    except Exception as exc:
        logger.warning("jobs status=degraded job_id=%s code=revoke_failed error=%s", job_id, exc)
        return False
    if not revoked:
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


class JobService:
    """The job subsystem as one object: submit, inspect, cancel, report health.

    It holds no state of its own - the records live in the cache facade - so the instance
    exists to be the thing the composition layer hands out and a test can fake.

    TWO SPELLINGS ON PURPOSE. `enqueue`/`status` are the JobQueue port
    (service/shared/ports/__init__.py), which speaks ids and records because a worker calls
    it in Python. `submit`/`require`/`cancel_job` are the HTTP surface's spelling and they
    differ in exactly one respect: they raise a ServiceError when the caller is wrong,
    instead of returning None or a ValueError. A route that had to translate those itself
    would be repeating the rule in four places; the port keeps its old contract so nothing
    that already calls it changes.
    """

    # --- the JobQueue port (service/shared/ports/__init__.py) ---------------
    def enqueue(self, kind, payload=None, run_inline=None):
        """Submit a job and return its id. See the module docstring for the modes."""
        return enqueue(kind, payload, run_inline)

    def status(self, job_id):
        """The job record, or None when the id is unknown or expired."""
        return status(job_id)

    # --- the rest of the surface -------------------------------------------
    def submit(self, kind, payload=None, run_inline=None):
        """Submit a job and return {id, kind, state} - the answer an HTTP client gets.

        An unknown kind is the CLIENT's mistake here, so it raises InvalidPayloadError
        (400) rather than the ValueError `enqueue` raises for a Python caller, where the
        same condition is a bug in the program. The state is read back from the store
        instead of assumed, because an inline job has already finished by now.
        """
        if kind not in KINDS:
            raise InvalidPayloadError(
                "unknown job kind %r; supported: %s" % (kind, ", ".join(supported_kinds()))
            )
        job_id = enqueue(kind, payload, run_inline)
        record = status(job_id) or {}
        return {
            "id": job_id,
            "kind": kind,
            "state": record.get("state", STATE_QUEUED),
        }

    def require(self, job_id):
        """The record for job_id, or a 404 when the store does not know it.

        A separate method from `status` because the two answer different questions: the
        port asks "what is the record, if any", which a polling worker must be able to
        answer with None; the route asks "give me this record or tell the client it does
        not exist", and "not found" is a response rather than an absence.
        """
        record = status(job_id) if job_id else None
        if record is None:
            raise NotFoundError(
                "job %r not found; it was never submitted or it has expired" % (job_id,)
            )
        return record

    def cancel_job(self, job_id):
        """Ask the broker to revoke a job and report what actually happened.

        `cancel` answers a bool because Python callers only need "did it work". A client
        needs the same answer plus the reason, and it needs "I do not know this job" to be
        distinguishable from "this job already ran", so the route gets the pair.
        """
        if not job_id or status(job_id) is None:
            return {"id": job_id, "cancelled": False, "reason": "unknown job id"}
        if cancel(job_id):
            return {"id": job_id, "cancelled": True, "reason": None}
        record = status(job_id) or {}
        if record.get("mode") != "celery":
            return {
                "id": job_id,
                "cancelled": False,
                "reason": "the job ran inline; nothing to revoke",
            }
        return {
            "id": job_id,
            "cancelled": False,
            "reason": "the broker did not accept the revoke request",
        }

    def cancel(self, job_id):
        """Best-effort revoke; True only when a revoke was actually requested."""
        return cancel(job_id)

    def supported_kinds(self):
        return supported_kinds()

    def celery_enabled(self):
        return celery_enabled()

    def health(self):
        """The health payload, reporting the mode the subsystem is really in."""
        return jobs_health()
