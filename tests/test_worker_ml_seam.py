"""The worker -> ML seam: it must speak the sidecar contract, not a guess.

Why this file exists: the first implementation of the worker ML seam (then src/workers/tasks.py,
# now service/infrastructure/messaging/tasks.py) probed for
modules named src.modelling.ml_client / ml.client and fell back to POSTing JSON
{"file_key","path"} to "/embed". None of those exist. Nothing failed at import
time, no test covered the call, and the mismatch would only surface as a failed
nightly job on the first real upload.

These tests pin the three things that were wrong, each against the real contract
in docs/SIDECARS.md and backend/service/services/ml_client.py:

  1. The embedder is reached through service.services.ml_client.get_user_embedding,
     which takes a FILESYSTEM PATH, not a key/URL pair.
  2. The response is a vector (a sequence of floats), which
     service/services/ml_client.py decodes from {"vector": [...], "dim": N}.
  3. The temporary file materialised for an object-store backend is removed
     afterwards, and a failure in the embedder does not leak it either.

They need neither torch nor the sidecar: the client module is replaced by a fake
that records what it was handed.
"""

import os

import pytest

from service.infrastructure.messaging import tasks as tasks_module


class FakeStorage(object):
    """Minimal stand-in for the storage facade, with both key shapes.

    `path_for` returns a path only when the object already lives on this disk;
    `read_bytes` is the object-store path that has to be materialised.
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


class FakeMlClient(object):
    """Records the path it was asked to embed and returns a plain vector."""

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


def _use(monkeypatch, storage, client):
    monkeypatch.setattr(tasks_module, "_storage", lambda: storage)
    monkeypatch.setattr(tasks_module, "_ml_client", lambda: client)


# ---------------------------------------------------------------------------
# _local_media_path: how an object becomes a path the sidecar can open
# ---------------------------------------------------------------------------
def test_existing_source_path_is_used_as_is(tmp_path):
    """A caller-resolved path must not be copied: it is already the media."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    storage = FakeStorage()

    path, temporary = tasks_module._local_media_path(storage, "files/clip.mp4", str(media))

    assert path == str(media)
    assert temporary is False
    assert storage.reads == []


def test_local_backend_path_is_used_without_copying(tmp_path):
    """LocalStorage holds the bytes; re-reading them would double the I/O."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    storage = FakeStorage(local_path=str(media))

    path, temporary = tasks_module._local_media_path(storage, "files/clip.mp4")

    assert path == str(media)
    assert temporary is False
    assert storage.reads == []


def test_object_store_object_is_materialised_and_flagged_temporary(tmp_path):
    """S3/MinIO hands back bytes, so a real file has to exist for the sidecar."""
    storage = FakeStorage(payload=b"object-store-bytes")
    storage.local_path = str(tmp_path / "gone.mp4")

    path, temporary = tasks_module._local_media_path(storage, "files/a/b/clip.mp4")
    try:
        assert temporary is True
        assert os.path.isfile(path)
        assert open(path, "rb").read() == b"object-store-bytes"
        # The extension is load-bearing: ffmpeg picks its demuxer partly from it.
        assert path.endswith(".mp4")
        assert storage.reads == ["files/a/b/clip.mp4"]
    finally:
        if os.path.isfile(path):
            os.unlink(path)


def test_materialised_file_survives_an_extensionless_key(tmp_path):
    storage = FakeStorage(payload=b"x")
    storage.local_path = str(tmp_path / "gone")

    path, temporary = tasks_module._local_media_path(storage, "files/noextension")
    try:
        assert temporary is True
        assert not path.endswith(".")
    finally:
        if os.path.isfile(path):
            os.unlink(path)


# ---------------------------------------------------------------------------
# the client seam itself
# ---------------------------------------------------------------------------
def test_ml_client_is_the_supported_module():
    """The one supported seam is service.services.ml_client (docs/SIDECARS.md 4)."""
    client = tasks_module._ml_client()
    assert client.__name__ == "service.services.ml_client"
    assert hasattr(client, "get_user_embedding")


def test_missing_client_is_reported_as_unavailable(monkeypatch):
    """A broken install must raise TaskUnavailable, not ImportError at import time."""
    import sys

    import service.services as services_package
    import service.services.ml_client  # noqa: F401 - make the submodule importable first

    # None in sys.modules is how CPython represents "this module cannot be
    # imported"; the parent attribute has to go too, or "from x import y" would
    # still find the already-imported object.
    monkeypatch.delattr(services_package, "ml_client", raising=False)
    monkeypatch.setitem(sys.modules, "service.services.ml_client", None)

    with pytest.raises(tasks_module.TaskUnavailable, match="ml_client_unavailable"):
        tasks_module._ml_client()


# ---------------------------------------------------------------------------
# embed_candidate end to end, against the fake client
# ---------------------------------------------------------------------------
def test_embed_candidate_hands_the_client_a_path_not_a_key(monkeypatch, tmp_path):
    """The regression that matters: key/URL in, vector out is the wrong contract."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    storage = FakeStorage(local_path=str(media))
    client = FakeMlClient()
    _use(monkeypatch, storage, client)

    result = tasks_module.embed_candidate("c1", "files/clip.mp4", source_path=str(media))

    assert client.calls == [str(media)]
    assert result["status"] == tasks_module.STATUS_OK
    assert result["embedder"] == "ml_client:in-process"
    assert result["embedding_key"].startswith(tasks_module.EMBEDDING_PREFIX)
    assert result["embedding_key"].endswith(".npy")
    assert result["result_url"].startswith("https://example.invalid/")


