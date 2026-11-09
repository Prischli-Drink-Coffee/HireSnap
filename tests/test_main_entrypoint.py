"""service/main.py: the entry point "python -m service.main" runs in the image.

WHY THE ARGUMENTS TO uvicorn.run ARE ASSERTED AND NOT THE SERVER: this module's whole job is
to hand uvicorn three correct things - an absolute import target, the YAML log config, and a
reload flag derived from DEBUG. Getting the target wrong is the failure that started this
refactor (the old "server:app" only resolved when the process was started from the right
directory), and it fails as a confusing import error rather than a clear one.

WHY THE SCHEMA BOOTSTRAP IS TESTED SEPARATELY FROM main(): it must stay LAZY. The image
imports this module at container start with no MySQL reachable, so importing service.main may
not import the schema module (which imports pandas, numpy and pymysql at module level).
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from service import main as main_module
from service.configuration.logging import load_logging_config
from service.configuration.paths import BACKEND_ROOT, LOGGING_YAML
from service.main import _debug_flag, bootstrap_schema, main, os_env_flag, run_server


class FakeEnv:
    """Stands in for service.configuration.env.Env: getattr reads a dict, no .env file."""

    def __init__(self, **values):
        self._values = values

    def __getattr__(self, name):
        try:
            return self._values[name]
        except KeyError as exc:  # pragma: no cover - a test asked for an undefined key
            raise AttributeError(name) from exc


# --- _debug_flag: the strict guard -----------------------------------------
@pytest.mark.parametrize("raw, expected", [("TRUE", True), ("FALSE", False)])
def test_debug_flag_accepts_the_two_manifest_values(raw, expected):
    assert _debug_flag(FakeEnv(DEBUG=raw)) is expected


@pytest.mark.parametrize("raw", ["true", "1", "YES", "", " true "])
def test_debug_flag_rejects_everything_else_with_the_exact_message(raw):
    """Lowercase "true" is rejected on purpose: the manifest spells it TRUE.

    A permissive reader here would hide a typo that also decides whether --reload is on in
    production, so the error message names the value it saw.
    """
    with pytest.raises(RuntimeError) as caught:
        _debug_flag(FakeEnv(DEBUG=raw))

    assert str(caught.value) == f"DEBUG must be TRUE or FALSE, got {raw!r}"


def test_debug_flag_does_not_default_when_the_variable_is_absent():
    """Env raises AttributeError for a missing key, and this function must not swallow it."""
    with pytest.raises(AttributeError):
        _debug_flag(FakeEnv())


# --- os_env_flag ------------------------------------------------------------
@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_os_env_flag_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv("INIT_DB", raw)

    assert os_env_flag("INIT_DB") is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE"])
def test_os_env_flag_falsy_spellings(monkeypatch, raw):
    monkeypatch.setenv("INIT_DB", raw)

    assert os_env_flag("INIT_DB") is False


@pytest.mark.parametrize("raw", ["", "  ", "maybe", "ture", "трuе"])
def test_os_env_flag_refuses_a_value_nobody_meant(monkeypatch, raw):
    """REPORTED, NOW FIXED (KNOWN_GAPS section 35): a typo used to read as "off".

    INIT_DB gates the schema bootstrap, so a typo silently skipped it and the container
    came up against an empty database. An unparseable value now raises at boot, which is
    the loud failure; see service/configuration/flags.py for the rule and the reason.
    """
    from service.configuration.flags import FlagValueError

    monkeypatch.setenv("INIT_DB", raw)

    with pytest.raises(FlagValueError):
        os_env_flag("INIT_DB")


def test_os_env_flag_is_false_for_an_unset_variable(monkeypatch):
    monkeypatch.delenv("INIT_DB", raising=False)

    assert os_env_flag("INIT_DB") is False


# --- bootstrap_schema: lazy import -----------------------------------------
def test_bootstrap_schema_imports_the_schema_module_on_call(monkeypatch):
    """The import lives inside the function, so importing service.main pulls in no pandas."""
    created = []

    class FakeCreateSQL:
        def __init__(self):
            created.append("constructed")

        def read_sql(self):
            created.append("read")

    fake = types.ModuleType("service.infrastructure.database.schema")
    fake.CreateSQL = FakeCreateSQL
    monkeypatch.setitem(sys.modules, "service.infrastructure.database.schema", fake)

    bootstrap_schema(FakeEnv())

    assert created == ["constructed", "read"]


def test_importing_the_entry_point_does_not_import_the_schema_module():
    """A container starts this module with no MySQL; the dump loader must not be imported yet.

    The probe runs in a SUBPROCESS because another test in the session may already have
    imported the schema module - absence in this process would prove nothing.
    """
    import subprocess
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent
    code = (
        "import sys; import service.main; "
        "print('schema=' + str('service.infrastructure.database.schema' in sys.modules)); "
        "print('pandas=' + str('pandas' in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "schema=False" in result.stdout
    assert "pandas=False" in result.stdout


# --- run_server -------------------------------------------------------------
@pytest.fixture()
def uvicorn_calls(monkeypatch):
    calls = {}

    def fake_run(target, **kwargs):
        calls.update(kwargs)
        calls["target"] = target

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)
    return calls


def test_run_server_hands_uvicorn_an_absolute_target_and_the_yaml_log_config(
    monkeypatch, uvicorn_calls
):
    """The target is a module path, not a file name: the reloader must find it from /app."""
    monkeypatch.setattr(
        main_module,
        "Env",
        lambda: FakeEnv(DEBUG="FALSE", HOST="127.0.0.1", SERVER_PORT="18099"),
    )

    run_server()

    assert uvicorn_calls["target"] == "service.api.app:app"
    assert uvicorn_calls["host"] == "127.0.0.1"
    assert uvicorn_calls["port"] == 18099  # cast from the string in the environment
    assert uvicorn_calls["reload"] is False
    # None, not the path: this module applies the YAML itself so the file handler's
    # relative name can be resolved. Handing uvicorn the path would make it apply the
    # config a second time, against the working directory.
    assert uvicorn_calls["log_config"] is None


def test_run_server_enables_the_reloader_only_for_debug_true(monkeypatch, uvicorn_calls):
    monkeypatch.setattr(
        main_module, "Env", lambda: FakeEnv(DEBUG="TRUE", HOST="0.0.0.0", SERVER_PORT="8080")
    )

    run_server()

    assert uvicorn_calls["reload"] is True


def test_run_server_applies_the_logging_yaml(monkeypatch, uvicorn_calls):
    """dictConfig is what makes logs/app.log and the uvicorn loggers exist before serving."""
    configured = []
    monkeypatch.setattr(
        main_module.logging.config, "dictConfig", lambda config: configured.append(config)
    )
    monkeypatch.setattr(
        main_module, "Env", lambda: FakeEnv(DEBUG="FALSE", HOST="0.0.0.0", SERVER_PORT="8080")
    )

    run_server()

    assert len(configured) == 1
    # The file is backend/service/configuration/logging.yaml; the config is a dict, not a path.
    assert configured[0]["version"] == 1
    assert "handlers" in configured[0] and "loggers" in configured[0]


def test_run_server_reads_the_log_config_from_the_package_not_the_working_directory(
    monkeypatch, tmp_path, uvicorn_calls
):
    """MEASURED: the old server.py opened src/logging.yaml relative to the CWD and broke.

    dictConfig is patched out here because the YAML's own file handler uses a RELATIVE
    filename ("logs/app.log") - see the test below, which is where that is pinned.
    """
    configured = []
    monkeypatch.setattr(
        main_module.logging.config, "dictConfig", lambda config: configured.append(config)
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        main_module, "Env", lambda: FakeEnv(DEBUG="FALSE", HOST="0.0.0.0", SERVER_PORT="8080")
    )

    run_server()

    assert uvicorn_calls["log_config"] is None
    assert main_module.LOGGING_YAML.is_absolute()
    assert len(configured) == 1
    # The handler target is absolute even though the YAML spells it relatively, so a
    # process started from tmp_path writes the same log file as one started from backend/.
    assert configured[0]["handlers"]["file"]["filename"] == str(BACKEND_ROOT / "logs" / "app.log")


def test_the_yaml_still_spells_the_log_file_relatively():
    """The FILE keeps the relative spelling; load_logging_config() resolves it at load.

    Pinning the file's own text matters because the fix lives in the loader: a later edit
    that hard-codes an absolute path into the YAML would make the same file useless on any
    host whose log directory is not this checkout's, and the resolver would stop being the
    one place that decides where the log goes.
    """
    import yaml

    config = yaml.safe_load(main_module.LOGGING_YAML.read_text(encoding="utf-8"))

    assert config["handlers"]["file"]["filename"] == "logs/app.log"


def test_load_logging_config_makes_the_file_handler_absolute(monkeypatch, tmp_path):
    """The resolved config is what dictConfig receives, so this is where the CWD stops."""
    monkeypatch.chdir(tmp_path)

    config = load_logging_config(LOGGING_YAML)

    assert config["handlers"]["file"]["filename"] == str(BACKEND_ROOT / "logs" / "app.log")
    # Everything else is untouched: the YAML stays the source of truth for the rest.
    assert config["handlers"]["file"]["class"] == "logging.FileHandler"
    assert config["handlers"]["file"]["encoding"] == "utf-8"
    assert sorted(config["loggers"]) == ["uvicorn", "uvicorn.access", "uvicorn.error"]
    assert config["root"]["handlers"] == ["file", "console"]


def test_load_logging_config_leaves_an_absolute_filename_alone(tmp_path):
    """A deployment that mounts its log elsewhere writes the path it configured."""
    import yaml

    source = tmp_path / "logging.yaml"
    payload = {
        "version": 1,
        "handlers": {"file": {"class": "logging.FileHandler", "filename": str(tmp_path / "x.log")}},
    }
    source.write_text(yaml.safe_dump(payload), encoding="utf-8")

    assert load_logging_config(source)["handlers"]["file"]["filename"] == str(tmp_path / "x.log")


def test_load_logging_config_without_a_file_handler_is_a_no_op(tmp_path):
    """A config with no file handler must not grow one - absence is not a request."""
    import yaml

    source = tmp_path / "logging.yaml"
    source.write_text(yaml.safe_dump({"version": 1, "handlers": {}}), encoding="utf-8")

    assert load_logging_config(source)["handlers"] == {}


# --- main -------------------------------------------------------------------
def test_main_bootstraps_the_schema_only_for_debug_true_and_init_db(monkeypatch):
    bootstrapped = []
    monkeypatch.setattr(main_module, "Env", lambda: FakeEnv(DEBUG="TRUE"))
    monkeypatch.setattr(main_module, "bootstrap_schema", lambda env: bootstrapped.append(env))
    monkeypatch.setattr(main_module, "run_server", lambda: None)
    monkeypatch.setattr(main_module, "debug_info", lambda message: None)
    monkeypatch.setenv("INIT_DB", "1")

    assert main() == 0
    assert len(bootstrapped) == 1


@pytest.mark.parametrize("debug, init_db", [("TRUE", "0"), ("FALSE", "1"), ("FALSE", "0")])
def test_main_skips_the_bootstrap_outside_the_debug_init_combination(monkeypatch, debug, init_db):
    """Both conditions are required: INIT_DB alone must not make a production boot touch DDL."""
    bootstrapped = []
    monkeypatch.setattr(main_module, "Env", lambda: FakeEnv(DEBUG=debug))
    monkeypatch.setattr(main_module, "bootstrap_schema", lambda env: bootstrapped.append(env))
    monkeypatch.setattr(main_module, "run_server", lambda: None)
    monkeypatch.setattr(main_module, "debug_info", lambda message: None)
    monkeypatch.setenv("INIT_DB", init_db)

    assert main() == 0
    assert bootstrapped == []


def test_main_always_starts_the_server_after_the_bootstrap(monkeypatch):
    order = []
    monkeypatch.setattr(main_module, "Env", lambda: FakeEnv(DEBUG="TRUE"))
    monkeypatch.setattr(main_module, "bootstrap_schema", lambda env: order.append("bootstrap"))
    monkeypatch.setattr(main_module, "run_server", lambda: order.append("serve"))
    monkeypatch.setattr(main_module, "debug_info", lambda message: None)
    monkeypatch.setattr(main_module.log, "info", lambda message: None)
    monkeypatch.setenv("INIT_DB", "true")

    main()

    assert order == ["bootstrap", "serve"]


def test_main_returns_zero_so_the_module_exit_code_is_success(monkeypatch):
    monkeypatch.setattr(main_module, "Env", lambda: FakeEnv(DEBUG="FALSE"))
    monkeypatch.setattr(main_module, "run_server", lambda: None)
    monkeypatch.setattr(main_module, "debug_info", lambda message: None)
    monkeypatch.setattr(main_module.log, "info", lambda message: None)

    assert main(argv=[]) == 0


def test_the_entry_point_is_importable_without_a_database_or_an_env_file():
    """The module-level code is a logger and imports only; nothing connects at import time."""
    assert callable(main) and callable(run_server)
    assert isinstance(main_module.log.name, str)
    assert logging.getLogger(main_module.log.name) is main_module.log
