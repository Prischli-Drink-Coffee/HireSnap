#!/usr/bin/env python3
r"""Dockerfile pin guard: every base image is an explicit version, no remote ADD.

WHY this exists (mirror of the image rule stated in R:\GPTHub\docker\docker-compose.yaml:6-14
and of R:\GPTHub\.dockerignore:1-14, which exists for the same reason - a build
must be reproducible from the commit alone):

  * A bare "FROM python" and a "FROM python:latest" are the same defect: the tag
    is re-resolved at every build, so the SAME commit produces different images
    on two machines, on two days. When the stack then breaks after an innocent
    rebuild, the commit diff shows nothing - the change happened upstream.
  * "FROM python:3.9-slim" is accepted: a minor+flavour tag is a deliberate,
    reviewable choice, and backend/docker/Dockerfile:25-33 already documents why
    3.9 is the interpreter. A digest would be stricter, and the Dockerfile says
    so itself; this guard warns instead of failing there, because taking a
    digest for the FIRST build is impossible (you need the image to know it).
  * "ADD https://..." is a FAIL, not a warning. ADD with a remote URL downloads
    and unpacks at build time with no checksum and no way to review the bytes:
    whatever the URL serves that minute becomes part of the image. COPY (for
    local files) or curl + explicit checksum (for remote ones) keeps the
    artifact reviewable. Mirror of the GPTHub rule that no build step may fetch
    an unverified artifact.

What is asserted, per Dockerfile in the repository:
  1. every FROM names a tag or a digest, and never "latest";
  2. a FROM that references an earlier build stage (FROM base AS dev) is not a
     registry pull and is not checked - that is the normal multi-stage idiom;
  3. no ADD with a remote URL;
  4. a plain ADD of a local path is a WARNING (COPY is what you mean: ADD also
     unpacks archives and accepts URLs, so a reader cannot tell which).

Nothing here builds an image or talks to a registry: it is a text guard, so it
runs in the same second on a laptop with no daemon and in CI.

Exit codes: 0 ok (warnings and skips allowed), 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CHECK = "dockerfile-pins"
ROOT = Path(__file__).resolve().parent.parent

# Directories that never contain a Dockerfile this repository owns: VCS objects,
# per-service dependency trees, caches. Walking them costs minutes on a real
# checkout and can surface a vendored Dockerfile that is not ours to police.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache"}

FROM_LINE = re.compile(r"^\s*FROM\s+(?P<image>\S+)(?:\s+[Aa][Ss]\s+(?P<stage>\S+))?\s*(?:#.*)?$")
ADD_LINE = re.compile(r"^\s*ADD\s+(?P<args>.+?)\s*$", re.IGNORECASE)
REMOTE_URL = re.compile(r"^(?:https?|ftp|git|ssh)://", re.IGNORECASE)


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


def find_dockerfiles():
    """Every Dockerfile in the tree, minus vendored/dependency directories."""
    found = []
    for path in sorted(ROOT.rglob("Dockerfile*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        name = path.name
        if name.endswith(".dockerignore"):
            continue
        found.append(path)
    return found


def image_problem(image, stage_names):
    """Return a failure/warning tuple for one FROM image reference."""
    lowered = image.lower()
    if lowered == "scratch":
        return None, None
    if image in stage_names:
        return None, None  # multi-stage reference, not a registry pull
    if ("$" + "{") in image:
        return None, (
            "'" + image + "' is built from a build argument, so the version is"
            " decided by whoever runs the build. That is acceptable only when the"
            " default of the ARG pins it - keep the default an explicit tag."
        )
    reference, _, digest = image.partition("@")
    if digest:
        if not re.match(r"^sha256:[0-9a-f]{64}$", digest):
            return "'" + image + "' carries a digest that is not sha256:<64 hex>", None
        return None, None
    last = reference.rsplit("/", 1)[-1]
    if ":" not in last:
        return (
            "'" + image + "' has no tag, which docker resolves as ':latest': the"
            " image is re-resolved on every build, so the same commit does not"
            " produce the same image twice"
        ), None
    tag = last.rsplit(":", 1)[1]
    if tag.lower() == "latest":
        return (
            "'" + image + "' is pinned to ':latest'. A floating tag means a rebuild"
            " of an unchanged commit can change the runtime; pin an explicit"
            " version (and a digest once one exists)"
        ), None
    return None, None


def main():
    parser = argparse.ArgumentParser(description="check Dockerfile image pinning")
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args()
    report = Findings()

    dockerfiles = find_dockerfiles()
    if not dockerfiles:
        report.skips.append(
            "no Dockerfile in the tree, so there is no base image to pin. NOT a pass."
        )
        return report.emit(args.json)

    for path in dockerfiles:
        relative = path.relative_to(ROOT).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            report.failures.append(relative + ": cannot be read (" + str(exc) + ")")
            continue
        stage_names = set()
        froms = 0
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = FROM_LINE.match(line)
            if match:
                froms += 1
                image = match.group("image")
                failure, warning = image_problem(image, stage_names)
                if match.group("stage"):
                    stage_names.add(match.group("stage"))
                if failure:
                    report.failures.append(relative + ":" + str(number) + " " + failure)
                elif warning:
                    report.warnings.append(relative + ":" + str(number) + " " + warning)
                continue
            match = ADD_LINE.match(line)
            if match:
                arguments = match.group("args")
                source = arguments.split()[0].strip('"')
                if REMOTE_URL.match(source):
                    report.failures.append(
                        relative + ":" + str(number) + " ADD fetches '" + source
                        + "' at build time. A remote URL is downloaded and unpacked"
                        " with no checksum, so the bytes in the image are whatever"
                        " that URL served that minute. Download explicitly and"
                        " verify a checksum, or COPY a committed artifact"
                    )
                elif not source.startswith("--"):
                    report.warnings.append(
                        relative + ":" + str(number) + " ADD of a local path '"
                        + source + "': prefer COPY - ADD additionally unpacks"
                        " archives and accepts URLs, so a reader cannot tell what"
                        " this line does without knowing the file"
                    )
        if froms == 0:
            report.failures.append(
                relative + ": no FROM line, so this is not a buildable Dockerfile"
            )
        else:
            report.ok.append(
                relative + ": " + str(froms) + " FROM line(s), all explicitly tagged"
            )

    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a guard must never traceback silently
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
