"""Test package.

WHY a package and not a bare directory: the snapshot generator is invoked as
`python -m tests.snapshot_tool`, which needs a stable module path, and qualified
test module names (`tests.test_ml_client`) make pytest failures unambiguous when
two directories hold a file with the same basename. Sub-directories
(fixtures/, integration/, snapshots/, vectors/) stay namespace packages, which is
the layout the GPTHub backend uses.
"""
