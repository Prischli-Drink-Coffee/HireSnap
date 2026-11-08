"""Worker tasks: the branches that only run when something is missing or failing.

WHY this file exists separately from test_worker_ml_seam.py: that file pins the
ML *contract* (a filesystem path in, a vector out, the temporary file removed).
This one pins the module's other half - the paths that execute when a dependency
is absent, a broker rejects a dispatch, a delete fails, or the database is down.
Those are the branches a worker actually takes at 03:00, and they were the least
covered part of the tree (54% branch coverage) because they are invisible in a
successful request.

The rule the assertions follow: a task that could not do its work must say so in
its result instead of reporting success, and a task that must not act (no
referenced-file list) must act on nothing. So the assertions are about the
reported status and the side effects (which objects were deleted, which tasks
were dispatched, which files were written), not about log formatting.

Nothing here needs celery, redis, boto3, numpy, the sidecar or MySQL: every
external edge is replaced, and the fakes come from tests/fixtures/platform.py so
there is one definition of each.
"""

import hashlib
import io
import logging
import os
import sys
import time

import pytest

from service.infrastructure.messaging import tasks as tasks_module
from tests.fixtures.platform import FakeMlClient, FakeStorage

LOGGER_NAME = "service.infrastructure.messaging.tasks"
NOW = 1_700_000_000.0


class ListingStorage(FakeStorage):
    """FakeStorage plus the three calls cleanup/reindex make on a real backend.

    The base fake returns an empty listing because the ML-seam tests never list
    anything. Overriding instead of copying keeps the object-store behaviour
    (read_bytes for a key with no local path) in one place.
    """

    def __init__(self, objects=(), payload=b"media-bytes"):
        FakeStorage.__init__(self, payload=payload)
        self.objects = [dict(item) for item in objects]
        self.listings = []
        self.deleted = []
        self.delete_fails = set()

    def list_objects(self, prefix="", limit=10000):
        self.listings.append((prefix, limit))
        return [dict(item) for item in self.objects]

    def delete(self, key):
        if key in self.delete_fails:
            raise RuntimeError("delete refused for %s" % (key,))
        self.deleted.append(key)


def _use_storage(monkeypatch, storage):
    monkeypatch.setattr(tasks_module, "_storage", lambda: storage)


def _freeze_time(monkeypatch, value=NOW):
    """Pin the clock cleanup_orphan_uploads reads.

    The age filter compares "now" with each object's modified_at, so a test that
    used the wall clock would pass today and fail after the process had been up
    long enough to cross the grace window.
    """
    monkeypatch.setattr(time, "time", lambda: value)


# --------------------------------------------------------------------------- #
# _env: presence, not truthiness
# --------------------------------------------------------------------------- #
def test_env_takes_the_first_name_that_is_present_even_when_empty(monkeypatch):
    """An empty value is a decision ("" is falsy but set), so it must win.

    WHY: the two spellings exist because compose renders a variable that may be
    unset; a variable that is set to the empty string must shadow the other
    spelling, otherwise a deliberate "unset it here" could never be expressed.
    """
    monkeypatch.setenv("HIRESNAP_ENV_FIRST", "")
    monkeypatch.setenv("HIRESNAP_ENV_SECOND", "second")

    assert tasks_module._env("HIRESNAP_ENV_FIRST", "HIRESNAP_ENV_SECOND", default="d") == ""


def test_env_falls_back_to_the_default_when_no_name_is_present(monkeypatch):
    monkeypatch.delenv("HIRESNAP_ENV_FIRST", raising=False)
    monkeypatch.delenv("HIRESNAP_ENV_SECOND", raising=False)

    assert tasks_module._env("HIRESNAP_ENV_FIRST", "HIRESNAP_ENV_SECOND", default="d") == "d"


# --------------------------------------------------------------------------- #
# _npy_bytes: the .npy container other writers produce
# --------------------------------------------------------------------------- #
def test_npy_bytes_returns_raw_bytes_untouched():
    payload = b"already-serialised"

    assert tasks_module._npy_bytes(payload) is payload
    assert tasks_module._npy_bytes(bytearray(b"abc")) == b"abc"


