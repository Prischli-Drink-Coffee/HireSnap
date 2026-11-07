#!/usr/bin/env python3
r"""Create or top up docker/.env.<mode> from docker/.env.example, and report drift.

Design rules, all mirrored from R:\GPTHub\docker\sync_env.py (read it before
editing this file):

  * The template is the contract. A key that exists in .env.example and not in
    the deployment file is appended with the template's value; nothing else is
    touched. (GPTHub sync_env.py:116-118 does the same append-only top-up.)
  * An existing value is NEVER rewritten. Rotating a live secret because the
    template changed its placeholder is the worst possible outcome: GPTHub
    sync_env.py:103-110 exists precisely to keep a direct secret stable and to
    refuse when it cannot tell a placeholder from a real value.
  * No value is ever printed, logged or included in an error message. A drift
    report names keys, never secrets (GPTHub compose_preflight.sh:1-3 makes the
    same promise for Compose output).
  * Writes are atomic: temp file in the same directory, fsync, os.replace, and
    a re-read before the swap to detect a concurrent edit
    (GPTHub sync_env.py:192-214). A half-written env file is unrecoverable,
    because the values in it do not exist anywhere else.
  * The file must not be a symlink (GPTHub sync_env.py:194, :209). Following a
    planted symlink would let the tool overwrite an arbitrary file.

Exit codes: 0 success, 1 drift found (--check), 2 usage or I/O failure.

Standard library only, and Python 3.9 compatible (the backend image and the
GPTHub tooling both target 3.9, so no 3.10+ syntax).
"""

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

MODES = ("dev", "prod")
TEMPLATE_NAME = ".env.example"
# A dotenv assignment. Deliberately strict: KEY=VALUE with a shell-identifier
# key. Lines that do not match are comments or blanks and are preserved
# verbatim, which is what keeps the section headers intact on a top-up.
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


class SyncError(Exception):
    """A refusal that must not modify the file."""


def parse_keys(body):
    """Return the ordered list of keys declared in a dotenv body.

    Order matters: Compose expands references in a single left-to-right pass, so
    an appended key that another key references must not land above its source
    (see the header of .env.example and GPTHub sync_env.py:54-67, which reorders
    exactly one such pair for this reason).
    """
    keys = []
    seen = set()
    for line in body.splitlines():
        match = ASSIGNMENT.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            raise SyncError("duplicate key in env body: " + key)
        seen.add(key)
        keys.append(key)
    return keys


def read_text(path):
    """Read a file that must not be a symlink, with an explicit encoding."""
    if path.is_symlink():
        raise SyncError("refusing to read a symlink: " + path.name)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise SyncError("cannot read " + path.name) from None


def append_missing(body, template_body, template_keys, current_keys):
    """Append, in template order, only the keys the file does not declare.

    The value copied is the template's own line, so a required key keeps
    whatever the template documents - never an invented value.
    """
    present = set(current_keys)
    missing = [key for key in template_keys if key not in present]
    if not missing:
        return body, []

    by_key = {}
    for line in template_body.splitlines():
        match = ASSIGNMENT.match(line)
        if match and match.group(1) not in by_key:
            by_key[match.group(1)] = line

    block = ["", "# --- added by sync_env.py from .env.example (values are the template's) ---"]
    for key in missing:
        block.append(by_key[key])
    if not body.endswith("\n"):
        body += "\n"
    return body + "\n".join(block) + "\n", missing


def save(path, original, updated):
    """Atomic replace with a concurrent-modification check.

    mkstemp in the SAME directory so os.replace stays on one filesystem and is
    therefore atomic; 0600 because the file holds credentials.
    """
    if path.is_symlink():
        raise SyncError("refusing to write through a symlink: " + path.name)
    # A first-run CREATE passes original="" against a path that does not exist
    # yet. read_text() refuses a missing file by design (it must never invent an
    # empty env), so the existence check comes first: comparing against a file
    # that is not there is not a concurrent modification, it is the whole point
    # of the create path.
    existed = path.exists()
    if existed and read_text(path) != original:
        raise SyncError("env changed while it was being updated; nothing was written")
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as pending:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            pending.write(updated)
            pending.flush()
            os.fsync(pending.fileno())
        # Re-read AFTER the write and BEFORE the swap: if anything changed the
        # file while the temp copy was being written, the operator's edit wins and
        # this update is abandoned (GPTHub sync_env.py:192-214).
        if existed and read_text(path) != original:
            raise SyncError("env changed while it was being updated; nothing was written")
        if not existed and path.exists():
            raise SyncError("env appeared while it was being created; nothing was written")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def mode_path(folder, mode):
    return folder / (".env." + mode)


