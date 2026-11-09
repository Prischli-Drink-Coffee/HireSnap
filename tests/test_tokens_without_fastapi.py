"""PROOF THAT THE TOKEN CODEC IMPORTS WITHOUT THE API STACK (KNOWN_GAPS section 36).

WHY THIS FILE EXISTS. `service/shared/tokens.py` is a CODEC: a worker, a CLI, a migration
script or a unit test may need to sign or verify a token without installing FastAPI,
Starlette or anything that only a web process needs. Every sibling in `service/shared/` is
framework-free on purpose; the codec used to be the exception, importing `fastapi` for the
401 it raises and building an `HTTPBearer` instance for a route decorator.

WHY A SUBPROCESS AND NOT A RELOAD. `importlib.reload` inside the test process proves almost
nothing: `service.api`, `fastapi` and `starlette` are already in `sys.modules` because other
tests imported them, so a module that says `from fastapi import HTTPException` would still
succeed from the cache. Poisoning the framework in a fresh interpreter makes the import fail
loudly if the dependency ever comes back, and it also proves nothing ELSE on the import path
drags the framework in (`service.models`, `service.shared.errors`,
`service.configuration.paths`).

WHAT IS *NOT* ASSERTED. The absence of pandas, numpy or pydantic: `service.models`
legitimately needs pydantic, so a codec that reads settings is allowed to load it. The rule
under test is narrow and checkable: no web framework.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

#: Modules the codec must not need. fastapi pulls starlette in, so blocking fastapi alone
#: would be enough for both, but the probe blocks and then checks the whole set so a report
#: names the offender rather than the dependency that dragged it in.
BLOCKED = ("fastapi", "starlette")

#: Imported inside the probe, after the block is installed. `{module}` is substituted.
PROBE = """
import importlib.abc
import json
import sys


class Blocker(importlib.abc.MetaPathFinder):
    def __init__(self, names):
        self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ImportError("blocked for this probe: %s" % fullname)
        return None


blocked = json.loads(sys.argv[1])
name_set = set(blocked)
for name in list(sys.modules):
    if name.split(".")[0] in name_set:
        del sys.modules[name]
# None in sys.modules makes a later `import fastapi` raise ImportError instead of
# re-importing it; the finder covers submodules of a name that is imported for the first time.
for name in blocked:
    sys.modules[name] = None
sys.meta_path.insert(0, Blocker(blocked))

import {module} as target  # noqa: E402  (the import IS the assertion)

leaked = sorted(
    name
    for name in sys.modules
    if name.split(".")[0] in name_set and sys.modules[name] is not None
)
print(json.dumps({"file": target.__file__, "leaked": leaked}))
"""


def _run_probe(module, blocked=BLOCKED):
    """Import `module` in a fresh interpreter with the frameworks blocked.

    Starts from the real environment (a stripped env breaks the interpreter's own startup,
    and the venv's site-packages are part of what makes the import meaningful) and only
    points PYTHONPATH at the backend root.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    return subprocess.run(
        [sys.executable, "-c", PROBE.replace("{module}", module), json.dumps(list(blocked))],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_the_token_codec_imports_with_fastapi_and_starlette_blocked():
    result = _run_probe("service.shared.tokens")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    # Not merely "did not raise": nothing pulled the framework in behind the block either.
    assert report["leaked"] == [], "the codec import pulled in %s" % report["leaked"]
    assert report["file"].endswith("service\\shared\\tokens.py") or report["file"].endswith(
        "service/shared/tokens.py"
    )


def test_the_probe_would_notice_a_restored_fastapi_import():
    """Anti-vacuity: if the block were ineffective, the test above would pass trivially.

    `service.api.security` imports fastapi at module level BY DESIGN, so under the same probe
    it must fail - otherwise the guard proves nothing about `tokens.py`.
    """
    result = _run_probe("service.api.security")

    assert result.returncode != 0
    # Two markers, because two mechanisms can fire first: the poisoned sys.modules entry
    # ("import of fastapi halted") or the meta-path finder ("blocked for this probe").
    assert "blocked for this probe" in result.stderr or "halted" in result.stderr, result.stderr

    unblocked = subprocess.run(
        [sys.executable, "-c", "import service.api.security"],
        cwd=str(BACKEND_ROOT),
        env=dict(os.environ, PYTHONPATH=str(BACKEND_ROOT)),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert unblocked.returncode == 0, unblocked.stderr
