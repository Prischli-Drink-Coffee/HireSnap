"""Contract tests for the modules the coverage report found unreached.

Every test here answers the same question from a different angle: what happens to
a caller when the thing underneath is missing? That is the normal state of this
tree - the CI interpreter has no celery, no redis and no boto3 - so the
degrade-instead-of-raise paths are not edge cases, they are the paths a fresh
checkout takes.

WHY THESE MODULES HAD NO TESTS AND STILL MATTERED:
  * `service/configuration/env.py` is the ONE reader of the environment; its
    setter/deleter were never called, so the fix recorded in its docstring (the
    old code wrote `.env` relative to the working directory) had nothing pinning
    it.
  * `service/shared/urls.py` decides what URL a browser is given, and the one
    test that touched it monkeypatched it away - the function had no own test.
  * `service/shared/debug.py` is a string comparison against the literal
    "TRUE"; a debug flag spelled "true" silently logs nothing.
  * `service/infrastructure/uploads.py` writes every uploaded file and every
    embedding; its paths are the contract the database rows quote.
  * `service/composition/repositories.py` maps a slice to the package that owns
    its SQL and had no caller at all.

No test touches the network, MySQL, or the real `.env`: the dotenv writers are
driven against `tmp_path`, and the broker module is exercised through the
sys.modules technique the rest of the suite already uses.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types

import numpy as np
import pytest
from dotenv import dotenv_values

from service.composition import repositories
from service.configuration import env as env_module
from service.infrastructure import uploads
from service.shared import debug as debug_module

# ---------------------------------------------------------------------------
# service/configuration/env.py
# ---------------------------------------------------------------------------


@pytest.fixture
def dotenv_file(monkeypatch, tmp_path):
    """Point the dotenv helper at a throwaway file, not backend/.env.

    WHY NOT let it write backend/.env: `set_key` would CREATE that file in the
    checkout, and a developer's real `.env` would then be appended to by a unit
    test. The helper is the seam the module already uses.
    """
    target = tmp_path / ".env"
    target.write_text("EXISTING=kept\n", encoding="utf-8")
    monkeypatch.setattr(env_module, "env_file", lambda: target)
    yield target
    monkeypatch.undo()


def test_a_present_variable_is_returned_verbatim(monkeypatch):
    monkeypatch.setenv("HIRESNAP_ENV_PROBE", "  spaced  ")
    assert env_module.Env().__getattr__("HIRESNAP_ENV_PROBE") == "  spaced  "


def test_a_missing_variable_raises_attribute_error_and_names_itself(monkeypatch):
    monkeypatch.delenv("HIRESNAP_ABSENT_PROBE", raising=False)
    with pytest.raises(AttributeError) as failure:
        env_module.Env().__getattr__("HIRESNAP_ABSENT_PROBE")
    assert str(failure.value) == (
        "Attribute HIRESNAP_ABSENT_PROBE not found in environment variables"
    )


def test_setting_a_variable_writes_both_the_process_and_the_file(monkeypatch, dotenv_file):
    monkeypatch.delenv("HIRESNAP_SET_PROBE", raising=False)
    env = env_module.Env()
    env.HIRESNAP_SET_PROBE = "written"

    assert os.environ["HIRESNAP_SET_PROBE"] == "written"
    # python-dotenv quotes the value it writes, so the assertion is about the KEY
    # being present and the value surviving a re-read, not about its quoting.
    written = dotenv_file.read_text(encoding="utf-8")
    assert "HIRESNAP_SET_PROBE" in written
    assert dotenv_values(str(dotenv_file))["HIRESNAP_SET_PROBE"] == "written"
    # The value that was already in the file must survive: set_key is a merge,
    # not a rewrite, and a dropped key here would silently reconfigure a service.
    assert "EXISTING=kept" in dotenv_file.read_text(encoding="utf-8")


def test_setting_a_non_string_is_refused_before_anything_is_written(monkeypatch, dotenv_file):
    monkeypatch.delenv("HIRESNAP_SET_PROBE", raising=False)
    with pytest.raises(TypeError) as failure:
        env_module.Env().HIRESNAP_SET_PROBE = 1
    assert str(failure.value) == (
        "Cannot set non-string value as environment variable: HIRESNAP_SET_PROBE"
    )
    assert "HIRESNAP_SET_PROBE" not in os.environ
    assert "HIRESNAP_SET_PROBE" not in dotenv_file.read_text(encoding="utf-8")


def test_deleting_a_variable_unsets_it_in_both_places(monkeypatch, dotenv_file):
    monkeypatch.setenv("HIRESNAP_DEL_PROBE", "here")
    env = env_module.Env()
    env.HIRESNAP_DEL_PROBE = "here"

    del env.HIRESNAP_DEL_PROBE

    assert "HIRESNAP_DEL_PROBE" not in os.environ
    assert "HIRESNAP_DEL_PROBE" not in dotenv_file.read_text(encoding="utf-8")


def test_deleting_an_absent_variable_raises_the_same_attribute_error(monkeypatch):
    monkeypatch.delenv("HIRESNAP_DEL_ABSENT", raising=False)
    with pytest.raises(AttributeError) as failure:
        delattr(env_module.Env(), "HIRESNAP_DEL_ABSENT")
    assert str(failure.value) == (
        "Attribute HIRESNAP_DEL_ABSENT not found in environment variables"
    )


# ---------------------------------------------------------------------------
# service/shared/urls.py
# ---------------------------------------------------------------------------


@pytest.fixture
def url_env(monkeypatch):
    """Every variable the helper may read, set explicitly, and no PUBLIC_BASE_URL."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("HOST", "10.0.0.7")
    monkeypatch.setenv("SERVER_PORT", "8080")
    return monkeypatch