def test_npy_bytes_serialises_a_sequence():
    np = pytest.importorskip("numpy")

    payload = tasks_module._npy_bytes([0.5, 0.25])

    assert payload[:6] == b"\x93NUMPY"
    assert list(np.load(io.BytesIO(payload))) == [0.5, 0.25]


def test_npy_bytes_reports_a_missing_numpy_as_unavailable(monkeypatch):
    """A vector that cannot be serialised must fail loudly, not store garbage."""
    monkeypatch.setitem(sys.modules, "numpy", None)

    with pytest.raises(tasks_module.TaskUnavailable, match="numpy_missing"):
        tasks_module._npy_bytes([0.1])


def test_media_is_materialised_for_a_backend_without_path_for(monkeypatch, tmp_path):
    """An object store exposes no filesystem path, so the branch must skip that probe.

    Guarding with callable() rather than hasattr() is what lets a backend be
    duck-typed: a fake (and S3Storage) simply has no path_for.
    """

    class _NoPathStorage(object):
        def __init__(self):
            self.reads = []

        def read_bytes(self, key):
            self.reads.append(key)
            return b"object-bytes"

    storage = _NoPathStorage()

    path, temporary = tasks_module._local_media_path(storage, "files/a/clip.mp4")
    try:
        assert temporary is True
        assert os.path.basename(path).endswith(".mp4")
        assert open(path, "rb").read() == b"object-bytes"
        assert storage.reads == ["files/a/clip.mp4"]
    finally:
        if os.path.isfile(path):
            os.unlink(path)


def test_storage_is_the_process_wide_backend(monkeypatch):
    """_storage() must return the memoised singleton, not construct a second one.

    Two backends in one worker would mean two caches of presigned URLs and two
    different bucket roots.
    """
    from service.infrastructure import storage as storage_module

    sentinel = object()
    monkeypatch.setattr(storage_module, "get_storage", lambda: sentinel)

    assert tasks_module._storage() is sentinel


def test_storage_is_reported_as_unavailable_when_the_module_cannot_be_imported(monkeypatch):
    """A broken install is a task-level failure with a reason, not an ImportError."""
    monkeypatch.setitem(sys.modules, "service.infrastructure.storage", None)

    with pytest.raises(tasks_module.TaskUnavailable, match="storage_unavailable"):
        tasks_module._storage()


# --------------------------------------------------------------------------- #
# _invalidate: best effort by construction
# --------------------------------------------------------------------------- #
def test_invalidate_returns_the_number_the_cache_reported(monkeypatch):
    import service.infrastructure.cache as cache_module

    class _Cache(object):
        def __init__(self):
            self.calls = []

        def invalidate_prefix(self, prefix, namespace=None):
            self.calls.append((prefix, namespace))
            return 3

    cache = _Cache()
    monkeypatch.setattr(cache_module, "get_cache", lambda: cache)

    assert tasks_module._invalidate("embeddings/", namespace="embeddings") == 3
    assert cache.calls == [("embeddings/", "embeddings")]


def test_invalidate_swallows_a_cache_failure(monkeypatch, caplog):
    """A stale cache entry is not a failed job: log and keep the task's success."""
    import service.infrastructure.cache as cache_module

    class _BrokenCache(object):
        def invalidate_prefix(self, prefix, namespace=None):
            raise RuntimeError("redis went away")

    monkeypatch.setattr(cache_module, "get_cache", lambda: _BrokenCache())

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        assert tasks_module._invalidate("embeddings/") == 0

    assert "cache_invalidate_failed" in caplog.text


def test_invalidate_reports_zero_when_the_cache_module_cannot_be_imported(monkeypatch):
    monkeypatch.setitem(sys.modules, "service.infrastructure.cache", None)

    assert tasks_module._invalidate("embeddings/") == 0


# --------------------------------------------------------------------------- #
# embed_candidate: the failure paths around a successful embedding
# --------------------------------------------------------------------------- #
def test_embed_candidate_rejects_a_missing_argument():
    """Both inputs feed the idempotency key; guessing either one writes a wrong object."""
    with pytest.raises(ValueError, match="requires candidate_id and file_key"):
        tasks_module.embed_candidate("", "files/a/clip.mp4")
    with pytest.raises(ValueError, match="requires candidate_id and file_key"):
        tasks_module.embed_candidate("c1", "")


