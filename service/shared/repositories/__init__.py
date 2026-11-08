"""Reserved for SQL that spans slices. Deliberately empty today, and that is the point.

WHY the directory exists at all: `service/shared/` is the bottom of the tree, and
`backend/tests/test_backend_structure.py` requires this directory to be there so that a
query needing two tables has an obvious home that is not "put it in whichever slice
called first".

WHY it holds nothing yet: every statement in this service touches ONE table, and a
single-table statement belongs to the slice that owns that table
(`service/services/<domain>/persistence/<domain>_repository.py`). Moving those files
here to fill the directory would be the same query in a worse place: the slice that
changes its own table would have to reach across the tree to do it.

WHAT BELONGS HERE when it appears: a read that genuinely joins two slices (a dashboard
count over users and vacancies, for example) or a migration helper. It may import
`service.models` and `service.infrastructure`, never `service.services` - a shared
repository that imports a slice would make the dependency circular, and
`backend/scripts/check_backend_layout.py` rule 7 fails the build for it.
"""

__all__: list = []