def test_public_base_url_wins_and_its_trailing_slash_is_stripped(url_env):
    url_env.setenv("PUBLIC_BASE_URL", "https://hiresnap.example.com///")
    from service.shared import urls

    assert urls.return_url_object("./data/files/a.mp4") == (
        "https://hiresnap.example.com/data/files/a.mp4"
    )


def test_an_empty_public_base_url_falls_back_to_host_and_port(url_env):
    url_env.setenv("PUBLIC_BASE_URL", "   ")
    from service.shared import urls

    assert urls.return_url_object("./data/files/a.mp4") == ("http://10.0.0.7:8080/data/files/a.mp4")


def test_without_public_base_url_the_original_expression_is_used_unchanged(url_env):
    from service.shared import urls

    assert urls.return_url_object("./data/files/a.mp4") == ("http://10.0.0.7:8080/data/files/a.mp4")


def test_the_helper_still_strips_exactly_two_characters(url_env):
    """The "./" prefix is a database-row convention, not a URL one.

    Pinned because it looks like a typo: it is `url[2:]`, and a future row that
    stops carrying "./" would silently lose its first two characters here.
    """
    from service.shared import urls

    assert urls.return_url_object("./embeddings/users/x.npy").endswith(
        "10.0.0.7:8080/embeddings/users/x.npy"
    )


# ---------------------------------------------------------------------------
# service/shared/debug.py
# ---------------------------------------------------------------------------


def _debug_calls(monkeypatch, debug_value):
    """Call all three helpers with DEBUG set, returning the log records made."""
    monkeypatch.setenv("DEBUG", debug_value)
    records = []
    for level in ("exception", "error", "info"):
        monkeypatch.setattr(
            debug_module.log, level, lambda *a, _level=level, **k: records.append(_level)
        )
    debug_module.debug_ex(RuntimeError("boom"))
    debug_module.debug_err("bad")
    debug_module.debug_info("detail")
    return records


