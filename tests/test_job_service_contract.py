"""The job contract behind the broker: the paths the inline mode never takes.

WHY A SECOND JOB FILE: `test_jobs_services.py` pins the contract a laptop sees -
enqueue returns an id, the task runs inline, the record says succeeded. That is one
half of the state machine. The other half is invisible here: the refresh from the
worker, a revoke, a cache that refuses to answer, a payload that is not a dictionary.
Those paths decide whether a polling client sees the truth, so they are driven
directly against a fake queue and a fake cache instead of through redis and celery.

WHY THE FAKES REPLACE `_cache`/`_queue` AND NOT `state`: the module resolves both
through `service.composition.state` inside the function (`_cache`, `_queue`), so
patching those two module-level names exercises the same code without depending on
which container the wiring root happens to hold - and it lets a failing cache be
expressed as an object instead of an unreachable server.
"""

import pytest

from service.services.jobs.application import job_service as jobs


class FakeCache:
    """The cache facade's three methods, with failure knobs per operation."""

    backend_name = "fake"

    def __init__(self, fail_set=False, fail_get=False):
        self.data = {}
        self.calls = []
        self.fail_set = fail_set
        self.fail_get = fail_get

    def set(self, key, value, ttl=None, namespace=None):
        self.calls.append(("set", key, value, ttl, namespace))
        if self.fail_set:
            raise RuntimeError("cache down")
        self.data[key] = value
        return True

    def get(self, key, default=None, namespace=None):
        self.calls.append(("get", key, default, namespace))
        if self.fail_get:
            raise RuntimeError("cache down")
        return self.data.get(key, default)


class FakeQueue:
    """The CeleryDispatcher surface the slice uses, answering whatever the test needs."""

    def __init__(self, configured=False, submit=(True, "task-1", None), state=None, revoke=True):
        self._configured = configured
        self.submit_answer = submit
        self.state_answer = state
        self.revoke_answer = revoke
        self.calls = []

    def configured(self):
        self.calls.append(("configured",))
        if isinstance(self._configured, Exception):
            raise self._configured
        return self._configured

    def submit(self, task_name, kwargs=None):
        self.calls.append(("submit", task_name, dict(kwargs or {})))
        if isinstance(self.submit_answer, Exception):
            raise self.submit_answer
        return self.submit_answer

    def run(self, task_name, kwargs=None):
        self.calls.append(("run", task_name))
        return {"status": "ok"}

    def state(self, task_id):
        self.calls.append(("state", task_id))
        if isinstance(self.state_answer, Exception):
            raise self.state_answer
        return self.state_answer

    def revoke(self, task_id):
        self.calls.append(("revoke", task_id))
        if isinstance(self.revoke_answer, Exception):
            raise self.revoke_answer
        return self.revoke_answer


@pytest.fixture()
def fakes(monkeypatch):
    """Point the slice at a fake cache and a fake queue; return both."""
    cache = FakeCache()
    queue = FakeQueue()
    monkeypatch.setattr(jobs, "_cache", lambda: cache)
    monkeypatch.setattr(jobs, "_queue", lambda: queue)
    return cache, queue


# --- _env -------------------------------------------------------------------
def test_env_takes_the_first_name_present_even_when_its_value_is_empty(monkeypatch):
    """The same presence-not-truthiness rule every module here implements: a key set to
    "" is a configuration value, not an absent key."""
    monkeypatch.setenv("HIRESNAP_JOBS_FIRST", "")
    monkeypatch.setenv("HIRESNAP_JOBS_SECOND", "second")

    assert jobs._env("HIRESNAP_JOBS_FIRST", "HIRESNAP_JOBS_SECOND", default="d") == ""
    assert jobs._env("HIRESNAP_JOBS_ABSENT", default="d") == "d"


# --- payload summary --------------------------------------------------------
def test_a_missing_payload_is_stored_as_none():
    assert jobs._serialise_payload(None) is None


def test_a_payload_that_is_not_a_mapping_is_kept_as_a_clipped_repr():
    """enqueue() copies the payload with dict(), but the helper is called elsewhere too;
    a non-mapping must not raise in the bookkeeping path."""
    summary = jobs._serialise_payload(["a", "b"])

    assert summary == {"_repr": "['a', 'b']"}


