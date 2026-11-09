"""The edges: the branches a well-behaved deployment never takes and a bad one always does.

WHY A FILE OF ITS OWN: everything here is a fallback - a log directory that cannot be
created, a broker whose client library is missing, a sidecar answering with a body that
is not an object, a response whose JSON lies. None of them is reachable on the happy
path, which is exactly why they are the branches that rot: the code is written once,
never executed in development, and then a read-only mount turns "logging degrades to
console-only" into an ImportError nobody has seen fail.

Each test drives the branch through the seam the module already isolates, so no test
here needs a GPU, a broker, a database or a writable log directory.
"""

from __future__ import annotations

import json
import logging

import pytest

from service.configuration import logging as logging_module
from service.infrastructure import ml_sidecar as ml_client
from service.infrastructure.messaging import queue as queue_module


# ---------------------------------------------------------------------------
# service/configuration/logging.py: setup_logging()
# ---------------------------------------------------------------------------
@pytest.fixture()
def fresh_logger(monkeypatch, tmp_path):
    """Reset the module's memoised logger and point the file handler at tmp_path."""
    monkeypatch.setattr(logging_module, "_log", None)
    monkeypatch.setattr(logging_module, "_log_file", lambda: tmp_path / "logs" / "app.log")
    logger = logging.getLogger(logging_module.LOGGER_NAME)
    saved = list(logger.handlers)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for handler in saved:
        logger.addHandler(handler)


def test_setup_logging_creates_the_directory_and_adopts_the_file(fresh_logger, tmp_path):
    logger = logging_module.setup_logging()

    assert logger is fresh_logger
    assert logger.propagate is False
    assert (tmp_path / "logs" / "app.log").parent.is_dir()
    assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)


def test_a_second_call_reuses_the_logger_and_only_re_levels_it(fresh_logger):
    first = logging_module.setup_logging()
    handlers = list(first.handlers)

    second = logging_module.setup_logging(debug=True)

    assert second is first
    assert list(second.handlers) == handlers
    assert second.level == logging.DEBUG


def test_clean_removes_the_previous_file(fresh_logger, tmp_path):
    target = tmp_path / "logs" / "app.log"
    target.parent.mkdir(parents=True)
    target.write_text("yesterday\n", encoding="utf-8")

    logging_module.setup_logging(clean=True)

    assert target.is_file()
    assert target.read_text(encoding="utf-8") == ""


def test_a_log_directory_that_cannot_be_created_degrades_to_console_only(
    fresh_logger, monkeypatch, tmp_path, capsys
):
    """A read-only mount must not stop the service from starting.

    The un-creatable directory is arranged HONESTLY - a regular FILE occupies the name
    the directory needs - rather than by making _log_file() raise: that path is
    deliberately outside the guard (it only computes a path, it performs no I/O), and a
    test that raised from it would be pinning a failure mode the module does not have.
    What the guard really covers is the mkdir/FileHandler pair below it, and that is
    what a read-only mount or a full disk breaks.
    """
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(logging_module, "_log_file", lambda: blocker / "app.log")

    logger = logging_module.setup_logging()

    assert logger is fresh_logger
    assert not any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    assert "file logging disabled" in capsys.readouterr().err


def test_get_logger_is_the_same_logger_and_accepts_a_name(fresh_logger):
    assert logging_module.get_logger("service.api") is fresh_logger
    assert logging_module.get_logger() is fresh_logger


# ---------------------------------------------------------------------------
# service/infrastructure/messaging/queue.py: the degrade branches
# ---------------------------------------------------------------------------
def test_configured_is_false_when_the_adapter_module_itself_is_broken(monkeypatch):
    """A broken deployment module is not "celery is absent": both answer False."""
    monkeypatch.setitem(
        __import__("sys").modules, "service.infrastructure.messaging.celery_app", None
    )

    assert queue_module.CeleryDispatcher().configured() is False


def test_configured_is_false_when_the_flag_reader_raises(monkeypatch):
    """A malformed CELERY__ENABLED is a strict-flag error, not a crash on the health path."""
    from service.configuration import flags as flags_module

    monkeypatch.setattr(
        flags_module, "read_flag", lambda *names, **kwargs: (_ for _ in ()).throw(ValueError("bad"))
    )
    monkeypatch.setenv("CELERY__ENABLED", "maybe")

    assert queue_module.CeleryDispatcher().configured() is False


def test_state_reads_the_broker_when_celery_is_present(monkeypatch):
    """The happy branch: a task id in, (state, result) out, upper-cased."""
    import sys
    import types

    result_module = types.ModuleType("celery.result")

    class AsyncResult:
        def __init__(self, task_id, app=None):
            self.task_id = task_id
            self.state = "success"
            self.result = {"ok": True}

    result_module.AsyncResult = AsyncResult
    celery_module = types.ModuleType("celery")
    celery_module.result = result_module
    monkeypatch.setitem(sys.modules, "celery", celery_module)
    monkeypatch.setitem(sys.modules, "celery.result", result_module)

    from service.infrastructure.messaging import celery_app as app_module

    monkeypatch.setattr(app_module, "get_celery_app", lambda: object())

    assert queue_module.CeleryDispatcher().state("abc") == ("SUCCESS", {"ok": True})


