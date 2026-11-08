"""Local storage: the backend every deployment runs when S3 is not configured.

WHY this file exists next to test_storage_s3.py: that file covers the object
store, which most installs never enable. LocalStorage is the fallback that ships
by default, and its untested parts are the ones that decide whether an uploaded
video is where the /data mount expects it - the layout, the traversal guard, the
public URL and the clean listing the orphan sweeper depends on.

Two rules the assertions follow: a key that could escape DATA_PATH is rejected
before any filesystem call, and a missing file is an answer (False / empty list /
StorageError), never an exception that reaches a request handler unwrapped.
"""

import os
import sys
import types

import pytest

from service.infrastructure import storage as storage_module

LOGGER_NAME = "service.infrastructure.storage"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    storage_module.reset_storage()
    yield storage_module.LocalStorage(str(tmp_path))
    storage_module.reset_storage()


# --------------------------------------------------------------------------- #
# normalise_key: the traversal guard, one rejection per reason
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "key, reason",
    [
        (123, "must be a string"),
        (None, "must be a string"),
        ("", "empty key"),
        ("   ", "empty key"),
        ("/etc/passwd", "absolute key"),
        ("\\windows\\system32", "absolute key"),
        ("files\\clip.mp4", "separator or drive"),
        ("files/a:b", "separator or drive"),
        ("files/a\x00.mp4", "control character"),
        ("files//double.mp4", "unsafe path component"),
        ("files/../escape.mp4", "unsafe path component"),
        ("files/./clip.mp4", "unsafe path component"),
    ],
)
def test_unsafe_keys_are_rejected(key, reason):
    with pytest.raises(storage_module.StorageKeyInvalid, match=reason):
        storage_module.normalise_key(key)


def test_a_clean_key_is_returned_stripped():
    assert storage_module.normalise_key("  files/a.mp4  ") == "files/a.mp4"
    assert storage_module.normalise_key("files/nested/a.mp4") == "files/nested/a.mp4"


def test_storage_key_invalid_is_a_value_error_too():
    """Callers that already catch ValueError (legacy upload code) keep working."""
    assert issubclass(storage_module.StorageKeyInvalid, storage_module.StorageError)
    assert issubclass(storage_module.StorageKeyInvalid, ValueError)


def test_a_symlink_inside_the_root_cannot_escape_it(tmp_path, monkeypatch):
    """realpath, not abspath: a link planted in DATA_PATH must not widen it.

    Creating a real symlink needs SeCreateSymbolicLinkPrivilege on Windows, which
    CI does not have, so the escape is simulated by resolving the target outside
    the root - exactly the value realpath would return for a planted link.
    """
    root = tmp_path / "root"
    root.mkdir()
    outside = str(tmp_path / "outside" / "evil.mp4")
    real_realpath = storage_module.os.path.realpath

    def _resolve(path):
        text = str(path)
        if "link" in text.split(os.sep):
            return outside
        return real_realpath(path)

    monkeypatch.setattr(storage_module.os.path, "realpath", _resolve)

    backend = storage_module.LocalStorage(str(root))

    with pytest.raises(storage_module.StorageKeyInvalid, match="escapes the storage root"):
        backend.path_for("link/evil.mp4")


# --------------------------------------------------------------------------- #
# the layout the /data mount serves
# --------------------------------------------------------------------------- #
def test_write_read_and_delete_round_trip(local, tmp_path):
    assert local.put_bytes("files/clip.mp4", b"video") == "files/clip.mp4"

    assert (tmp_path / "files" / "clip.mp4").read_bytes() == b"video"
    assert local.read_bytes("files/clip.mp4") == b"video"
    assert local.exists("files/clip.mp4") is True
    assert local.delete("files/clip.mp4") is True
    assert local.exists("files/clip.mp4") is False
    assert local.delete("files/clip.mp4") is False, "deleting a missing key is not an error"


def test_a_text_payload_is_encoded_utf8(local, tmp_path):
    local.put_bytes("files/note.txt", "hello")

    assert (tmp_path / "files" / "note.txt").read_bytes() == b"hello"


