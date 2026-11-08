"""Test package for the ML sidecar.

WHY THIS FILE EXISTS (and why the flat modules still have none):
`tests/` is a package so a helper can be imported as `from tests.snapshot_helpers
import ...` from any test module, exactly the way R:/GPTHub/backend/tests/ does it.
`service/` deliberately has NO `__init__.py`: those modules are imported by bare
name (`import app`, `import settings`) both in the image (PYTHONPATH=/app/service)
and in this suite (pyproject.toml pythonpath = ["service"]). Adding a package there
would make `import service.app` possible and `import app` ambiguous, and the two
layouts would drift.
"""
