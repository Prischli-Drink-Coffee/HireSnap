"""service/infrastructure/storage.py: the S3 backend, driven against a fake boto3.

WHY THIS FILE EXISTS SEPARATELY FROM test_infra_fallbacks.py: that module covers the
decisions (which backend, what happens when boto3 is missing, how health degrades). This one
covers the S3 operations themselves, which a developer without a MinIO container can
otherwise never execute - and which therefore used to run for the first time in production.

WHY boto3 IS FAKED RATHER THAN SKIPPED: boto3 is an OPTIONAL dependency (`backend/
requirements-platform.txt`), so a test that needs it installed would silently skip in CI and
in the API image. A fake client exercises the real code path - key normalisation, error
translation, pagination, the presign call - with no socket and no library.

WHAT IS PINNED HERE AND WHY IT IS NOT COSMETIC:
  * `_get_client()` is lazy and built once: constructing S3Storage must not touch the
    network, and a retry after a transient failure must not leak a second client.
  * every operation translates an exception into StorageError, because storage holds user
    data and must fail closed. A raw boto3 exception escaping this class would surface as a
    500 with a boto3 traceback instead of the documented error.
  * a 404 on `exists()` is False, not an error; every other failure is an error.
  * the object key gets the configured prefix exactly once - a double prefix silently
    writes to a different key than the one read back.
"""

from __future__ import annotations

import sys
import types

import pytest

from service.infrastructure import storage as storage_module


class FakeBotoClient:
    """The subset of boto3's S3 client this module calls, with configurable failures."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.objects = {}
        self.pages = []

    def _record(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error

    def head_bucket(self, Bucket):  # noqa: N803 - boto3's own keyword spelling
        self._record("head_bucket", Bucket=Bucket)
        return {}

    def create_bucket(self, Bucket):  # noqa: N803
        self._record("create_bucket", Bucket=Bucket)
        return {}

    def put_object(self, **kwargs):
        self._record("put_object", **kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}

    def upload_file(self, filename, bucket, key, ExtraArgs=None):  # noqa: N803
        self._record("upload_file", Bucket=bucket, Key=key, ExtraArgs=ExtraArgs)
        self.objects[key] = filename
        return {}

    def head_object(self, **kwargs):
        self._record("head_object", **kwargs)
        return {}

    def delete_object(self, **kwargs):
        self._record("delete_object", **kwargs)
        return {}

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):  # noqa: N803
        self._record(
            "generate_presigned_url", Operation=operation, Params=Params, ExpiresIn=ExpiresIn
        )
        return "https://signed.example/%s" % Params["Key"]

    def get_paginator(self, name):
        self.calls.append(("get_paginator", {"name": name}))
        return self

    def paginate(self, **kwargs):
        self.calls.append(("paginate", kwargs))
        for page in self.pages:
            yield page


class FakeClientError(Exception):
    """boto3's ClientError shape: the code lives in .response, not in the message.

    The module reads `exc.response["Error"]["Code"]`, so a fake that raised a bare
    RuntimeError("NoSuchKey") would be classified as an unknown failure - which is the real
    behaviour and worth seeing in one test below.
    """

    def __init__(self, code):
        super().__init__("An error occurred (%s)" % code)
        self.response = {"Error": {"Code": code}}


class FakeConfig:
    """Stands in for botocore.config.Config; records the arguments instead of validating them."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture()
