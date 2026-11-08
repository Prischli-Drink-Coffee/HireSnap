"""The matching slice owns no table of its own.

It writes `personality_candidates`, `personality_vacancies` and `candidate_vacancies`, and
all three belong to the link slices that also expose CRUD routes for them. The SQL therefore
lives with those slices, and this directory is the placeholder the slice shape implies - a
second copy of a link-table INSERT here is how two repositories start to disagree about which
columns exist.
"""

__all__: list = []