def test_embed_candidate_stores_the_vector_under_a_deterministic_key(monkeypatch, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    storage = FakeStorage(local_path=str(media))
    _use(monkeypatch, storage, FakeMlClient(vector=b"raw-vector-bytes"))

    first = tasks_module.embed_candidate("c1", "files/clip.mp4", source_path=str(media))
    second = tasks_module.embed_candidate("c1", "files/clip.mp4", source_path=str(media))

    # Same inputs, same key: a broker redelivery overwrites instead of orphaning.
    assert first["embedding_key"] == second["embedding_key"]
    assert [put[0] for put in storage.puts] == [first["embedding_key"]] * 2
    assert storage.puts[0][1] == b"raw-vector-bytes"
    assert storage.puts[0][2] == "application/octet-stream"


def test_embed_candidate_removes_the_materialised_temporary(monkeypatch, tmp_path):
    storage = FakeStorage(payload=b"object-store-bytes")
    storage.local_path = str(tmp_path / "gone.mp4")
    _use(monkeypatch, storage, FakeMlClient())

    captured = {}
    client = FakeMlClient()
    original = client.get_user_embedding

    def spy_wrapper(video_path, **kwargs):
        captured["path"] = video_path
        assert os.path.isfile(video_path), "the sidecar needs a materialised file"
        return original(video_path, **kwargs)

    client.get_user_embedding = spy_wrapper
    _use(monkeypatch, storage, client)

    tasks_module.embed_candidate("c1", "files/a/clip.mp4")

    assert not os.path.exists(captured["path"]), "the temp file outlived the task"


def test_embed_candidate_cleans_up_after_a_failure(monkeypatch, tmp_path):
    """A failing embedder must not leave media behind on the worker's disk."""
    storage = FakeStorage(payload=b"object-store-bytes")
    storage.local_path = str(tmp_path / "gone.mp4")
    client = FakeMlClient()
    original = client.get_user_embedding
    captured = {}

    def exploding(video_path, **kwargs):
        captured["path"] = video_path
        return original(video_path, **kwargs)

    client.get_user_embedding = exploding
    client.raises = RuntimeError("GPU on fire")
    monkeypatch.setattr(tasks_module, "_ml_client", lambda: client)
    monkeypatch.setattr(tasks_module, "_storage", lambda: storage)

    with pytest.raises(RuntimeError, match="GPU on fire"):
        tasks_module.embed_candidate("c1", "files/a/clip.mp4")

    assert captured["path"] and not os.path.exists(captured["path"])
