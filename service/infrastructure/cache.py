"""Cache facade: one interface, three backends, no request-path surprises.

WHY this module exists (mirror of R:/GPTHub/backend/service/infrastructure/cache):
the API answers the same read-heavy questions on every request - a candidate's
file list, a vacancy's personality weights - with a fresh MySQL round trip each
time. Redis is the obvious relief valve, but making Redis a hard dependency
would mean the API can no longer start without a second container. So the
backend is selected by CACHE__BACKEND and the two default-safe options need no
infrastructure at all:

  memory  (default) in-process dictionary with per-entry expiry and an LRU cap.
          Correctness-equivalent to "no cache" for the single uvicorn worker the
          stack runs today, and it can never serve another process's data.
  redis   shared cache for multi-worker / multi-container deployments.
  noop    explicit off switch for load tests and for "is the cache lying to me".

Invariants, mirroring GPTHub and the SERVICE_CONTRACTS fail-open rule:
  * No method raises into the request path. A cache miss and a cache outage are
    indistinguishable to the caller: both return the default. A cache is an
    optimisation, never a source of truth.
  * The redis package is imported lazily inside the factory, so
    "import service.infrastructure.cache" works on the base image that ships without it.
  * Keys are always namespaced "<CACHE__PREFIX>:<namespace>:<key>". Several
    consumers share redis db 1 (operational state), and business data stays in
    MySQL; the prefix is what keeps those tenants from colliding.
  * Values are JSON. That forces callers to cache plain data instead of live
    ORM/DB objects, and it makes a redis deployment and a memory deployment
    behave identically.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

DEFAULT_NAMESPACE = "default"
DEFAULT_TTL = 300
DEFAULT_MAX_ENTRIES = 2048
DEFAULT_TIMEOUT = 0.5
DEFAULT_BACKOFF = 30.0

_TRUTHY = {"1", "true", "yes", "on", "enabled", "y"}


def _env(*names, default=""):
    """First of the given environment names that is present, else the default.

    WHY two names: the env manifest in docker/.env.example uses the
    double-underscore nesting form (CACHE__BACKEND) and compose may inject
    either that key or its flattened form (CACHE_BACKEND) depending on how the
    service block maps it. Accepting both keeps the code independent of that
    mapping. An explicitly empty value counts as present, so an operator can
    blank a key without it silently falling back to a default.
    """
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _truthy(value):
    return str(value or "").strip().lower() in _TRUTHY


def _bootstrap_env():
    """Load backend/.env once, best effort.

    WHY: compose injects flat environment variables, so in the stack nothing is
    needed here. On a developer machine the values live in backend/.env and are
    normally read by env.Env. We deliberately do not import env.Env: its
    __getattr__ raises AttributeError for any key that is not configured, which
    is exactly the wrong behaviour for optional feature flags. A missing
    python-dotenv is not an error either - the base image installs it, but this
    module must stay importable without it.
    """
    if getattr(_bootstrap_env, "_done", False):
        return
    _bootstrap_env._done = True
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - only on a bare install
        logger.debug("cache: python-dotenv not installed, using the process environment")
        return
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(backend_dir, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path)
    except Exception:  # pragma: no cover - defensive
        logger.debug("cache: could not load backend/.env, using the process environment")


def _redact(url):
    """Drop credentials from a connection URL before it reaches a log line."""
    return re.sub(r"//([^:/@]+)(:[^@/]*)?@", "//***:***@", str(url or ""))


def _glob_escape(value):
    """Escape redis SCAN glob metacharacters in a literal prefix.

    WHY: invalidate_prefix takes a caller-supplied literal string; an unescaped
    "*" would silently invalidate keys the caller never named.
    """
    return re.sub(r"([\\*?\[\]])", r"\\\1", str(value))


class CacheBackend(object):
    """Minimal contract every backend implements. Payloads are already encoded."""

    name = "base"

    def get_raw(self, key):
        raise NotImplementedError

    def set_raw(self, key, payload, ttl):
        raise NotImplementedError

    def delete_raw(self, key):
        raise NotImplementedError

    def clear_prefix(self, prefix):
        raise NotImplementedError

    def ping(self):
        return True

    def describe(self):
        return {"backend": self.name}


class MemoryCache(CacheBackend):
    """Process-local cache. The default, and the behaviour with no infrastructure.

    A single uvicorn process is what the stack runs today, so a process-local
    cache cannot leave a stale entry visible to a different worker. Entries are
    capped and evicted least-recently-used first, because an unbounded dict in a
    long-lived API process is a memory leak with a friendly name.
    """

    name = "memory"

    def __init__(self, max_entries=DEFAULT_MAX_ENTRIES):
        self._lock = threading.Lock()
        self._data = OrderedDict()
        self._max_entries = max(1, int(max_entries))

    def get_raw(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, payload = item
            if expires_at is not None and expires_at <= time.time():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return payload

    def set_raw(self, key, payload, ttl):
        expires_at = None if not ttl or ttl <= 0 else time.time() + float(ttl)
        with self._lock:
            self._data[key] = (expires_at, payload)
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)
        return True

    def delete_raw(self, key):
        with self._lock:
            return self._data.pop(key, None) is not None

    def clear_prefix(self, prefix):
        removed = 0
        with self._lock:
            for key in [k for k in self._data if k.startswith(prefix)]:
                self._data.pop(key, None)
                removed += 1
        return removed

    def ping(self):
        return True


class NoopCache(CacheBackend):
    """Deliberately always-miss backend, for load tests and cache debugging."""

    name = "noop"

    def get_raw(self, key):
        return None

    def set_raw(self, key, payload, ttl):
        return True

    def delete_raw(self, key):
        return False

    def clear_prefix(self, prefix):
        return 0


class RedisCache(CacheBackend):
    """Redis backend with lazy connection, short timeouts and a failure backoff.

    Design decisions worth stating:

    * The client is created on first use, not in __init__, so constructing the
      backend (and therefore importing this module) never touches the network.
    * Every socket operation has a short timeout. A cache must fail in
      milliseconds; a 30 second default timeout would turn a redis outage into a
      request timeout, which is strictly worse than a miss.
    * After a transport failure the backend stops retrying for
      CACHE__RETRY_BACKOFF seconds. Without that, a dead redis adds one failed
      connect attempt and one warning log line to every request.
    * A missing redis package is sticky: the base image legitimately does not
      install it, and re-importing it on every call is pure overhead.
    """

    name = "redis"

    def __init__(self, url, timeout=DEFAULT_TIMEOUT, backoff=DEFAULT_BACKOFF, log_url=None):
        self._url = url or ""
        self._timeout = max(0.05, float(timeout))
        self._backoff = max(0.0, float(backoff))
        self._log_url = log_url or _redact(self._url)
        self._client = None
        self._lock = threading.Lock()
        self._missing_library = False
        self._blocked_until = 0.0

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        if self._missing_library:
            return None
        if time.time() < self._blocked_until:
            return None
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                import redis  # lazy on purpose: the base image has no redis package
            except Exception:
                self._missing_library = True
                logger.warning(
                    "cache backend=redis status=unavailable code=cache_library_missing "
                    "action=every call becomes a cache miss"
                )
                return None
            try:
                self._client = redis.Redis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_connect_timeout=self._timeout,
                    socket_timeout=self._timeout,
                    health_check_interval=30,
                )
            except Exception as exc:
                self._degrade("client_create_failed", exc)
                return None
        return self._client

    def _degrade(self, code, exc):
        self._blocked_until = time.time() + self._backoff
        logger.warning(
            "cache backend=redis status=degraded code=%s url=%s error=%s "
            "action=serving misses for %.0fs",
            code,
            self._log_url,
            exc,
            self._backoff,
        )

    def get_raw(self, key):
        client = self._client_or_none()
        if client is None:
            return None
        try:
            return client.get(key)
        except Exception as exc:
            self._degrade("get_failed", exc)
            return None

    def set_raw(self, key, payload, ttl):
        client = self._client_or_none()
        if client is None:
            return False
        try:
            if ttl and ttl > 0:
                client.setex(key, max(1, int(ttl)), payload)
            else:
                client.set(key, payload)
            return True
        except Exception as exc:
            self._degrade("set_failed", exc)
            return False

    def delete_raw(self, key):
        client = self._client_or_none()
        if client is None:
            return False
        try:
            return bool(client.delete(key))
        except Exception as exc:
            self._degrade("delete_failed", exc)
            return False

    def clear_prefix(self, prefix):
        client = self._client_or_none()
        if client is None:
            return 0
        removed = 0
        try:
            batch = []
            for key in client.scan_iter(match=_glob_escape(prefix) + "*", count=500):
                batch.append(key)
                if len(batch) >= 500:
                    removed += int(client.delete(*batch) or 0)
                    batch = []
            if batch:
                removed += int(client.delete(*batch) or 0)
        except Exception as exc:
            self._degrade("scan_failed", exc)
        return removed

    def ping(self):
        client = self._client_or_none()
        if client is None:
            return False
        try:
            return bool(client.ping())
        except Exception as exc:
            self._degrade("ping_failed", exc)
            return False

    def describe(self):
        return {"backend": self.name, "url": self._log_url}


class Cache(object):
    """Facade used by the rest of the application.

    Callers never see a backend, a socket or a JSON error. The contract is:
    get returns the value or the default; set/delete return a bool that is
    advisory only (a False set is not an application error); get_or_set computes
    at most once per miss.
    """

    def __init__(self, backend, prefix="hiresnap", default_ttl=DEFAULT_TTL):
        self._backend = backend
        self._prefix = str(prefix or "hiresnap").strip(":") or "hiresnap"
        self._default_ttl = int(default_ttl or 0)

    @property
    def backend_name(self):
        return getattr(self._backend, "name", "unknown")

    def make_key(self, key, namespace=DEFAULT_NAMESPACE):
        return "%s:%s:%s" % (self._prefix, namespace or DEFAULT_NAMESPACE, key)

    def get(self, key, default=None, namespace=DEFAULT_NAMESPACE):
        full = self.make_key(key, namespace)
        try:
            raw = self._backend.get_raw(full)
        except Exception as exc:  # defensive: a backend must never escape
            logger.warning("cache status=degraded code=get_failed key=%s error=%s", full, exc)
            return default
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            # A payload written by an incompatible version must not poison the
            # caller forever: purge it and let the next call repopulate.
            logger.warning("cache status=corrupt_payload key=%s action=purge", full)
            self.delete(key, namespace=namespace)
            return default

    def set(self, key, value, ttl=None, namespace=DEFAULT_NAMESPACE):
        full = self.make_key(key, namespace)
        effective_ttl = self._default_ttl if ttl is None else ttl
        try:
            payload = json.dumps(value, default=str)
        except Exception as exc:
            logger.warning("cache status=not_serialisable key=%s error=%s", full, exc)
            return False
        try:
            return bool(self._backend.set_raw(full, payload, effective_ttl))
        except Exception as exc:
            logger.warning("cache status=degraded code=set_failed key=%s error=%s", full, exc)
            return False

    def delete(self, key, namespace=DEFAULT_NAMESPACE):
        full = self.make_key(key, namespace)
        try:
            return bool(self._backend.delete_raw(full))
        except Exception as exc:
            logger.warning("cache status=degraded code=delete_failed key=%s error=%s", full, exc)
            return False

    def get_or_set(self, key, producer, ttl=None, namespace=DEFAULT_NAMESPACE):
        """Return the cached value, or compute it with producer() and store it.

        producer() errors are NOT swallowed: it is normally a database read, and
        hiding a broken query behind "cache returned None" would be the worst of
        both worlds. Only cache operations are fail-open.
        """
        sentinel = _MISS
        hit = self.get(key, sentinel, namespace=namespace)
        if hit is not sentinel:
            return hit
        value = producer()
        self.set(key, value, ttl=ttl, namespace=namespace)
        return value

    def invalidate_prefix(self, prefix, namespace=DEFAULT_NAMESPACE):
        """Remove every entry whose key starts with prefix inside one namespace.

        Returns the number of removed entries, which is 0 whenever the backend is
        degraded. Correctness never depends on that number.
        """
        full = self.make_key(prefix, namespace)
        try:
            return int(self._backend.clear_prefix(full) or 0)
        except Exception as exc:
            logger.warning(
                "cache status=degraded code=invalidate_failed prefix=%s error=%s", full, exc
            )
            return 0

    def ping(self):
        try:
            return bool(self._backend.ping())
        except Exception:
            return False

    def describe(self):
        try:
            data = dict(self._backend.describe())
        except Exception:
            data = {"backend": self.backend_name}
        data.setdefault("prefix", self._prefix)
        data.setdefault("default_ttl", self._default_ttl)
        return data


_MISS = object()


def build_cache(backend=None):
    """Build a facade for the requested backend name (default: the env flag)."""
    _bootstrap_env()
    name = (
        str(backend or _env("CACHE__BACKEND", "CACHE_BACKEND", default="memory")).strip().lower()
        or "memory"
    )
    prefix = _env("CACHE__PREFIX", "CACHE_PREFIX", default="hiresnap")
    default_ttl = _env("CACHE__TTL", "CACHE_TTL", default=str(DEFAULT_TTL))

    if name == "redis":
        url = _env("CACHE__REDIS_URL", "CACHE_REDIS_URL", default="redis://localhost:6379/1")
        timeout = _env("CACHE__TIMEOUT", "CACHE_TIMEOUT", default=str(DEFAULT_TIMEOUT))
        backoff = _env("CACHE__RETRY_BACKOFF", "CACHE_RETRY_BACKOFF", default=str(DEFAULT_BACKOFF))
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError):
            timeout_value = DEFAULT_TIMEOUT
        try:
            backoff_value = float(backoff)
        except (TypeError, ValueError):
            backoff_value = DEFAULT_BACKOFF
        backend_obj = RedisCache(url, timeout=timeout_value, backoff=backoff_value)
    elif name == "noop":
        backend_obj = NoopCache()
    else:
        # Unknown values intentionally fall back to memory instead of raising:
        # a typo in an env file must not take the API down.
        if name != "memory":
            logger.warning("cache status=unknown_backend value=%s action=using memory", name)
        max_entries = _env(
            "CACHE__MAX_ENTRIES", "CACHE_MAX_ENTRIES", default=str(DEFAULT_MAX_ENTRIES)
        )
        try:
            max_entries_value = int(max_entries)
        except (TypeError, ValueError):
            max_entries_value = DEFAULT_MAX_ENTRIES
        backend_obj = MemoryCache(max_entries=max_entries_value)

    try:
        ttl_value = int(default_ttl)
    except (TypeError, ValueError):
        ttl_value = DEFAULT_TTL
    return Cache(backend_obj, prefix=prefix, default_ttl=ttl_value)


# Annotated for the same reason as _STORAGES in service/infrastructure/storage.py: the mypy
# gate rejects an unannotated empty-dict module global, and the concrete backend
# type is picked at runtime from CACHE__BACKEND.
_CACHES: dict[str, object] = {}
_CACHES_LOCK = threading.Lock()


def get_cache():
    """Process-wide facade for the configured backend.

    Cached per backend name so an env change plus reset_cache() (tests) or a
    process restart is enough to switch, and so connection setup happens once.
    """
    name = (
        str(_env("CACHE__BACKEND", "CACHE_BACKEND", default="memory")).strip().lower() or "memory"
    )
    with _CACHES_LOCK:
        existing = _CACHES.get(name)
        if existing is not None:
            return existing
        created = build_cache(name)
        _CACHES[name] = created
        return created


def reset_cache():
    """Drop cached facades (tests and configuration reloads only)."""
    with _CACHES_LOCK:
        _CACHES.clear()


def cache_health():
    """Small, non-blocking health payload for the platform health task."""
    facade = get_cache()
    return {
        "backend": facade.backend_name,
        "ok": bool(facade.ping()) if facade.backend_name == "redis" else True,
        "key_prefix": facade._prefix,
    }
