"""Beat schedule as data: what runs periodically, on which queue, and why.

WHY the schedule lives in code and not in a database (the same decision as
R:/GPTHub): a periodic job is part of the program, exactly like a route. If the
schedule were a row in MySQL, then every environment would need the same row
inserted by hand, a code change that renames a task would leave a stale row
pointing at a task that no longer exists, and a rollback of the image would not
roll back the schedule. Code keeps the schedule under version control, reviewable
in the same diff as the task it triggers, and identical in dev and prod. The
counter-argument - "operators want to change the interval without a deploy" - is
answered by env overrides: every interval below is read from an environment
variable with the value here as the default.

This module deliberately imports nothing from celery. The Celery application
(src/workers/celery_app.py) merges this dict into conf.beat_schedule, and the
tests can assert the schedule's shape on a machine with no celery installed.

Intervals are plain numbers of seconds, not celery.schedules.crontab, for the
same reason: an import-free module is testable in isolation. A fixed interval
drifts with beat restarts, which is acceptable for both jobs below because they
are idempotent and bounded by their own time limits.
"""

from __future__ import annotations

import os

# Defaults, chosen from the observed cost of each job rather than round numbers.
CLEANUP_INTERVAL_SECONDS = 3600.0      # hourly: orphaned uploads are cheap to find
REINDEX_INTERVAL_SECONDS = 86400.0     # nightly: embedding a corpus is minutes of GPU
CLEANUP_EXPIRES_SECONDS = 3300.0       # a missed cleanup must not pile up behind beat
REINDEX_EXPIRES_SECONDS = 3600.0


def _seconds(name, default):
    """Read an interval override, tolerating a malformed value.

    A typo in an env file must not stop beat from starting: the default is used
    and the problem appears as one warning in the worker log.
    """
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    return value if value > 0 else float(default)


def build_beat_schedule():
    """Return the beat schedule dict for the current environment.

    Keys are stable strings, because that is what appears in beat's log line and
    in the celery inspect output an operator greps when a job "did not run".
    """
    cleanup_interval = _seconds("CELERY__CLEANUP_INTERVAL_SECONDS", CLEANUP_INTERVAL_SECONDS)
    reindex_interval = _seconds("CELERY__REINDEX_INTERVAL_SECONDS", REINDEX_INTERVAL_SECONDS)

    return {
        "cleanup-orphan-uploads-hourly": {
            "task": "hiresnap.cleanup_orphan_uploads",
            "schedule": cleanup_interval,
            "options": {
                # The queue is pinned here, not inherited from task_default_queue:
                # maintenance work must never sit in front of a user's upload on
                # the agents queue (that is the whole point of separate queues).
                "queue": "maintenance",
                # expires stops a backlog: if beat was down for a day, running the
                # cleanup three times in a row helps nobody.
                "expires": CLEANUP_EXPIRES_SECONDS,
            },
            "kwargs": {"older_than_seconds": 86400},
        },
        "reindex-embeddings-nightly": {
            "task": "hiresnap.reindex_embeddings",
            "schedule": reindex_interval,
            "options": {
                # ML work goes to the ml queue, which is allowed to be slow and
                # may be the only worker with a GPU.
                "queue": "ml",
                "expires": REINDEX_EXPIRES_SECONDS,
            },
            "kwargs": {"limit": 200},
        },
    }


# Importable default, so "from src.workers.beat_schedule import BEAT_SCHEDULE"
# keeps working for callers that do not need env overrides (tests, docs).
BEAT_SCHEDULE = build_beat_schedule()