def test_debug_helpers_stay_silent_unless_the_flag_is_the_literal_true(monkeypatch):
    assert _debug_calls(monkeypatch, "true") == []
    assert _debug_calls(monkeypatch, "1") == []
    assert _debug_calls(monkeypatch, "FALSE") == []


def test_debug_helpers_log_when_the_flag_is_the_literal_true(monkeypatch):
    assert _debug_calls(monkeypatch, "TRUE") == ["exception", "error", "info"]


# ---------------------------------------------------------------------------
# service/infrastructure/uploads.py
# ---------------------------------------------------------------------------


class _Upload:
    """The one attribute `write_file_into_server` reads from a FastAPI UploadFile."""

    def __init__(self, filename, payload):
        self.filename = filename
        self._payload = payload

    async def read(self):
        return self._payload


@pytest.fixture
def data_path(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "data"))
    return tmp_path / "data"


def test_an_upload_lands_under_its_namespace_and_keeps_the_extension(data_path):
    name = asyncio.run(uploads.write_file_into_server("videos", _Upload("clip.MP4", b"bytes")))

    assert name.endswith(".MP4")
    written = data_path / "videos" / name
    assert written.read_bytes() == b"bytes"


def test_an_upload_without_a_dot_uses_the_whole_name_as_the_extension(data_path):
    """`split(".")[-1]` on a bare name yields the name itself - pinned, not fixed.

    A file called "clip" therefore lands as "<uuid>.clip". Changing the rule is a
    behaviour change for every caller, so the current one is recorded here.
    """
    name = asyncio.run(uploads.write_file_into_server("videos", _Upload("clip", b"x")))

    assert name.endswith(".clip")


def test_an_embedding_lands_under_embeddings_and_round_trips(data_path):
    vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)

    name = uploads.write_embedding_into_server("users", vector)

    assert name.endswith(".npy")
    stored = data_path / "embeddings" / "users" / name
    assert np.load(stored).tolist() == pytest.approx([1.0, 2.0, 3.0])


def test_two_embeddings_of_the_same_object_do_not_collide(data_path):
    vector = np.zeros(4, dtype=np.float32)

    first = uploads.write_embedding_into_server("vacancies", vector)
    second = uploads.write_embedding_into_server("vacancies", vector)

    assert first != second
    assert len(list((data_path / "embeddings" / "vacancies").iterdir())) == 2


def test_the_namespace_directory_is_created_on_demand(data_path):
    assert not (data_path / "embeddings" / "candidates").exists()

    uploads.write_embedding_into_server("candidates", np.zeros(2, dtype=np.float32))

    assert (data_path / "embeddings" / "candidates").is_dir()


