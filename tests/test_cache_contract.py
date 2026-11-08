"""Cache: the redis backend, the base contract, and every fail-open edge.

WHY this file exists next to test_infra_fallbacks.py: that file proves the
*degradation* story (a dead redis looks like a miss). This one proves the parts a
degradation test cannot reach, because they only execute when redis actually
answers or when a caller hands the facade a broken backend:

  * the redis backend's happy path - get/set/setex/delete/scan batching/ping -
    which is the whole reason the backend exists and had no test at all;
  * the retry backoff window, so a dead redis costs one connect attempt, not one
    per request;
  * the facade's defensive wrappers, which exist so a misbehaving backend can
    never raise into a request path;
  * the configuration fallbacks: a malformed CACHE__TIMEOUT or CACHE__TTL is a
    typo in an env file, not a startup failure.

The fake redis is a module injected into sys.modules, so nothing here needs a
server, a socket or the redis package.
"""

import sys
import types

import pytest

from service.infrastructure import cache as cache_module

LOGGER_NAME = "service.infrastructure.cache"


class FakeRedisClient(object):
    """Minimal redis client: a dict plus the two shapes scan_iter can take."""

    def __init__(self, data=None, fail_on=None, error=None):
        self.data = dict(data or {})
        self.calls = []
        self.fail_on = fail_on
        self.error = error

    def _step(self, name):
        self.calls.append(name)
        if self.fail_on == name:
            raise self.error

    def get(self, key):
        self._step("get")
        return self.data.get(key)

    def set(self, key, value):
        self._step("set")
        self.data[key] = value
        return True

    def setex(self, key, ttl, value):
        self.calls.append(("setex", key, ttl))
        if self.fail_on == "setex":
            raise self.error
        self.data[key] = value
        return True

    def delete(self, *keys):
        self.calls.append(("delete", keys))
        if self.fail_on == "delete":
            raise self.error
        removed = 0
        for key in keys:
            if self.data.pop(key, None) is not None:
                removed += 1
        return removed

    def scan_iter(self, match=None, count=None):
        self.calls.append(("scan_iter", match, count))
        if self.fail_on == "scan_iter":
            raise self.error
        prefix = (match or "").replace("\\", "").rstrip("*")
        for key in list(self.data):
            if key.startswith(prefix):
                yield key

    def ping(self):
        self._step("ping")
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    """Install a fake "redis" package and hand back its client factory."""
    module = types.ModuleType("redis")
    state = {"client": FakeRedisClient(), "raise_on_create": None, "kwargs": None}

    class _Redis(object):
        @staticmethod
        def from_url(url, **kwargs):
            if state["raise_on_create"] is not None:
                raise state["raise_on_create"]
            state["kwargs"] = dict(kwargs)
            state["url"] = url
            return state["client"]

    module.Redis = _Redis
    monkeypatch.setitem(sys.modules, "redis", module)

    def _install(client=None, raise_on_create=None):
        state["client"] = client or FakeRedisClient()
        state["raise_on_create"] = raise_on_create
        return state["client"]

    return types.SimpleNamespace(module=module, state=state, install=_install)


# --------------------------------------------------------------------------- #
# the base contract
# --------------------------------------------------------------------------- #
def test_the_base_backend_is_an_interface_that_refuses_to_guess():
    """Every method raises rather than returning a fake value.

    A backend that silently answered "miss" would hide a wiring bug behind a
    cache miss, which is indistinguishable from a cold cache.
    """
    backend = cache_module.CacheBackend()

    with pytest.raises(NotImplementedError):
        backend.get_raw("k")
    with pytest.raises(NotImplementedError):
        backend.set_raw("k", "v", 1)
    with pytest.raises(NotImplementedError):
        backend.delete_raw("k")
    with pytest.raises(NotImplementedError):
        backend.clear_prefix("p")
    assert backend.ping() is True
    assert backend.describe() == {"backend": "base"}


def test_the_noop_backend_answers_no_to_everything():
    backend = cache_module.NoopCache()

    assert backend.get_raw("k") is None
    assert backend.set_raw("k", "v", 60) is True
    assert backend.delete_raw("k") is False
    assert backend.clear_prefix("p") == 0


def test_memory_backend_ping_and_delete():
    backend = cache_module.MemoryCache()

    assert backend.ping() is True
    backend.set_raw("k", "v", 0)
    assert backend.delete_raw("k") is True
    assert backend.delete_raw("k") is False


# --------------------------------------------------------------------------- #
# _truthy / _bootstrap_env / _glob_escape
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", True),
        ("true", True),
        (" TRUE ", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("off", False),
        ("", False),
        (None, False),
    ],
)
def test_truthy_accepts_the_documented_spellings(value, expected):
    assert cache_module._truthy(value) is expected


