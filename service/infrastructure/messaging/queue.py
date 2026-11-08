"""The broker adapter: the ONLY place a business rule can reach celery.

WHY THIS EXISTS: `service/services/jobs/application/job_service.py` used to import
`service.infrastructure.messaging.celery_app` and `.tasks` directly, which the boundary test
(`backend/tests/test_service_boundary.py`, rule 3) forbids - and it is right to forbid it: a
rule that names the broker cannot be unit-tested without one, and cannot run in a process
where celery is not installed. Everything the job slice needs from the broker is expressed
here as five methods, and the slice asks `service.composition.state.get_job_queue()` for this
object instead of importing a module.

WHY `configured()`, `submit()`, `run()`, `state()` AND `revoke()` AND NOT MORE: they are
exactly the operations the four-state job machine performs (queue it, or run it here; then
look at where it got to; or ask for it to stop). A larger surface would leak the broker
vocabulary - task ids, ETA, chains - into the slice, which is what the adapter is for.

WHY EVERY METHOD DEGRADES INSTEAD OF RAISING: celery is an OPTIONAL dependency
(`backend/requirements-platform.txt`, not `requirements.txt`) and the API image is built
without it. `configured()` therefore answers False when the import fails, `submit()` answers
`(False, None, reason)` so the caller can fall back to inline execution with the reason
recorded, and `state()` answers None so a status lookup returns the row it already has
instead of an error. The one exception is `run()`: a task that raises must raise, because
the job record has to say "failed".
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from service.configuration.logging import get_logger

log = get_logger()


class CeleryDispatcher:
    """Thin, lazy, and honest about a missing celery."""

    def configured(self) -> bool:
        """True when a broker is configured AND celery is importable."""
        try:
            from service.infrastructure.messaging.celery_app import is_configured
        except Exception:
            return False
        try:
            return bool(is_configured())
        except Exception:
            return False

    def submit(
        self, task_name: str, kwargs: Optional[dict] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Send one task to the broker. -> (accepted, task_id, reason_when_rejected).

        Returning the reason instead of raising is deliberate: the caller runs the task
        inline on False and stores the reason on the job record, so an operator sees
        "broker unreachable" in the job status rather than a mystery queue.
        """
        try:
            from service.infrastructure.messaging.celery_app import get_celery_app

            async_result = get_celery_app().send_task(task_name, kwargs=dict(kwargs or {}))
        except Exception as exc:
            log.warning("queue status=degraded code=submit_failed task=%s error=%s", task_name, exc)
            return False, None, str(exc)
        return True, getattr(async_result, "id", None), None

    def run(self, task_name: str, kwargs: Optional[dict] = None) -> Any:
        """Execute a task body in this process. Raises whatever the task raises."""
        from service.infrastructure.messaging.tasks import get_task

        return get_task(task_name)(**dict(kwargs or {}))

    def state(self, task_id: str) -> Optional[Tuple[str, Any]]:
        """The broker's view of a task -> (raw_state, result), or None when unavailable."""
        try:
            from celery.result import AsyncResult

            from service.infrastructure.messaging.celery_app import get_celery_app

            async_result = AsyncResult(task_id, app=get_celery_app())
            return str(async_result.state or "").upper(), async_result.result
        except Exception as exc:
            log.debug(
                "queue status=degraded code=state_unavailable task_id=%s error=%s", task_id, exc
            )
            return None

    def revoke(self, task_id: str) -> bool:
        """Ask the broker to stop a task. False when that was not possible."""
        try:
            from service.infrastructure.messaging.celery_app import get_celery_app

            get_celery_app().control.revoke(task_id, terminate=False)
        except Exception as exc:
            log.warning(
                "queue status=degraded code=revoke_failed task_id=%s error=%s", task_id, exc
            )
            return False
        return True


def build_job_queue() -> CeleryDispatcher:
    """The instance the composition layer hands out. Cheap: it holds nothing."""
    return CeleryDispatcher()
