"""jobs router, served by the jobs slice: submit, poll and cancel a background job.

WHAT THIS SURFACE IS FOR: until now the job machinery had no caller in the request path
(docs/KNOWN_GAPS.md sections 13 and 27). Everything that takes seconds - embedding a
candidate video, reindexing embeddings, sweeping orphan uploads, sending mail - either ran
inside the request or was not reachable at all. These four routes are that caller: POST to
submit, GET /jobs/{id} to poll, DELETE to ask the broker to revoke, and GET /jobs/ to learn
which mode the deployment is really in.

WHY THE PATHS KEEP THEIR LEADING SLASH: they are mounted under /server, so the public URLs
are /server/jobs/... and the gateway reaches them as /api/jobs/... - the same convention as
every other router in this package.

WHY "INLINE" IS A SUPPORTED ANSWER RATHER THAN A DEGRADED ONE: with CELERY__ENABLED=false
(the default on a laptop and in every deployment without redis) the identical task body runs
inside the POST, so the job has already finished when the response is written. The contract
still holds - you get an id, and polling it returns the outcome - which is what makes the
feature testable with no broker. The state is never fabricated: a job id the store does not
know is a 404, not "queued".

WHY AN UNKNOWN KIND IS A 400 AND NOT A 500: JobService.submit validates against the published
kind list before it touches the queue, so a caller learns it sent the wrong kind instead of
receiving "Internal Server Error" over a programming detail. The bare module-level enqueue()
keeps raising ValueError for Python callers, where it genuinely is a bug.

WHY THE MODELS LIVE IN THIS FILE RATHER THAN IN A schemas.py BESIDE IT: the layout checks
(backend/scripts/check_backend_layout.py rule 5 and backend/tests/test_backend_structure.py)
compare every module under a routers/ directory against the mount list, and only the helper
name "schemas" is excused - by the guard, not by the structure test. Two models do not
justify teaching the structure test a second exception, so they sit here, next to the routes
that use them.

WHY EVERY ROUTE REQUIRES AN ADMIN TOKEN: a submission runs a named task with a payload the
client supplies - embedding a file key, sending arbitrary mail - which no ordinary candidate
or employer account may do. access_level=1 means "verified token whose subject is admin"
(service/api/security.py).
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from service.api.security import JWTBearer
from service.composition.state import get_job_service
from service.configuration.logging import get_logger
from service.shared.errors import ServiceError

router = APIRouter()
log = get_logger()


class JobRequest(BaseModel):
    """A job to submit. The kind must be one of those GET /server/jobs/ reports."""

    kind: str = Field(..., description="Job kind; see the kinds list on GET /server/jobs/.")
    payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Task arguments, passed to the worker unchanged. Keep it small: only "
        "the shape and a clipped preview of it are stored with the job.",
    )


class JobAccepted(BaseModel):
    """What a submission returns: the id to poll, and the state the job is already in.

    A submission is NOT a promise that the work finished. With CELERY__ENABLED=false the
    task has already run inside the request, so state is usually "succeeded" or "failed" by
    the time this body is written; with a broker it is "queued". Poll
    GET /server/jobs/{id} until the state is terminal.
    """

    id: str = Field(..., description="Job id to poll.")
    kind: str = Field(..., description="The kind that was accepted.")
    state: str = Field(..., description="queued | running | succeeded | failed.")


class JobCancelled(BaseModel):
    """The answer to a revoke request, which is a request and not a guarantee.

    cancelled is what actually happened: a worker may have finished the task before the
    revoke arrived, and an inline job is finished by definition. When it is false, reason
    says why.
    """

    id: str = Field(..., description="The job the request named.")
    cancelled: bool = Field(..., description="True only when a revoke was actually issued.")
    reason: Optional[str] = Field(
        default=None, description="Why the revoke did not happen, when it did not."
    )


@router.get(
    "/jobs/",
    response_model=Dict,
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def describe_jobs(service=Depends(get_job_service)):
    """
    Состояние подсистемы задач: режим, хранилище состояния и поддерживаемые виды.

    Reports the mode the subsystem is REALLY in - "inline" when no broker is configured,
    "celery" when one is - together with the store behind the job records and the kinds
    that POST /server/jobs/ accepts.
    """
    try:
        return service.health()
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.post(
    "/jobs/",
    response_model=JobAccepted,
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def submit_job(job: JobRequest, service=Depends(get_job_service)):
    """
    Поставить задачу в очередь и получить её идентификатор.

    The body names the kind (see GET /server/jobs/) and carries the task arguments. With no
    broker the task has already run by the time this returns, so the state may be
    "succeeded" or "failed" rather than "queued" - poll GET /server/jobs/{id} for the record
    and its result.
    """
    try:
        return service.submit(job.kind, job.payload)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.get(
    "/jobs/{job_id}",
    response_model=Dict,
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def get_job(job_id: str, service=Depends(get_job_service)):
    """
    Состояние задачи по её идентификатору.

    Answers the stored record: queued | running | succeeded | failed, plus result_url and
    error when they exist. An id the store does not know - never submitted, or expired
    after 24 hours - is a 404 rather than a fabricated "queued", because a polling client
    that cannot tell the two apart waits forever.
    """
    try:
        return service.require(job_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex


@router.delete(
    "/jobs/{job_id}",
    response_model=JobCancelled,
    tags=["Main"],
    dependencies=[Depends(JWTBearer(access_level=1))],
)
async def cancel_job(job_id: str, service=Depends(get_job_service)):
    """
    Попросить брокер отозвать задачу.

    A revoke is a REQUEST, not a guarantee: the worker may already be running the task.
    cancelled therefore reports what actually happened, and reason says why not when it is
    false. A job that ran inline is already finished, so there is nothing to revoke.
    """
    try:
        return service.cancel_job(job_id)
    except ServiceError as ex:
        log.exception("Error", exc_info=ex)
        raise ex