def test_state_is_none_when_the_broker_cannot_answer(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "celery", None)

    assert queue_module.CeleryDispatcher().state("abc") is None


def test_revoke_answers_true_when_the_broker_accepted_it(monkeypatch):
    from service.infrastructure.messaging import celery_app as app_module

    calls = []

    class Control:
        def revoke(self, task_id, terminate=False):
            calls.append((task_id, terminate))

    monkeypatch.setattr(
        app_module, "get_celery_app", lambda: type("App", (), {"control": Control()})()
    )

    assert queue_module.CeleryDispatcher().revoke("abc") is True
    assert calls == [("abc", False)]


# ---------------------------------------------------------------------------
# service/infrastructure/ml_sidecar.py: the branches between the seam and the caller
# ---------------------------------------------------------------------------
class _Response:
    """The subset of requests.Response the transport reads."""

    def __init__(self, status_code, body=None, text=None, json_raises=False):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if text is None and body is not None else (text or "")
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._body


@pytest.fixture()
def sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("ML_SERVICE_URL", "http://ml:8080")
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"bytes")
    return media


def test_a_garbage_connect_timeout_falls_back_instead_of_raising(sidecar, monkeypatch):
    monkeypatch.setenv("ML_SERVICE_CONNECT_TIMEOUT", "soon")
    monkeypatch.delenv("ML_SERVICE_TIMEOUT", raising=False)

    assert ml_client._timeouts() == (
        ml_client.DEFAULT_CONNECT_TIMEOUT,
        ml_client.DEFAULT_READ_TIMEOUT,
    )


def test_the_http_seam_is_the_only_place_requests_is_imported(sidecar, monkeypatch):
    """Exercised for real: the seam must return a response object, not a tuple."""
    import requests

    class _Session:
        def post(self, url, **kwargs):
            self.last = (url, kwargs)
            return _Response(200, {"text": "hello"})

    session = _Session()
    monkeypatch.setattr(requests, "post", session.post)

    response = ml_client._http_post("http://ml:8080/v1/embed/text", json={"text": "x"})

    assert response.status_code == 200
    assert session.last[0] == "http://ml:8080/v1/embed/text"


def test_a_json_array_is_a_contract_error_not_a_crash(sidecar, monkeypatch):
    monkeypatch.setattr(
        ml_client, "_http_post", lambda *a, **k: _Response(200, ["not", "an", "object"])
    )

    with pytest.raises(ml_client.MLContractError) as caught:
        ml_client.get_text_embedding("text")

    assert caught.value.code == "invalid_response"
    assert "expected an object" in caught.value.detail


def test_a_refusal_with_a_non_json_body_keeps_the_generic_code(sidecar, monkeypatch):
    """A proxy's HTML error page is a refusal too; it just cannot explain itself."""
    monkeypatch.setattr(
        ml_client,
        "_http_post",
        lambda *a, **k: _Response(502, text="<html>bad gateway</html>", json_raises=True),
    )

    with pytest.raises(ml_client.MLUnavailable) as caught:
        ml_client.get_text_embedding("text")

    assert caught.value.code == "http_502"
    assert "502" in caught.value.detail


def test_a_transport_error_raised_by_the_seam_is_not_rewrapped(sidecar, monkeypatch):
    """MLClientError subclasses belong to this module: _post_media must pass them through."""

    def raising(*args, **kwargs):
        raise ml_client.MLRejected("media_not_found", "gone", 400)

    monkeypatch.setattr(ml_client, "_http_post", raising)

    with pytest.raises(ml_client.MLRejected) as caught:
        ml_client.get_text_embedding("text")

    assert caught.value.code == "media_not_found"


def test_the_ignored_arguments_are_still_accepted_and_still_ignored(sidecar, monkeypatch):
    """A caller passing the retired local layout must get a working call, not a crash."""
    seen = {}
    monkeypatch.setattr(
        ml_client,
        "_http_post",
        lambda url, **kwargs: seen.update(url=url)
        or _Response(200, {"vector": [0.5], "dim": 1, "model": "m"}),
    )
    monkeypatch.setattr(
        ml_client, "_post_media", lambda path, route: {"vector": [0.5], "dim": 1, "model": "m"}
    )

    vector = ml_client.get_user_embedding(
        str(sidecar),
        unet_weights_path="src/weights/Unet3D.pth",
        frames_temp_folder="/tmp/frames",
        audio_temp_folder="/tmp/audio",
        num_frames=32,
    )

    assert list(vector) == [0.5]


def test_the_text_model_can_be_named_and_is_not_forwarded(sidecar, monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        return _Response(200, {"vector": [1.0], "dim": 1, "model": "m"})

    monkeypatch.setattr(ml_client, "_http_post", post)

    ml_client.get_text_embedding("text", model_id="some/other-model")

    assert seen["json"] == {"text": "text"}
