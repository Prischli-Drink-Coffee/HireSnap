"""HireSnap backend service package.

This package is the whole backend. It is imported as `service.*` and is the only
top-level Python package the image puts on PYTHONPATH, so an import either works
from anywhere in the container or it does not work at all - there is no
"works because the process happened to start in that directory" case.

Layout, and why each directory exists:

    api/            HTTP layer. Routers validate a request and delegate; they hold
                    no SQL and no business rules. security.py is the JWT dependency.
    configuration/  Process configuration: the env wrapper, logging, the YAML
                    loader and the path helpers. Nothing here imports anywhere else.
    domain/         The pydantic models that describe the data. Pure data, no I/O.
    infrastructure/ Swappable I/O: the database connection and schema loader, the
                    cache, object storage, mail, uploads and the Celery messaging
                    layer. Each backend is chosen by an environment flag and has a
                    local default, so importing this package never requires a server.
    repositories/   Raw SQL, one module per table. They take a model or a dict and
                    return rows; they never raise an HTTP error.
    services/       Business rules, one module per vertical. They compose
                    repositories and raise HTTPException, which is what the routers
                    surface.
    shared/         Small helpers with no dependencies of their own: password
                    hashing, JWTs, URL building, validation helpers and debug logging.

The rule that keeps this honest: importing `service.api.app` must work with no
MySQL, no `.env` and no model weights. tests/test_backend_structure.py asserts the
layout (one package, the short root allowlist, the layer directories, no `src.*`
imports, every router mounted) and tests/test_import_without_infra.py asserts the
import in a fresh interpreter from a temporary working directory.

Mirror of R:\\GPTHub\\backend\\service (one package, layers named after the job they do).
"""

__version__ = "1.0.0"
