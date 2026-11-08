"""HireSnap backend service package.

This package is the whole backend. It is imported as `service.*` and is the only
top-level Python package the image puts on PYTHONPATH, so an import either works
from anywhere in the container or it does not work at all - there is no
"works because the process happened to start in that directory" case.

Layout, and why each directory exists:

    api/                HTTP plumbing only: `app.py` builds the root app, mounts
                        `/server`, `/public` and `/data`, installs CORS and the one
                        error handler; `registry.py` is the ordered router list and
                        `security.py` the JWT dependency. It holds no business rule.
    configuration/      Process configuration: the env wrapper, logging, the YAML
                        loader and the path helpers. Nothing here imports anywhere else.
    composition/        The ONLY wiring root. `services.py` knows which class
                        implements a domain, `repositories.py` which module holds its
                        SQL, `infra.py` which concrete cache/storage/mailer/queue/ML
                        transport a flag selects, `container.py` owns their lifetime and
                        `state.py` is the accessor surface a router calls.
    models/             The pydantic models that describe the data, split into
                        `auth_models.py` (credentials, tokens, JWT settings) and
                        `db/db_models.py` (the ten tables). Pure data, no I/O, and
                        nothing here may import `service.services` or `service.api`.
    infrastructure/     Swappable I/O: the database connection and schema loader, the
                        cache, object storage, mail, uploads and the messaging layer.
                        Each backend is chosen by an environment flag and has a local
                        default, so importing this package never requires a server.
    services/<domain>/  One vertical slice per business capability, laid out as
                        `application/` (the rules), `persistence/` (raw SQL for that
                        slice), `presentation/routers/<name>_api/` (the HTTP routes) and
                        `domain/` where a slice has rules of its own. A slice talks to
                        another slice through `composition.state`, never by import.
    shared/             Small helpers with no dependencies of their own: the error
                        hierarchy, the port protocols, password hashing, JWTs, URL
                        building and debug logging.

Why the split is real rather than cosmetic: the old tree had `services/` as twelve flat
modules that imported each other and raised `HTTPException` directly, `repositories/` at
the top level, and models mixed in. A rule that raises an HTTP exception cannot be called
from a worker, and two slices importing each other cannot be tested alone. Now a domain
failure is a `ServiceError` that exactly one handler maps to a status, and a slice reaches
a sibling through the composition layer.

The rule that keeps this honest: importing `service.api.app` must work with no MySQL, no
`.env`, no broker and no model weights. `tests/test_backend_structure.py` asserts the
layout, `tests/test_service_boundary.py` asserts the layer rules,
`tests/test_ports_contracts.py` asserts the ports pull in no driver, and
`tests/test_import_without_infra.py` asserts the import in a fresh interpreter from a
temporary working directory. `backend/scripts/check_backend_layout.py` is the same layout
claim expressed as a pre-commit and CI gate, so a reviewer does not have to be the gate.

Mirror of R:\\GPTHub\\backend\\service (one package, slices named after the business job).
"""

__version__ = "1.0.0"
