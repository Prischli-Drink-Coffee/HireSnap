"""The shapes the application is allowed to depend on, with no driver behind them.

WHY a package rather than four modules that import their implementations: importing this
module must pull in NOTHING - no fastapi, no pymysql, no redis, no celery, no boto3 - so
that a business rule can be type-checked and unit-tested on an image that has none of
them installed. `backend/tests/test_ports_contracts.py` proves that in a subprocess, and
also proves each concrete backend satisfies its protocol by `isinstance`, which is why
every protocol here is `@runtime_checkable` and why the members are ordinary methods
(a Protocol with data members cannot use `issubclass`, so `isinstance` is the portable
check).

WHY the degradation direction differs per port, spelled out because it is a design
decision and not an accident:

  * `Cache` fails OPEN. Redis missing means every read misses, never that the request
    fails: a cache is an optimisation and an optimisation must not be an outage.
  * `FileStorage` fails CLOSED. An upload that cannot be stored raises, because the
    alternative is a 200 for a file that does not exist.
  * `Mailer` fails QUIET. A notification that cannot be sent is logged, not raised.
  * `JobQueue` falls back to running the work INLINE when no broker is configured, which
    is why its contract is "always returns a job id" rather than "always enqueues".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class Cache(Protocol):
    """A best-effort key/value store whose backend may be nothing at all."""

    backend_name: str

    def get(self, key: str, default: Any = None, namespace: str = ...) -> Any: ...

    def set(
        self, key: str, value: Any, ttl: Optional[float] = None, namespace: str = ...
    ) -> bool: ...

    def delete(self, key: str, namespace: str = ...) -> bool: ...

    def get_or_set(
        self, key: str, producer: Any, ttl: Optional[float] = None, namespace: str = ...
    ) -> Any: ...

    def invalidate_prefix(self, prefix: str, namespace: str = ...) -> int: ...

    def ping(self) -> bool: ...

    def describe(self) -> Dict[str, Any]: ...


@runtime_checkable
class FileStorage(Protocol):
    """Where user files live. Local disk by default, S3 when configured."""

    def put_bytes(self, key: str, data: bytes, content_type: str = ...) -> str: ...

    def put_file(self, key: str, source: Any, content_type: str = ...) -> str: ...

    def read_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> bool: ...

    def list_objects(self, prefix: str = ...) -> List[str]: ...

    def public_url(self, key: str) -> str: ...

    def presigned_url(self, key: str, expires_in: Optional[int] = None) -> Optional[str]: ...

    def describe(self) -> Dict[str, Any]: ...


@runtime_checkable
class Mailer(Protocol):
    """Outbound mail. Disabled unless a manifest says otherwise."""

    def send(self, to: str, subject: str, body: str, html: Optional[str] = None) -> bool: ...

    def describe(self) -> Dict[str, Any]: ...


@runtime_checkable
class JobQueue(Protocol):
    """Background work. Runs inline when no broker is configured."""

    def enqueue(
        self, kind: str, payload: Optional[Dict[str, Any]] = None, run_inline: Optional[bool] = None
    ) -> str: ...

    def status(self, job_id: str) -> Dict[str, Any]: ...


@runtime_checkable
class MlClient(Protocol):
    """The inference sidecar, reached over HTTP. There is no in-process fallback."""

    def get_user_embedding(self, video_path: Any, **kwargs: Any) -> List[float]: ...

    def get_text_embedding(self, text: str, model_id: Optional[str] = None) -> List[float]: ...

    def get_video_transcript(self, video_path: Any) -> str: ...

    def describe_transport(self) -> Dict[str, Any]: ...


__all__ = ["Cache", "FileStorage", "JobQueue", "Mailer", "MlClient"]
