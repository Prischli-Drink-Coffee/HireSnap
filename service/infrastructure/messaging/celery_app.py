"""Celery application for HireSnap, built by a factory so the base image still runs.

WHY a factory instead of an import-time Celery(...) call (R:/GPTHub builds the
app at import time; we cannot, because celery is an OPTIONAL dependency here):
the API image must stay runnable with docker/.env unchanged and no worker
containers. If merely touching this module constructed a Celery app, then any
import of src.workers.* - including the inline fallback in
service/services/jobs_services.py - would need celery installed. So:

  * module level  = plain data only (queue names, routes, time limits)
  * get_celery_app() = the only place that imports celery, memoised
  * module __getattr__ exposes "app" / "celery" lazily, which is what
    "celery -A service.infrastructure.messaging.celery_app worker" needs to find the application.

Topology mirrors the GPTHub stack: three queues with different availability
requirements. A queue is not just a priority lane, it is a failure domain -
maintenance work that scans the storage tree must never delay a user's upload,
and ML work that holds a GPU for minutes must never delay either.

Operational state (broker, results, cache, job status) lives in redis db 1 and
never in MySQL, which stays the system of record for business data.
"""

from __future__ import annotations

import logging
import os
import re
import threading

from service.configuration.flags import read_flag

logger = logging.getLogger(__name__)

APP_NAME = "hiresnap"

# Queue names are part of the operating contract: docker/docker-compose.yaml
# starts one worker per queue with "--queues=<name>". Renaming one here without
# changing the compose command silently leaves tasks unconsumed.
QUEUE_AGENTS = "agents"
QUEUE_MAINTENANCE = "maintenance"
QUEUE_ML = "ml"
QUEUES = (QUEUE_AGENTS, QUEUE_MAINTENANCE, QUEUE_ML)

DEFAULT_BROKER_URL = "redis://localhost:6379/1"
DEFAULT_RESULT_BACKEND = "redis://localhost:6379/1"

# Time limits. The soft limit raises SoftTimeLimitExceeded inside the task so it
# can clean up and record a failure; the hard limit kills the worker process.
# There is a gap between them on purpose: a task that ignores the soft signal
# still gets SIGKILL rather than wedging the pool forever.
SOFT_TIME_LIMIT = 300
HARD_TIME_LIMIT = 330
# Embedding an hour of video legitimately takes minutes; the ML queue gets its
# own, longer budget rather than loosening the limit for everything.
ML_SOFT_TIME_LIMIT = 1800
ML_HARD_TIME_LIMIT = 1860

# Task routing. Keys are the task names registered in service/infrastructure/messaging/tasks.py; a
# name that does not exist is inert, so this table is also the readable contract
# of "which queue does what".
TASK_ROUTES = {
    "hiresnap.embed_candidate": {"queue": QUEUE_ML},
    "hiresnap.reindex_embeddings": {"queue": QUEUE_ML},
    "hiresnap.cleanup_orphan_uploads": {"queue": QUEUE_MAINTENANCE},
    "hiresnap.health": {"queue": QUEUE_MAINTENANCE},
    "hiresnap.send_email": {"queue": QUEUE_AGENTS},
}

# Per-task overrides applied by register_tasks(); kept here so the numbers are in
# one place next to the routing table that explains why they differ.
TASK_TIME_LIMITS = {
    "hiresnap.embed_candidate": (ML_SOFT_TIME_LIMIT, ML_HARD_TIME_LIMIT),
    "hiresnap.reindex_embeddings": (ML_SOFT_TIME_LIMIT, ML_HARD_TIME_LIMIT),
}

_APP = None
_APP_LOCK = threading.Lock()


def _env(*names, default=""):
    """First present environment name wins (see service/infrastructure/cache.py for the WHY)."""
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _bootstrap_env():
    """Load backend/.env once, best effort, so local runs honour the same flags."""
    if getattr(_bootstrap_env, "_done", False):
        return
    _bootstrap_env._done = True
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - bare install
        return
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(backend_dir, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path)
    except Exception:  # pragma: no cover - defensive
        logger.debug("celery: could not load backend/.env")


def redact_url(url):
    """Remove the password from a URL before it reaches a log line.

    Mirrors R:/GPTHub/backend/service/infrastructure/messaging/celery_app.py. A
    broker URL is the one string in this file that routinely carries a password,
    and it is exactly the string an operator pastes into a bug report.
    """
    return re.sub(r"//([^:/@]+)(:[^@/]*)?@", "//***:***@", str(url or ""))