def test_both_writers_require_data_path(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_PATH", raising=False)
    with pytest.raises(AttributeError):
        uploads.write_embedding_into_server("users", np.zeros(2, dtype=np.float32))
    with pytest.raises(AttributeError):
        asyncio.run(uploads.write_file_into_server("videos", _Upload("a.mp4", b"x")))


# ---------------------------------------------------------------------------
# service/composition/repositories.py
# ---------------------------------------------------------------------------


def test_every_documented_domain_resolves_to_an_importable_package():
    for domain in repositories.describe():
        module = repositories.get_repository(domain)
        assert module is not None, domain
        assert module.__name__ == repositories.REPOSITORY_MODULES[domain]


def test_a_slice_without_a_table_of_its_own_resolves_to_none():
    """auth and matching read other slices' tables; they own none.

    The registry says so by omission, and `get_repository` must answer None rather
    than raise: a caller probing "does this slice have SQL?" is asking a question,
    not making a mistake.
    """
    assert repositories.get_repository("auth") is None
    assert repositories.get_repository("matching") is None
    assert repositories.get_repository("jobs") is None


def test_describe_is_a_copy_not_the_live_table():
    described = repositories.describe()
    described["users"] = "somewhere.else"

    assert repositories.describe()["users"] == "service.services.users.persistence"


def test_the_table_names_only_real_slices():
    for domain, module_name in repositories.describe().items():
        assert module_name.endswith(f"service.services.{domain}.persistence")


# ---------------------------------------------------------------------------
# service/infrastructure/messaging/queue.py
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher():
    from service.infrastructure.messaging.queue import CeleryDispatcher

    return CeleryDispatcher()


@pytest.fixture
def no_celery(monkeypatch):
    """Make `import celery` fail no matter what this interpreter has installed.

    WHY THIS IS NOT JUST "the CI interpreter has no celery": it did have one. The
    suite must give the same answer under `requirements.txt` (no celery, the CI
    interpreter) and under a developer venv that also installed
    `requirements-platform.txt` - and the first version of these two tests silently
    assumed the former. Under the latter they did not fail fast, they POSTED to a
    redis that was not there and spent 20 reconnect retries per test before
    answering, which is how a 47-second suite became a 4m38s one inside `make
    lint`.

    `sys.modules[name] = None` is what makes an import raise ImportError even when
    the distribution is installed; the memoised app is dropped because a celery
    built by an earlier test would otherwise be handed back.
    """
    from service.infrastructure.messaging import celery_app as app_module

    monkeypatch.setitem(sys.modules, "celery", None)
    app_module.reset_celery_app()
    yield
    app_module.reset_celery_app()


def test_configured_is_false_when_the_flag_is_off(dispatcher):
    """The flag is the whole answer; the conftest fixture leaves it unset."""
    assert dispatcher.configured() is False


def test_the_flag_alone_counts_as_configured_and_submit_reports_the_missing_package(
    monkeypatch, no_celery, dispatcher
):
    """Flag on, package missing: True here, and the ImportError at submit().

    PINNED ON PURPOSE, because it reads like a bug. If `configured()` also
    checked for the package, `job_service` would take the inline branch silently
    and never record `fallback_reason` - the operator's queue would be off with
    nothing in the job record saying so. Answering True here makes `submit()`
    produce the exact ImportError text the job record shows.
    """
    monkeypatch.setenv("CELERY__ENABLED", "true")
    assert dispatcher.configured() is True

    accepted, task_id, reason = dispatcher.submit("hiresnap.health")

    assert (accepted, task_id) == (False, None)
    assert "celery" in reason


def test_submit_reports_why_it_could_not_queue(no_celery, dispatcher):
    accepted, task_id, reason = dispatcher.submit("hiresnap.health")

    assert accepted is False
    assert task_id is None
    assert reason, "a rejected submit must carry the reason the caller stores"
    assert "celery" in reason.lower() or "No module named" in reason


def test_submit_never_raises_even_when_the_broker_module_is_broken(monkeypatch, dispatcher):
    broken = types.ModuleType("service.infrastructure.messaging.celery_app")

    def _explode():
        raise RuntimeError("broker went away")

    broken.get_celery_app = _explode
    monkeypatch.setitem(sys.modules, "service.infrastructure.messaging.celery_app", broken)

    accepted, task_id, reason = dispatcher.submit("hiresnap.health", {"a": 1})

    assert (accepted, task_id) == (False, None)
    assert "broker went away" in reason


def test_run_executes_the_task_body_in_this_process(dispatcher):
    assert dispatcher.run("hiresnap.health")["status"] in {"ok", "degraded", "failed"}


def test_run_raises_for_an_unknown_task(dispatcher):
    with pytest.raises(KeyError):
        dispatcher.run("hiresnap.nope")


def test_state_answers_none_without_a_broker(dispatcher):
    assert dispatcher.state("not-a-task-id") is None


def test_revoke_answers_false_without_a_broker(dispatcher):
    assert dispatcher.revoke("not-a-task-id") is False


def test_build_job_queue_returns_the_adapter_the_composition_layer_hands_out():
    from service.infrastructure.messaging.queue import CeleryDispatcher, build_job_queue

    assert isinstance(build_job_queue(), CeleryDispatcher)
