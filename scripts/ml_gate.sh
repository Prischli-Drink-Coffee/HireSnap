#!/usr/bin/env bash
# Run one ml/ verification step with a Python that actually exists.
#
# WHY THIS EXISTS: the hooks in ml/.pre-commit-config.yaml used to call
# `python -m ruff` / `python -m pytest` directly. On Windows the name `python` on
# PATH is the Microsoft Store stub, which starts nothing and exits 49, so
# pre-commit reported "exit code: 9009" for both hooks and blocked every commit
# in this submodule - a gate that always fails is a gate every developer turns
# off. The root repository solved the same problem in scripts/run_guard.sh: RUN
# each candidate with `-c "import sys"` and use the first that starts, instead of
# guessing a name.
#
# WHY A SKIP IS NOT A PASS: if no interpreter works, this prints SKIP and exits
# 0 - pre-commit would otherwise be unusable on a machine that has no Python - but
# it says so loudly, and CI runs the same commands with a real interpreter, so the
# check still happens somewhere it can be trusted (docs/ADR/0005).
set -u

usage() {
  echo "usage: scripts/ml_gate.sh <ruff|pytest|layout> [extra args...]" >&2
  echo "" >&2
  echo "  ruff     ruff check over this checkout (configuration: pyproject.toml)" >&2
  echo "  pytest   the contract tests; they must pass with no torch and no weights" >&2
  echo "  layout   check_ml_service_layout.py - the tree shape and the README routes" >&2
  exit 2
}

if [ "$#" -lt 1 ]; then
  usage
fi

GATE="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# CHANGE INTO THE CHECKOUT BEFORE CHOOSING AN INTERPRETER. A candidate is only
# judged where it will actually run. ml/.python-version pins 3.9 - the minor the
# image installs through deadsnakes - and on a host without 3.9 pyenv makes every
# shim in this directory exit 1 with "no global/local python version has been set".
# Probing in the caller directory and cd-ing afterwards picked python3.13 in the
# repository root and then ran it here, where it died: the gate failed for a reason
# that had nothing to do with the tree.
cd "${REPO_ROOT}" || exit 1

# Candidates, same order and same reasoning as scripts/run_guard.sh in the
# superproject: the versioned spellings are tried first because on pyenv-win the
# plain names are the Store stubs while python3.13/python3.12/... are real
# interpreters. An activated virtualenv wins over everything, because that is the
# environment the developer is actually testing against.
#
# WHY "can it start" IS NOT ENOUGH FOR ruff/pytest: a candidate also has to IMPORT
# the tool. On this host `python3` is uv-managed 3.12 with no ruff in it, while
# python3.13 has one - so a gate that only asked "does it start" picked 3.12 and
# failed with "No module named ruff" on a tree that was clean. That is the same
# class of bug as the Store stub: a gate that reports a failure it did not find.
# The layout gate is stdlib-only, so for it starting is enough.
NEEDS_MODULE=""
case "${GATE}" in
  ruff) NEEDS_MODULE="ruff" ;;
  pytest) NEEDS_MODULE="pytest" ;;
esac

usable() {
  # usable <interpreter command...>: starts, and can import NEEDS_MODULE when one is
  # required. Deliberately silent: the reason for a miss is reported once, at the end.
  if [ -n "${NEEDS_MODULE}" ]; then
    "$@" -c "import ${NEEDS_MODULE}" >/dev/null 2>&1
  else
    "$@" -c "import sys" >/dev/null 2>&1
  fi
}

PYTHON=""
# Escape hatch, and the only hint that survives a host whose PATH holds nothing usable:
# ML_GATE_PYTHON=/path/to/python names the interpreter explicitly. It is tried first and
# only accepted when it passes the same "can import the tool" test, so a stale path is a
# SKIP, not a false pass.
if [ -n "${ML_GATE_PYTHON:-}" ]; then
  if usable "${ML_GATE_PYTHON}"; then
    PYTHON="${ML_GATE_PYTHON}"
  else
    echo "FAIL ml ${GATE}: ML_GATE_PYTHON=${ML_GATE_PYTHON} cannot import ${NEEDS_MODULE:-sys}." >&2
    exit 1
  fi
fi

