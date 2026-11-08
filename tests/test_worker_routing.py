"""Queue topology: which task runs where, and why it cannot drift.

Two classes of defect this file exists to catch, both invisible at runtime until
a job silently never runs:

  1. A task registered under a name that TASK_ROUTES does not know, so it lands
     on task_default_queue and competes with user uploads.
  2. A beat entry pointing at a task that no longer exists, so the periodic job
     stops firing and nothing raises.

Neither needs celery installed: the module level of service/infrastructure/messaging/celery_app.py is
plain data, which is the design decision under test as much as the data itself.
"""

import importlib
import sys

import pytest

from service.infrastructure.messaging import beat_schedule as beat_module
from service.infrastructure.messaging import celery_app as app_module
from service.infrastructure.messaging import tasks as tasks_module


def test_importing_the_modules_does_not_require_celery():
    """The base image has no celery; importing these modules must still work."""
    for name in ("service.infrastructure.cache", "service.infrastructure.storage", "service.infrastructure.mail",
                 "service.infrastructure.messaging.celery_app", "service.infrastructure.messaging.tasks",
                 "service.infrastructure.messaging.beat_schedule", "service.services.jobs_services"):
        assert importlib.import_module(name) is not None


def test_queues_are_the_three_documented_ones():
    assert app_module.QUEUES == ("agents", "maintenance", "ml")
    assert app_module.APP_NAME == "hiresnap"


def test_every_registered_task_has_a_route():
    """An unrouted task silently lands on the default queue."""
    for name in tasks_module.TASKS:
        assert app_module.queue_for(name) is not None, name + " has no route"


def test_every_route_names_a_real_task():
    """A route for a task that does not exist is dead documentation."""
    for name in app_module.TASK_ROUTES:
        assert name in tasks_module.TASKS, name + " is routed but not registered"


def test_task_names_are_namespaced():
    """Operators grep "hiresnap." in celery inspect output."""
    for name in tasks_module.TASKS:
        assert name.startswith("hiresnap."), name


def test_routing_puts_user_work_ahead_of_maintenance():
    """The queues are failure domains, not priorities: verify the split."""
    assert app_module.queue_for("hiresnap.embed_candidate") == "ml"
    assert app_module.queue_for("hiresnap.reindex_embeddings") == "ml"
    assert app_module.queue_for("hiresnap.cleanup_orphan_uploads") == "maintenance"
    assert app_module.queue_for("hiresnap.health") == "maintenance"
    assert app_module.queue_for("hiresnap.send_email") == "agents"


def test_routes_only_use_declared_queues():
    for name in app_module.TASK_ROUTES:
        assert app_module.queue_for(name) in app_module.QUEUES, name


def test_time_limits_have_a_gap_and_ml_gets_more_room():
    assert app_module.SOFT_TIME_LIMIT < app_module.HARD_TIME_LIMIT
    soft, hard = app_module.TASK_TIME_LIMITS["hiresnap.embed_candidate"]
    assert soft > app_module.SOFT_TIME_LIMIT, "embedding is slower than the default budget"
    assert soft < hard, "the hard limit must be a backstop beyond the soft one"


# ---------------------------------------------------------------------------
# beat schedule
# ---------------------------------------------------------------------------
def test_beat_schedule_targets_known_tasks_and_queues():
    schedule = beat_module.build_beat_schedule()
    assert schedule, "beat with an empty schedule is a silent no-op"
    for entry_name, entry in schedule.items():
        assert entry["task"] in tasks_module.TASKS, entry_name + " targets a missing task"
        assert entry["options"]["queue"] in app_module.QUEUES, entry_name + " targets an unknown queue"
        assert float(entry["schedule"]) > 0, entry_name + " has a non-positive interval"
        assert "expires" in entry["options"], entry_name + " can pile up behind a beat restart"


def test_beat_intervals_match_the_documented_cadence():
    schedule = beat_module.build_beat_schedule()
    assert schedule["cleanup-orphan-uploads-hourly"]["schedule"] == 3600.0
    assert schedule["reindex-embeddings-nightly"]["schedule"] == 86400.0


def test_beat_intervals_can_be_overridden_by_env(monkeypatch):
    """The schedule lives in code, but the cadence must stay tunable per host."""
    monkeypatch.setenv("CELERY__CLEANUP_INTERVAL_SECONDS", "120")
    schedule = beat_module.build_beat_schedule()
    assert schedule["cleanup-orphan-uploads-hourly"]["schedule"] == 120.0


def test_malformed_interval_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CELERY__REINDEX_INTERVAL_SECONDS", "not-a-number")
    schedule = beat_module.build_beat_schedule()
    assert schedule["reindex-embeddings-nightly"]["schedule"] == 86400.0


# ---------------------------------------------------------------------------
# application factory
# ---------------------------------------------------------------------------
def test_is_configured_is_false_by_default():
    """Every flag off must mean "run inline", never "fail"."""
    assert app_module.is_configured() is False


def test_is_configured_follows_the_env_flag(monkeypatch):
    monkeypatch.setenv("CELERY__ENABLED", "true")
    assert app_module.is_configured() is True
    monkeypatch.setenv("CELERY__ENABLED", "false")
    assert app_module.is_configured() is False


def test_default_urls_put_operational_state_in_redis_db_1():
    """Business data stays in MySQL; broker and results live in db 1."""
    assert app_module.DEFAULT_BROKER_URL.endswith("/1")
    assert app_module.DEFAULT_RESULT_BACKEND.endswith("/1")


def test_broker_credentials_are_redacted():
    redacted = app_module.redact_url("redis://default:hunter2@redis:6379/1")
    assert "hunter2" not in redacted
    assert "***" in redacted
    assert app_module.redact_url("") == ""


def test_unknown_module_attribute_raises_attribute_error():
    """The lazy __getattr__ must not turn typos into None."""
    with pytest.raises(AttributeError):
        getattr(app_module, "definitely_not_an_attribute")


def test_register_tasks_binds_every_name_with_its_queue():
    """Binding is asserted against a fake app, so no celery is needed."""
    bound = {}

    class _FakeApp:
        def task(self, name=None, **options):
            def decorator(function):
                bound[name] = {"options": options, "function": function}
                return function
            return decorator

    registered = tasks_module.register_tasks(_FakeApp())
    assert set(registered) == set(tasks_module.TASKS)
    for name, entry in bound.items():
        assert entry["options"]["queue"] == app_module.queue_for(name)
        assert entry["options"]["time_limit"] > entry["options"]["soft_time_limit"]
        assert entry["function"] is tasks_module.TASKS[name]


def test_health_task_reports_every_subsystem():
    """The worker health task probes for real; the API health endpoint does not."""
    report = tasks_module.health()
    assert report["status"] in ("ok", "failed")
    assert set(report["checks"]) == {"cache", "storage", "mail"}


def test_dispatch_runs_inline_when_unconfigured(monkeypatch):
    called = {}

    def fake_health():
        called["hit"] = True
        return {"status": "ok"}

    monkeypatch.setitem(tasks_module.TASKS, "hiresnap.health", fake_health)
    tasks_module.dispatch("hiresnap.health")
    assert called.get("hit") is True


def test_get_task_rejects_an_unknown_name():
    with pytest.raises(KeyError):
        tasks_module.get_task("hiresnap.nope")