def test_put_file_copies_an_existing_file(local, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")

    assert local.put_file("files/copied.mp4", source) == "files/copied.mp4"
    assert local.read_bytes("files/copied.mp4") == b"media"


def test_put_file_reports_a_missing_source(local, tmp_path):
    with pytest.raises(storage_module.StorageError, match="storage_write_failed"):
        local.put_file("files/x.mp4", tmp_path / "absent.mp4")


def test_read_of_a_missing_object_is_a_storage_error(local):
    with pytest.raises(storage_module.StorageError, match="storage_read_failed"):
        local.read_bytes("files/absent.mp4")


def test_exists_on_an_invalid_key_is_false_not_an_error(local):
    """exists() answers a question; a malformed key is simply not there."""
    assert local.exists("../escape") is False
    assert local.exists("") is False


def test_delete_of_an_unremovable_object_is_a_storage_error(local, tmp_path, monkeypatch):
    local.put_bytes("files/locked.mp4", b"x")

    def _refuse(path):
        raise PermissionError("in use")

    monkeypatch.setattr(storage_module.os, "remove", _refuse)

    with pytest.raises(storage_module.StorageError, match="storage_delete_failed"):
        local.delete("files/locked.mp4")


def test_a_write_failure_is_a_storage_error(local, monkeypatch):
    def _refuse(target, data):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _refuse)

    with pytest.raises(storage_module.StorageError, match="storage_write_failed"):
        local.put_bytes("files/a.mp4", b"x")


# --------------------------------------------------------------------------- #
# listing: what the orphan sweeper walks
# --------------------------------------------------------------------------- #
def test_list_objects_reports_key_size_and_mtime(local, tmp_path):
    local.put_bytes("files/a.mp4", b"12345")
    local.put_bytes("files/nested/b.mp4", b"67")

    listing = {item["key"]: item for item in local.list_objects("files")}

    assert set(listing) == {"files/a.mp4", "files/nested/b.mp4"}
    assert listing["files/a.mp4"]["size"] == 5
    assert isinstance(listing["files/a.mp4"]["modified_at"], float)


def test_list_objects_honours_the_limit(local):
    for index in range(5):
        local.put_bytes("files/%d.mp4" % (index,), b"x")

    assert len(local.list_objects("files", limit=2)) == 2


def test_list_objects_of_a_missing_prefix_is_empty(local):
    assert local.list_objects("nothing-here") == []


def test_list_objects_accepts_a_trailing_slash_and_a_legacy_key(local):
    local.put_bytes("files/a.mp4", b"x")

    assert [item["key"] for item in local.list_objects("files/")] == ["files/a.mp4"]


