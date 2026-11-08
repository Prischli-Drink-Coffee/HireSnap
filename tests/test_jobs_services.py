"""The job contract: enqueue returns an id, status reports the outcome.

The point of these tests is the fallback path. With CELERY__ENABLED unset (the
default) a "job" runs inline and synchronously, so the contract - id, kind,
state, created_at, result_url, error - can be verified with no broker, no redis
and no network. Broker mode is covered by test_worker_routing.py at the routing
level, because a real broker cannot exist in this test suite.
"""

import sys
import types

import pytest

from service.infrastructure.messaging import tasks as tasks_module

# The job subsystem is a vertical slice: service/services/jobs/application/job_service.py.
# The module-level functions are kept (the slice's own tests and scripts use them) and
# JobService is the object the composition layer hands out; both are covered below.
from service.services.jobs.application import job_service as jobs_services


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    """A working mail backend, so send_email succeeds without an SMTP server."""
    path = tmp_path / "outbox.log"
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "file")
    monkeypatch.setenv("MAIL__FILE_PATH", str(path))
    return path


def test_supported_kinds_are_documented():
    assert jobs_services.supported_kinds() == [
        "cleanup_orphan_uploads",
        "embed_candidate",
        "reindex_embeddings",
        "send_email",
    ]


def test_enqueue_returns_an_id_and_records_a_job(outbox):
    job_id = jobs_services.enqueue(
        "send_email", {"to": "a@example.com", "subject": "Hi", "body": "body"}
    )
    assert isinstance(job_id, str) and len(job_id) == 32

    record = jobs_services.status(job_id)
    assert record["id"] == job_id
    assert record["kind"] == "send_email"
    assert record["state"] == jobs_services.STATE_SUCCEEDED
    assert record["created_at"].endswith("Z")
    assert record["error"] is None
    assert record["mode"] == "inline"


def test_enqueue_actually_ran_the_task(outbox):
    jobs_services.enqueue("send_email", {"to": "b@example.com", "subject": "Ran", "body": "body"})
    assert "Ran" in outbox.read_text(encoding="utf-8")


def test_unknown_job_id_returns_none():
    """An unknown id and a queued job must not look identical to a polling client."""
    assert jobs_services.status("does-not-exist") is None
    assert jobs_services.status("") is None
    assert jobs_services.status(None) is None


def test_unknown_kind_is_a_caller_error():
    with pytest.raises(ValueError, match="unknown job kind"):
        jobs_services.enqueue("transcode_video", {})


def test_failing_task_produces_a_failed_job(monkeypatch):
    """A task error is recorded, not raised: the caller already has the id."""

    def explode(**kwargs):
        raise RuntimeError("embedder exploded")

    monkeypatch.setitem(tasks_module.TASKS, "hiresnap.send_email", explode)
    job_id = jobs_services.enqueue(
        "send_email", {"to": "a@example.com", "subject": "s", "body": "b"}
    )

    record = jobs_services.status(job_id)
    assert record["state"] == jobs_services.STATE_FAILED
    assert "embedder exploded" in record["error"]
    assert record["error"].startswith("RuntimeError")


def test_result_url_is_extracted_for_polling_clients(monkeypatch):
    """status() must expose the stored object's URL without a second call."""

    def fake_embed(**kwargs):
        return {
            "status": "ok",
            "embedding_key": "embeddings/users/x.npy",
            "result_url": "https://x/y.npy",
        }

    monkeypatch.setitem(tasks_module.TASKS, "hiresnap.embed_candidate", fake_embed)
    job_id = jobs_services.enqueue(
        "embed_candidate", {"candidate_id": "c1", "file_key": "files/a.mp4"}
    )
    record = jobs_services.status(job_id)
    assert record["state"] == jobs_services.STATE_SUCCEEDED
    assert record["result_url"] == "https://x/y.npy"


def test_payload_is_summarised_not_stored_verbatim(tmp_path):
    """Job state lives in redis; a phone number must not be copied there."""
    import os

    os.environ["MAIL__ENABLED"] = "false"
    job_id = jobs_services.enqueue(
        "send_email", {"to": "a@example.com", "subject": "s", "body": "b"}
    )
    record = jobs_services.status(job_id)
    summary = record["payload_summary"]
    assert summary["keys"] == ["body", "subject", "to"]
    # The recipient address is truncated into a preview string, never kept as a
    # structured field a later lookup could enumerate.
    assert isinstance(summary["preview"], str)