def test_a_nested_value_is_summarised_as_a_repr_not_stored_whole():
    """The point of the summary: shapes for debugging, no candidate data in redis."""
    summary = jobs._serialise_payload(
        {"candidate_id": 7, "vector": [0.1, 0.2, 0.3], "note": "x" * 500}
    )

    assert summary["keys"] == ["candidate_id", "note", "vector"]
    assert isinstance(summary["preview"], str)
    assert "0.1" in summary["preview"]
    # A long scalar is published in the preview only in clipped form.
    assert "x" * 201 not in summary["preview"]


# --- degradation ------------------------------------------------------------
def test_a_cache_that_cannot_store_does_not_raise_into_the_caller(monkeypatch):
    """Job state is disposable; a dead cache must cost a status lookup, not the request."""
    monkeypatch.setattr(jobs, "_cache", lambda: FakeCache(fail_set=True))

    record = jobs._save({"id": "abc", "kind": "send_email", "state": jobs.STATE_QUEUED})

    assert record["id"] == "abc"


def test_a_cache_that_cannot_load_answers_none(monkeypatch):
    monkeypatch.setattr(jobs, "_cache", lambda: FakeCache(fail_get=True))

    assert jobs._load("abc") is None
    assert jobs.status("abc") is None


def test_celery_is_disabled_when_the_dispatcher_cannot_be_resolved(monkeypatch):
    def explode():
        raise RuntimeError("no wiring root")

    monkeypatch.setattr(jobs, "_queue", explode)

    assert jobs.celery_enabled() is False


# --- result summarising -----------------------------------------------------
def test_a_structural_result_is_summarised_by_type_not_by_value():
    """A task can return a numpy array; the record must stay JSON-serialisable."""
    assert jobs._summarise_result([1, 2, 3]) == {"type": "list"}
    assert jobs._summarise_result({"kept": "v", "dropped": [1]}) == {"kept": "v"}


def test_a_scalar_result_is_wrapped_under_value():
    assert jobs._summarise_result("done") == {"value": "done"}
    assert jobs._summarise_result(None) == {"value": None}


def test_result_url_accepts_the_three_documented_keys_and_nothing_else():
    assert jobs._extract_result_url({"result_url": "u1"}) == "u1"
    assert jobs._extract_result_url({"url": "u2"}) == "u2"
    assert jobs._extract_result_url({"public_url": "u3"}) == "u3"
    assert jobs._extract_result_url({"url": ""}) is None
    assert jobs._extract_result_url({"other": "x"}) is None
    assert jobs._extract_result_url("not a mapping") is None


# --- refresh from the worker ------------------------------------------------
def _stored_job(fakes, broker=True, **extra):
    """Store a queued job, with a task id, exactly as broker mode leaves it.

    broker=True also turns the fake queue "configured": status() only asks the worker
    when a broker is configured, so without it every refresh test would silently
    assert the inline path.
    """
    cache, queue = fakes
    queue._configured = broker
    record = jobs._record("job-1", "embed_candidate", jobs.STATE_QUEUED, task_id="task-1")
    record.update(extra)
    cache.data[jobs._key("job-1")] = record
    return record


def test_a_running_job_is_refreshed_from_the_workers_own_state(fakes):
    cache, queue = fakes
    queue.state_answer = ("STARTED", None)
    _stored_job(fakes)

    record = jobs.status("job-1")

    assert record["state"] == jobs.STATE_RUNNING
    assert queue.calls[-1] == ("state", "task-1")
    # The refreshed record is written back, so a second poll sees the same answer
    # even when the broker becomes unreachable in between.
    assert cache.data[jobs._key("job-1")]["state"] == jobs.STATE_RUNNING


def test_a_succeeded_task_brings_its_result_and_url_home(fakes):
    _, queue = fakes
    queue.state_answer = ("SUCCESS", {"status": "ok", "result_url": "https://x/y.npy"})
    _stored_job(fakes)

    record = jobs.status("job-1")

    assert record["state"] == jobs.STATE_SUCCEEDED
    # The summary keeps every scalar of the result, and the URL is ALSO lifted to the
    # top level so a polling client does not have to know the task's own shape.
    assert record["result"] == {"status": "ok", "result_url": "https://x/y.npy"}
    assert record["result_url"] == "https://x/y.npy"


def test_a_failed_task_records_the_failure_text(fakes):
    _, queue = fakes
    queue.state_answer = ("FAILURE", "embedder exploded")
    _stored_job(fakes)

    record = jobs.status("job-1")

    assert record["state"] == jobs.STATE_FAILED
    assert record["error"] == "embedder exploded"


