"""Integration checks: they need the real stack and are skipped by default.

WHY a separate package rather than a marker alone: "python -m pytest" from
backend/ must stay hermetic, and a directory of skipped modules is a visible
signal in the output, unlike a test that silently does not exist. Every module in
here guards itself with HIRESNAP_INTEGRATION=1 so collection alone never opens a
database connection.

Stays a namespace package like tests/fixtures, matching GPTHub.
"""