def test_bootstrap_env_reads_the_backend_env_file_once(monkeypatch):
    loaded = []
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda path: loaded.append(path)
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.setattr(cache_module._bootstrap_env, "_done", False, raising=False)
    monkeypatch.setattr(cache_module.os.path, "isfile", lambda path: True)

    cache_module._bootstrap_env()
    cache_module._bootstrap_env()

    assert len(loaded) == 1
    assert loaded[0].endswith(".env")


def test_bootstrap_env_skips_the_file_when_it_is_absent(monkeypatch):
    loaded = []
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda path: loaded.append(path)
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.setattr(cache_module._bootstrap_env, "_done", False, raising=False)
    monkeypatch.setattr(cache_module.os.path, "isfile", lambda path: False)

    cache_module._bootstrap_env()

    assert loaded == []


def test_bootstrap_env_survives_a_missing_dotenv(monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setattr(cache_module._bootstrap_env, "_done", False, raising=False)

    cache_module._bootstrap_env()

    assert cache_module._bootstrap_env._done is True


def test_glob_escape_neutralises_scan_metacharacters():
    """An unescaped "*" would invalidate keys the caller never named."""
    assert cache_module._glob_escape("user:*") == "user:\\*"
    assert cache_module._glob_escape("a?b") == "a\\?b"
    assert cache_module._glob_escape("a[b]") == "a\\[b\\]"
    assert cache_module._glob_escape("plain") == "plain"


# --------------------------------------------------------------------------- #
# RedisCache: the happy path
# --------------------------------------------------------------------------- #
def test_redis_client_is_created_lazily_with_short_timeouts(fake_redis):
    fake_redis.install()
    backend = cache_module.RedisCache("redis://cache:6379/1")

    assert backend._client is None, "constructing a backend must not touch the network"
    assert backend.get_raw("k") is None
    assert fake_redis.state["kwargs"]["decode_responses"] is True
    assert fake_redis.state["kwargs"]["socket_timeout"] == cache_module.DEFAULT_TIMEOUT
    assert fake_redis.state["kwargs"]["health_check_interval"] == 30


def test_redis_get_set_delete_round_trip(fake_redis):
    fake_redis.install()
    backend = cache_module.RedisCache("redis://cache:6379/1")

    assert backend.set_raw("k", "payload", 60) is True
    assert backend.get_raw("k") == "payload"
    assert ("setex", "k", 60) in fake_redis.state["client"].calls
    assert backend.delete_raw("k") is True
    assert backend.get_raw("k") is None


def test_redis_set_without_a_ttl_uses_plain_set(fake_redis):
    """ttl=0 means "no expiry", which is redis SET, not SETEX with a huge number."""
    fake_redis.install()
    backend = cache_module.RedisCache("redis://cache:6379/1")

    assert backend.set_raw("k", "payload", 0) is True

    client_calls = fake_redis.state["client"].calls
    names = [call[0] if isinstance(call, tuple) else call for call in client_calls]
    assert "set" in names
    assert not any(isinstance(call, tuple) and call[0] == "setex" for call in client_calls)


def test_redis_ping_answers_through_the_backend(fake_redis):
    fake_redis.install()

    assert cache_module.RedisCache("redis://cache:6379/1").ping() is True


def test_redis_clear_prefix_deletes_in_scanned_batches(fake_redis):
    """A prefix with more than 500 keys is deleted in batches, not one call."""
    client = FakeRedisClient(data={"p:%d" % index: "v" for index in range(1200)})
    fake_redis.install(client)
    backend = cache_module.RedisCache("redis://cache:6379/1")

    removed = backend.clear_prefix("p:")

    assert removed == 1200
    assert client.data == {}
    deletes = [call for call in client.calls if isinstance(call, tuple) and call[0] == "delete"]
    assert len(deletes) == 3  # 500 + 500 + 200


def test_redis_clear_prefix_handles_a_partial_final_batch(fake_redis):
    client = FakeRedisClient(data={"p:1": "v", "p:2": "v"})
    fake_redis.install(client)

    assert cache_module.RedisCache("redis://x/1").clear_prefix("p:") == 2


def test_redis_clear_prefix_with_exactly_one_full_batch(fake_redis):
    """A scan that ends on a 500 boundary must not issue an empty DELETE."""
    client = FakeRedisClient(data={"p:%d" % index: "v" for index in range(500)})
    fake_redis.install(client)

    assert cache_module.RedisCache("redis://x/1").clear_prefix("p:") == 500

    deletes = [call for call in client.calls if isinstance(call, tuple) and call[0] == "delete"]
    assert len(deletes) == 1
    assert len(deletes[0][1]) == 500


def test_redis_describe_reports_the_redacted_url_and_nothing_else():
    described = cache_module.RedisCache("redis://user:secret@host:6379/1").describe()

    assert described == {"backend": "redis", "url": "redis://***:***@host:6379/1"}


# --------------------------------------------------------------------------- #
# RedisCache: failures and the backoff window
# --------------------------------------------------------------------------- #
def test_a_failed_client_creation_degrades_and_backs_off(fake_redis, monkeypatch):
    fake_redis.install(raise_on_create=RuntimeError("bad url"))
    backend = cache_module.RedisCache("redis://x/1", backoff=30)

    assert backend.get_raw("k") is None
    assert backend._blocked_until > 0

    calls_before = len(fake_redis.state["client"].calls)
    assert backend.get_raw("k") is None
    assert len(fake_redis.state["client"].calls) == calls_before, (
        "inside the backoff window no new connection may be attempted"
    )


def test_the_backoff_window_expires(fake_redis, monkeypatch):
    """A cache that never retries after one outage is a cache turned off forever."""
    fake_redis.install(raise_on_create=RuntimeError("bad url"))
    backend = cache_module.RedisCache("redis://x/1", backoff=30)
    backend.get_raw("k")

    monkeypatch.setattr(cache_module.time, "time", lambda: backend._blocked_until + 1)
    fake_redis.state["raise_on_create"] = None
    client = fake_redis.state["client"]
    client.data["k"] = "back"

    assert backend.get_raw("k") == "back"


@pytest.mark.parametrize("operation", ["get", "setex", "delete", "scan_iter", "ping"])
def test_every_redis_operation_degrades_instead_of_raising(fake_redis, operation):
    """One table for all five: the invariant is per operation, not per method."""
    client = FakeRedisClient(data={"p:1": "v"}, fail_on=operation)
    fake_redis.install(client)
    backend = cache_module.RedisCache("redis://x/1", backoff=30)
    backend._client = client  # skip the lazy creation so the fake failures are reached

    if operation == "get":
        assert backend.get_raw("p:1") is None
    elif operation == "setex":
        assert backend.set_raw("p:1", "v", 60) is False
    elif operation == "delete":
        assert backend.delete_raw("p:1") is False
    elif operation == "scan_iter":
        assert backend.clear_prefix("p:") == 0
    else:
        assert backend.ping() is False

    assert backend._blocked_until > 0


@pytest.mark.parametrize("operation", ["delete", "clear"])
def test_operations_without_a_client_answer_no(fake_redis, monkeypatch, operation):
    """No client yet (blocked, missing library) is a miss for every write too."""
    monkeypatch.setitem(sys.modules, "redis", None)
    backend = cache_module.RedisCache("redis://x/1")

    if operation == "delete":
        assert backend.delete_raw("k") is False
    else:
        assert backend.clear_prefix("p") == 0

    assert backend._client is None


def test_the_double_checked_lock_reuses_a_client_created_concurrently(fake_redis):
    """Two threads entering _client_or_none must not create two clients.

    Simulated by a lock whose acquisition installs the client: that is exactly the
    state the second thread sees after the first one released the lock.
    """
    fake_redis.install()
    backend = cache_module.RedisCache("redis://x/1")

    class _RacingLock(object):
        def __enter__(self):
            backend._client = "already-created-by-another-thread"
            return self

        def __exit__(self, *exc_info):
            return False

    backend._lock = _RacingLock()

    assert backend._client_or_none() == "already-created-by-another-thread"
    assert fake_redis.state["kwargs"] is None, "no second client may be built"


def test_a_missing_redis_package_is_sticky(fake_redis, monkeypatch):
    """Re-importing a package that is not installed on every call is pure overhead."""
    monkeypatch.setitem(sys.modules, "redis", None)
    backend = cache_module.RedisCache("redis://x/1")

    assert backend.get_raw("k") is None
    assert backend._missing_library is True

    # Even if the package appeared later, the backend stays degraded for this process.
    monkeypatch.setitem(sys.modules, "redis", fake_redis.module)
    assert backend.get_raw("k") is None


def test_default_arguments_are_clamped():
    backend = cache_module.RedisCache("", timeout="0", backoff="-5")

    assert backend._timeout == 0.05, "a zero timeout would hang the request path"
    assert backend._backoff == 0.0
    assert backend._log_url == ""


# --------------------------------------------------------------------------- #
# Cache facade: a backend that misbehaves must not escape
# --------------------------------------------------------------------------- #
class HostileBackend(object):
    """A backend where every operation raises. The facade must absorb all of it."""

    name = "hostile"

    def get_raw(self, key):
        raise RuntimeError("get exploded")

    def set_raw(self, key, payload, ttl):
        raise RuntimeError("set exploded")

    def delete_raw(self, key):
        raise RuntimeError("delete exploded")

    def clear_prefix(self, prefix):
        raise RuntimeError("clear exploded")

    def ping(self):
        raise RuntimeError("ping exploded")

    def describe(self):
        raise RuntimeError("describe exploded")


def test_facade_absorbs_every_failing_backend_operation():
    facade = cache_module.Cache(HostileBackend())

    assert facade.get("k", "default") == "default"
    assert facade.set("k", "v") is False
    assert facade.delete("k") is False
    assert facade.invalidate_prefix("k") == 0
    assert facade.ping() is False
    assert facade.describe() == {
        "backend": "hostile",
        "prefix": "hiresnap",
        "default_ttl": cache_module.DEFAULT_TTL,
    }


def test_facade_refuses_to_cache_an_unserialisable_value():
    """A non-JSON value is a caller bug: report it, do not store a repr()."""
    facade = cache_module.Cache(cache_module.MemoryCache())

    class Unserialisable(object):
        def __repr__(self):
            raise RuntimeError("no repr either")

    assert facade.set("k", Unserialisable()) is False
    assert facade.get("k", "miss") == "miss"


def test_facade_serialises_dates_through_the_default_hook():
    import datetime

    facade = cache_module.Cache(cache_module.MemoryCache())
    facade.set("stamp", {"at": datetime.date(2024, 1, 2)})

    assert facade.get("stamp") == {"at": "2024-01-02"}


def test_facade_prefix_strips_colons_and_falls_back():
    assert cache_module.Cache(cache_module.MemoryCache(), prefix=":tenant:")._prefix == "tenant"
    assert cache_module.Cache(cache_module.MemoryCache(), prefix="")._prefix == "hiresnap"
    assert cache_module.Cache(cache_module.MemoryCache(), prefix=None)._prefix == "hiresnap"


def test_facade_unknown_backend_name_is_reported_as_unknown():
    class Anonymous(object):
        def get_raw(self, key):
            return None

    assert cache_module.Cache(Anonymous()).backend_name == "unknown"


# --------------------------------------------------------------------------- #
# configuration fallbacks
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "variable, value",
    [
        ("CACHE__TIMEOUT", "not-a-number"),
        ("CACHE__RETRY_BACKOFF", "soon"),
        ("CACHE__MAX_ENTRIES", "many"),
        ("CACHE__TTL", "forever"),
    ],
)
def test_a_malformed_setting_falls_back_to_the_default(monkeypatch, variable, value):
    """A typo in an env file must not take the API down at startup."""
    monkeypatch.setenv("CACHE__BACKEND", "memory")
    monkeypatch.setenv(variable, value)

    facade = cache_module.build_cache()

    assert facade.backend_name == "memory"
    assert facade._default_ttl == cache_module.DEFAULT_TTL


