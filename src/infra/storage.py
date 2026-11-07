"""Object storage abstraction: local disk today, S3/MinIO when a bucket exists.

WHY this module exists: HireSnap writes uploaded videos into DATA_PATH and serves
them back through the /data static mount (backend/src/pipeline/server.py mounts
that directory). That works on one host and fails the moment there are two. The
platform layer adds an S3 backend, but the local backend must stay byte-identical
- same directory layout, same file names, same bytes - because existing rows in
MySQL hold keys like "files/<uuid>.mp4" and the frontend already resolves them
through PUBLIC_BASE_URL. A changed layout would orphan every stored upload.

Layout contract (kept from backend/src/utils/write_file_into_server.py):
    <DATA_PATH>/<namespace>/<file name>          e.g. files/8f1c....mp4
    <DATA_PATH>/embeddings/<namespace>/<name>    e.g. embeddings/users/8f1c....npy

Backend selection: STORAGE__BACKEND=local (default) | s3.

Failure semantics differ from the cache on purpose. A cache is an optimisation
and fails open silently; storage holds user data and fails closed. If S3 is
selected but boto3 is missing we log once and fall back to local *because the
write still succeeds and the returned key still resolves through /data* - the
only thing that changes is where the bytes live, and the log line plus the health
payload make that visible. Once a backend is in use, an operation that fails
raises StorageError: silently dropping an upload would lose the user's file.

Every optional dependency (boto3) is imported lazily inside the backend, so
"import src.infra.storage" works on the base image.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading

logger = logging.getLogger(__name__)

DEFAULT_BACKEND = "local"
DEFAULT_BUCKET = "hiresnap-uploads"
DEFAULT_PRESIGN_TTL = 3600
DEFAULT_REGION = "us-east-1"

_TRUTHY = {"1", "true", "yes", "on", "enabled", "y"}


class StorageError(RuntimeError):
    """A storage operation failed. Callers decide whether the request fails."""


class StorageKeyInvalid(StorageError, ValueError):
    """The key is not a safe relative object path."""


class StorageUnavailable(StorageError):
    """The configured backend cannot be used at all (missing library/config)."""


def _env(*names, default=""):
    """First present environment name wins (see src/infra/cache.py for the WHY)."""
    for name in names:
        if name in os.environ:
            return os.environ[name]
    return default


def _truthy(value):
    return str(value or "").strip().lower() in _TRUTHY


def _bootstrap_env():
    """Load backend/.env once, best effort, without importing env.Env."""
    if getattr(_bootstrap_env, "_done", False):
        return
    _bootstrap_env._done = True
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - bare install
        return
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(backend_dir, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path)
    except Exception:  # pragma: no cover - defensive
        logger.debug("storage: could not load backend/.env")


def normalise_key(key):
    """Validate and return a relative object key.

    Mirrors R:/GPTHub/backend/service/infrastructure/storage/local_file_storage.py.
    Every rejection here is a traversal or ambiguity that a naive join would
    resolve to a path outside DATA_PATH: an absolute path, a Windows drive, a
    backslash (which is also a separator on Windows but not on Linux, so the same
    key would mean two files), a colon (alternate data stream), a control
    character, or a "." / ".." component.
    """
    if not isinstance(key, str):
        raise StorageKeyInvalid("storage_key_invalid: key must be a string")
    cleaned = key.strip()
    if not cleaned:
        raise StorageKeyInvalid("storage_key_invalid: empty key")
    if cleaned.startswith("/") or cleaned.startswith("\\"):
        raise StorageKeyInvalid("storage_key_invalid: absolute key %r" % (key,))
    if "\\" in cleaned or ":" in cleaned:
        raise StorageKeyInvalid("storage_key_invalid: separator or drive in %r" % (key,))
    if any(ord(char) < 32 for char in cleaned):
        raise StorageKeyInvalid("storage_key_invalid: control character in %r" % (key,))
    parts = cleaned.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise StorageKeyInvalid("storage_key_invalid: unsafe path component in %r" % (key,))
    return "/".join(parts)


def _public_base_url():
    """Same URL contract as backend/src/utils/return_url_object.py."""
    configured = _env("PUBLIC_BASE_URL", "PUBLIC_BASE_URL", default="").strip()
    if configured:
        return configured.rstrip("/")
    host = _env("HOST", "HOST", default="localhost")
    port = _env("SERVER_PORT", "SERVER_PORT", default="8080")
    return "http://%s:%s" % (host, port)


def local_root():
    """DATA_PATH, or backend/data when unset (the path server.py mounts)."""
    configured = _env("DATA_PATH", "DATA_PATH", default="").strip()
    if configured:
        return configured
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(backend_dir, "data")


class LocalStorage(object):
    """Files on disk under DATA_PATH. This is today's behaviour, unchanged."""

    name = "local"

    def __init__(self, root=None):
        self._root = os.path.abspath(root or local_root())

    @property
    def root(self):
        return self._root

    def path_for(self, key):
        key = normalise_key(key)
        target = os.path.abspath(os.path.join(self._root, *key.split("/")))
        root_real = os.path.realpath(self._root)
        target_real = os.path.realpath(target)
        # realpath resolves symlinks, so a link planted inside DATA_PATH cannot
        # be used to write outside it.
        if target_real != root_real and not target_real.startswith(root_real + os.sep):
            raise StorageKeyInvalid("storage_key_invalid: %r escapes the storage root" % (key,))
        return target_real

    def put_bytes(self, key, data, content_type=None):
        """Write bytes, creating intermediate directories like write_file_into_server."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        target = self.path_for(key)
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            with open(target, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            raise StorageError("storage_write_failed key=%s error=%s" % (key, exc)) from exc
        return normalise_key(key)

    def put_file(self, key, source_path, content_type=None):
        """Copy an existing file into storage (server-side movers, fixtures)."""
        target = self.path_for(key)
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            shutil.copyfile(str(source_path), target)
        except OSError as exc:
            raise StorageError("storage_write_failed key=%s error=%s" % (key, exc)) from exc
        return normalise_key(key)

    def read_bytes(self, key):
        target = self.path_for(key)
        try:
            with open(target, "rb") as handle:
                return handle.read()
        except OSError as exc:
            raise StorageError("storage_read_failed key=%s error=%s" % (key, exc)) from exc

    def exists(self, key):
        try:
            return os.path.isfile(self.path_for(key))
        except StorageKeyInvalid:
            return False

    def delete(self, key):
        target = self.path_for(key)
        try:
            os.remove(target)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError("storage_delete_failed key=%s error=%s" % (key, exc)) from exc

    def list_objects(self, prefix="", limit=10000):
        """Objects under prefix as {key, size, modified_at}, oldest last."""
        cleaned = prefix.strip("/") if prefix else ""
        base = self.path_for(cleaned or ".")
        if cleaned and not os.path.isdir(base):
            return []
        results = []
        for directory, _dirs, files in os.walk(base):
            for name in files:
                absolute = os.path.join(directory, name)
                relative = os.path.relpath(absolute, self._root).replace(os.sep, "/")
                if cleaned and not relative.startswith(cleaned + "/") and relative != cleaned:
                    continue
                try:
                    stat = os.stat(absolute)
                except OSError:
                    continue
                results.append({"key": relative, "size": stat.st_size, "modified_at": stat.st_mtime})
                if len(results) >= limit:
                    return results
        return results

    def public_url(self, key):
        """URL the /data static mount serves, i.e. what a browser can fetch.

        server.py mounts DATA_PATH at "/data", so the key maps to
        <base>/data/<key>. We build it here instead of calling
        return_url_object() because that helper strips two characters from a
        database URL ("./"), which is a storage-concern of the row, not of the
        object key.
        """
        return "%s/data/%s" % (_public_base_url(), normalise_key(key))

    def presigned_url(self, key, expires=None):
        """Local backend has no signing: return the public URL."""
        return self.public_url(key)

    def ensure_bucket(self):
        """No-op for local; the analogue is making DATA_PATH exist."""
        os.makedirs(self._root, exist_ok=True)
        return True

    def describe(self):
        return {"backend": self.name, "root": self._root}


class S3Storage(object):
    """S3-compatible storage (MinIO in the stack, AWS S3 in principle).

    Path-style addressing is forced: MinIO is reached at http://storage:9000 and
    virtual-host addressing would resolve "<bucket>.storage", which does not
    exist on the compose network.
    """

    name = "s3"

    def __init__(self, bucket=None, endpoint=None, access_key=None, secret_key=None,
                 region=None, secure=None, prefix=None, presign_ttl=None):
        self._bucket = bucket or DEFAULT_BUCKET
        self._endpoint = endpoint or ""
        self._access_key = access_key or ""
        self._secret_key = secret_key or ""
        self._region = region or DEFAULT_REGION
        self._secure = bool(secure) if secure is not None else False
        self._prefix = (prefix or "").strip("/")
        self._presign_ttl = int(presign_ttl or DEFAULT_PRESIGN_TTL)
        self._client = None
        self._lock = threading.Lock()

    # -- client -----------------------------------------------------------
    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            # Lazy: the base image has no boto3. The ignores are for the type
            # checker only - boto3 lives in requirements_platform.txt, which is
            # optional, so the stub is legitimately absent on a bare install and
            # both codes are named in case a future stub changes the flavour.
            import boto3  # type: ignore[import-not-found,import-untyped]
            from botocore.config import Config  # type: ignore[import-not-found,import-untyped]
        except Exception as exc:
            raise StorageUnavailable(
                "storage backend=s3 code=boto3_missing action=install "
                "requirements_platform.txt or use STORAGE__BACKEND=local"
            ) from exc
        with self._lock:
            if self._client is not None:
                return self._client
            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint or None,
                aws_access_key_id=self._access_key or None,
                aws_secret_access_key=self._secret_key or None,
                region_name=self._region,
                use_ssl=self._secure,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._client

    def object_key(self, key):
        cleaned = normalise_key(key)
        return "%s/%s" % (self._prefix, cleaned) if self._prefix else cleaned

    # -- operations -------------------------------------------------------
    def ensure_bucket(self):
        client = self._get_client()
        try:
            client.head_bucket(Bucket=self._bucket)
            return True
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", "") or "")
            if "404" not in code and "NoSuchBucket" not in code:
                raise StorageError("storage_bucket_unavailable bucket=%s error=%s" % (self._bucket, exc)) from exc
        try:
            client.create_bucket(Bucket=self._bucket)
            return True
        except Exception as exc:
            # Two workers racing to create the same bucket is normal at startup.
            if "BucketAlreadyOwnedByYou" in str(exc) or "BucketAlreadyExists" in str(exc):
                return True
            raise StorageError("storage_bucket_create_failed bucket=%s error=%s" % (self._bucket, exc)) from exc

    def put_bytes(self, key, data, content_type=None):
        if isinstance(data, str):
            data = data.encode("utf-8")
        client = self._get_client()
        try:
            client.put_object(
                Bucket=self._bucket,
                Key=self.object_key(key),
                Body=data,
                ContentType=content_type or "application/octet-stream",
            )
        except Exception as exc:
            raise StorageError("storage_write_failed key=%s error=%s" % (key, exc)) from exc
        return normalise_key(key)

    def put_file(self, key, source_path, content_type=None):
        client = self._get_client()
        extra = {"ContentType": content_type} if content_type else None
        try:
            if extra:
                client.upload_file(str(source_path), self._bucket, self.object_key(key), ExtraArgs=extra)
            else:
                client.upload_file(str(source_path), self._bucket, self.object_key(key))
        except Exception as exc:
            raise StorageError("storage_write_failed key=%s error=%s" % (key, exc)) from exc
        return normalise_key(key)

    def exists(self, key):
        client = self._get_client()
        try:
            client.head_object(Bucket=self._bucket, Key=self.object_key(key))
            return True
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", "") or "")
            if "404" in code or "NoSuchKey" in code or "NotFound" in code:
                return False
            raise StorageError("storage_head_failed key=%s error=%s" % (key, exc)) from exc

    def delete(self, key):
        client = self._get_client()
        try:
            client.delete_object(Bucket=self._bucket, Key=self.object_key(key))
        except Exception as exc:
            raise StorageError("storage_delete_failed key=%s error=%s" % (key, exc)) from exc
        return True

    def presigned_url(self, key, expires=None):
        client = self._get_client()
        ttl = int(expires or self._presign_ttl)
        try:
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": self.object_key(key)},
                ExpiresIn=ttl,
            )
        except Exception as exc:
            raise StorageError("storage_presign_failed key=%s error=%s" % (key, exc)) from exc

    def list_objects(self, prefix="", limit=10000):
        client = self._get_client()
        wanted = prefix.strip("/") if prefix else ""
        search = self.object_key(wanted) if wanted else self._prefix
        results = []
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=search or ""):
                for item in page.get("Contents", []) or []:
                    full = item.get("Key", "")
                    relative = full[len(self._prefix) + 1:] if self._prefix and full.startswith(self._prefix + "/") else full
                    modified = item.get("LastModified")
                    results.append({
                        "key": relative,
                        "size": item.get("Size", 0),
                        "modified_at": modified.timestamp() if hasattr(modified, "timestamp") else 0.0,
                    })
                    if len(results) >= limit:
                        return results
        except Exception as exc:
            raise StorageError("storage_list_failed prefix=%s error=%s" % (prefix, exc)) from exc
        return results

    def describe(self):
        return {
            "backend": self.name,
            "bucket": self._bucket,
            "endpoint": self._endpoint,
            "prefix": self._prefix,
            "secure": self._secure,
        }


# Annotated because mypy runs in the pre-commit gate over src/infra: an empty
# dict literal infers dict[Any, Any] and a bare annotation-free module global is
# rejected outright. The values are backend instances (LocalStorage / S3Storage),
# kept as object because the concrete type is chosen at runtime by env var.
_STORAGES: dict[str, object] = {}
_STORAGES_LOCK = threading.Lock()


def build_storage(backend=None):
    """Build the storage backend, falling back to local when S3 is unusable.

    The fallback is deliberate and logged: with STORAGE__BACKEND=s3 but no boto3
    installed, writing locally still produces a key that the /data mount serves,
    so the upload is not lost - it is only stored in the wrong place, which the
    operator has to see. Raising here instead would break uploads entirely and
    would make the feature flag a footgun.
    """
    _bootstrap_env()
    name = str(backend or _env("STORAGE__BACKEND", "STORAGE_BACKEND", default=DEFAULT_BACKEND)).strip().lower()
    if name != "s3":
        if name != "local":
            logger.warning("storage status=unknown_backend value=%s action=using local", name)
        return LocalStorage()

    s3 = S3Storage(
        bucket=_env("STORAGE__BUCKET", "STORAGE_BUCKET", default=DEFAULT_BUCKET),
        endpoint=_env("STORAGE__ENDPOINT", "STORAGE_ENDPOINT", default=""),
        access_key=_env("STORAGE__ACCESS_KEY", "STORAGE_ACCESS_KEY", default=""),
        secret_key=_env("STORAGE__SECRET_KEY", "STORAGE_SECRET_KEY", default=""),
        region=_env("STORAGE__REGION", "STORAGE_REGION", default=DEFAULT_REGION),
        secure=_truthy(_env("STORAGE__SECURE", "STORAGE_SECURE", default="false")),
        prefix=_env("STORAGE__PREFIX", "STORAGE_PREFIX", default=""),
        presign_ttl=_env("STORAGE__PRESIGN_TTL", "STORAGE_PRESIGN_TTL", default=str(DEFAULT_PRESIGN_TTL)),
    )
    try:
        import boto3  # noqa: F401 - presence check only; S3Storage imports it again lazily
    except Exception:
        logger.error(
            "storage backend=s3 status=unavailable code=boto3_missing fallback=local "
            "action=uploads keep succeeding under DATA_PATH; install requirements_platform.txt"
        )
        return LocalStorage()
    return s3


def get_storage():
    """Process-wide storage backend for the configured STORAGE__BACKEND."""
    name = str(_env("STORAGE__BACKEND", "STORAGE_BACKEND", default=DEFAULT_BACKEND)).strip().lower()
    with _STORAGES_LOCK:
        existing = _STORAGES.get(name)
        if existing is not None:
            return existing
        created = build_storage(name)
        _STORAGES[name] = created
        return created


def reset_storage():
    """Drop cached backends (tests and configuration reloads only)."""
    with _STORAGES_LOCK:
        _STORAGES.clear()


def storage_health():
    """Cheap health payload: never performs a network round trip.

    A health endpoint is called by the gateway on a timer; probing S3 there would
    turn a storage hiccup into a failing health check for the whole API. The
    worker-side health task probes for real.
    """
    try:
        backend = get_storage()
        description = backend.describe()
    except Exception as exc:
        return {"backend": "unknown", "ok": False, "error": str(exc)}
    if description.get("backend") == "local":
        root = description.get("root")
        description["ok"] = bool(root) and os.path.isdir(root)
    else:
        description["ok"] = bool(description.get("bucket")) and bool(description.get("endpoint"))
    return description
