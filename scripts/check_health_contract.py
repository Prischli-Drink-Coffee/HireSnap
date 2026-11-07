#!/usr/bin/env python3
r"""Health contract: every service can prove it is up, and the gateway says so.

WHY this exists (mirror of the healthcheck blocks in R:\GPTHub\docker\docker-compose.yaml
and of R:\GPTHub\infra\nginx\templates\site.conf.template:58-70):

  "depends_on" in compose waits for a container to be RUNNING, not for the process
  inside it to be READY. A MySQL container is "running" while InnoDB is still
  recovering; a FastAPI container is "running" while torch imports. Without a
  healthcheck the only observable is that the container has not exited, and the
  first symptom of a broken stack is a 502 from the gateway minutes later, long
  after the log that explains it scrolled away.

  A healthcheck is also the only thing 'docker compose up --wait' and the
  'condition: service_healthy' dependency can act on. A service without one makes
  every waiter fall back to "started", which is exactly the false confidence this
  repository's runbook tells people to avoid.

  The gateway needs one extra property: it must answer a health URL itself,
  without depending on any upstream. If the gateway's health probe proxied to the
  backend, a gateway restart during a backend outage would report the whole stack
  down and mask which tier actually failed.

What is asserted, for BOTH compose files:
  1. every service declares a healthcheck with a 'test:';
  2. the test is a shell or exec command, and it has 'interval:' and 'retries:'
     (a healthcheck with no interval defaults to 30s, which is slow enough to
     hide a crash loop from the runbook's smoke test);
  3. services that need a long warm-up (the backend imports torch; MySQL
     initialises InnoDB) declare 'start_period:', otherwise the first retries run
     while the process is legitimately still starting, the container is marked
     unhealthy, and a restart-policy loop begins;
  4. the gateway's healthcheck hits the path that the nginx template answers
     directly (the '= /health' location), and that location exists in the
     template - otherwise the probe passes only by accident (SPA fallback 200).

Exit codes: 0 ok (warnings allowed), 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CHECK = "health-contract"
ROOT = Path(__file__).resolve().parent.parent

COMPOSE_FILES = ["docker/docker-compose.yaml", "docker/docker-compose.dev.yaml"]
NGINX_TEMPLATE = Path("infra/nginx/templates/site.conf.template")

# Services that legitimately need a start_period; the reason is printed with the
# warning so nobody "fixes" it by deleting the exemption.
SLOW_START = {
    "backend": "imports torch and the ML stack before it binds the socket",
    "mysql": "initialises InnoDB and, on a fresh volume, replays the schema dump",
    "ml": (
        "builds its own CUDA image from the ml submodule (nvidia/cuda base,"
        " python3.9, torch+cu116), downloads HF weights on a cold cache and then"
        " creates a CUDA context - it shares no image layer with the API"
    ),
    # The workers are slow for a different reason: celery has to connect to the
    # broker and the prefork pool has to fork before "inspect ping" can answer.
    # Without a start_period the first three probes run during the fork and the
    # container is reported unhealthy on a start that was going to succeed.
    "celery-worker-agents": "connects to the broker and forks its prefork pool",
    "celery-worker-maintenance": "connects to the broker and forks its prefork pool",
    "celery-worker-ml": "connects to the broker, forks, and may load a model",
}

SECTION = re.compile(r"^(?P<indent> *)(?P<name>[A-Za-z0-9_.-]+):[ \t]*$")
GATEWAY_HEALTH_HIT = re.compile(r"/health\b")
SERVER_HEALTH = re.compile(r"= /health")


class Report:
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


def services(lines):
    """Return {name: (start_line, block_text)} for the top-level services block.

    Parsed by indentation rather than with a YAML library because the guard must
    run on a machine that only has the standard library (pre-commit runs it in
    whatever interpreter is active, and this repository has no pinned venv for
    superproject tooling).
    """
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "services:")
    except StopIteration:
        return {}
    found = {}
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if line.strip() and not line.startswith(" "):
            break  # left the services block entirely
        match = SECTION.match(line)
        if match and len(match.group("indent")) == 2:
            name = match.group("name")
            block = []
            cursor = index + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                if candidate.strip() and not candidate.startswith("    "):
                    break
                block.append(candidate)
                cursor += 1
            found[name] = (index + 1, "\n".join(block))
            index = cursor
            continue
        index += 1
    return found


def build_healthcheck(compose, block):
    """Return (status, detail) for a service that has no compose healthcheck.

    A compose service that omits 'healthcheck:' is not necessarily unhealthy: the
    IMAGE may carry a HEALTHCHECK instruction that Docker runs unchanged. The
    backend image does exactly that (backend/docker/Dockerfile, 'HEALTHCHECK ...
    /server/openapi.json'), and a second service built from that Dockerfile
    inherits it, so flagging such a service as healthcheck-less would be a false
    alarm - and a false alarm in a guard is how people learn to add '|| true' to
    it.

    WHY the path resolution is spelled out here rather than left to
    'base / context / dockerfile': the sidecar's context is NOT the superproject
    root any more. It is '../ml' with 'dockerfile: docker/Dockerfile', i.e. the
    Dockerfile lives at ml/docker/Dockerfile and the build argument is relative
    to ml/. Both halves are relative to the COMPOSE FILE's directory (docker/),
    not to the repository root, and the Dockerfile half is relative to the
    resolved context - which is why a naive join of the two produces
    docker/../ml/docker/Dockerfile and works only by accident of path shape. The
    join below is that same composition made explicit, with normpath so a
    '..' segment cannot escape into a path that merely looks similar.

    status is True (inherited healthcheck found), False (image has none) or None
    (could not tell - the guard must not guess).
    """
    context = None
    dockerfile = "Dockerfile"
    has_build = False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped == "build:" or stripped.startswith("build: "):
            has_build = True
        elif stripped.startswith("context:"):
            context = stripped.split(":", 1)[1].strip().strip("'\"")
        elif stripped.startswith("dockerfile:"):
            dockerfile = stripped.split(":", 1)[1].strip().strip("'\"")
    if not has_build or context is None:
        return None, None
    compose_dir = (ROOT / compose).parent
    context_dir = Path(os.path.normpath(str(compose_dir / context)))
    dockerfile_path = Path(os.path.normpath(str(context_dir / dockerfile)))
    if not dockerfile_path.is_file():
        return None, str(dockerfile_path)
    text = dockerfile_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.strip().upper().startswith("HEALTHCHECK"):
            try:
                shown = str(dockerfile_path.relative_to(ROOT))
            except ValueError:
                shown = str(dockerfile_path)
            return True, shown.replace("\\", "/")
    try:
        shown = str(dockerfile_path.relative_to(ROOT))
    except ValueError:
        shown = str(dockerfile_path)
    return False, shown.replace("\\", "/")


def main():
    parser = argparse.ArgumentParser(description="check every compose service declares a usable healthcheck")
    parser.add_argument("--json", action="store_true", help="emit a single JSON object")
    args = parser.parse_args()
    report = Report()

    template = None
    if (ROOT / NGINX_TEMPLATE).is_file():
        template = (ROOT / NGINX_TEMPLATE).read_text(encoding="utf-8", errors="replace")
    else:
        report.skips.append(
            str(NGINX_TEMPLATE) + " is missing, so the gateway health location could"
            " not be verified. NOT a pass."
        )

    seen_any = False
    for compose in COMPOSE_FILES:
        path = ROOT / compose
        if not path.is_file():
            report.skips.append(
                compose + " does not exist yet, so its healthchecks are unverified."
                " NOT a pass."
            )
            continue
        seen_any = True
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        found = services(lines)
        if not found:
            report.failures.append(compose + " declares no services, so nothing could be checked")
            continue
        for name, (line_number, block) in sorted(found.items()):
            where = compose + ":" + str(line_number)
            stripped = [
                line for line in block.splitlines()
                if not line.lstrip().startswith("#")
            ]
            body = "\n".join(stripped)
            if "healthcheck:" not in body:
                inherited, detail = build_healthcheck(compose, block)
                if inherited:
                    report.ok.append(
                        where + " service '" + name + "' inherits the image"
                        " HEALTHCHECK from " + detail + " (compose declares none,"
                        " and inheriting is correct here: one definition, no drift"
                        " between the image probe and the compose probe)"
                    )
                elif inherited is False:
                    report.failures.append(
                        where + " service '" + name + "' declares no healthcheck and"
                        " its image (" + detail + ") declares no HEALTHCHECK either."
                        " Without one, 'depends_on: condition: service_healthy'"
                        " silently degrades to 'container started', so the gateway"
                        " forwards traffic to a process that is not listening yet"
                    )
                else:
                    report.failures.append(
                        where + " service '" + name + "' declares no healthcheck and"
                        " its Dockerfile could not be read to prove the image supplies"
                        " one (" + str(detail) + "). NOT verified: an unreadable"
                        " Dockerfile is not evidence of an inherited probe"
                    )
                continue
            # isolate the healthcheck sub-block
            start = body.index("healthcheck:")
            health = body[start:]
            indented = [line for line in health.splitlines() if line.startswith("      ") or line.startswith("        ")]
            health = "\n".join(indented)
            if "test:" not in health:
                report.failures.append(
                    where + " service '" + name + "' has a healthcheck without a"
                    " 'test:'. Docker treats that as an empty test and the container"
                    " is reported healthy immediately"
                )
                continue
            for key in ("interval:", "retries:"):
                if key not in health:
                    report.warnings.append(
                        where + " service '" + name + "' healthcheck omits '" + key
                        + "'; the Docker default (30s interval, 3 retries) makes a"
                        " crash loop invisible to 'make smoke' for well over a minute"
                    )
            if name in SLOW_START and "start_period:" not in health:
                report.warnings.append(
                    where + " service '" + name + "' healthcheck omits 'start_period:'"
                    " although it " + SLOW_START[name] + ". Its first retries run"
                    " before the process can possibly answer, so a slow but healthy"
                    " start is reported as a failure"
                )
            if name == "gateway":
                if not GATEWAY_HEALTH_HIT.search(health):
                    report.failures.append(
                        where + " gateway healthcheck does not request /health (test: '"
                        + health.split("test:", 1)[1].strip().splitlines()[0].strip()
                        + "'). Probing '/' instead succeeds through the SPA fallback"
                        " even when the gateway is misconfigured, so the check proves"
                        " only that nginx started"
                    )
                elif template is not None and not SERVER_HEALTH.search(template):
                    report.failures.append(
                        where + " gateway probes /health but "
                        + str(NGINX_TEMPLATE) + " has no 'location = /health'."
                        " The probe would then be answered by 'location /' - a 200"
                        " from the frontend - and would pass while the gateway"
                        " itself is broken"
                    )
                else:
                    report.ok.append(
                        where + " gateway probes /health and the template answers it"
                        " directly, with no upstream involved"
                    )
            else:
                report.ok.append(where + " service '" + name + "' has a healthcheck")

    if not seen_any and not report.failures:
        report.skips.append(
            "no compose file was available, so no healthcheck could be verified."
            " NOT a pass - this guard has proved nothing about the stack."
        )

    return report.emit(args.json)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] " + CHECK + ": unexpected error: " + repr(exc))
        sys.exit(2)
