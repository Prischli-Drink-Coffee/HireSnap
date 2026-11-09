"""HTTP surface of the jobs slice.

WHY THIS PACKAGE EXISTS AT ALL: it did not, on purpose, until the job machinery had a
caller worth exposing (see docs/KNOWN_GAPS.md section 27 for the history). A slice with
no route is a slice whose rules are only reachable from Python; the jobs slice is the
first one where that stopped being true.
"""