def fake_boto(monkeypatch):
    """Install a fake boto3 + botocore.config and hand back a client to inspect."""
    client = FakeBotoClient()
    module = types.ModuleType("boto3")
    calls = []

    def client_factory(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return client

    module.client = client_factory
    botocore = types.ModuleType("botocore")
    config_module = types.ModuleType("botocore.config")
    config_module.Config = FakeConfig
    botocore.config = config_module
    monkeypatch.setitem(sys.modules, "boto3", module)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", config_module)
    return types.SimpleNamespace(client=client, calls=calls, config=FakeConfig)


def _backend(**overrides):
    kwargs = {
        "bucket": "hiresnap-uploads",
        "endpoint": "http://minio:9000",
        "access_key": "key",
        "secret_key": "secret",
    }
    kwargs.update(overrides)
    return storage_module.S3Storage(**kwargs)


# --- the client -------------------------------------------------------------
def test_construction_touches_no_network(fake_boto):
    """S3Storage is built at import-time-ish (build_storage) and must stay inert."""
    backend = _backend()

    assert fake_boto.calls == []
    assert backend._client is None


def test_the_client_is_created_once_with_path_style_addressing(fake_boto):
    """MinIO lives at http://storage:9000; virtual-host addressing would not resolve."""
    backend = _backend()

    assert backend._get_client() is backend._get_client()
    assert len(fake_boto.calls) == 1
    service_name, kwargs = fake_boto.calls[0]
    assert service_name == "s3"
    assert kwargs["endpoint_url"] == "http://minio:9000"
    assert kwargs["aws_access_key_id"] == "key"
    assert kwargs["aws_secret_access_key"] == "secret"
    assert kwargs["use_ssl"] is False
    # Config is the fake's, but its arguments are the contract.
    assert kwargs["config"].kwargs["signature_version"] == "s3v4"
    assert kwargs["config"].kwargs["s3"] == {"addressing_style": "path"}


def test_an_empty_endpoint_is_passed_as_none(fake_boto):
    """boto3 rejects endpoint_url="" whereas None means "AWS's own endpoint"."""
    _backend(endpoint="")._get_client()

    assert fake_boto.calls[0][1]["endpoint_url"] is None


def test_a_missing_library_names_the_file_to_install(monkeypatch):
    """The error has to say WHAT to install; "No module named boto3" is a treasure hunt."""
    monkeypatch.setitem(sys.modules, "boto3", None)

    with pytest.raises(storage_module.StorageUnavailable) as excinfo:
        _backend()._get_client()

    message = str(excinfo.value)
    assert "boto3_missing" in message
    assert "requirements-platform.txt" in message


# --- keys -------------------------------------------------------------------
def test_the_prefix_is_prepended_exactly_once(fake_boto):
    backend = _backend(prefix="uploads")

    assert backend.object_key("files/a.mp4") == "uploads/files/a.mp4"
    assert backend.object_key("files/a.mp4") == "uploads/files/a.mp4"


def test_an_empty_prefix_leaves_the_key_alone(fake_boto):
    assert _backend(prefix="").object_key("files/a.mp4") == "files/a.mp4"


def test_a_traversing_key_is_rejected_before_the_client_is_built(fake_boto):
    backend = _backend()

    with pytest.raises(storage_module.StorageKeyInvalid):
        backend.object_key("../../etc/passwd")
    assert fake_boto.calls == []


# --- bucket -----------------------------------------------------------------
def test_an_existing_bucket_is_not_recreated(fake_boto):
    assert _backend().ensure_bucket() is True
    names = [name for name, _kwargs in fake_boto.client.calls]
    assert names == ["head_bucket"]


def test_a_missing_bucket_is_created(fake_boto):
    def head_missing(Bucket):  # noqa: N803
        raise FakeClientError("404")

    fake_boto.client.head_bucket = head_missing

    assert _backend().ensure_bucket() is True
    assert "create_bucket" in [name for name, _kwargs in fake_boto.client.calls]


def test_two_workers_creating_the_bucket_at_once_is_success(fake_boto):
    """A startup race is normal: the second worker must not fail the boot."""

    def head_missing(Bucket):  # noqa: N803
        raise FakeClientError("404")

    def create_raises(Bucket):  # noqa: N803
        raise FakeClientError("BucketAlreadyOwnedByYou")

    fake_boto.client.head_bucket = head_missing
    fake_boto.client.create_bucket = create_raises

    assert _backend().ensure_bucket() is True


def test_any_other_bucket_error_fails_closed(fake_boto):
    def head_denied(Bucket):  # noqa: N803
        raise FakeClientError("403")

    fake_boto.client.head_bucket = head_denied

    with pytest.raises(storage_module.StorageError) as excinfo:
        _backend().ensure_bucket()
    assert "storage_bucket_unavailable" in str(excinfo.value)


# --- writes -----------------------------------------------------------------
def test_put_bytes_writes_to_the_prefixed_key(fake_boto):
    backend = _backend(prefix="uploads")

    assert backend.put_bytes("files/a.mp4", b"bytes") == "files/a.mp4"
    name, kwargs = fake_boto.client.calls[-1]
    assert name == "put_object"
    assert kwargs["Bucket"] == "hiresnap-uploads"
    assert kwargs["Key"] == "uploads/files/a.mp4"
    assert kwargs["Body"] == b"bytes"
    assert kwargs["ContentType"] == "application/octet-stream"


def test_put_bytes_encodes_a_string_and_honours_the_content_type(fake_boto):
    _backend().put_bytes("files/a.txt", "text", content_type="text/plain")

    kwargs = fake_boto.client.calls[-1][1]
    assert kwargs["Body"] == b"text"
    assert kwargs["ContentType"] == "text/plain"


def test_put_file_passes_extra_args_only_when_a_content_type_is_given(fake_boto, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    backend = _backend()

    backend.put_file("files/clip.mp4", source)
    assert fake_boto.client.calls[-1][1]["ExtraArgs"] is None

    backend.put_file("files/clip.mp4", source, content_type="video/mp4")
    assert fake_boto.client.calls[-1][1]["ExtraArgs"] == {"ContentType": "video/mp4"}


def test_a_failed_write_raises_storage_error(fake_boto):
    """Silently dropping an upload loses the user's file, so this must raise."""
    fake_boto.client.error = RuntimeError("connection reset")

    with pytest.raises(storage_module.StorageError) as excinfo:
        _backend().put_bytes("files/a.mp4", b"bytes")
    assert "storage_write_failed" in str(excinfo.value)


# --- reads ------------------------------------------------------------------
def test_exists_is_true_when_the_head_succeeds(fake_boto):
    assert _backend().exists("files/a.mp4") is True


def test_a_missing_object_is_false_not_an_error(fake_boto):
    """A 404 is an answer; only other failures are errors."""

    def head_missing(**kwargs):
        raise FakeClientError("404")

    fake_boto.client.head_object = head_missing

    assert _backend().exists("files/a.mp4") is False


def test_the_error_CODE_comes_from_the_structured_response_not_the_message(fake_boto):
    """boto3 puts the code in exc.response; a message that merely says "NoSuchKey" is not one.

    Pinned because it is easy to "fix" with a substring check on str(exc), which would turn
    every unrelated failure whose text mentions 404 into a silent "the object is missing".
    """

    def head_message_only(**kwargs):
        raise RuntimeError("NoSuchKey")

    fake_boto.client.head_object = head_message_only

    with pytest.raises(storage_module.StorageError):
        _backend().exists("files/a.mp4")


def test_a_head_failure_that_is_not_404_fails_closed(fake_boto):
    def head_denied(**kwargs):
        raise FakeClientError("403")

    fake_boto.client.head_object = head_denied

    with pytest.raises(storage_module.StorageError) as excinfo:
        _backend().exists("files/a.mp4")
    assert "storage_head_failed" in str(excinfo.value)


def test_delete_reports_success_and_failure(fake_boto):
    assert _backend().delete("files/a.mp4") is True

    fake_boto.client.error = RuntimeError("boom")
    with pytest.raises(storage_module.StorageError) as excinfo:
        _backend().delete("files/a.mp4")
    assert "storage_delete_failed" in str(excinfo.value)


def test_presigned_url_uses_the_configured_ttl(fake_boto):
    backend = _backend(presign_ttl=120)

    url = backend.presigned_url("files/a.mp4")

    assert url == "https://signed.example/files/a.mp4"
    kwargs = fake_boto.client.calls[-1][1]
    assert kwargs["ExpiresIn"] == 120
    assert kwargs["Operation"] == "get_object"


def test_presign_failure_is_translated(fake_boto):
    fake_boto.client.error = RuntimeError("signing failed")

    with pytest.raises(storage_module.StorageError) as excinfo:
        _backend().presigned_url("files/a.mp4")
    assert "storage_presign_failed" in str(excinfo.value)


# --- listing ----------------------------------------------------------------
def test_list_objects_strips_the_prefix_from_the_returned_keys(fake_boto):
    """The caller asked for keys relative to the bucket root, not object-store paths."""
    backend = _backend(prefix="uploads")
    fake_boto.client.pages = [
        {
            "Contents": [
                {"Key": "uploads/files/a.mp4", "Size": 5, "LastModified": None},
                {"Key": "uploads/files/b.mp4", "Size": 7, "LastModified": None},
            ]
        }
    ]

    listed = backend.list_objects("files")

    assert [item["key"] for item in listed] == ["files/a.mp4", "files/b.mp4"]
    assert [item["size"] for item in listed] == [5, 7]
    assert listed[0]["modified_at"] == 0.0


def test_list_objects_honours_the_limit(fake_boto):
    fake_boto.client.pages = [
        {"Contents": [{"Key": "files/%d.mp4" % index, "Size": 1} for index in range(5)]}
    ]

    assert len(_backend().list_objects("files", limit=2)) == 2


def test_a_listing_failure_is_translated(fake_boto):
    def paginate(**kwargs):
        raise RuntimeError("listing denied")
        yield  # pragma: no cover - makes this a generator like boto3's

    fake_boto.client.paginate = paginate

    with pytest.raises(storage_module.StorageError) as excinfo:
        _backend().list_objects("files")
    assert "storage_list_failed" in str(excinfo.value)


# --- description ------------------------------------------------------------
def test_describe_reports_the_configuration_and_no_secret(fake_boto):
    described = _backend(prefix="uploads", secure=True).describe()

    assert described == {
        "backend": "s3",
        "bucket": "hiresnap-uploads",
        "endpoint": "http://minio:9000",
        "prefix": "uploads",
        "secure": True,
    }
    assert "secret" not in repr(described)