def test_embed_candidate_survives_a_temp_file_that_cannot_be_deleted(monkeypatch, caplog, tmp_path):
    """Cleanup must never mask the result: a leftover temp file is a warning.

    The object-store branch materialises the media into a temporary file. If the
    OS refuses the unlink (a Windows handle, a read-only temp dir) the embedding
    was still stored, so the task reports success and logs the leftover.
    """
    storage = ListingStorage(payload=b"object-store-bytes")
    # An object-store key has no local file: the fake must answer a path that
    # does not exist so the materialise-then-delete branch is the one taken.
    storage.local_path = str(tmp_path / "absent.mp4")
    client = FakeMlClient(vector=(0.5, 0.25))
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "_ml_client", lambda: client)
    monkeypatch.setattr(tasks_module, "_invalidate", lambda prefix, namespace=None: 0)

    def refuse(path):
        raise OSError("file is in use")

    monkeypatch.setattr(tasks_module.os, "unlink", refuse)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = tasks_module.embed_candidate("c1", "files/a/clip.mp4")

    assert result["status"] == tasks_module.STATUS_OK
    assert "temp_cleanup_failed" in caplog.text
    # Proves "embedder" is derived from describe_transport(), not hardcoded.
    assert result["embedder"] == "ml_client:%s" % (
        client.describe_transport().get("transport", "unknown"),
    )


# --------------------------------------------------------------------------- #
# reindex_embeddings: find the gap, enqueue one file at a time
# --------------------------------------------------------------------------- #
def _digest_for(file_key):
    """The presence hint reindex builds: sha1(""|key|MODEL_VERSION)[:32].

    Recomputed here rather than imported so the test states the algorithm it
    relies on; if the task changes it, this test fails instead of silently
    matching.
    """
    return hashlib.sha1(
        ("%s|%s|%s" % ("", file_key, tasks_module.MODEL_VERSION)).encode("utf-8")
    ).hexdigest()[:32]


def test_reindex_enqueues_only_the_uploads_without_an_embedding(monkeypatch):
    covered = "files/covered.mp4"
    missing = "files/missing.mp4"
    storage = ListingStorage(objects=[{"key": covered}, {"key": missing}])
    existing = [{"key": "%s%s.npy" % (tasks_module.EMBEDDING_PREFIX, _digest_for(covered))}]
    original_list = storage.list_objects

    def list_objects(prefix="", limit=10000):
        if prefix.startswith("embeddings/"):
            storage.listings.append((prefix, limit))
            return [dict(item) for item in existing]
        return original_list(prefix, limit)

    monkeypatch.setattr(storage, "list_objects", list_objects)
    _use_storage(monkeypatch, storage)
    dispatched = []
    monkeypatch.setattr(
        tasks_module,
        "dispatch",
        lambda name, kwargs=None, **rest: dispatched.append((name, kwargs)),
    )

    result = tasks_module.reindex_embeddings(limit=10)

    assert result == {
        "status": tasks_module.STATUS_OK,
        "scanned": 2,
        "queued": 1,
        "skipped": 1,
        "dispatched": 1,
    }
    assert dispatched == [
        ("hiresnap.embed_candidate", {"candidate_id": "missing", "file_key": missing})
    ]


def test_reindex_stops_at_the_limit(monkeypatch):
    """A nightly run is bounded: the next night continues where this one stopped."""
    storage = ListingStorage(objects=[{"key": "files/a.mp4"}, {"key": "files/b.mp4"}])
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "dispatch", lambda name, kwargs=None, **rest: None)

    result = tasks_module.reindex_embeddings(limit=1)

    assert (result["queued"], result["dispatched"]) == (1, 1)


def test_reindex_falls_back_to_two_hundred_on_a_bad_limit(monkeypatch):
    storage = ListingStorage()
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "dispatch", lambda name, kwargs=None, **rest: None)

    tasks_module.reindex_embeddings(limit="not-a-number")

    # 200 * 10: the listing over-fetches so a full reindex is not silently short.
    assert storage.listings == [("files/", 2000), ("embeddings/users", 2000)]


