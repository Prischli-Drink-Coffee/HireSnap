"""Fallback behaviour of the three infrastructure facades.

Every test here asserts the same property from a different angle: with no redis,
no S3, no SMTP server and no broker, the platform layer still answers - it
degrades, it logs, and it never raises into a request path. That property is the
whole justification for the feature flags, so it is the thing under test.

No test touches the network. The redis backend is exercised through a fake
module injected into sys.modules, which is also the only way to prove the
"package missing" path without uninstalling a dependency.
"""

import sys
import types

import pytest

from src.infra import cache as cache_module
from src.infra import mail as mail_module
from src.infra import storage as storage_module


class _BrokenRedisClient:
    """A redis client whose every operation fails, like a host that went away."""

    def get(self, key):
        raise RuntimeError("connection refused")

    def set(self, key, value):
        raise RuntimeError("connection refused")

    def setex(self, key, ttl, value):
        raise RuntimeError("connection refused")

    def delete(self, *keys):
        raise RuntimeError("connection refused")

    def scan_iter(self, match=None, count=None):
        raise RuntimeError("connection refused")

    def ping(self):
        raise RuntimeError("connection refused")


@pytest.fixture
def broken_redis(monkeypatch):
    """Install a fake "redis" package whose client always fails."""
    module = types.ModuleType("redis")

    class _Redis:
        @staticmethod
        def from_url(url, **kwargs):
            return _BrokenRedisClient()

    module.Redis = _Redis
    monkeypatch.setitem(sys.modules, "redis", module)
    cache_module.reset_cache()
    yield module
    cache_module.reset_cache()


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------
def test_memory_cache_is_the_default_backend():
    """With no flags at all the facade must be usable and process-local."""
    facade = cache_module.build_cache()
    assert facade.backend_name == "memory"
    facade.set("answer", {"value": 42}, namespace="test")
    assert facade.get("answer", namespace="test") == {"value": 42}
    assert facade.get("missing", "default", namespace="test") == "default"


def test_memory_cache_expires_and_evicts():
    """A cache that never drops an entry is a memory leak."""
    facade = cache_module.build_cache()
    facade.set("short", "value", ttl=1, namespace="test")
    assert facade.get("short", namespace="test") == "value"
    # Rewrite the stored deadline rather than sleeping: the test must not depend
    # on wall-clock timing.
    backend = facade._backend
    key = facade.make_key("short", "test")
    expires_at, payload = backend._data[key]
    backend._data[key] = (expires_at - 3600, payload)
    assert facade.get("short", namespace="test") is None

    small = cache_module.Cache(cache_module.MemoryCache(max_entries=2), prefix="p")
    small.set("a", 1, namespace="t")
    small.set("b", 2, namespace="t")
    small.set("c", 3, namespace="t")
    assert small.get("a", namespace="t") is None, "least recently used entry must be evicted"
    assert small.get("c", namespace="t") == 3


def test_cache_keys_are_namespaced():
    """Two tenants sharing redis db 1 must not collide."""
    facade = cache_module.build_cache()
    facade.set("same", "one", namespace="alpha")
    facade.set("same", "two", namespace="beta")
    assert facade.get("same", namespace="alpha") == "one"
    assert facade.get("same", namespace="beta") == "two"
    assert facade.make_key("same", "alpha") == "hiresnap:alpha:same"


def test_cache_invalidate_prefix_only_touches_its_prefix():
    facade = cache_module.build_cache()
    facade.set("user:1", "a", namespace="t")
    facade.set("user:2", "b", namespace="t")
    facade.set("vacancy:1", "c", namespace="t")
    removed = facade.invalidate_prefix("user:", namespace="t")
    assert removed == 2
    assert facade.get("user:1", namespace="t") is None
    assert facade.get("vacancy:1", namespace="t") == "c"


def test_cache_get_or_set_computes_once():
    facade = cache_module.build_cache()
    calls = []

    def producer():
        calls.append(1)
        return {"computed": len(calls)}

    first = facade.get_or_set("k", producer, namespace="t")
    second = facade.get_or_set("k", producer, namespace="t")
    assert first == second == {"computed": 1}
    assert len(calls) == 1


