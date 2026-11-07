#!/usr/bin/env bash
# ==============================================================================
# run_guard.sh - resolve a usable Python and run one stdlib guard script
# ==============================================================================
# WHY THIS FILE EXISTS
#
# Every hook in .pre-commit-config.yaml used to spell out
#
#     if command -v python3; then python3 <script>; elif command -v python; then ...
#
# and that probe is WRONG on Windows. PATH normally contains
# C:\Users\<user>\AppData\Local\Microsoft\WindowsApps, whose python3.exe and
# python.exe are the Microsoft Store *stubs*: "command -v python3" SUCCEEDS
# (the name resolves to a real file), the stub is then executed, and it exits 49
# without running anything. All seven guards reported "Failed" on a host where
# the same scripts pass when invoked directly, which is worse than no gate: it
# trains the reader to skip the output.
#
# The fix is the same one the Makefile already applies (Makefile:66-78): run the
# candidate instead of trusting its name. A candidate counts as usable only if
# "import sys" actually succeeds in it. The versioned spellings are tried too,
# because on pyenv-win and python.org installs the plain names are the stubs
# while python3.13/python3.12/... are the real interpreters.
#
# WHY A SHARED SCRIPT and not seven copies of the probe: a probe that exists in
# seven places drifts in seven ways, and the failure mode above was found once
# and would have to be fixed seven times. The guards themselves stay standalone
# (make guards calls them directly; CI calls them directly) - this file only
# decides WHICH interpreter runs them.
#
# USAGE
#     bash scripts/run_guard.sh scripts/check_env_parity.py [args...]
#     bash scripts/run_guard.sh docker/sync_env.py --check
#
# EXIT CODES
#     The guard's own exit code when an interpreter was found and run.
#     0 with a loud SKIP line when no usable interpreter exists - a missing
#     local prerequisite is not a broken commit (.pre-commit-config.yaml:161-164).
# ==============================================================================
set -u

if [ "$#" -lt 1 ]; then
  printf '%s\n' "run_guard.sh: ERROR no script argument" >&2
  exit 2
fi

script=$1
shift

if [ ! -f "$script" ]; then
  printf '%s\n' "run_guard.sh: ERROR $script does not exist" >&2
  exit 2
fi

# "import sys" and nothing else: the guards use the standard library only, so a
# version floor is not needed, but the interpreter must actually start.
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  if "$candidate" -c 'import sys' >/dev/null 2>&1; then
    "$candidate" "$script" "$@"
    exit $?
  fi
done

# The Windows launcher is not on PATH inside Git Bash, so this only helps when
# it is; same order as Makefile:78.
if py -3 -c 'import sys' >/dev/null 2>&1; then
  py -3 "$script" "$@"
  exit $?
fi

printf '%s\n' \
  "SKIP $script: no usable python3/python on PATH (tried python3, python3.13..3.9, python, py -3)." \
  "     The guards are stdlib-only; install Python >= 3.8 or pass an interpreter explicitly."
exit 0
