"""The jobs HTTP surface: submit, poll, cancel, describe - driven end to end.

WHY THIS FILE EXISTS: docs/KNOWN_GAPS.md sections 13 and 27 recorded that the job machinery
had no caller in the request path, so nothing proved that a route, the JobService and the
queue adapter agree with each other. The unit tests around JobService (test_jobs_services.py)
call the module functions directly; these tests go through TestClient, so they also pin the
status codes, the response models and the fact that every route is behind an admin token.

WHAT IS FAKED AND WHAT IS REAL: the DISPATCHER is faked (that is the seam to redis and to
celery), everything else is the production object - the real JobService from the
composition layer, the real cache facade in its in-memory mode, the real router. That is
the smallest fake that still exercises the wiring, and it keeps the suite free of a broker.

WHY THE DISPATCHER IS SUBSTITUTED AT service.composition.state: JobService resolves it
there (`_queue()`), not through a module global, precisely so a test can replace it. The
celery flag is answered by the same fake, which is how the "inline" and "broker" modes are
both reachable without installing anything.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from service.api.app import create_app
from service.api.security import JWTBearer
from service.composition import state
from service.services.jobs.application import job_service as jobs

MOUNT = "/server"

#: The four operations this router declares, as (method, path template).
JOBS_PATHS = (
    ("get", "/jobs/"),
    ("post", "/jobs/"),
    ("get", "/jobs/{job_id}"),
    ("delete", "/jobs/{job_id}"),
)

_APP = create_app()
_SERVER = next(route.app for route in _APP.routes if getattr(route, "path", None) == MOUNT)
_CLIENT = TestClient(_APP)

#: The exact kind used by the happy-path tests: it exists in KINDS and its task body is
#: never run, because the faked dispatcher answers `run` itself.
KIND = "send_email"


class _FakeDispatcher:
    """Stands in for the celery adapter: records what was asked, answers what was told."""

    def __init__(self, *, configured=False, run_result=None, run_error=None, task_id="task-1"):
        self._configured = configured
        self._run_result = run_result if run_result is not None else {"sent": True}
        self._run_error = run_error
        self._task_id = task_id
        self.submitted = []
        self.ran = []
        self.revoked = []
        self.accept = True
        self.revoke_ok = True
        self.observed = None

    def configured(self):
        return self._configured

    def submit(self, task_name, kwargs=None):
        self.submitted.append((task_name, kwargs))
        if not self.accept:
            return False, None, "no broker here"
        return True, self._task_id, None

    def run(self, task_name, kwargs=None):
        self.ran.append((task_name, kwargs))
        if self._run_error is not None:
            raise self._run_error
        return self._run_result

    def state(self, task_id):
        return self.observed

    def revoke(self, task_id):
        self.revoked.append(task_id)
        return self.revoke_ok


@pytest.fixture(autouse=True)
def _clean_overrides():
    _SERVER.dependency_overrides.clear()
    yield
    _SERVER.dependency_overrides.clear()


@pytest.fixture
def dispatcher(monkeypatch):
    """Install a fake dispatcher and return it; also used as the admin-token bypass."""
    fake = _FakeDispatcher()
    monkeypatch.setattr(state, "get_dispatcher", lambda: fake)
    _allow_admin()
    return fake


def _allow_admin():
    """Satisfy every JWTBearer(access_level=1) instance the jobs router captured.

    WHY THE INSTANCES ARE COLLECTED FROM THE ROUTES: each `Depends(JWTBearer(...))` builds a
    new object at import, so there is no single instance to patch. This is the same seam the
    router catalogue test uses, and it keeps the real dependency out of the way without
    weakening the "no token means 403" test below, which clears these overrides first.
    """
    for route in _jobs_routes():
        for dep in route.dependant.dependencies:
            if isinstance(dep.call, JWTBearer):
                _SERVER.dependency_overrides[dep.call] = lambda: "test-token"


def _jobs_routes():
    from service.services.jobs.presentation.routers.jobs_api import jobs_api

    return [route for route in jobs_api.router.routes]


def _submit(kind=KIND, payload=None):
    response = _CLIENT.post(MOUNT + "/jobs/", json={"kind": kind, "payload": payload})
    assert response.status_code == 200, response.text
    return response.json()


# --- describe ----------------------------------------------------------------


def test_describe_reports_the_mode_the_subsystem_is_really_in(dispatcher):
    response = _CLIENT.get(MOUNT + "/jobs/")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "inline", (
        "CELERY__ENABLED is off in this fixture, so the honest answer is inline; "
        "reporting celery here would make an operator look for a worker that is not running"
    )
    assert body["ok"] is True
    assert body["store"] == "memory"
    assert sorted(body["kinds"]) == sorted(jobs.KINDS)


def test_describe_says_celery_when_a_broker_is_configured(monkeypatch):
    monkeypatch.setattr(state, "get_dispatcher", lambda: _FakeDispatcher(configured=True))
    _allow_admin()

    assert _CLIENT.get(MOUNT + "/jobs/").json()["mode"] == "celery"


# --- submit ------------------------------------------------------------------


def test_submit_returns_an_id_kind_and_the_state_it_is_already_in(dispatcher):
    body = _submit()

    assert set(body) == {"id", "kind", "state"}
    assert len(body["id"]) == 32 and int(body["id"], 16) >= 0
    assert body["kind"] == KIND
    assert body["state"] == jobs.STATE_SUCCEEDED, (
        "with no broker the task ran inside the request, so the honest state is terminal; "
        "answering 'queued' would send a client to poll something that already finished"
    )
    assert dispatcher.ran == [(jobs.KINDS[KIND], {})]


def test_submit_forwards_the_payload_to_the_task(dispatcher):
    _submit(payload={"to": "a@b.c", "subject": "hi"})

    assert dispatcher.ran == [(jobs.KINDS[KIND], {"to": "a@b.c", "subject": "hi"})]


def test_submitting_an_unknown_kind_is_a_400_naming_the_supported_ones(dispatcher):
    response = _CLIENT.post(MOUNT + "/jobs/", json={"kind": "not_a_kind"})

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "unknown job kind 'not_a_kind'" in detail
    assert "send_email" in detail
    assert dispatcher.ran == [], "a rejected kind must not reach the queue"


def test_a_failing_task_is_a_recorded_failure_not_a_500(dispatcher):
    dispatcher._run_error = RuntimeError("the worker exploded")

    body = _submit()

    assert body["state"] == jobs.STATE_FAILED
    record = _CLIENT.get(MOUNT + "/jobs/" + body["id"]).json()
    assert record["state"] == jobs.STATE_FAILED
    assert "RuntimeError" in record["error"] and "the worker exploded" in record["error"]


# --- poll --------------------------------------------------------------------


def test_polling_the_returned_id_returns_the_stored_record(dispatcher):
    body = _submit()

    record = _CLIENT.get(MOUNT + "/jobs/" + body["id"]).json()

    assert record["id"] == body["id"]
    assert record["kind"] == KIND
    assert record["state"] == jobs.STATE_SUCCEEDED
    assert record["updated_at"] and record["created_at"]
    assert record["result"] == {"sent": True}


def test_polling_an_unknown_id_is_a_404_not_a_fabricated_state(dispatcher):
    response = _CLIENT.get(MOUNT + "/jobs/abc123")

    assert response.status_code == 404, (
        "an id the store does not know must not look like 'queued' - a polling client "
        "cannot tell the two apart and waits forever"
    )
    assert "not found" in response.json()["detail"]


# --- cancel ------------------------------------------------------------------


def test_cancelling_an_inline_job_explains_that_there_was_nothing_to_revoke(dispatcher):
    body = _submit()

    answer = _CLIENT.delete(MOUNT + "/jobs/" + body["id"]).json()

    assert answer["id"] == body["id"]
    assert answer["cancelled"] is False
    assert answer["reason"] == "the job ran inline; nothing to revoke"
    assert dispatcher.revoked == [], "there is nothing to revoke, so the broker is not asked"


def test_cancelling_an_unknown_id_is_an_answer_not_an_error(dispatcher):
    response = _CLIENT.delete(MOUNT + "/jobs/nope")

    assert response.status_code == 200
    assert response.json() == {"id": "nope", "cancelled": False, "reason": "unknown job id"}


def test_cancelling_a_queued_job_reports_the_revoke(monkeypatch):
    fake = _FakeDispatcher(configured=True)
    monkeypatch.setattr(state, "get_dispatcher", lambda: fake)
    _allow_admin()
    body = _submit()

    answer = _CLIENT.delete(MOUNT + "/jobs/" + body["id"]).json()

    assert body["state"] == jobs.STATE_QUEUED
    assert answer == {"id": body["id"], "cancelled": True, "reason": None}
    assert fake.revoked == ["task-1"]


def test_a_refused_revoke_says_so_rather_than_claiming_success(monkeypatch):
    fake = _FakeDispatcher(configured=True)
    fake.revoke_ok = False
    monkeypatch.setattr(state, "get_dispatcher", lambda: fake)
    _allow_admin()
    body = _submit()

    answer = _CLIENT.delete(MOUNT + "/jobs/" + body["id"]).json()

    assert answer["cancelled"] is False
    assert answer["reason"] == "the broker did not accept the revoke request"


# --- the broker path ---------------------------------------------------------


def test_a_broker_submission_is_queued_and_records_the_task_id(monkeypatch):
    fake = _FakeDispatcher(configured=True)
    monkeypatch.setattr(state, "get_dispatcher", lambda: fake)
    _allow_admin()

    body = _submit()

    assert body["state"] == jobs.STATE_QUEUED
    assert fake.submitted == [(jobs.KINDS[KIND], {})]
    record = _CLIENT.get(MOUNT + "/jobs/" + body["id"]).json()
    assert record["mode"] == "celery"
    assert record["task_id"] == "task-1"


def test_a_broker_that_refuses_falls_back_inline_and_records_why(monkeypatch):
    fake = _FakeDispatcher(configured=True)
    fake.accept = False
    monkeypatch.setattr(state, "get_dispatcher", lambda: fake)
    _allow_admin()

    body = _submit()

    assert body["state"] == jobs.STATE_SUCCEEDED
    record = _CLIENT.get(MOUNT + "/jobs/" + body["id"]).json()
    assert record["mode"] == "inline"
    assert record["fallback_reason"] == "no broker here", (
        "the operator has to be able to see that the queue path was skipped and why; "
        "a silent inline run looks like a working broker"
    )


def test_a_worker_that_finished_updates_the_state_a_poll_reports(monkeypatch):
    fake = _FakeDispatcher(configured=True)
    monkeypatch.setattr(state, "get_dispatcher", lambda: fake)
    _allow_admin()
    body = _submit()
    fake.observed = ("SUCCESS", {"public_url": "http://uploads/x.npy"})

    record = _CLIENT.get(MOUNT + "/jobs/" + body["id"]).json()

    assert record["state"] == jobs.STATE_SUCCEEDED
    assert record["result_url"] == "http://uploads/x.npy"


# --- the token requirement ---------------------------------------------------


@pytest.mark.parametrize("method,path", JOBS_PATHS)
def test_every_jobs_route_rejects_a_missing_token(method, path):
    """The real dependency runs here: no override is installed, so Starlette answers 403."""
    url = MOUNT + path.replace("{job_id}", "abc")

    response = _CLIENT.request(
        method.upper(), url, json={"kind": KIND} if method == "post" else None
    )

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "Not authenticated"}


def test_the_surface_is_behind_the_admin_level_not_the_user_level():
    """access_level=1 is what makes 'submit a task with a payload' an operator action."""
    levels = {
        dep.call.access_level
        for route in _jobs_routes()
        for dep in route.dependant.dependencies
        if isinstance(dep.call, JWTBearer)
    }
    assert levels == {1}, "an ordinary account must not be able to enqueue arbitrary tasks"


# --- the port did not move ---------------------------------------------------


def test_the_job_queue_port_still_answers_none_for_an_unknown_id():
    """The HTTP spelling raises; the port must keep answering None for a Python caller."""
    service = jobs.JobService()

    assert service.status("never-enqueued") is None
    with pytest.raises(Exception) as caught:
        service.require("never-enqueued")
    assert getattr(caught.value, "status_code", None) == 404


def test_the_port_still_refuses_an_unknown_kind_with_a_value_error():
    with pytest.raises(ValueError, match="unknown job kind"):
        jobs.JobService().enqueue("not_a_kind")