def test_cleanup_refuses_to_delete_when_the_database_is_unreachable(tmp_path, monkeypatch):
    """ "Cannot read the references" must mean "delete nothing"."""
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    monkeypatch.setattr(tasks_module, "_referenced_keys", lambda: None)

    job_id = jobs_services.enqueue("cleanup_orphan_uploads", {"older_than_seconds": 0})
    record = jobs_services.status(job_id)
    assert record["state"] == jobs_services.STATE_SUCCEEDED
    assert record["result"]["status"] == "skipped"
    assert record["result"]["reason"] == "database_unavailable"
    assert record["result"]["deleted"] == 0


def test_degraded_enqueue_falls_back_to_inline(monkeypatch, outbox):
    """CELERY__ENABLED=true with no celery installed must still run the job."""
    monkeypatch.setenv("CELERY__ENABLED", "true")
    # "import celery" raises, exactly like the base image without the optional
    # requirements file installed.
    monkeypatch.setitem(sys.modules, "celery", None)

    job_id = jobs_services.enqueue(
        "send_email", {"to": "a@example.com", "subject": "Fallback", "body": "b"}
    )
    record = jobs_services.status(job_id)
    assert record["state"] == jobs_services.STATE_SUCCEEDED
    assert record["mode"] == "inline"
    assert "fallback_reason" in record
    assert "Fallback" in outbox.read_text(encoding="utf-8")


def test_broker_mode_is_reported_when_a_broker_is_configured(monkeypatch):
    monkeypatch.setenv("CELERY__ENABLED", "true")
    assert jobs_services.celery_enabled() is True
    # A fake celery application: proves enqueue hands the task to the broker
    # without a real broker and without importing the real celery package.
    sent = {}

    class _FakeResult:
        id = "task-123"

    class _FakeApp:
        def send_task(self, name, kwargs=None, **options):
            sent["name"] = name
            sent["kwargs"] = kwargs
            return _FakeResult()

    fake_celery = types.ModuleType("service.infrastructure.messaging.celery_app")
    fake_celery.is_configured = lambda: True
    fake_celery.get_celery_app = lambda: _FakeApp()
    monkeypatch.setitem(sys.modules, "service.infrastructure.messaging.celery_app", fake_celery)

    job_id = jobs_services.enqueue(
        "send_email", {"to": "a@example.com", "subject": "s", "body": "b"}
    )
    record = jobs_services.status(job_id)
    assert sent["name"] == "hiresnap.send_email"
    assert sent["kwargs"]["to"] == "a@example.com"
    assert record["mode"] == "celery"
    assert record["task_id"] == "task-123"
    assert record["state"] == jobs_services.STATE_QUEUED


def test_cancel_is_false_for_an_inline_job(outbox):
    """Inline jobs are already finished; claiming a cancel would be a lie."""
    job_id = jobs_services.enqueue(
        "send_email", {"to": "a@example.com", "subject": "s", "body": "b"}
    )
    assert jobs_services.cancel(job_id) is False
    assert jobs_services.cancel("unknown-id") is False


def test_jobs_health_reports_the_mode():
    health = jobs_services.jobs_health()
    assert health["mode"] == "inline"
    assert health["ok"] is True
    assert health["store"] == "memory"


def test_job_records_expire():
    """Operational state is disposable: the TTL is part of the contract."""
    assert jobs_services.JOB_TTL_SECONDS > 0
    assert jobs_services.JOB_TTL_SECONDS <= 7 * 24 * 3600


def test_job_service_object_delegates_to_the_same_records(outbox):
    """JobService is what the composition layer hands out, so it must not be a
    second implementation: a job enqueued through the object is visible through
    the module functions and vice versa."""
    service = jobs_services.JobService()

    job_id = service.enqueue("send_email", {"to": "c@example.com", "subject": "Obj", "body": "b"})
    assert isinstance(job_id, str) and len(job_id) == 32
    assert jobs_services.status(job_id)["id"] == job_id
    assert service.status(job_id)["state"] == jobs_services.STATE_SUCCEEDED
    assert service.supported_kinds() == jobs_services.supported_kinds()
    assert service.celery_enabled() is jobs_services.celery_enabled()
    assert service.health()["mode"] == "inline"
    assert service.cancel(job_id) is False
    assert service.status("does-not-exist") is None
