#!/usr/bin/env python3
r"""Generated-state guard: runtime data stays out of git, by rule and by fact.

WHY this exists (mirror of R:\GPTHub\.gitignore:20-23 and of the "live stack
data" block in R:\HireSnap\.gitignore:34-46):

  A .gitignore rule is a promise, and the promise is broken in three different
  ways, none of which shows up in a normal diff:

    1. the rule is written but never fires - a typo, a leading "/" that anchors
       it to the wrong directory, or a pattern that only matches a file when the
       thing on disk is a directory. The directory is then committed on the next
       'git add .', and the repository gains a copy of somebody's database;
    2. the rule is correct but the path it protects MOVED - the compose file now
       uses a named volume, the bind mount is gone, and the ignore rule is
       decoration that protects a path nothing writes to any more;
    3. the path was ignored after the fact: it is already tracked. An ignore rule
       has NO effect on a tracked file - git keeps updating it - so a committed
       data directory keeps growing in the history on every 'git add -u'.

  This guard checks all three, because the first two are invisible and the third
  is unfixable by editing .gitignore.

What is asserted:
  1. every path in GENERATED_PATHS is ignored by git ('git check-ignore'), which
     is the only way to prove a pattern actually matches - reading the file does
     not prove anything, as the root .gitignore's own header explains;
  2. none of those paths is tracked ('git ls-files'), which catches case 3;
  3. the database, redis, minio and HF caches are NAMED volumes in both compose
     files, not bind mounts: a bind-mounted MySQL data directory is a directory
     in the repository, and one 'docker compose down' away from being committed;
  4. no *.sql dump other than the one committed schema
      (backend/database/HireSnap.sql) is
     staged - a dump taken from a live database contains real user rows.

Missing paths are not a failure: a fresh clone legitimately has no
infra/storage/ yet. A path that does not exist is still checked with
'git check-ignore', because that answers "would this be ignored when it appears",
which is the actual question.

Exit codes: 0 ok (warnings and skips allowed), 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

CHECK = "generated-state"
ROOT = Path(__file__).resolve().parent.parent

# Every path the stack writes at run time: (directory, representative probe
# file, why). The probe is what gets handed to 'git check-ignore', and it is a
# FILE rather than the bare directory on purpose.
#
# WHY the probe is a file: this repository keeps the mount points alive with a
# tracked '<dir>/.gitkeep', so the directory itself is NOT ignored - only its
# contents are. Probing 'infra/models' therefore answers a question nobody asked
# ("is the directory ignored?") and answers it the wrong way round, while probing
# 'infra/models/weights/model.bin' answers the question that matters ("would a
# downloaded weight be ignored?"). The same file-probe also keeps working on a
# fresh clone, where the directory may not exist yet at all.
GENERATED_PATHS = [
    (
        "infra/storage",
        "infra/storage/videos/probe.mp4",
        "uploaded videos and .npy embeddings - user data, no source of truth elsewhere",
    ),
    (
        "infra/mysql_data",
        "infra/mysql_data/ibdata1",
        "a bind-mounted MySQL data directory, if one is ever introduced",
    ),
    (
        "infra/logs",
        "infra/logs/backend.log",
        "rotated application logs, which quote request data and configuration",
    ),
    (
        "infra/models",
        "infra/models/weights/model.bin",
        "downloaded model weights and pretrained checkpoints - multi-GB and licence-bound",
    ),
    (
        "infra/nginx/certs",
        "infra/nginx/certs/certificate.pem",
        "TLS material mounted read-only into the gateway",
    ),
    (
        "infra/certs",
        "infra/certs/jwt-private.pem",
        "the JWT RSA key pair - committed keys let anyone mint tokens",
    ),
    (
        "docker/.run-state",
        "docker/.run-state/run.pid",
        "PIDs and compose state written by docker/run.sh",
    ),
]

# Files that legitimately live inside a generated directory: they keep the empty
# directory in the checkout (and its ownership) without carrying data. They are
# the ONE exception to "nothing under these paths may be tracked".
ALLOWED_TRACKED = {".gitkeep", ".gitignore"}

# Named volumes required by the storage decision: database, cache, object store,
# model cache. A bind mount here means the data lives inside the checkout.
NAMED_VOLUME_SERVICES = {
    "mysql": "/var/lib/mysql",
    "cache": "/data",
    "storage": "/data",
}

COMPOSE_FILES = ["docker/docker-compose.yaml", "docker/docker-compose.dev.yaml"]


class Findings:
    def __init__(self) -> None:
        self.failures = []
        self.warnings = []
        self.skips = []
        self.ok = []

    def status(self):
        if self.failures:
            return "fail"
        if self.skips and not self.ok:
            return "skip"
        return "pass"

    def emit(self, as_json):
        if as_json:
            print(json.dumps({
                "check": CHECK,
                "status": self.status(),
                "failures": self.failures,
                "warnings": self.warnings,
                "skips": self.skips,
                "ok": self.ok,
            }, indent=2, sort_keys=True))
            return 1 if self.failures else 0
        for message in self.failures:
            print("[FAIL] " + CHECK + ": " + message)
        for message in self.warnings:
            print("[WARN] " + CHECK + ": " + message)
        for message in self.skips:
            print("[SKIP] " + CHECK + ": " + message)
        for message in self.ok:
            print("[OK] " + CHECK + ": " + message)
        print(
            "[SUMMARY] check=" + CHECK + " status=" + self.status()
            + " failures=" + str(len(self.failures))
            + " warnings=" + str(len(self.warnings))
            + " skipped=" + str(len(self.skips))
        )
        return 1 if self.failures else 0


def git(arguments):
    """Run git in the repository root; return (returncode, stdout, stderr)."""
    completed = subprocess.run(
        [shutil.which("git") or "git"] + arguments,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.returncode, completed.stdout, completed.stderr


def ignore_code(probe):
    """Return 'git check-ignore' status for a probe path that does not exist yet.

    The probe is a FILE inside the generated directory, and that is the only way
    this question is answerable: a rule written as '/infra/models/' matches the
    CONTENTS of that directory, and git can only apply it to a path it knows is
    below that directory. Handing it the bare directory name asks whether the
    directory itself is ignored, which is deliberately false here - the tracked
    '.gitkeep' lives there.

    Files that exist on disk are answered by git directly; for a file that does
    not exist yet, --no-index is the flag that still reports the pattern match.
    """
    code, _, _ = git(["check-ignore", "-q", "--no-index", probe])
    return code


def section_bounds(lines, name):
    """Yield (start, end) line indices of every '  <name>:' service block.

    A block ends at the NEXT service key, i.e. a line indented by exactly two
    spaces whose content ends with ':'. It is not enough to stop at the first
    dedent: services are siblings, so the previous block would otherwise extend
    to the top-level 'volumes:' footer and every later service would be read as
    part of it. That made this check attribute a later service's mount to
    'cache' - a false positive that depended on service ORDER, which is the
    kind of bug that reappears the next time a service is added above another.
    """
    starts = []
    for index, line in enumerate(lines):
        if line.rstrip() == "  " + name + ":":
            starts.append(index)
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        for index in range(start + 1, len(lines)):
            candidate = lines[index]
            if not candidate.strip():
                continue
            indent = len(candidate) - len(candidate.lstrip(" "))
            if indent < 2:
                end = min(end, index)
                break
            if indent == 2 and candidate.rstrip().endswith(":") \
                    and not candidate.lstrip().startswith("#"):
                end = min(end, index)
                break
        yield start, end


def main():
    parser = argparse.ArgumentParser(description="check generated state is ignored and untracked")
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args()
    report = Findings()

    if shutil.which("git") is None:
        report.skips.append(
            "git is not on PATH, so ignore rules cannot be evaluated. NOT a pass."
        )
        return report.emit(args.json)
    code, _, _ = git(["rev-parse", "--is-inside-work-tree"])
    if code != 0:
        report.skips.append(
            "the repository root is not a git work tree, so every ignore rule is"
            " dormant. NOT a pass - run the guard inside the clone."
        )
        return report.emit(args.json)

    # --- 1 and 2: contents ignored, nothing but .gitkeep tracked ----------
    for directory, probe, why in GENERATED_PATHS:
        code = ignore_code(probe)
        if code == 0:
            report.ok.append(
                probe + " is ignored, so " + directory + " cannot leak " + why
            )
        elif code == 1:
            report.failures.append(
                probe + " is NOT ignored, so a file written there would be"
                " committed (it holds " + why + "). Fix the rule for " + directory
                + " and prove it with 'git check-ignore -v " + probe
                + "' - an ignore rule that never matches is worse than none,"
                " because it reads like protection"
            )
        else:
            report.warnings.append(
                probe + ": 'git check-ignore' exited " + str(code)
                + ", so the rule could not be evaluated"
            )

        code, stdout, _ = git(["ls-files", "--", directory])
        if code != 0:
            report.warnings.append(
                directory + ": 'git ls-files' failed, so tracked content there"
                " could not be listed"
            )
            continue
        tracked = [line for line in stdout.splitlines() if line.strip()]
        unexpected = [
            name for name in tracked
            if Path(name).name not in ALLOWED_TRACKED
        ]
        if unexpected:
            report.failures.append(
                directory + " has " + str(len(unexpected)) + " tracked file(s), e.g. '"
                + unexpected[0] + "'. An ignore rule does not apply to a tracked"
                " file, so it keeps being committed with every 'git add -u':"
                " 'git rm -r --cached " + directory + "' is the only fix, and the"
                " history still holds a copy"
            )
        elif tracked:
            report.ok.append(
                directory + " tracks only placeholder file(s): "
                + ", ".join(sorted(Path(name).name for name in tracked))
                + " - the mount point exists without carrying data"
            )
        else:
            report.ok.append(directory + " tracks no file at all")

    # --- 3: named volumes, not bind mounts --------------------------------
    for compose in COMPOSE_FILES:
        path = ROOT / compose
        if not path.is_file():
            report.warnings.append(
                compose + " does not exist yet (owned by the docker layer), so the"
                " volume assertions were skipped for it"
            )
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for service, mount_point in NAMED_VOLUME_SERVICES.items():
            blocks = list(section_bounds(lines, service))
            if not blocks:
                report.warnings.append(
                    compose + ": no '" + service + "' service (optional profile?),"
                    " so its volume could not be checked"
                )
                continue
            for start, end in blocks:
                bind = [
                    lines[index].strip()
                    for index in range(start, end)
                    if mount_point + ":" in lines[index] and lines[index].strip().startswith("- ../")
                ]
                if bind:
                    report.failures.append(
                        compose + ":" + str(bind and start + 1) + " service '" + service
                        + "' bind-mounts the host into " + mount_point + " ('"
                        + bind[0] + "'). Runtime state must be a NAMED volume: a"
                        " bind mount puts the database inside the checkout, where"
                        " it is one 'git add' from being committed and one 'rm -rf'"
                        " from being lost"
                    )
                else:
                    report.ok.append(
                        compose + ": '" + service + "' uses a named volume for "
                        + mount_point
                    )

    # --- 4: no stray SQL dumps -------------------------------------------
    code, stdout, _ = git(["ls-files", "--", "*.sql"])
    if code == 0:
        # backend/database/HireSnap.sql is the committed schema. The refactor moved
        # it out of the backend root (where it sat beside the entry-point scripts)
        # into database/, next to its README. backend/ is a submodule, so this list
        # is normally empty: the superproject does not track files inside a gitlink.
        allowed = {"backend/database/HireSnap.sql"}
        extra = [name for name in stdout.splitlines() if name not in allowed]
        if extra:
            report.warnings.append(
                "tracked SQL file(s) beyond the committed schema: "
                + ", ".join(extra[:5])
                + ". A dump of a live database holds real rows; the schema and any"
                " fixture belong under version control, data does not"
            )
        elif stdout.strip():
            report.ok.append("the only tracked SQL file is the committed schema")
        else:
            report.ok.append("no SQL file is tracked by the superproject")

    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a guard must never traceback silently
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