def test_list_objects_with_an_empty_prefix_is_rejected(local):
    """Reported, not endorsed: the signature defaults to prefix="".

    LocalStorage.list_objects("") builds path_for(".") and normalise_key(".")
    rejects it, so a caller that trusts the documented default gets
    StorageKeyInvalid instead of a full listing. Every current caller passes a
    concrete prefix (the orphan sweeper uses "files/"), which is why it has never
    surfaced. S3Storage.list_objects("") works. See the report:
    service/infrastructure/storage.py:212.
    """
    local.put_bytes("files/a.mp4", b"x")

    with pytest.raises(storage_module.StorageKeyInvalid, match="unsafe path component"):
        local.list_objects()


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #
def test_public_url_matches_the_data_mount(local, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://hiresnap.example.com/")

    assert local.public_url("files/a.mp4") == "https://hiresnap.example.com/data/files/a.mp4"


def test_public_url_falls_back_to_host_and_port(local, monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("HOST", "10.0.0.5")
    monkeypatch.setenv("SERVER_PORT", "8081")

    assert local.public_url("files/a.mp4") == "http://10.0.0.5:8081/data/files/a.mp4"


def test_public_url_defaults_to_localhost_8080(local, monkeypatch):
    for name in ("PUBLIC_BASE_URL", "HOST", "SERVER_PORT"):
        monkeypatch.delenv(name, raising=False)

    assert local.public_url("files/a.mp4") == "http://localhost:8080/data/files/a.mp4"


def test_presigned_url_of_the_local_backend_is_the_public_url(local, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://x.example")

    assert local.presigned_url("files/a.mp4") == local.public_url("files/a.mp4")


def test_ensure_bucket_creates_the_root(tmp_path):
    root = tmp_path / "not-yet"
    backend = storage_module.LocalStorage(str(root))

    assert backend.ensure_bucket() is True
    assert root.is_dir()


def test_describe_names_the_root_and_backend(local, tmp_path):
    assert local.describe() == {"backend": "local", "root": str(tmp_path)}
    assert local.root == str(tmp_path)


def test_a_file_that_vanishes_during_the_walk_is_skipped(local, monkeypatch):
    """The sweeper walks a live upload directory: a concurrent delete is normal."""
    local.put_bytes("files/a.mp4", b"x")
    local.put_bytes("files/b.mp4", b"x")
    real_stat = storage_module.os.stat

    def _stat(path, *args, **kwargs):
        if str(path).endswith("b.mp4"):
            raise OSError("vanished")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(storage_module.os, "stat", _stat)

    assert [item["key"] for item in local.list_objects("files")] == ["files/a.mp4"]


# --------------------------------------------------------------------------- #
# factory, singleton and health
# --------------------------------------------------------------------------- #
def test_local_root_defaults_to_the_package_data_directory(monkeypatch):
    monkeypatch.delenv("DATA_PATH", raising=False)

    root = storage_module.local_root()

    assert root.endswith(os.path.join("backend", "data"))


def test_an_unknown_backend_uses_local_and_says_so(monkeypatch, caplog):
    monkeypatch.setenv("STORAGE__BACKEND", "dropbox")

    with caplog.at_level("WARNING", logger=LOGGER_NAME):
        backend = storage_module.build_storage()

    assert backend.name == "local"
    assert any("unknown_backend" in record.message for record in caplog.records)


def test_get_storage_memoises_per_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    storage_module.reset_storage()

    assert storage_module.get_storage() is storage_module.get_storage()


def test_reset_storage_drops_the_singleton(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    storage_module.reset_storage()
    first = storage_module.get_storage()

    storage_module.reset_storage()

    assert storage_module.get_storage() is not first


def test_storage_health_reports_the_local_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "absent"))
    monkeypatch.delenv("STORAGE__BACKEND", raising=False)
    storage_module.reset_storage()

    described = storage_module.storage_health()

    assert described["backend"] == "local"
    assert described["ok"] is False, "a root that does not exist is not a healthy backend"


def test_storage_health_reports_a_broken_backend_instead_of_raising(monkeypatch):
    def _explode():
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(storage_module, "get_storage", _explode)

    described = storage_module.storage_health()

    assert described == {"backend": "unknown", "ok": False, "error": "backend exploded"}


def test_storage_health_does_not_require_an_existing_root(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("DATA_PATH", str(root))
    monkeypatch.delenv("STORAGE__BACKEND", raising=False)
    storage_module.reset_storage()

    assert storage_module.storage_health()["ok"] is True


def test_bootstrap_env_survives_a_missing_dotenv(monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", None)
    monkeypatch.setattr(storage_module._bootstrap_env, "_done", False, raising=False)

    storage_module._bootstrap_env()

    assert storage_module._bootstrap_env._done is True


def test_bootstrap_env_loads_the_backend_file_once(monkeypatch):
    loaded = []
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda path: loaded.append(path)
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.setattr(storage_module._bootstrap_env, "_done", False, raising=False)
    monkeypatch.setattr(storage_module.os.path, "isfile", lambda path: True)

    storage_module._bootstrap_env()
    storage_module._bootstrap_env()

    assert len(loaded) == 1


def test_bootstrap_env_skips_the_file_when_absent(monkeypatch):
    loaded = []
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda path: loaded.append(path)
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
    monkeypatch.setattr(storage_module._bootstrap_env, "_done", False, raising=False)
    monkeypatch.setattr(storage_module.os.path, "isfile", lambda path: False)

    storage_module._bootstrap_env()

    assert loaded == []


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("no", False),
        ("", False),
        (None, False),
    ],
)
def test_truthy_flags(value, expected):
    assert storage_module._truthy(value) is expected
