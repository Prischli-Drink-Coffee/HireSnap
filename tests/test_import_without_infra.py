"""Importing the application must not require a database, a dotenv or a model.

WHY this is a subprocess test: the property is about a *fresh* interpreter. In the
pytest process other tests have already imported half the package, so an import-time
connection would be masked by module caching and the assertion would pass on a tree
that cannot start in a container.

Three regressions this pins, all of which existed before the service/ refactor:

1. `src/database/my_connector.py` opened a MySQL socket from a module-level
   `db = Database()`, so importing the server failed on a host without MySQL.
2. `src/utils/custom_logging.py` created `setup.log` in the current working
   directory at import time, which is why a stray log kept appearing at the repo root.
3. `src/database/models.py` read `ACCESS_TOKEN_EXPIRE_MINUTES` at import and raised
   when it was unset, so a container without that key died before serving /health.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The health payload the deployment probes. A literal because
#: docs/SERVICE_CONTRACTS.md section 1.1 promises this exact shape to compose, to the
#: nginx gateway and to the runbook.
EXPECTED_HEALTH = {"status": "ok", "service": "backend", "version": "1.0.0"}

#: Runs in a fresh interpreter, from a temporary working directory, with every DB_*
#: and MYSQL_* variable stripped. It reports what the import did, not what the
#: pytest process already cached.
PROBE = r"""
import json
import sys

import service.api.app as app_module
from fastapi.testclient import TestClient

connection_module = sys.modules.get("service.infrastructure.database.connection")
connection = getattr(connection_module, "db", None)

client = TestClient(app_module.create_app())
response = client.get("/server/health")

print(json.dumps({
    "has_src": any(name == "src" or name.startswith("src.") for name in sys.modules),
    "connection_open": bool(getattr(connection, "_connection", None)),
    "status_code": response.status_code,
    "body": response.json(),
}))
"""


def _run(code, tmp):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    for name in list(env):
        if name.startswith(("DB_", "MYSQL_")):
            del env[name]
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_app_imports_without_database_or_dotenv(tmp_path):
    result = _run(PROBE, tmp_path)
    assert result.returncode == 0, "import failed:\n%s\n%s" % (result.stdout, result.stderr)
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["has_src"] is False, "the retired src package was imported"
    assert payload["connection_open"] is False, (
        "importing the app opened a MySQL connection; the connection must stay lazy so "
        "that /server/health answers before the database is up"
    )
    assert payload["status_code"] == 200
    assert payload["body"] == EXPECTED_HEALTH


def test_import_creates_no_file_in_the_working_directory(tmp_path):
    """The old logging bootstrap wrote setup.log next to wherever the process started."""
    result = _run("import service.api.app\n", tmp_path)
    assert result.returncode == 0, result.stderr
    created = sorted(child.name for child in tmp_path.iterdir())
    assert created == [], "importing the app wrote %r into the working directory" % (created,)
