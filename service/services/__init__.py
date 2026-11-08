"""Business rules, one PACKAGE per vertical slice.

Each slice under this directory is a small bounded context, laid out the same way:

    <domain>/application/     the rules. Plain Python: no fastapi, no broker, no SQL.
    <domain>/persistence/     the raw SQL for that slice, and nothing else.
    <domain>/presentation/    the HTTP routes (routers/<name>_api/), when the slice has any.
    <domain>/domain/          a slice-local model, when the rules need one.

An `__init__.py`-only layer is a placeholder that states the slice has nothing to put
there yet - not a TODO.

Two rules hold the slice boundary up, and both are asserted rather than intended:

  1. A slice NEVER imports another slice. It asks `service.composition.state` for the
     collaborator (`get_user_service()`), which is the only place the object graph is
     built. Two slices importing each other cannot be tested alone, and the cycle that
     used to exist here (twelve flat `*_services.py` modules, all importing each other)
     is exactly what this layout removes.
  2. A slice raises `service.shared.errors.ServiceError`, never `fastapi.HTTPException`.
     A rule that knows about HTTP cannot be called from a worker, and a failure that is
     an exception class cannot be mapped in one place. Exactly one handler in
     `service/api/app.py` turns it into a status.

`backend/tests/test_service_boundary.py` asserts both, plus the layering inside a slice;
`backend/scripts/check_backend_layout.py` asserts the shape (a slice directory, not a flat
module) so the pile cannot quietly come back.
"""
