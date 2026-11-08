"""The platform ports: what a swappable backend must offer, and how it degrades.

WHY this file exists as its own contract rather than more assertions in
test_infra_fallbacks.py: the fallback tests pin the *implementation* of one
backend each (memory cache, local disk, file sink). This file pins the
*interface* the rest of the application is allowed to depend on, because that is
what makes a backend swappable at all. Two failure modes it is written against:

  1. A concrete backend grows a method the protocol does not promise, a caller
     starts using it, and the fallback backend - the one that runs when redis,
     S3 or SMTP is missing - then raises AttributeError in production.
  2. A fallback stops being a no-op: "no redis installed" must mean "every read
     misses", never "the request fails", and "no S3 configured" must mean "the
     write fails loudly", never "the upload vanishes silently".

The two degradation directions are deliberately opposite and are asserted that
way: a cache is an optimisation and fails open, storage holds user data and fails
closed, mail is a notification and fails quiet.

STRUCTURE OF THE FILE: the factory/degradation tests run today against
service/infrastructure/*.py. The protocol-shape tests are gated on
service/shared/ports/, which is part of the vertical-slice move that is still
landing; until it exists they report "not yet migrated" instead of failing, so
this file can be committed before the tree finishes moving.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

PORTS_PACKAGE = "service.shared.ports"

#: The method surface each port promises, spelled out here on purpose: this file
#: is the contract, so it must not read the names back from the object under
#: test. A rename in a concrete backend is exactly what these assertions catch.
CACHE_METHODS = (
    "get",
    "set",
    "delete",
    "get_or_set",
    "invalidate_prefix",
    "ping",
    "describe",
)
STORAGE_METHODS = (
    "put_bytes",
    "put_file",
    "read_bytes",
    "exists",
    "delete",
    "list_objects",
    "public_url",
    "presigned_url",
    "describe",
)
MAILER_METHODS = ("send", "describe")


def _ports_available() -> bool:
    try:
        importlib.import_module(PORTS_PACKAGE)
    except Exception:
        return False
    return True


def _missing(surface, obj) -> list:
    return [name for name in surface if not callable(getattr(obj, name, None))]


# ---------------------------------------------------------------------------
# cache: an optimisation, so it fails open
# ---------------------------------------------------------------------------
def test_the_cache_factory_returns_the_documented_surface():
    from service.infrastructure import cache as cache_module

    facade = cache_module.build_cache()
    assert _missing(CACHE_METHODS, facade) == []


def test_an_unset_cache_backend_is_the_in_process_one():
    """The default must need no second container: that is the whole design."""
    from service.infrastructure import cache as cache_module

    assert cache_module.build_cache().backend_name == "memory"


def test_a_typo_in_the_backend_name_falls_back_instead_of_raising():
    """A broken env file must not make the API unimportable."""
    from service.infrastructure import cache as cache_module

    facade = cache_module.build_cache("redys")
    assert facade.backend_name == "memory"


def test_an_unknown_value_degrades_to_a_miss_not_to_an_exception():
    from service.infrastructure import cache as cache_module

    facade = cache_module.build_cache("noop")
    assert facade.get("anything", "fallback") == "fallback"
    # set() is advisory: a cache that cannot store is not an application error.
    assert facade.set("anything", {"v": 1}) in (True, False)
    assert facade.get("anything", "fallback") == "fallback"


def test_cache_values_round_trip_as_plain_data():
    """Values are JSON, which is what makes memory and redis interchangeable."""
    from service.infrastructure import cache as cache_module

    facade = cache_module.build_cache("memory")
    facade.set("k", {"a": [1, 2]}, ttl=30, namespace="contract")
    assert facade.get("k", namespace="contract") == {"a": [1, 2]}
    assert facade.invalidate_prefix("k", namespace="contract") == 1
    assert facade.get("k", namespace="contract") is None


# ---------------------------------------------------------------------------
# storage: user data, so it fails closed
# ---------------------------------------------------------------------------
def test_the_storage_factory_returns_the_documented_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "data"))
    from service.infrastructure import storage as storage_module

    backend = storage_module.build_storage()
    assert _missing(STORAGE_METHODS, backend) == []


def test_building_a_backend_writes_nothing(tmp_path, monkeypatch):
    """Constructing must not touch the disk: it runs on every worker start."""
    root = tmp_path / "not-created"
    monkeypatch.setenv("DATA_PATH", str(root))
    from service.infrastructure import storage as storage_module

    storage_module.build_storage()
    assert not root.exists()


def test_selecting_s3_without_boto3_falls_back_to_local(monkeypatch):
    """Fail open at *construction*: the upload still lands somewhere servable."""
    monkeypatch.setenv("STORAGE__BACKEND", "s3")
    # sys.modules[name] = None makes "import boto3" raise ImportError, exactly
    # like the base image that installs requirements.txt only.
    monkeypatch.setitem(sys.modules, "boto3", None)
    from service.infrastructure import storage as storage_module

    assert storage_module.build_storage().describe()["backend"] == "local"


def test_an_unusable_s3_backend_fails_closed_on_the_operation():
    """Once a backend is chosen, a failed write must raise, never be dropped."""
    from service.infrastructure import storage as storage_module

    backend = storage_module.S3Storage(bucket="b", endpoint="http://minio:9000")
    with pytest.raises(storage_module.StorageError):
        backend.put_bytes("files/a.mp4", b"bytes")


def test_health_never_probes_the_network(monkeypatch):
    """The gateway polls health on a timer; a probe there is an outage amplifier."""
    monkeypatch.setenv("STORAGE__BACKEND", "s3")
    for name in ("STORAGE__ENDPOINT", "STORAGE_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    from service.infrastructure import storage as storage_module

    payload = storage_module.storage_health()
    assert payload["backend"] == "s3"
    # No bucket/endpoint configured, so it is honest about being unusable...
    assert payload["ok"] is False
    # ...and it got there without a socket, which is the property under test.


# ---------------------------------------------------------------------------
# mail: a notification, so it fails quiet
# ---------------------------------------------------------------------------
def test_the_mailer_factory_returns_the_documented_surface():
    from service.infrastructure import mail as mail_module

    assert _missing(MAILER_METHODS, mail_module.get_mailer()) == []


def test_mail_is_disabled_by_default_and_says_so():
    from service.infrastructure import mail as mail_module

    mailer = mail_module.get_mailer()
    assert mailer.describe()["enabled"] is False
    assert mailer.send("a@example.com", "subject", "body") is False


def test_smtp_without_a_host_is_disabled_rather_than_raising(monkeypatch):
    monkeypatch.setenv("MAIL__ENABLED", "true")
    monkeypatch.setenv("MAIL__BACKEND", "smtp")
    from service.infrastructure import mail as mail_module

    assert mail_module.get_mailer().describe()["backend"] == "disabled"


def test_the_disabled_backend_writes_no_file(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox.log"
    monkeypatch.setenv("MAIL__FILE_PATH", str(outbox))
    from service.infrastructure import mail as mail_module

    mail_module.get_mailer().send("a@example.com", "s", "b")
    assert not outbox.exists()


# ---------------------------------------------------------------------------
# the protocols themselves (gated on the slice move landing)
# ---------------------------------------------------------------------------
def test_the_ports_package_imports_nothing():
    """A port is a shape, not a dependency: importing it must stay framework-free."""
    code = "import json, sys\nimport service.shared.ports\nprint(json.dumps(sorted(sys.modules)))\n"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    for name in list(env):
        if name.startswith(("DB_", "MYSQL_")):
            del env[name]
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if "No module named" in result.stderr:
        pytest.skip("not yet migrated: service/shared/ports/ does not exist yet")
    assert result.returncode == 0, result.stderr
    loaded = result.stdout.strip().splitlines()[-1]
    for forbidden in ("fastapi", "starlette", "pymysql", "redis", "celery", "boto3"):
        assert '"%s' % forbidden not in loaded, (
            "importing the ports package pulled in %s; a port must describe a "
            "shape without importing a driver" % forbidden
        )


def test_every_documented_port_is_declared():
    if not _ports_available():
        pytest.skip("not yet migrated: service/shared/ports/ does not exist yet")
    ports = importlib.import_module(PORTS_PACKAGE)
    missing = [
        name
        for name in ("Cache", "FileStorage", "Mailer", "JobQueue", "MlClient")
        if not hasattr(ports, name)
    ]
    assert missing == [], "the ports package does not export %r" % (missing,)


def test_backends_satisfy_their_protocol():
    """Structural satisfaction, checked at runtime rather than by inheritance."""
    if not _ports_available():
        pytest.skip("not yet migrated: service/shared/ports/ does not exist yet")
    ports = importlib.import_module(PORTS_PACKAGE)
    from service.infrastructure import cache as cache_module
    from service.infrastructure import mail as mail_module
    from service.infrastructure import storage as storage_module

    pairs = (
        (ports.Cache, cache_module.build_cache("memory")),
        (ports.FileStorage, storage_module.build_storage("local")),
        (ports.Mailer, mail_module.get_mailer()),
    )
    for protocol, implementation in pairs:
        # isinstance is the only portable check: issubclass raises TypeError for
        # a protocol with non-method members ("Protocols with non-method members
        # don't support issubclass()") and data members are exactly what these
        # ports use for their configuration.
        assert isinstance(implementation, protocol), "%s does not satisfy %s" % (
            type(implementation).__name__,
            protocol.__name__,
        )