def test_reindex_keeps_going_when_one_dispatch_is_rejected(monkeypatch, caplog):
    """One bad file must not stop the batch; the count reports what was accepted."""
    storage = ListingStorage(objects=[{"key": "files/a.mp4"}, {"key": "files/b.mp4"}])
    _use_storage(monkeypatch, storage)

    def dispatch(name, kwargs=None, **rest):
        if kwargs["file_key"] == "files/a.mp4":
            raise RuntimeError("broker refused")

    monkeypatch.setattr(tasks_module, "dispatch", dispatch)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = tasks_module.reindex_embeddings(limit=10)

    assert (result["queued"], result["dispatched"]) == (2, 1)
    assert "status=partial" in caplog.text


# --------------------------------------------------------------------------- #
# cleanup_orphan_uploads: deleting nothing versus deleting a video
# --------------------------------------------------------------------------- #
def test_cleanup_deletes_nothing_when_the_database_is_unreachable(monkeypatch):
    """THE rule of this task: no reference list means no deletion, ever."""
    storage = ListingStorage(objects=[{"key": "files/old.mp4", "modified_at": NOW - 90000}])
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "_referenced_keys", lambda: None)

    result = tasks_module.cleanup_orphan_uploads()

    assert result == {
        "status": tasks_module.STATUS_SKIPPED,
        "reason": "database_unavailable",
        "scanned": 1,
        "deleted": 0,
    }
    assert storage.deleted == []


def test_cleanup_deletes_only_old_unreferenced_objects(monkeypatch):
    _freeze_time(monkeypatch)
    storage = ListingStorage(
        objects=[
            {"key": "files/old-orphan.mp4", "modified_at": NOW - 90000},
            {"key": "files/old-referenced.mp4", "modified_at": NOW - 90000},
            {"key": "files/fresh.mp4", "modified_at": NOW - 10},
        ]
    )
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "_referenced_keys", lambda: {"old-referenced.mp4"})
    invalidated = []
    monkeypatch.setattr(
        tasks_module,
        "_invalidate",
        lambda prefix, namespace=None: invalidated.append((prefix, namespace)),
    )

    result = tasks_module.cleanup_orphan_uploads()

    assert storage.deleted == ["files/old-orphan.mp4"]
    assert result == {
        "status": tasks_module.STATUS_OK,
        "scanned": 3,
        "candidates": 1,
        "deleted": 1,
        "dry_run": False,
    }
    # The files listing is cached too: deleting without invalidating would serve
    # a listing of objects that no longer exist.
    assert invalidated == [("files/", "files")]


def test_cleanup_dry_run_reports_candidates_without_deleting(monkeypatch):
    _freeze_time(monkeypatch)
    storage = ListingStorage(objects=[{"key": "files/old.mp4", "modified_at": NOW - 90000}])
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "_referenced_keys", lambda: set())
    invalidated = []
    monkeypatch.setattr(
        tasks_module, "_invalidate", lambda prefix, namespace=None: invalidated.append(prefix)
    )

    result = tasks_module.cleanup_orphan_uploads(dry_run=True)

    assert storage.deleted == []
    assert (result["candidates"], result["deleted"], result["dry_run"]) == (1, 1, True)
    # A dry run reports what it WOULD have deleted by appending to the same list,
    # so `deleted` counts candidates and the cache is invalidated as if it had.
    # Pinned rather than called a defect: a dry run is a rehearsal of the real run.
    assert invalidated == ["files/"]


def test_cleanup_does_not_count_a_failed_delete(monkeypatch, caplog):
    _freeze_time(monkeypatch)
    storage = ListingStorage(objects=[{"key": "files/old.mp4", "modified_at": NOW - 90000}])
    storage.delete_fails = {"files/old.mp4"}
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "_referenced_keys", lambda: set())
    monkeypatch.setattr(tasks_module, "_invalidate", lambda prefix, namespace=None: 0)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = tasks_module.cleanup_orphan_uploads()

    assert (result["candidates"], result["deleted"]) == (1, 0)
    assert "status=partial" in caplog.text


def test_cleanup_falls_back_to_a_one_day_grace_window(monkeypatch):
    """A malformed threshold must widen the window, not delete everything.

    float() of a bad value falls back to 86400, so an object younger than a day
    survives. The assertion is the survival, not the constant.
    """
    _freeze_time(monkeypatch)
    storage = ListingStorage(objects=[{"key": "files/half-day.mp4", "modified_at": NOW - 43200}])
    _use_storage(monkeypatch, storage)
    monkeypatch.setattr(tasks_module, "_referenced_keys", lambda: set())
    monkeypatch.setattr(tasks_module, "_invalidate", lambda prefix, namespace=None: 0)

    result = tasks_module.cleanup_orphan_uploads(older_than_seconds="yesterday")

    assert (result["candidates"], storage.deleted) == (0, [])


