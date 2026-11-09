"""The celery factory, exercised with a stand-in for celery itself.

WHY A FAKE AND NOT A SKIP: `build_celery_app()` is the only place in the tree that
imports celery, and every line below it - the queue declarations, the routing
table, the beat schedule merge, the registration call - is business configuration
that a reviewer must be able to see fail. The CI interpreter has no celery, so
this file injects a module that records what the factory asks for.

WHAT THIS FILE DOES NOT PROVE: that celery accepts the configuration. That is what
the compose stack and `celery inspect` are for, and the runbook says so.
"""

from __future__ import annotations

import sys
import types

import pytest

from service.infrastructure.messaging import beat_schedule as beat_module
from service.infrastructure.messaging import celery_app as app_module
from service.infrastructure.messaging import tasks as tasks_module


class _FakeConf(dict):
    """The subset of celery's conf a factory touches.

    celery's `conf` answers BOTH `conf[...]` and `conf.attr`, and the factory uses
    both spellings (`conf.update(...)` then `conf.task_queues = ...`). A plain dict
    would silently lose the attribute write, so the stand-in has to do both.
    """

    def __setattr__(self, name, value):
        self[name] = value

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _FakeCeleryApp:
    """Records the constructor arguments and every `task()` binding."""

    def __init__(self, name, broker=None, backend=None, include=None, **kwargs):
        self.name = name
        self.arguments = {"broker": broker, "backend": backend, "include": include}
        self.conf = _FakeConf()
        self.decorated = []

    def task(self, **options):
        def _decorator(body):
            self.decorated.append((body.__name__, options))
            return body

        return _decorator


@pytest.fixture
def fake_celery(monkeypatch):
    """Install a `celery` and a `kombu` module the factory can import."""
    captured = {}

    celery_module = types.ModuleType("celery")

    def _Celery(name, **kwargs):
        app = _FakeCeleryApp(name, **kwargs)
        captured["app"] = app
        return app

    celery_module.Celery = _Celery

    kombu_module = types.ModuleType("kombu")

    class _Queue:
        def __init__(self, name, **kwargs):
            self.name = name

        def __repr__(self):
            return "<Queue %s>" % self.name

    kombu_module.Queue = _Queue

    monkeypatch.setitem(sys.modules, "celery", celery_module)
    monkeypatch.setitem(sys.modules, "kombu", kombu_module)
    app_module.reset_celery_app()
    yield captured
    app_module.reset_celery_app()
    monkeypatch.undo()


@pytest.fixture
def no_celery(monkeypatch):
    """`import celery` raises ImportError, like the API image."""
    monkeypatch.setitem(sys.modules, "celery", None)
    app_module.reset_celery_app()
    yield
    app_module.reset_celery_app()


# ---------------------------------------------------------------------------
# get_celery_app / reset_celery_app
# ---------------------------------------------------------------------------


def test_the_memoised_app_is_raised_before_celery_is_imported(no_celery):
    """The failure is an ImportError naming celery, not an AttributeError."""
    with pytest.raises(ImportError):
        app_module.get_celery_app()


def test_get_celery_app_builds_once_and_hands_back_the_same_object(fake_celery):
    first = app_module.get_celery_app()
    second = app_module.get_celery_app()

    assert first is second
    assert first.name == app_module.APP_NAME


def test_reset_drops_the_memoised_app_so_a_new_one_is_built(fake_celery):
    first = app_module.get_celery_app()
    app_module.reset_celery_app()
    second = app_module.get_celery_app()

    assert first is not second


# ---------------------------------------------------------------------------
# build_celery_app
# ---------------------------------------------------------------------------


def test_the_factory_wires_the_documented_configuration(fake_celery):
    app = app_module.get_celery_app()

    assert app.arguments["broker"] == app_module.DEFAULT_BROKER_URL
    assert app.arguments["backend"] == app_module.DEFAULT_RESULT_BACKEND
    assert app.arguments["include"] == ["service.infrastructure.messaging.tasks"]
    assert app.conf["task_serializer"] == "json"
    assert app.conf["accept_content"] == ["json"]
    assert app.conf["task_acks_late"] is True
    assert app.conf["worker_prefetch_multiplier"] == 1
    assert app.conf["task_default_queue"] == app_module.QUEUE_AGENTS
    assert [queue.name for queue in app.conf["task_queues"]] == list(app_module.QUEUES)


def test_the_factory_routes_every_task_and_gives_ml_a_longer_limit(fake_celery):
    app = app_module.get_celery_app()

    assert app.conf["task_routes"] == dict(app_module.TASK_ROUTES)
    ml_soft, ml_hard = app_module.TASK_TIME_LIMITS["hiresnap.embed_candidate"]
    assert (ml_soft, ml_hard) == (
        app_module.ML_SOFT_TIME_LIMIT,
        app_module.ML_HARD_TIME_LIMIT,
    )
    assert ml_soft > app_module.SOFT_TIME_LIMIT