def test_a_revoked_task_is_reported_as_failed(fakes):
    _, queue = fakes
    queue.state_answer = ("REVOKED", None)
    _stored_job(fakes)

    assert jobs.status("job-1")["state"] == jobs.STATE_FAILED


def test_an_unmapped_broker_state_leaves_the_record_alone(fakes):
    """Celery's vocabulary is larger than ours; an unknown word is not a state change."""
    _, queue = fakes
    queue.state_answer = ("SOMETHING_NEW", None)
    record = _stored_job(fakes)

    assert jobs.status("job-1") == record


def test_a_state_that_did_not_change_is_not_rewritten(fakes):
    cache, queue = fakes
    queue.state_answer = ("PENDING", None)
    _stored_job(fakes)
    writes_before = [call for call in cache.calls if call[0] == "set"]

    jobs.status("job-1")

    assert [call for call in cache.calls if call[0] == "set"] == writes_before


def test_an_unreachable_broker_does_not_fail_the_status_lookup(fakes):
    """The row we wrote at submission time is still the best answer we have."""
    cache, queue = fakes
    queue.state_answer = RuntimeError("broker gone")
    _stored_job(fakes)
    writes_before = [call for call in cache.calls if call[0] == "set"]

    record = jobs.status("job-1")

    assert record["state"] == jobs.STATE_QUEUED
    assert [call for call in cache.calls if call[0] == "set"] == writes_before


def test_a_task_the_worker_does_not_know_yet_keeps_the_stored_row(fakes):
    _, queue = fakes
    queue.state_answer = None
    _stored_job(fakes)

    assert jobs.status("job-1")["state"] == jobs.STATE_QUEUED


def test_an_inline_record_is_never_refreshed_from_the_broker(fakes):
    """No task_id means nothing to ask about, even when a broker is configured."""
    _, queue = fakes
    queue._configured = True
    cache = fakes[0]
    cache.data[jobs._key("job-2")] = jobs._record("job-2", "send_email", jobs.STATE_SUCCEEDED)

    assert jobs.status("job-2")["state"] == jobs.STATE_SUCCEEDED
    assert ("state", "task-1") not in queue.calls
    assert all(call[0] != "state" for call in queue.calls)


# --- cancel -----------------------------------------------------------------
def test_cancelling_a_queued_job_asks_the_broker_and_records_it(fakes):
    cache, queue = fakes
    record = _stored_job(fakes, mode="celery")

    assert jobs.cancel("job-1") is True
    assert queue.calls[-1] == ("revoke", "task-1")
    stored = cache.data[jobs._key("job-1")]
    assert stored["state"] == jobs.STATE_FAILED
    assert stored["error"] == "cancelled"
    assert record["id"] == "job-1"


def test_a_refused_revoke_reports_false_and_leaves_the_record_queued(fakes):
    cache, queue = fakes
    queue.revoke_answer = False
    _stored_job(fakes, mode="celery")

    assert jobs.cancel("job-1") is False
    assert cache.data[jobs._key("job-1")]["state"] == jobs.STATE_QUEUED


def test_a_revoke_that_raises_reports_false(fakes):
    _, queue = fakes
    queue.revoke_answer = RuntimeError("broker gone")
    _stored_job(fakes, mode="celery")

    assert jobs.cancel("job-1") is False


def test_an_unknown_job_cannot_be_cancelled(fakes):
    assert jobs.cancel("nope") is False


class _Unprintable:
    """A worker result whose __str__ fails; celery can hand back anything."""

    def __str__(self):
        raise RuntimeError("cannot render")


class _UniterableDict(dict):
    """A dict whose items() fails, so the summary itself raises."""

    def items(self):
        raise RuntimeError("cannot iterate")


def test_a_result_that_cannot_be_summarised_does_not_lose_the_state_change(fakes):
    """The state arrived; only the summary failed, and a failed summary must not
    discard the fact that the job succeeded."""
    _, queue = fakes
    queue.state_answer = ("SUCCESS", _UniterableDict([("a", 1)]))
    _stored_job(fakes)

    record = jobs.status("job-1")

    assert record["state"] == jobs.STATE_SUCCEEDED
    assert "result" not in record


def test_a_failure_text_that_cannot_be_rendered_still_marks_the_job_failed(fakes):
    _, queue = fakes
    queue.state_answer = ("FAILURE", _Unprintable())
    _stored_job(fakes)

    record = jobs.status("job-1")

    assert record["state"] == jobs.STATE_FAILED
    assert record["error"] == "task failed"
