"""Child process for test_service_boundary.py: what does `import app` actually load?

WHY A SEPARATE SCRIPT AND NOT A -c STRING
-------------------------------------------------
A one-liner would have to be assembled inside the test, and the day it needs a branch
the quoting becomes the test. This file is imported by nothing; the test runs it with a
fresh interpreter that can only see ml/service/. Keep it dependency-free: standard
library only, no pytest, no settings.

It prints one comma-separated line of offending module names and exits 0. An empty line
means the app pulled in nothing from outside ml/.
"""

from __future__ import annotations

import sys

FORBIDDEN_ROOTS = ("backend", "src", "service")


def offending_modules() -> list:
    found = []
    for name in sys.modules:
        if name.split(".")[0] in FORBIDDEN_ROOTS:
            found.append(name)
    return sorted(found)


def main() -> int:
    import app  # noqa: F401 - the import IS the probe

    print(",".join(offending_modules()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
