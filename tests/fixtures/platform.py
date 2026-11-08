"""Reusable test doubles: one home for the fakes three suites were cloning.

WHY this module and not a pytest plugin: a plugin is discovered implicitly, so a
reader of a test file cannot tell where a fixture came from and a typo in the
plugin's own name silently disables it. A plain import is explicit, works when
the file is executed by something other than pytest (the snapshot tool, a
debugger), and keeps the doubles out of `conftest.py`, whose autouse fixture is
about the environment rather than about objects.

The doubles here are deliberately dumb: they record what they were handed and
return a prepared value. Nothing in tests/fixtures may import a service module,
because these objects are also usable while the service tree is being moved -
an import here would turn "the layout changed" into "every test errors at
collection time".

What lives here, and why each one is shared:

  FakeResponse / FakeHTTP   the single HTTP seam of the ML transport. Two suites
                            drive ml_client through it, and a second copy would
                            drift exactly where the error mapping matters.
  FakeMlClient              a vector-producing stand-in, no torch, no sidecar.
  FakeStorage               the storage facade: `path_for` returns a local path
                            only when the object already lives on this disk, so
                            the object-store branch (read_bytes + materialise)
                            stays reachable in a test.
  FakeJobQueue              records submissions; the celery-shaped tests only
                            care which task and which kwargs were queued.
  FakeClock                 deterministic time for anything that stores
                            timestamps or TTLs. Tests must not sleep: a suite
                            that waits on wall-clock time is slow on a laptop and
                            flaky on a loaded CI runner.
"""

import json
import os


# --------------------------------------------------------------------------- #
# ML transport: the one HTTP seam
# --------------------------------------------------------------------------- #
class FakeResponse(object):
    """The subset of requests.Response that the ML client actually reads.

    Only `status_code`, `text` and `json()` are used by ml_client, so only those
    are provided; a fuller fake would let a future edit rely on `headers` and
    pass here while failing against a real gateway.
    """

    def __init__(self, status_code, body=None, text=None):
        self.status_code = status_code
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(body if body is not None else {})
        self._body = body

    def json(self):
        if self._body is None and self.text is not None:
            raise ValueError("no json body")
        return self._body


class FakeHTTP(object):
    """Records every call and returns a prepared response (or raises).

    `raises` is an instance, not a class, so a test can assert the exact message
    the client surfaces; `calls` keeps url and kwargs so a test can prove the
    request was a POST to /v1/embed/video with `data=` bytes rather than JSON.
    """

    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(dict(kwargs, url=url))
        if self.raises is not None:
            raise self.raises
        return self.response


class FakeMlClient(object):
    """Records the path it was asked to embed and returns a plain vector.

    `get_user_embedding` takes a FILESYSTEM PATH (the sidecar opens the file
    itself), which is the contract the worker seam got wrong once already;
    recording the argument is what makes that regression detectable.
    """

    def __init__(self, vector=(0.5, 0.25), raises=None):
        self.vector = vector
        self.raises = raises
        self.calls = []

    def describe_transport(self):
        return {"transport": "in-process", "service_url": None}

    def get_user_embedding(self, video_path, **kwargs):
        self.calls.append(video_path)
        if self.raises is not None:
            raise self.raises
        return self.vector


# --------------------------------------------------------------------------- #
# Storage facade
# --------------------------------------------------------------------------- #
class FakeStorage(object):
    """Minimal stand-in for the storage facade, with both key shapes.

    `path_for` returns a path only when the object already lives on this disk;
    `read_bytes` is the object-store path that has to be materialised. Keeping
    both in one double is what lets one test prove "no copy happens locally"
    while the next proves "a copy is made and then removed" for S3.
    """

    def __init__(self, local_path=None, payload=b"media-bytes"):
        self.local_path = local_path
        self.payload = payload
        self.puts = []
        self.reads = []

    def path_for(self, key):
        return self.local_path or os.path.join(os.path.dirname(self.payload or ""), "missing")

    def read_bytes(self, key):
        self.reads.append(key)
        return self.payload

    def put_bytes(self, key, data, content_type=None):
        self.puts.append((key, data, content_type))
        return key

    def presigned_url(self, key, expires=None):
        return "https://example.invalid/" + key

    def exists(self, key):
        return False

    def list_objects(self, prefix="", limit=10000):
        return []


# --------------------------------------------------------------------------- #
# Deterministic time
# --------------------------------------------------------------------------- #
class FakeClock(object):
    """A clock the test advances by hand.

    `__call__` makes an instance usable where the code expects a callable
    (`now`), and `time()` covers the epoch-seconds shape the cache backend uses.
    Neither ever returns two different readings for the same instant, so a TTL
    assertion cannot become flaky on a slow machine.
    """

    def __init__(self, start=1_700_000_000.0):
        self._now = float(start)

    def time(self):
        return self._now

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += float(seconds)
        return self._now


# --------------------------------------------------------------------------- #
# Job queue
# --------------------------------------------------------------------------- #
class FakeJobQueue(object):
    """Records submissions instead of reaching a broker.

    `submit(task, **kwargs)` returns a deterministic id so tests can assert the
    id round-trips through the job record; `task_id` mimics the celery
    AsyncResult attribute the queue adapter reads.
    """

    def __init__(self, task_id="task-1"):
        self.task_id = task_id
        self.submitted = []
        self.revoked = []

    def submit(self, task, **kwargs):
        self.submitted.append((task, kwargs))
        result = type("FakeAsyncResult", (object,), {"id": self.task_id})()
        return result

    def revoke(self, task_id, terminate=False):
        self.revoked.append((task_id, terminate))
        return True

    def describe(self):
        return {"backend": "fake", "task_id": self.task_id}