# --------------------------------------------------------------------------- #
# _referenced_keys: the two ways it can answer "I do not know"
# --------------------------------------------------------------------------- #
def test_referenced_keys_reports_an_import_failure_as_unknown(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "service.services.files.persistence", None)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert tasks_module._referenced_keys() is None

    assert "reason=repository_import" in caplog.text


def test_referenced_keys_reports_a_database_failure_as_unknown(monkeypatch, caplog):
    import service.services.files.persistence as persistence

    def explode():
        raise RuntimeError("Can't connect to MySQL server")

    monkeypatch.setattr(persistence, "get_all_files", explode)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert tasks_module._referenced_keys() is None

    assert "reason=database_unreachable" in caplog.text


def test_referenced_keys_collects_basenames_from_models_and_dicts(monkeypatch):
    """The repository returns models today; the dict branch keeps it usable in a probe."""
    import service.services.files.persistence as persistence

    class _Row(object):
        Url = "files/uuid-1.mp4"

    monkeypatch.setattr(
        persistence,
        "get_all_files",
        lambda: [_Row(), {"Url": "files/uuid-2.mp4"}, {"Url": None}, _Row()],
    )

    assert tasks_module._referenced_keys() == {"uuid-1.mp4", "uuid-2.mp4"}


# --------------------------------------------------------------------------- #
# send_email: at-most-once, and reported as such
# --------------------------------------------------------------------------- #
def test_send_email_rejects_a_missing_recipient_or_subject():
    with pytest.raises(ValueError, match="requires to and subject"):
        tasks_module.send_email("", "subject", "body")
    with pytest.raises(ValueError, match="requires to and subject"):
        tasks_module.send_email("someone@example.com", "", "body")


def test_send_email_reports_delivery(monkeypatch):
    import service.infrastructure.mail as mail_module

    calls = []
    monkeypatch.setattr(
        mail_module,
        "send_now",
        lambda to, subject, body, html=None: calls.append((to, subject, body, html)) or True,
    )

    result = tasks_module.send_email("a@example.com", "s", "b", html="<p>b</p>")

    assert result == {
        "status": tasks_module.STATUS_OK,
        "to": "a@example.com",
        "subject": "s",
        "sent": True,
    }
    assert calls == [("a@example.com", "s", "b", "<p>b</p>")]


def test_send_email_reports_a_disabled_mailer_as_skipped(monkeypatch):
    """A mailer that refused is not a delivered message, so the status must differ."""
    import service.infrastructure.mail as mail_module

    monkeypatch.setattr(mail_module, "send_now", lambda *a, **k: False)

    result = tasks_module.send_email("a@example.com", "s", "b")

    assert (result["status"], result["sent"]) == (tasks_module.STATUS_SKIPPED, False)


# --------------------------------------------------------------------------- #
# health: a worker health report probes, unlike the HTTP endpoint
# --------------------------------------------------------------------------- #
def _stub_probes(monkeypatch, **overrides):
    import service.infrastructure.cache as cache_module
    import service.infrastructure.mail as mail_module
    import service.infrastructure.storage as storage_module

    healthy = {"ok": True}
    for module, name in (
        (cache_module, "cache_health"),
        (storage_module, "storage_health"),
        (mail_module, "mail_health"),
    ):
        value = overrides.get(name, healthy)
        if isinstance(value, Exception):

            def raiser(_value=value):
                raise _value

            monkeypatch.setattr(module, name, raiser)
        else:
            monkeypatch.setattr(module, name, lambda _value=value: dict(_value))


def test_health_reports_every_probe(monkeypatch):
    _stub_probes(monkeypatch)
    monkeypatch.setenv("HOSTNAME", "worker-7")

    report = tasks_module.health()

    assert report["status"] == tasks_module.STATUS_OK
    assert report["worker"] == "worker-7"
    assert sorted(report["checks"]) == ["cache", "mail", "storage"]


