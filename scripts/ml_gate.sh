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
  echo "usage: scripts/ml_gate.sh <ruff|pytest> [extra args...]" >&2
  echo "" >&2
  echo "  ruff     ruff check over this checkout (configuration: pyproject.toml)" >&2
  echo "  pytest   the contract tests; they must pass with no torch and no weights" >&2
  exit 2
}

if [ "$#" -lt 1 ]; then
  usage
fi

GATE="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Candidates, same order and same reasoning as scripts/run_guard.sh in the
# superproject: the versioned spellings are tried first because on pyenv-win the
# plain names are the Store stubs while python3.13/python3.12/... are real
# interpreters. An activated virtualenv wins over everything, because that is the
# environment the developer is actually testing against.
PYTHON=""
if [ -n "${VIRTUAL_ENV:-}" ]; then
  for candidate in "${VIRTUAL_ENV}/bin/python" "${VIRTUAL_ENV}/Scripts/python.exe"; do
    if "${candidate}" -c "import sys" >/dev/null 2>&1; then
      PYTHON="${candidate}"
      break
    fi
  done
fi

if [ -z "${PYTHON}" ]; then
  for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    if "${candidate}" -c "import sys" >/dev/null 2>&1; then
      PYTHON="${candidate}"
      break
    fi
  done
fi

# The Windows launcher is not on PATH inside Git Bash, so this only helps when it
# is; it is the one spelling that resolves a real CPython even when every plain
# name is the Store stub.
if [ -z "${PYTHON}" ] && py -3 -c "import sys" >/dev/null 2>&1; then
  PYTHON="py -3"
fi

if [ -z "${PYTHON}" ]; then
  echo "SKIP ml ${GATE}: no working python3/python on PATH (the Microsoft Store"
  echo "     alias exits without running anything). Activate a venv or set"
  echo "     VIRTUAL_ENV; CI runs this gate with setup-python, so it is covered"
  echo "     there. Nothing was checked here."
  exit 0
fi

cd "${REPO_ROOT}" || exit 1

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
  *)
    usage
    ;;
esac