def test_the_factory_merges_the_beat_schedule(fake_celery):
    app = app_module.get_celery_app()

    assert app.conf["beat_schedule"] == beat_module.build_beat_schedule()


def test_the_factory_registers_every_task_body_once(fake_celery):
    app = app_module.get_celery_app()

    # register_tasks() decorates each BODY, so the recorded callback is the
    # function name; the task NAME (with its "hiresnap." namespace) is what it
    # passes to app.task(), and that is the option asserted below.
    assert [body for body, _options in app.decorated] == [
        name.rsplit(".", 1)[-1] for name in tasks_module.TASKS
    ]
    assert [options["name"] for _body, options in app.decorated] == list(tasks_module.TASKS)
    options = {options["name"]: options for _body, options in app.decorated}[
        "hiresnap.embed_candidate"
    ]
    assert options["queue"] == app_module.QUEUE_ML
    assert options["time_limit"] == app_module.ML_HARD_TIME_LIMIT


def test_the_broker_password_never_reaches_the_log(fake_celery, caplog, monkeypatch):
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://user:hunter2@cache:6379/1")
    app_module.reset_celery_app()

    with caplog.at_level("INFO"):
        app_module.get_celery_app()

    assert "hunter2" not in caplog.text
    assert "***:***@" in caplog.text


# ---------------------------------------------------------------------------
# Env readers
# ---------------------------------------------------------------------------


def test_broker_and_backend_urls_follow_the_environment(monkeypatch):
    monkeypatch.setenv("CELERY__BROKER_URL", "redis://broker:6379/9")
    monkeypatch.setenv("CELERY__RESULT_BACKEND", "redis://results:6379/9")

    assert app_module.broker_url() == "redis://broker:6379/9"
    assert app_module.result_backend() == "redis://results:6379/9"


def test_the_flat_spelling_is_accepted_too(monkeypatch):
    """An operator copying a compose file may write CELERY_BROKER_URL (one underscore)."""
    monkeypatch.delenv("CELERY__BROKER_URL", raising=False)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://flat:6379/2")

    assert app_module.broker_url() == "redis://flat:6379/2"


def test_queue_for_answers_none_for_an_unrouted_task():
    assert app_module.queue_for("hiresnap.not_routed") is None


def test_the_module_attribute_proxy_materialises_the_app(fake_celery):
    assert app_module.app is app_module.get_celery_app()
    assert app_module.celery is app_module.get_celery_app()
    assert app_module.celery_app is app_module.get_celery_app()


def test_the_module_attribute_proxy_refuses_everything_else():
    with pytest.raises(AttributeError) as failure:
        # Assigned, not left as a bare expression: the point is the AttributeError, and
        # a statement whose only effect is the exception reads as dead code to a linter.
        _ = app_module.not_a_celery_attribute
    assert "not_a_celery_attribute" in str(failure.value)


# ---------------------------------------------------------------------------
# service/shared/validation.py
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, row_id, name):
        self.ID = row_id
        self.Name = name


def test_check_for_duplicates_skips_the_row_being_updated():
    from service.shared.validation import check_for_duplicates

    rows = [_Row("1", "same"), _Row("2", "other")]

    check_for_duplicates(lambda: rows, 1, "Name", "same", "taken")


def test_check_for_duplicates_raises_for_another_row_holding_the_value():
    from service.shared.errors import ConflictError
    from service.shared.validation import check_for_duplicates

    rows = [_Row("1", "mine"), _Row("2", "taken")]

    with pytest.raises(ConflictError) as failure:
        check_for_duplicates(lambda: rows, 1, "Name", "taken", "Name already exist")
    assert failure.value.detail == "Name already exist"


def test_check_for_duplicates_compares_ids_coming_from_json_as_strings():
    """A JSON body carries "1"; the row carries 1. int() is what makes them equal."""
    from service.shared.validation import check_for_duplicates

    rows = [_Row(1, "same")]

    check_for_duplicates(lambda: rows, "1", "Name", "same", "taken")


def test_check_if_exists_raises_when_any_row_holds_the_value():
    from service.shared.errors import ConflictError
    from service.shared.validation import check_if_exists

    rows = [_Row("1", "taken")]

    with pytest.raises(ConflictError):
        check_if_exists(lambda: rows, "Name", "taken", "Name already exist")


def test_check_if_exists_passes_on_an_empty_table():
    from service.shared.validation import check_if_exists

    check_if_exists(lambda: [], "Name", "anything", "Name already exist")


def test_return_id_if_exists_answers_the_id_or_none():
    from service.shared.validation import return_id_if_exists

    rows = [_Row("7", "known")]

    assert return_id_if_exists(lambda: rows, "Name", "known") == "7"
    assert return_id_if_exists(lambda: rows, "Name", "unknown") is None