def command_sync(folder, mode):
    template_body, template_keys = template_lines(folder / TEMPLATE_NAME)
    path = mode_path(folder, mode)
    if not path.exists():
        # A first run creates the file with the template's full body: the
        # sections and their comments are part of the contract, not decoration,
        # and a bare key list would lose them.
        if not folder.is_dir():
            raise SyncError("docker/ directory not found: " + str(folder))
        save(path, "", template_body)
        print("[OK] created " + path.name + " from " + TEMPLATE_NAME
              + " (" + str(len(template_keys)) + " keys)")
        print("[OK] no value was printed; secrets in the copy are template placeholders")
        return 0
    original = read_text(path)
    current_keys = parse_keys(original)
    updated, missing = append_missing(original, template_body, template_keys, current_keys)
    if updated != original:
        save(path, original, updated)
        print("[OK] " + path.name + ": added " + str(len(missing)) + " missing key(s)")
        for key in missing:
            print("    + " + key)
    else:
        print("[OK] " + path.name + ": already declares every template key")
    extra = [key for key in current_keys if key not in set(template_keys)]
    if extra:
        # Extra keys are reported but kept: they may be deliberate local
        # overrides the template has not caught up with yet.
        print("[WARN] " + path.name + " declares " + str(len(extra))
              + " key(s) absent from the template (kept as-is):")
        for key in extra:
            print("    ! " + key)
    return 0


def template_lines(path):
    body = read_text(path)
    return body, parse_keys(body)


def command_check(folder):
    """Report drift between the template and both deployment files.

    A key present in .env.example but missing from a deployment file is a real
    defect: the service silently falls back to a code default. GPTHub
    run.sh:109-135 makes the same check at start time and explains why it cannot
    live in CI - the deployment files are gitignored, so the only host that has
    them is the deployment host. Here it is a hard failure (non-zero exit) for
    the explicit check command, while run.sh downgrades it to a warning.
    """
    _, template_keys = template_lines(folder / TEMPLATE_NAME)
    template_set = set(template_keys)
    failures = 0
    for mode in MODES:
        path = mode_path(folder, mode)
        if not path.exists():
            print("[MISSING] " + path.name + " does not exist")
            failures += 1
            continue
        current_keys = parse_keys(read_text(path))
        current_set = set(current_keys)
        missing = [key for key in template_keys if key not in current_set]
        extra = [key for key in current_keys if key not in template_set]
        if not missing and not extra:
            print("[OK] " + path.name + ": " + str(len(current_keys)) + " keys, matches the template")
            continue
        failures += 1
        if missing:
            print("[DRIFT] " + path.name + " is missing " + str(len(missing)) + " template key(s):")
            for key in missing:
                print("    - " + key)
        if extra:
            print("[DRIFT] " + path.name + " declares " + str(len(extra))
                  + " key(s) the template does not:")
            for key in extra:
                print("    + " + key)
    if failures:
        print("[ERROR] env drift detected; run: python docker/sync_env.py --mode dev|prod")
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODES,
                        help="deployment file to create or top up")
    parser.add_argument("--check", action="store_true",
                        help="report drift between .env.example, .env.dev and .env.prod")
    parser.add_argument("--dir", default=None,
                        help="directory holding the env files (default: this script's directory)")
    args = parser.parse_args(argv)

    if not args.check and not args.mode:
        parser.error("one of --mode or --check is required")
    if args.check and args.mode:
        parser.error("--check and --mode are mutually exclusive")

    folder = Path(args.dir).resolve() if args.dir else Path(__file__).resolve().parent
    try:
        if args.check:
            return command_check(folder)
        return command_sync(folder, args.mode)
    except SyncError as exc:
        print("[ERROR] " + str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        # Report the operation, never the file's contents.
        print("[ERROR] env file could not be read or written: " + str(exc.strerror), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
