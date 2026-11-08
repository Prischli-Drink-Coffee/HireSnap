"""Swappable I/O: database, cache, storage, mail, uploads and messaging.

Every backend in this package is chosen by an environment flag and has a LOCAL
default, so importing `service.api.app` requires no server of any kind. That is a
contract, not a coincidence: tests/test_import_without_infra.py asserts it.
"""
