"""The auth slice owns no table of its own.

Its rules read and write `users`, and that table belongs to the users slice, so the SQL
lives at `service/services/users/persistence/user_repository.py` and this directory
stays as the placeholder the slice shape implies (AGENTS.md: an empty layer is a
placeholder, not a to-do). Putting a second copy of the users SQL here is how two
repositories start to disagree.
"""

__all__: list = []
