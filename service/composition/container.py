"""The container: every built object in one place, with one way to ask for it.

WHAT THIS FILE IS FOR: it owns the lifetime of the objects `infra.py` and `services.py`
know how to build. `state.py` used to do this alone; the container splits the wiring (what
is built and in which order) from the access (who asks for it and when). The split matters
because the access is called from a router on every request, while the wiring is read by a
human once.

WHY THE CONTAINER IS LAZY: `describe()` and `get()` exist before anything is built. A
container that eagerly constructed all thirteen slices would open thirteen import chains -
and, through `service.infrastructure.database.connection`, one MySQL client per worker -
at the moment the module was imported. `/server/health` must answer with the database down,
so nothing is built until a route needs it.

WHY A FAILED BUILD IS NOT CACHED: a cache that could not reach redis at boot must not be
remembered as broken for the life of the process. The next call retries, and the backend
behind it already handles a late redis.

WHY THE LOCK IS REENTRANT: building the matching slice asks for nine others, and each of
those may ask for its repository. With a plain Lock the first nested call deadlocks.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from service.composition import infra, services


class Container:
    """The object graph. One per process, held by `state.py`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._platform: Dict[str, Any] = {}
        self._services: Dict[str, Any] = {}

    # --- internals ---------------------------------------------------------
    def _singleton(self, store: Dict[str, Any], key: str, factory: Callable[[], Any]) -> Any:
        """Build once under the lock; a failure is not remembered."""
        existing = store.get(key)
        if existing is not None:
            return existing
        with self._lock:
            existing = store.get(key)
            if existing is not None:
                return existing
            built = factory()
            store[key] = built
            return built

    def resolve(self, domain: str) -> Any:
        """The resolver handed to `services.build_service`: a sibling slice, built once."""
        return self.get_service(domain)

    # --- platform ports ----------------------------------------------------
    def get_infra(self, name: str) -> Any:
        """One platform port by name: cache, storage, mailer or ml_client."""
        if name not in infra.INFRA_FACTORIES:
            raise KeyError(
                "unknown infrastructure port %r; known ports: %s"
                % (name, ", ".join(sorted(infra.INFRA_FACTORIES)))
            )
        return self._singleton(self._platform, name, infra.INFRA_FACTORIES[name])

    def get_cache(self) -> Any:
        return self.get_infra("cache")

    def get_storage(self) -> Any:
        return self.get_infra("storage")

    def get_mailer(self) -> Any:
        return self.get_infra("mailer")

    def get_ml_client(self) -> Any:
        return self.get_infra("ml_client")

    def get_dispatcher(self) -> Any:
        return self.get_infra("dispatcher")

    # --- slice services ----------------------------------------------------
    def get_service(self, domain: str) -> Any:
        """One slice service by domain name, built through `services.build_service`."""
        return self._singleton(
            self._services,
            domain,
            lambda: services.build_service(domain, self.resolve),
        )

    def build_all(self) -> Dict[str, Any]:
        """Build every slice in dependency order and return them. For a warm start or a probe."""
        built: Dict[str, Any] = {}
        for domain in services.BUILD_ORDER:
            built[domain] = self.get_service(domain)
        return built

    # --- introspection -----------------------------------------------------
    def reset(self) -> None:
        """Forget every built object. Used by tests and by a configuration reload."""
        with self._lock:
            self._services.clear()
            self._platform.clear()

    def describe(self) -> Dict[str, Any]:
        """What is built right now, for a post-mortem or a diagnostic endpoint."""
        with self._lock:
            return {
                "services": sorted(self._services),
                "platform": sorted(self._platform),
            }


#: The process-wide container. Tests replace the accessors, not this object, so a test that
#: forgets to restore it cannot leave a container half-filled for the next one - `reset()`
#: is what `state.reset_state()` calls.
_CONTAINER: Optional[Container] = None
_CONTAINER_LOCK = threading.Lock()


def get_container() -> Container:
    """The one container, created on first use."""
    global _CONTAINER
    existing = _CONTAINER
    if existing is not None:
        return existing
    with _CONTAINER_LOCK:
        if _CONTAINER is None:
            _CONTAINER = Container()
        return _CONTAINER


def reset_container() -> None:
    """Drop the container entirely, so the next call builds a fresh one."""
    global _CONTAINER
    with _CONTAINER_LOCK:
        if _CONTAINER is not None:
            _CONTAINER.reset()
        _CONTAINER = None
