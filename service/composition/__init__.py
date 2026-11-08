"""The composition root: the only package that decides which concrete object is used.

`container.py` owns the object graph and its lifetime, `infra.py` builds the platform
ports, `services.py` builds the slice services, `repositories.py` says where the SQL lives
and `state.py` is the access surface a router calls.

WHY THE PACKAGE IS ONE DIRECTORY AND NOT FIVE MODULES: every one of these files exists to
answer a question about wiring, and a reader looking for "where is X built" should have one
place to look. `backend/scripts/check_backend_layout.py` treats the directory as a single
unit and forbids `service/models` and `service/shared` from importing it.

WHY `state` IS NOT RE-EXPORTED HERE: importing the package must stay cheap, and `state`
imports the container, which imports the builders. A module that only wants the package
docstring should not pay for the graph. Import `service.composition.state` explicitly.
"""

__all__: list = []