def test_health_treats_a_missing_ok_flag_as_healthy(monkeypatch):
    """A probe that reports something other than an ok flag must not fail the worker.

    The default is deliberate: an informational probe (backend name, root path)
    means the dependency answered, and only an explicit ok=False is a failure.
    """
    _stub_probes(monkeypatch, cache_health={"backend": "memory"})

    assert tasks_module.health()["status"] == tasks_module.STATUS_OK


def test_health_fails_when_a_probe_reports_not_ok(monkeypatch):
    _stub_probes(monkeypatch, storage_health={"ok": False})

    assert tasks_module.health()["status"] == tasks_module.STATUS_FAILED


@pytest.mark.parametrize(
    "probe, name",
    [
        ("cache_health", "cache"),
        ("storage_health", "storage"),
        ("mail_health", "mail"),
    ],
)
def test_health_captures_a_raising_probe_instead_of_failing(monkeypatch, probe, name):
    """Every probe is individually guarded: one broken dependency must not blank the report."""
    _stub_probes(monkeypatch, **{probe: RuntimeError("%s down" % (name,))})

    report = tasks_module.health()

    assert report["status"] == tasks_module.STATUS_FAILED
    assert report["checks"][name] == {"ok": False, "error": "%s down" % (name,)}
    # The other probes still answered.
    assert len(report["checks"]) == 3


# --------------------------------------------------------------------------- #
# dispatch: celery when configured, the same body inline otherwise
# --------------------------------------------------------------------------- #
def test_get_task_refuses_an_unknown_name_and_lists_the_known_ones():
    with pytest.raises(KeyError) as excinfo:
        tasks_module.get_task("hiresnap.nope")

    message = str(excinfo.value)
    assert "unknown task %r" % ("hiresnap.nope",) in message
    for name in tasks_module.TASKS:
        assert name in message


def test_dispatch_runs_inline_when_asked_to(monkeypatch):
    """The inline path is what makes a job runnable on a laptop with no redis."""
    calls = []
    monkeypatch.setitem(tasks_module.TASKS, "hiresnap.probe", lambda **kw: calls.append(kw))

    result = tasks_module.dispatch("hiresnap.probe", {"answer": 42}, async_mode=False)

    assert calls == [{"answer": 42}]
    assert result is None


def test_dispatch_defaults_to_inline_when_the_celery_module_cannot_be_imported(monkeypatch):
    calls = []
    monkeypatch.setitem(tasks_module.TASKS, "hiresnap.probe", lambda **kw: calls.append(kw))
    monkeypatch.setitem(sys.modules, "service.infrastructure.messaging.celery_app", None)

    tasks_module.dispatch("hiresnap.probe", {"answer": 1})

    assert calls == [{"answer": 1}]


def test_dispatch_sends_to_the_broker_when_configured(monkeypatch):
    import service.infrastructure.messaging.celery_app as celery_module

    class _App(object):
        def __init__(self):
            self.sent = []

        def send_task(self, name, kwargs=None):
            self.sent.append((name, kwargs))
            return "task-id-1"

    app = _App()
    monkeypatch.setattr(celery_module, "is_configured", lambda: True)
    monkeypatch.setattr(celery_module, "get_celery_app", lambda: app)

    result = tasks_module.dispatch("hiresnap.embed_candidate", {"candidate_id": "c1"})

    assert result == "task-id-1"
    assert app.sent == [("hiresnap.embed_candidate", {"candidate_id": "c1"})]


def test_dispatch_copies_the_kwargs_it_was_given(monkeypatch):
    """dispatch must not mutate the caller's dict: the job record keeps its copy."""
    import service.infrastructure.messaging.celery_app as celery_module

    class _App(object):
        def send_task(self, name, kwargs=None):
            return name

    monkeypatch.setattr(celery_module, "is_configured", lambda: True)
    monkeypatch.setattr(celery_module, "get_celery_app", lambda: _App())
    payload = {"candidate_id": "c1"}

    tasks_module.dispatch("hiresnap.embed_candidate", payload, async_mode=True)

    assert payload == {"candidate_id": "c1"}


def test_task_unavailable_is_a_runtime_error():
    """Callers catch RuntimeError-shaped failures from a task runner; keep the base."""
    assert issubclass(tasks_module.TaskUnavailable, RuntimeError)