def is_configured():
    """True when celery should be used instead of the inline fallback.

    Two independent conditions, both required: the operator switched it on
    (CELERY__ENABLED=true) and a broker URL exists. An enabled flag with no
    broker is a misconfiguration, not a reason to crash the API - it degrades to
    inline execution and logs once.
    """
    _bootstrap_env()
    # Strict: a typo here left the workers idle while the API did the work inline,
    # which is exactly the latency the celery profile was enabled to remove.
    return read_flag("CELERY__ENABLED", "CELERY_ENABLED", default=False)


def broker_url():
    _bootstrap_env()
    return _env("CELERY__BROKER_URL", "CELERY_BROKER_URL", default=DEFAULT_BROKER_URL)


def result_backend():
    _bootstrap_env()
    return _env("CELERY__RESULT_BACKEND", "CELERY_RESULT_BACKEND", default=DEFAULT_RESULT_BACKEND)


def build_celery_app():
    """Construct the Celery application. The only import of celery in the codebase."""
    from celery import Celery  # lazy on purpose: optional dependency

    from service.infrastructure.messaging.beat_schedule import build_beat_schedule

    app = Celery(
        APP_NAME,
        broker=broker_url(),
        backend=result_backend(),
        # Task modules are imported by the worker at startup. tasks.py registers
        # its functions through register_tasks(), which the worker calls below.
        include=["service.infrastructure.messaging.tasks"],
    )

    app.conf.update(
        # JSON only: a pickle-serialised task is remote code execution if the
        # broker is ever reachable by someone else, and this broker is on the
        # internal network but not necessarily isolated.
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        # Acknowledge AFTER the work is done: a worker killed mid-task (OOM, a
        # deploy) loses the acknowledgement, so the broker redelivers instead of
        # silently dropping the job.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # One task in flight per worker process. Prefetching a batch means a long
        # task hides the queue length and delays everything behind it.
        worker_prefetch_multiplier=1,
        worker_max_tasks_per_child=100,
        task_soft_time_limit=SOFT_TIME_LIMIT,
        task_time_limit=HARD_TIME_LIMIT,
        result_expires=86400,
        result_extended=True,
        task_default_retry_delay=60,
        task_max_retries=3,
        task_default_queue=QUEUE_AGENTS,
        task_routes=dict(TASK_ROUTES),
        broker_connection_retry_on_startup=True,
        # A worker must survive a redis restart: without a retry window it exits
        # at startup and the compose restart policy becomes a crash loop.
        broker_transport_options={"visibility_timeout": 3600},
        beat_schedule=build_beat_schedule(),
    )

    # Declare the queues explicitly so a worker started with --queues=ml binds a
    # real queue even before any task has been routed to it.
    from kombu import Queue

    app.conf.task_queues = tuple(Queue(name) for name in QUEUES)

    from service.infrastructure.messaging.tasks import register_tasks

    register_tasks(app)

    logger.info(
        "celery app=%s status=built broker=%s backend=%s queues=%s",
        APP_NAME,
        redact_url(broker_url()),
        redact_url(result_backend()),
        ",".join(QUEUES),
    )
    return app


def get_celery_app():
    """Memoised application. Raises ImportError when celery is not installed."""
    global _APP
    if _APP is not None:
        return _APP
    with _APP_LOCK:
        if _APP is None:
            _APP = build_celery_app()
    return _APP


def reset_celery_app():
    """Drop the memoised app (tests and configuration reloads only)."""
    global _APP
    with _APP_LOCK:
        _APP = None


def queue_for(task_name):
    """Queue a task name is routed to, or None when it is unrouted."""
    route = TASK_ROUTES.get(task_name)
    if isinstance(route, dict):
        return route.get("queue")
    return None


def __getattr__(name):
    """Expose the app lazily for "celery -A service.infrastructure.messaging.celery_app".

    Celery's CLI imports the module named after -A and then looks for an
    attribute holding an application instance. A module-level construction would
    defeat the whole point of this file, so the attribute is materialised on
    first access instead. Python 3.7+ module __getattr__ (PEP 562).
    """
    if name in ("app", "celery", "celery_app"):
        return get_celery_app()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