if [ -z "${PYTHON}" ] && [ -n "${VIRTUAL_ENV:-}" ]; then
  for candidate in "${VIRTUAL_ENV}/bin/python" "${VIRTUAL_ENV}/Scripts/python.exe"; do
    if usable "${candidate}"; then
      PYTHON="${candidate}"
      break
    fi
  done
  # An activated virtualenv that lacks the tool is a broken developer environment, not
  # a reason to quietly run some other interpreter: that is how a hook checks a tree
  # the developer never opened. Say so and stop.
  if [ -z "${PYTHON}" ] && [ -n "${NEEDS_MODULE}" ]; then
    echo "FAIL ml ${GATE}: ${VIRTUAL_ENV} is active but cannot import ${NEEDS_MODULE}." >&2
    echo "     Install the dev extras (pip install -e .[dev] in ml/) or deactivate." >&2
    exit 1
  fi
fi

if [ -z "${PYTHON}" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
    if usable "${candidate}"; then
      PYTHON="${candidate}"
      break
    fi
  done
fi

# The Windows launcher is not on PATH inside Git Bash, so this only helps when it
# is; it is the one spelling that resolves a real CPython even when every plain
# name is the Store stub.
if [ -z "${PYTHON}" ] && usable py -3; then
  PYTHON="py -3"
fi

# LAST RESORT: LOOK WHERE THE VERSION MANAGERS INSTALL INTERPRETERS. ml/.python-version
# pins the minor the image installs (3.9), and on a developer machine that has not
# installed that minor every pyenv shim in this directory refuses to start - so every
# name on PATH fails even though a perfectly good CPython is present, just not the pinned
# one. That is a property of the machine, not of the tree, and silently skipping would
# turn the hooks into a rubber stamp on exactly the machine where someone is editing.
# Enumerate the standard install roots; the pinned version is still what CI and the image
# use, this only lets a check happen locally.
if [ -z "${PYTHON}" ]; then
  for candidate in \
    "${HOME}"/.pyenv/pyenv-win/versions/*/python.exe \
    "${HOME}"/.pyenv/versions/*/bin/python3 \
    "${HOME}"/.local/share/uv/python/*/bin/python3; do
    [ -e "${candidate}" ] || continue
    if usable "${candidate}"; then
      PYTHON="${candidate}"
      break
    fi
  done
fi

if [ -z "${PYTHON}" ]; then
  echo "SKIP ml ${GATE}: no python3/python on PATH that can import ${NEEDS_MODULE:-sys}"
  echo "     (the Microsoft Store alias exits without running anything, and uv-managed"
  echo "     interpreters ship no ruff). Activate a venv with the dev extras or set"
  echo "     VIRTUAL_ENV, or set ML_GATE_PYTHON to an interpreter that has the dev"
  echo "     extras. CI runs this gate with setup-python, so it is covered"
  echo "     there. Nothing was checked here."
  exit 0
fi

# RESOLVE THE WINNER TO ITS REAL EXECUTABLE. A pyenv shim is a launcher, not an
# interpreter: it re-reads the working directory on every run, so passing "it started"
# during the probe says nothing about the run that follows. Ask the winner where its
# interpreter actually is and run that absolute path - a path has no version to resolve
# and therefore no second chance to fail.
# shellcheck disable=SC2086
RESOLVED="$(${PYTHON} -c "import sys; print(sys.executable)" 2>/dev/null)"
if [ -n "${RESOLVED}" ]; then
  # Convert BEFORE testing the path: on Windows sys.executable is C:\...\python.exe and
  # bash cannot stat that spelling. cygpath ships with Git for Windows; on Linux and
  # macOS sys.executable is already POSIX, so the conversion is not needed there.
  if command -v cygpath >/dev/null 2>&1; then
    CONVERTED="$(cygpath -u "${RESOLVED}" 2>/dev/null || true)"
    if [ -n "${CONVERTED}" ]; then
      RESOLVED="${CONVERTED}"
    fi
  fi
  if [ -f "${RESOLVED}" ] || [ -x "${RESOLVED}" ]; then
    PYTHON="${RESOLVED}"
  fi
fi

case "${GATE}" in
  ruff)
    # No implicit "check" subcommand here: the hook passes the whole ruff
    # command line ("ruff check ."), and adding one would turn the path argument
    # into a second file called "check".
    # shellcheck disable=SC2086
    ${PYTHON} -m ruff "$@"
    ;;
  pytest)
    # The suite imports the flat modules through pyproject.toml,
    # [tool.pytest.ini_options] pythonpath = ["service"] - no sys.path editing.
    # shellcheck disable=SC2086
    ${PYTHON} -m pytest "$@"
    ;;
  layout)
    # The guard is stdlib-only on purpose, so this needs no virtualenv and no
    # torch: any interpreter that starts can check the tree.
    # shellcheck disable=SC2086
    ${PYTHON} scripts/check_ml_service_layout.py "$@"
    ;;
  *)
    usage
    ;;
esac