def test_producer_errors_are_not_swallowed():
    """A broken database query must surface; only CACHE operations fail open."""
    facade = cache_module.build_cache()

    def broken():
        raise RuntimeError("database is down")

    with pytest.raises(RuntimeError, match="database is down"):
        facade.get_or_set("k", broken, namespace="t")


def test_corrupt_payload_is_purged_and_reads_as_miss():
    facade = cache_module.build_cache()
    facade._backend.set_raw(facade.make_key("bad", "t"), "{not json", 60)
    assert facade.get("bad", "fallback", namespace="t") == "fallback"
    assert facade._backend.get_raw(facade.make_key("bad", "t")) is None, "corrupt entry must be purged"


def test_noop_backend_never_stores():
    facade = cache_module.build_cache("noop")
    assert facade.backend_name == "noop"
    facade.set("k", "v", namespace="t")
    assert facade.get("k", "miss", namespace="t") == "miss"


def test_unknown_backend_falls_back_to_memory(monkeypatch, caplog):
    monkeypatch.setenv("CACHE__BACKEND", "memcached-typo")
    facade = cache_module.build_cache()
    assert facade.backend_name == "memory", "a typo in an env file must not break the API"


def test_redis_backend_degrades_when_every_call_fails(broken_redis):
    """A redis outage must look exactly like a cache miss."""
    facade = cache_module.build_cache("redis")
    assert facade.backend_name == "redis"
    assert facade.get("k", "miss", namespace="t") == "miss"
    assert facade.set("k", "v", namespace="t") is False
    assert facade.delete("k", namespace="t") is False
    assert facade.invalidate_prefix("k", namespace="t") == 0
    assert facade.ping() is False


def test_redis_backend_degrades_when_library_is_missing(monkeypatch):
    """The base image has no redis package; importing must still work."""
    monkeypatch.setitem(sys.modules, "redis", None)  # "import redis" now raises ImportError
    cache_module.reset_cache()
    facade = cache_module.build_cache("redis")
    assert facade.get("k", "miss", namespace="t") == "miss"
    assert facade.set("k", "v", namespace="t") is False
    assert facade.ping() is False


def test_redis_url_is_redacted_in_logs_and_health():
    facade = cache_module.build_cache("redis")
    described = facade.describe()
    assert "secret" not in str(cache_module._redact("redis://user:secret@host:6379/1"))
    assert "***" in cache_module._redact("redis://user:secret@host:6379/1")
    assert described["backend"] == "redis"


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def test_local_storage_keeps_todays_layout(tmp_path, monkeypatch):
    """Byte-identical to write_file_into_server: <root>/<namespace>/<name>."""
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    backend = storage_module.build_storage("local")
    assert backend.name == "local"
    key = backend.put_bytes("files/abc.mp4", b"video-bytes")
    assert key == "files/abc.mp4"
    assert (tmp_path / "files" / "abc.mp4").read_bytes() == b"video-bytes"
    assert backend.exists("files/abc.mp4") is True
    assert backend.read_bytes("files/abc.mp4") == b"video-bytes"
    assert backend.delete("files/abc.mp4") is True
    assert backend.exists("files/abc.mp4") is False


def test_local_storage_embeddings_namespace_matches_the_request_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    backend = storage_module.build_storage("local")
    backend.put_bytes("embeddings/users/xyz.npy", b"npy")
    assert (tmp_path / "embeddings" / "users" / "xyz.npy").is_file()


def test_local_storage_public_url_matches_the_data_mount(tmp_path, monkeypatch):
    """server.py mounts DATA_PATH at /data, so a key must resolve under /data."""
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://hiresnap.example/")
    backend = storage_module.build_storage("local")
    assert backend.public_url("files/abc.mp4") == "https://hiresnap.example/data/files/abc.mp4"


