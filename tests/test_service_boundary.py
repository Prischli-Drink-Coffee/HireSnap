"""The sidecar boundary: ml/service imports nothing from backend/, and nothing else either.

WHY THIS IS A TEST AND NOT A README SENTENCE
---------------------------------------------
The defect this repository already paid for was a sidecar that reached back into
backend/ through a sys.path insert plus an AST rewrite of the imported module. It worked,
so no assertion noticed it, and it only failed later - in the image, when backend/ was not
part of the build context. The rule is cheap to state and easy to break with one convenient
import, so it is checked: every module under ml/service/ is parsed and its imports are
inspected, and a fresh interpreter is asked what it loaded after importing the app.

Grep would not do: backend appears in dozens of comments and docstrings here (the code
was moved out of backend/, and the WHY comments say so). AST sees imports only, so prose
cannot satisfy or trip it. R:/GPTHub/backend/tests/test_service_boundary.py uses the same
technique for the mirror-image rule.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Tuple

from tests.conftest import CONFIG_ENV

SERVICE_DIR = Path(__file__).resolve().parents[1] / "service"
VENDORED_PACKAGE = "cosmos_tokenizer"

# The flat modules of service/. An import that resolves to one of these by bare name is a
# sibling dependency, which is the point of the flat layout: it works in the image
# (PYTHONPATH=/app/service) and in the suite (pyproject pythonpath=["service"]) alike.
SIBLING_MODULES = (
    "app",
    "engine",
    "frames",
    "settings",
    "text_embedding",
    "user_embedding",
    "video_embedding",
    "video_to_text",
)

# Prefixes that may never appear in an import inside the sidecar. backend is the tree the
# image does not contain; src is the backend package root and the shape the old shim used;
# service is a real package name in backend/, so importing it would mean the sidecar is
# being loaded from the superproject root instead of standing on its own.
FORBIDDEN_PREFIXES = ("backend", "src", "service")

# Third-party distributions the sidecar is allowed to import, mirroring
# ml/docker/requirements.txt. Deliberately closed: a new import must be added here AND to
# the requirements file in the same commit, so the image keeps matching the code.
THIRD_PARTY_MODULES = (
    "fastapi",
    "moviepy",
    "numpy",
    "PIL",
    "pydantic",
    "sentence_transformers",
    "starlette",
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
    "uvicorn",
)

STANDARD_LIBRARY = set(sys.stdlib_module_names)


def _module_names(tree: ast.AST) -> List[Tuple[str, int]]:
    """Every imported module name in one file, with its line number.

    A relative import (from . import x) resolves inside service/ and cannot reach
    backend/ or src/ at all, so it is recorded separately by the test that forbids it
    rather than folded in here.
    """
    found: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            found.append((node.module, node.lineno))
    return found


def _top_level(name: str) -> str:
    return name.split(".")[0]


def _service_files() -> List[Path]:
    """service/*.py plus the vendored package, without __pycache__."""
    return sorted(p for p in SERVICE_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _relative(path: Path) -> str:
    return path.relative_to(SERVICE_DIR.parent).as_posix()

def test_service_never_imports_the_backend_tree() -> None:
    """The rule the old sys.path shim broke, checked on every file in the service."""
    offenders: List[str] = []
    for path in _service_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, lineno in _module_names(tree):
            if _top_level(name) in FORBIDDEN_PREFIXES:
                offenders.append("%s:%d -> %s" % (_relative(path), lineno, name))
    assert not offenders, (
        "the sidecar imports something outside ml/ (or a package prefix rather than a",
        "bare sibling name):\n  " + "\n  ".join(offenders),
    )


def test_service_uses_no_relative_imports() -> None:
    """Relative imports only work when service/ is a package, and it is not one.

    The image runs python3 app.py from /app/service and the suite runs pytest with
    pythonpath=["service"]: both give bare top-level modules. A relative import would pass
    a casual check and fail inside the container.
    """
    offenders: List[str] = []
    for path in _service_files():
        if VENDORED_PACKAGE in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0:
                offenders.append(
                    "%s:%d (level %d)" % (_relative(path), node.lineno, node.level)
                )
    assert not offenders, (
        "relative imports in a flat module tree:\n  " + "\n  ".join(offenders)
    )


def test_every_import_is_a_sibling_a_dependency_or_the_standard_library() -> None:
    """No import in service/ may name a local module other than a flat sibling.

    This is the positive half of the rule above: it catches a helper.py that was left
    outside service/ (so the import works in a developer checkout and fails in the
    image), which a prefix check cannot see because the name looks harmless.
    """
    allowed = set(SIBLING_MODULES) | set(THIRD_PARTY_MODULES) | {VENDORED_PACKAGE}
    offenders: List[str] = []
    for path in _service_files():
        if VENDORED_PACKAGE in path.parts:
            # Upstream NVIDIA code, vendored verbatim (ml/README.md, "Vendored code").
            # It imports itself as cosmos_tokenizer.*; that package IS allowed above.
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, lineno in _module_names(tree):
            top = _top_level(name)
            if top in allowed or top in STANDARD_LIBRARY:
                continue
            offenders.append("%s:%d -> %s" % (_relative(path), lineno, name))
    assert not offenders, (
        "unknown import in service/; add the distribution to ml/docker/requirements.txt",
        "and to THIRD_PARTY_MODULES here in the same commit:\n  " + "\n  ".join(offenders),
    )

def _run_probe(source_path: Path) -> str:
    """Run a helper in a fresh interpreter whose PYTHONPATH is only service/.

    subprocess is the point: the pytest process has already imported pytest, its plugins
    and every module earlier tests touched, so asking sys.modules here would be a
    statement about the test runner rather than about the sidecar.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_DIR)
    # A stray ML_* value in the parent shell must not change what the child imports or
    # how it answers, so the documented keys are removed rather than inherited.
    for name in CONFIG_ENV:
        env.pop(name, None)
    completed = subprocess.run(
        [sys.executable, str(source_path)],
        cwd=str(SERVICE_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_importing_the_app_pulls_in_no_backend_package() -> None:
    """In a fresh interpreter, importing app must not load backend, src.* or service.*.

    The probe prints the offending names, so a failure says WHICH convenience import did
    it: a sys.modules assertion here would report the test runner instead of the app.
    """
    probe_path = Path(__file__).resolve().parent / "boundary_probe.py"
    loaded = [name for name in _run_probe(probe_path).strip().split(",") if name]
    assert not loaded, "importing app loaded modules from outside ml/: %s" % (loaded,)


def _settings_env_names() -> Set[str]:
    """Every literal name settings.py passes to one of its own readers.

    Parsed rather than executed: executing would read the developer shell. settings.py is
    the only module that touches os.environ, and it does so through exactly one primitive
    (`_raw`), which the typed helpers `_text`/`_int`/`_float`/`_path` wrap. The first
    positional argument of any of them is therefore a configuration key, and the set of
    those literals IS the configuration surface.
    """
    readers = {"_raw", "_text", "_int", "_float", "_path"}
    tree = ast.parse((SERVICE_DIR / "settings.py").read_text(encoding="utf-8"))
    names: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr
        else:
            continue
        if func_name not in readers or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


def test_settings_is_the_only_reader_of_the_environment() -> None:
    """One module reads os.environ, so the whole surface is one file to audit.

    A second reader is how a service ends up with a key that is not in the README, is not
    in the compose file and is not in docker/.env.example - and a deployment silently runs
    on a default nobody knew about.
    """
    offenders: List[str] = []
    for path in _service_files():
        if VENDORED_PACKAGE in path.parts or path.name == "settings.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if not (isinstance(node.value, ast.Name) and node.value.id == "os"):
                continue
            if node.attr in ("environ", "getenv", "putenv", "environb"):
                offenders.append(
                    "%s:%d -> os.%s" % (_relative(path), node.lineno, node.attr)
                )
    assert not offenders, (
        "os.environ is read outside settings.py; route the value through a settings.py",
        "getter so the README table and the code cannot disagree:\n  " + "\n  ".join(offenders),
    )

def test_every_environment_name_is_namespaced() -> None:
    """Every name is ML_-prefixed or one of the documented deployment keys.

    The convention (ml/README.md, "Configuration") is that the compose file translates
    ML__X into the flat ML_X the sidecar reads. The exceptions below are deliberate and
    each has a reason:

    * DATA_PATH, WEIGHTS_DIR, PRETRAINED_CKPT_DIR, HF_HOME name HOST paths that the
      operator mounts. They keep the spelling the other services use for the same mount,
      so one manifest line means one thing in the stack.
    * DEVICE and FFMPEG_BINARY are deployment facts (a GPU is or is not present, ffmpeg
      lives at this path), not knobs of this service.
    * MAX_UPLOAD_MB, MAX_TEXT_CHARS and MODEL_IDLE_UNLOAD_SECONDS describe the WORK, and
      the backend mirrors them under the same names; prefixing them here would create two
      spellings of one limit.

    A new name has to be added to this set AND to the table in README.md, which is the
    point: the test forces the documentation question.
    """
    unprefixed = {
        "DATA_PATH",
        "WEIGHTS_DIR",
        "PRETRAINED_CKPT_DIR",
        "HF_HOME",
        "DEVICE",
        "FFMPEG_BINARY",
        "MAX_UPLOAD_MB",
        "MAX_TEXT_CHARS",
        "MODEL_IDLE_UNLOAD_SECONDS",
    }
    unexpected = {
        name
        for name in _settings_env_names()
        if not name.startswith("ML_") and name not in unprefixed
    }
    assert not unexpected, "undeclared configuration names: %s" % (sorted(unexpected),)


def test_the_test_environment_matches_the_settings_surface() -> None:
    """tests/conftest.py clears exactly the names settings.py reads.

    If the two drift the suite stops being hermetic in one direction (a name is cleared
    that nothing reads - harmless) or the other (a key the code reads survives from the
    developer shell, and a test passes or fails depending on the machine).
    """
    assert set(CONFIG_ENV) == _settings_env_names()


def test_service_has_one_nested_package_and_it_is_the_vendored_one() -> None:
    """The only directory with an __init__.py is the vendored tokenizer.

    A second nested package would mean the flat layout is quietly becoming a tree, and
    the image (which copies service/ and sets PYTHONPATH=/app/service) would start needing
    package imports that the suite would have to reproduce.
    """
    packages = sorted(
        p.relative_to(SERVICE_DIR).as_posix()
        for p in SERVICE_DIR.rglob("__init__.py")
        if "__pycache__" not in p.parts
    )
    assert packages, "the vendored tokenizer package disappeared"
    assert all(path.startswith(VENDORED_PACKAGE + "/") for path in packages), packages


def test_the_service_dir_itself_is_not_a_package() -> None:
    """No service/__init__.py: the modules are top-level, not a package.

    This is the one line that keeps `import app` working in both the image and the suite
    without either of them guessing; with a package marker pytest would resolve the bare
    name to whatever it found first.
    """
    assert not (SERVICE_DIR / "__init__.py").exists()