@pytest.mark.parametrize(
    "variable",
    ["CACHE__TIMEOUT", "CACHE__RETRY_BACKOFF"],
)
def test_redis_ignores_a_malformed_timeout_or_backoff(monkeypatch, fake_redis, variable):
    """A typo here must not stop the backend from being built."""
    monkeypatch.setenv("CACHE__BACKEND", "redis")
    monkeypatch.setenv(variable, "soon")

    backend = cache_module.build_cache("redis")._backend

    assert backend._timeout == cache_module.DEFAULT_TIMEOUT
    assert backend._backoff == cache_module.DEFAULT_BACKOFF


def test_redis_timeout_and_backoff_read_from_the_environment(monkeypatch, fake_redis):
    fake_redis.install()
    monkeypatch.setenv("CACHE__REDIS_URL", "redis://configured:6379/2")
    monkeypatch.setenv("CACHE__TIMEOUT", "0.25")
    monkeypatch.setenv("CACHE__RETRY_BACKOFF", "12")

    backend = cache_module.build_cache("redis")._backend

    assert backend._timeout == 0.25
    assert backend._backoff == 12.0


def test_unknown_backend_is_announced(monkeypatch, caplog):
    monkeypatch.setenv("CACHE__BACKEND", "memcached-typo")

    with caplog.at_level("WARNING", logger=LOGGER_NAME):
        cache_module.build_cache()

    assert any("unknown_backend" in record.message for record in caplog.records)


def test_get_cache_memoises_per_backend_name(monkeypatch):
    first = cache_module.get_cache()
    second = cache_module.get_cache()

    assert first is second


def test_cache_health_never_touches_a_socket_for_memory(monkeypatch):
    monkeypatch.setenv("CACHE__BACKEND", "memory")
    cache_module.reset_cache()

    described = cache_module.cache_health()

    assert described == {"backend": "memory", "ok": True, "key_prefix": "hiresnap"}


def test_cache_health_pings_only_the_redis_backend(monkeypatch, fake_redis):
    fake_redis.install()
    monkeypatch.setenv("CACHE__BACKEND", "redis")
    cache_module.reset_cache()

    described = cache_module.cache_health()

    assert described["backend"] == "redis"
    assert described["ok"] is True
    assert "ping" in fake_redis.state["client"].calls