@pytest.mark.parametrize(
    "key",
    ["/etc/passwd", "C:/windows/system32", "files/../../escape", "", "files//double", "files/a:b", "files\\win"],
)
def test_local_storage_rejects_traversal_keys(key, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    backend = storage_module.build_storage("local")
    with pytest.raises(storage_module.StorageKeyInvalid):
        backend.path_for(key)


def test_storage_falls_back_to_local_when_boto3_is_missing(monkeypatch, tmp_path):
    """STORAGE__BACKEND=s3 without boto3 must not break uploads."""
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    monkeypatch.setenv("STORAGE__BACKEND", "s3")
    monkeypatch.setitem(sys.modules, "boto3", None)  # import boto3 now raises
    storage_module.reset_storage()
    backend = storage_module.build_storage()
    assert backend.name == "local", "a missing driver must degrade to local, not raise"
    backend.put_bytes("files/ok.mp4", b"x")
    assert (tmp_path / "files" / "ok.mp4").is_file()


def test_s3_backend_is_constructed_without_touching_the_network():
    """Constructing the S3 backend must not open a connection."""
    backend = storage_module.S3Storage(
        bucket="b", endpoint="http://storage:9000", access_key="a", secret_key="s"
    )
    described = backend.describe()
    assert described["backend"] == "s3"
    assert described["bucket"] == "b"
    assert described["secure"] is False


def test_s3_backend_reports_unavailable_rather_than_crashing(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)
    backend = storage_module.S3Storage(bucket="b")
    with pytest.raises(storage_module.StorageUnavailable):
        backend.put_bytes("files/a.mp4", b"x")


def test_storage_health_is_cheap_and_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    storage_module.reset_storage()
    health = storage_module.storage_health()
    assert health["backend"] == "local"
    assert isinstance(health["ok"], bool)


# ---------------------------------------------------------------------------
# mail
# ---------------------------------------------------------------------------
def test_mail_is_disabled_by_default():
    mailer = mail_module.get_mailer()
    assert mailer.name == "disabled"
    assert mailer.send("a@example.com", "subject", "body") is False


def test_file_mailer_writes_a_readable_message(tmp_path, monkeypatch):
    """The development sink: no SMTP server, but the reset link is readable."""
    outbox = tmp_path / "outbox.log"
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "file")
    monkeypatch.setenv("MAIL__FILE_PATH", str(outbox))
    mailer = mail_module.get_mailer()
    assert mailer.name == "file"
    assert mailer.send("a@example.com", "Reset", "https://example/reset/1") is True
    content = outbox.read_text(encoding="utf-8")
    assert "a@example.com" in content
    assert "Reset" in content
    assert "https://example/reset/1" in content


def test_smtp_backend_without_host_degrades_to_disabled(monkeypatch):
    """MAIL__BACKEND=smtp with no host is a misconfiguration, not a crash."""
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "smtp")
    monkeypatch.delenv("MAIL__HOST", raising=False)
    assert mail_module.get_mailer().name == "disabled"


def test_smtp_mailer_never_logs_the_password(monkeypatch):
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "smtp")
    monkeypatch.setenv("MAIL__HOST", "mailpit")
    monkeypatch.setenv("MAIL__USER", "user")
    monkeypatch.setenv("MAIL__PASSWORD", "hunter2")
    described = mail_module.get_mailer().describe()
    assert "hunter2" not in str(described)
    assert described["authenticated"] is True


def test_fire_and_forget_send_never_raises(monkeypatch):
    """A broken mailer must not fail the request that triggered it."""

    def explode(*args, **kwargs):
        raise RuntimeError("smtp is down")

    monkeypatch.setattr(mail_module, "send_now", explode)
    thread = mail_module.send_now_or_later("a@example.com", "subject", "body")
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_fire_and_forget_delivers_with_the_file_backend(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox.log"
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "file")
    monkeypatch.setenv("MAIL__FILE_PATH", str(outbox))
    thread = mail_module.send_now_or_later("a@example.com", "Async", "body")
    thread.join(timeout=5)
    assert "Async" in outbox.read_text(encoding="utf-8")


def test_mail_health_never_sends(monkeypatch):
    monkeypatch.setenv("MAIL__ENABLED", "false")
    health = mail_module.mail_health()
    assert health["backend"] == "disabled"
    assert health["ok"] is True
