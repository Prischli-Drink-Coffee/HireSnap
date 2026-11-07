#!/usr/bin/env python3
r"""Compose parity guard: the dev and prod stacks must describe the same stack.

WHY this exists (mirror of the reasoning behind R:\GPTHub\docker\run.sh:109-135,
which refuses to start when the env files disagree): the dev compose file is the
only place a developer can reproduce a production problem. The moment the two
files drift - a service that exists in prod but not in dev, an image re-tagged
"latest" on one side, a database port quietly published to the host - "works on
my machine" becomes unfalsifiable, and the drift is invisible in review because
the files are 200 lines apart.

What is asserted:

  1. Both files parse, with all profiles enabled (docker compose config).
  2. Every service image is pinned to an explicit tag or digest; "latest" is a
     defect, not a style choice, because an unpinned tag makes a rebuild
     non-reproducible.
  3. In prod only the gateway publishes host ports. A published mysql:3306 puts
     the database on the host network; a published backend:8080 bypasses the
     gateway's rate limit and security headers.
  4. The prod stack declares an internal (no-egress) network, so a compromised
     service cannot reach the internet and the database is not reachable from a
     service that does not need it.
  5. The two stacks use different compose project names, so dev and prod
     containers, networks and volumes cannot collide.
  6. Every service enabled for a profile in prod is also present for that
     profile in dev (the reverse is only a warning: dev may add a mock).
  7. The sidecar is one service, named ml, in the CORE profile, built from the
     ml submodule's own context - and nothing else builds from ml/.

Exit codes: 0 ok (skips and warnings allowed), 1 violations, 2 usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CHECK = "compose-parity"
ROOT = Path(__file__).resolve().parent.parent
PROD_FILE = ROOT / "docker" / "docker-compose.yaml"
DEV_FILE = ROOT / "docker" / "docker-compose.dev.yaml"
ENV_PROD = ROOT / "docker" / ".env.prod"
ENV_DEV = ROOT / "docker" / ".env.dev"
ENV_TEMPLATE = ROOT / "docker" / ".env.example"

# Service names allowed to publish host ports in the prod stack. The gateway is
# the single ingress; everything else is reachable only on the compose network.
GATEWAY_SERVICES = {"nginx", "gateway", "proxy"}

# "profiles: [\"prod\"]" (inline list) and the block form
# "profiles:\n  - prod". Both are read from the RAW text, because
# `docker compose config` only renders the services of profiles it was asked
# for - asking it to enumerate profiles would be circular.
PROFILE_INLINE = re.compile(r"^[ \t]+profiles:[ \t]*\[(?P<body>[^\]]*)\]", re.M)
PROFILE_BLOCK = re.compile(
    r"^[ \t]+profiles:[ \t]*\n(?P<body>(?:^[ \t]+-[ \t]*.*\n?)+)", re.M
)
PROFILE_ITEM = re.compile(r"['\"]?(?P<name>[A-Za-z0-9_.-]+)['\"]?")


class Findings:
    def __init__(self) -> None:
        self.failures = []
        self.warnings = []
        self.skips = []
        self.ok = []

    def fail(self, message):
        self.failures.append(message)

    def warn(self, message):
        self.warnings.append(message)

    def skip(self, message):
        self.skips.append(message)

    def good(self, message):
        self.ok.append(message)

    @property
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
                "status": self.status,
                "failures": self.failures,
                "warnings": self.warnings,
                "skips": self.skips,
                "ok": self.ok,
            }, indent=2, sort_keys=True))
        else:
            for message in self.failures:
                print("[FAIL] " + CHECK + ": " + message)
            for message in self.warnings:
                print("[WARN] " + CHECK + ": " + message)
            for message in self.skips:
                print("[SKIP] " + CHECK + ": " + message)
            for message in self.ok:
                print("[OK] " + CHECK + ": " + message)
            print("[SUMMARY] check=" + CHECK + " status=" + self.status
                  + " failures=" + str(len(self.failures))
                  + " warnings=" + str(len(self.warnings))
                  + " skipped=" + str(len(self.skips)))
        return 1 if self.failures else 0


def profiles_in(text):
    """Profile names declared anywhere in a compose file, in file order."""
    names = []
    for match in PROFILE_INLINE.finditer(text):
        for item in PROFILE_ITEM.finditer(match.group("body")):
            names.append(item.group("name"))
    for match in PROFILE_BLOCK.finditer(text):
        for item in PROFILE_ITEM.finditer(match.group("body")):
            names.append(item.group("name"))
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def render(path, env_file, profiles):
    """Return the fully resolved compose document, or raise RuntimeError."""
    command = [shutil.which("docker") or "docker", "compose", "-f", str(path)]
    if env_file is not None:
        command += ["--env-file", str(env_file)]
    for profile in profiles:
        command += ["--profile", profile]
    command += ["config", "--format", "json"]
    completed = subprocess.run(
        command, cwd=str(ROOT), capture_output=True, text=True, timeout=120
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or completed.stdout or "").strip().splitlines()[-1:]
            or ["docker compose config failed"]
        )
    return json.loads(completed.stdout)


def image_problem(image):
    """Return a human reason the image reference is not pinned, or None."""
    if not image:
        return None
    if "@sha256:" in image:
        return None
    last = image.rsplit("/", 1)[-1]
    if ":" not in last:
        return "no tag (implicitly :latest, so a rebuild can silently change the stack)"
    tag = last.split(":", 1)[1]
    if tag == "latest":
        return "tag is 'latest', which is not a pin"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="compose dev/prod parity guard")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args(argv)

    report = Findings()

    missing = [p.name for p in (PROD_FILE, DEV_FILE) if not p.is_file()]
    if missing:
        report.skip(
            "compose file(s) not present yet: " + ", ".join(missing)
            + " - the docker/ directory is owned by the docker teammate and may not"
            " have been written at the time this guard ran. NOT a pass."
        )
        return report.emit(args.json)

    if shutil.which("docker") is None:
        report.skip(
            "docker CLI not on PATH, so the compose files cannot be resolved"
            " (interpolation, profiles, merged keys). NOT a pass."
        )
        return report.emit(args.json)

    documents = {}
    for label, path, env_file in (
        ("prod", PROD_FILE, ENV_PROD if ENV_PROD.is_file() else ENV_TEMPLATE),
        ("dev", DEV_FILE, ENV_DEV if ENV_DEV.is_file() else ENV_TEMPLATE),
    ):
        text = path.read_text(encoding="utf-8", errors="replace")
        profiles = profiles_in(text)
        try:
            documents[label] = render(path, env_file, profiles)
        except RuntimeError as exc:
            detail = exc.args[0]
            if isinstance(detail, list):
                detail = " | ".join(detail)
            report.fail(
                label + " stack does not resolve: " + str(detail)
                + " (file " + str(path.relative_to(ROOT)).replace("\\", "/") + ")"
            )
            documents[label] = None
        else:
            report.good(
                label + " stack resolves with profiles "
                + (",".join(profiles) if profiles else "(none)")
                + " and " + str(len(documents[label].get("services") or {})) + " services"
            )

    if any(document is None for document in documents.values()):
        return report.emit(args.json)

    # --- 1. image pinning --------------------------------------------------
    for label in ("prod", "dev"):
        for name, service in sorted((documents[label].get("services") or {}).items()):
            problem = image_problem(service.get("image"))
            if problem:
                report.fail(
                    label + " service '" + name + "' image '" + str(service.get("image"))
                    + "': " + problem
                )

    # --- 2. host port publication in prod ----------------------------------
    for name, service in sorted((documents["prod"].get("services") or {}).items()):
        ports = service.get("ports") or []
        if not ports:
            continue
        rendered = ", ".join(
            str(port.get("published", "?")) + "->" + str(port.get("target", "?"))
            + "/" + str(port.get("protocol", "tcp"))
            if isinstance(port, dict) else str(port)
            for port in ports
        )
        if name in GATEWAY_SERVICES:
            report.good("prod gateway '" + name + "' publishes " + rendered)
            continue
        report.fail(
            "prod service '" + name + "' publishes host port(s) " + rendered
            + " - only the gateway may publish in prod (R:\\GPTHub\\docker\\docker-compose.yaml:158-165"
            " publishes nothing but the reverse proxy). A published service bypasses the"
            " gateway's rate limit and security headers."
        )

    # --- 3. internal network ------------------------------------------------
    networks = documents["prod"].get("networks") or {}
    internal = [name for name, spec in networks.items()
                if isinstance(spec, dict) and spec.get("internal")]
    if not networks:
        report.fail(
            "prod stack declares no top-level networks block, so there is no"
            " no-egress network for the database and the internal services."
        )
    elif not internal:
        report.fail(
            "prod stack declares networks " + ", ".join(sorted(networks))
            + " but none has internal: true, so every service can reach the internet"
            " (convention: internal has no egress, default does)."
        )
    else:
        report.good("prod internal (no-egress) network(s): " + ", ".join(sorted(internal)))

    # --- 4. project names must differ --------------------------------------
    prod_name = str(documents["prod"].get("name") or "")
    dev_name = str(documents["dev"].get("name") or "")
    if prod_name and dev_name and prod_name == dev_name:
        report.fail(
            "both stacks resolve to the compose project name '" + prod_name
            + "' - dev and prod containers, networks and volumes would collide"
            " (contract: hiresnap-prod vs hiresnap-dev)."
        )
    elif prod_name and dev_name:
        report.good("project names differ: " + prod_name + " / " + dev_name)

    # --- 5. per-profile service parity -------------------------------------
    # The two stacks name their primary profile after their own mode: prod
    # services sit behind "prod", dev services behind "dev". Comparing profile
    # names literally would therefore report that the entire core stack exists
    # "only in prod", which is meaningless. The primary profile is renamed to
    # PRIMARY before comparing, so the core sets are matched against each
    # other, while the optional profiles (cache, storage, celery, mail, ml) keep
    # their real names and are matched by name - a service that exists in prod's
    # "cache" profile must also exist in dev's "cache" profile.
    PRIMARY = "<primary>"

    def primary_profile(doc):
        """Return the profile that owns the stack's ingress - its core.

        IDENTIFIED BY THE GATEWAY, not by "the profile with the most services".
        That count is a tie magnet: prod and dev each ship four core services
        (mysql, backend, frontend, gateway), and the moment the optional
        "celery" profile also has four, the old rule broke the tie
        alphabetically and declared "celery" the core stack. The resulting
        failure message ("backend exists in prod but not in dev") pointed at
        the wrong profile and would have reappeared every time a service was
        added to an optional profile.

        The gateway is the property that actually defines the core: it is the
        only service that publishes a port in prod, so the profile owning it is
        the one an operator brings up to serve traffic.
        """
        gateway_profiles = set()
        for name, service in (doc.get("services") or {}).items():
            if name in GATEWAY_SERVICES:
                gateway_profiles.update(service.get("profiles") or [])
        if gateway_profiles:
            counted = {}
            for service in (doc.get("services") or {}).values():
                for profile in service.get("profiles") or []:
                    if profile in gateway_profiles:
                        counted[profile] = counted.get(profile, 0) + 1
            return sorted(counted.items(), key=lambda item: (-item[1], item[0]))[0][0]
        # No gateway at all (a partial file, or a stack with an external proxy):
        # fall back to the largest profile, which is the best available answer.
        counted = {}
        for service in (doc.get("services") or {}).values():
            for profile in service.get("profiles") or []:
                counted[profile] = counted.get(profile, 0) + 1
        if not counted:
            return None
        return sorted(counted.items(), key=lambda item: (-item[1], item[0]))[0][0]

    prod_primary = primary_profile(documents["prod"])
    dev_primary = primary_profile(documents["dev"])

    def by_profile(doc, primary):
        mapping = {}
        for name, service in (doc.get("services") or {}).items():
            declared = service.get("profiles") or []
            keys = [PRIMARY if profile == primary else profile for profile in declared]
            for key in keys or ["<default>"]:
                mapping.setdefault(key, set()).add(name)
        return mapping

    prod_map = by_profile(documents["prod"], prod_primary)
    dev_map = by_profile(documents["dev"], dev_primary)
    if prod_primary and dev_primary:
        report.good(
            "primary (core) profile: prod '" + prod_primary + "' vs dev '" + dev_primary
            + "' - compared as one stack"
        )
    for profile in sorted(set(prod_map) | set(dev_map)):
        prod_services = prod_map.get(profile, set())
        dev_services = dev_map.get(profile, set())
        only_prod = sorted(prod_services - dev_services)
        only_dev = sorted(dev_services - prod_services)
        label = "core stack" if profile == PRIMARY else "profile '" + profile + "'"
        if only_prod:
            report.fail(
                label + ": service(s) " + ", ".join(only_prod)
                + " exist in prod but not in dev - that part of the stack cannot be"
                " reproduced locally."
            )
        if only_dev:
            report.warn(
                label + ": service(s) " + ", ".join(only_dev)
                + " exist only in dev (allowed, but they must not be load-bearing)."
            )
        if not only_prod and not only_dev:
            report.good(
                label + ": " + str(len(prod_services))
                + " service(s) identical in both stacks"
            )

    # --- 6. "ml" is the SIDECAR, built from the ml submodule --------------
    # HISTORY, because this is the defect that motivated the rule: both compose
    # files once declared an "ml" service as a second backend replica - same
    # image, same DB_* environment, depends_on mysql - and no ML__SERVICE_URL
    # anywhere. Every other assertion in this file passed on those files: the
    # image was pinned by the Dockerfile build, nothing published a port in
    # prod, the profile sets matched, and the healthcheck existed. A stack that
    # starts a service called "ml" which cannot answer a single inference route
    # is exactly the class of error a parity guard is supposed to catch
    # (docs/KNOWN_GAPS.md keeps the post-mortem).
    #
    # THREE PROPERTIES, checked against the RESOLVED document rather than the
    # raw text, because "context: ../ml" is only meaningful once compose has
    # resolved it against the compose file's own directory:
    #
    #   a. the service named ml exists in both stacks, in the CORE profile.
    #      ml is not an optional extra any more: the backend calls it on the
    #      upload path, so a stack that starts without it serves 503s. It used
    #      to sit in a profile called "ml", which meant 'docker compose up'
    #      could bring up the whole product and none of its inference.
    #   b. that service's BUILD CONTEXT is the ml submodule, not the repository
    #      root. This is the difference between "an image that happens to be
    #      built from ml/Dockerfile" and "an image that cannot reach backend/".
    #      A root context ships the entire superproject to the daemon, lets the
    #      Dockerfile COPY backend/ (which is how the sidecar kept importing
    #      src.modelling), and ignores ml/.dockerignore. It also breaks the
    #      submodule boundary: ml/ must build from ml/ so a backend-only change
    #      cannot alter the sidecar image.
    #   c. NO OTHER service builds from ml/. A second service pointed at the
    #      sidecar Dockerfile is the old shape wearing a new path - it is how
    #      "celery-worker-ml" would quietly become a second inference server
    #      with a different environment.
    #
    # The rule stays static (paths and profiles) because checking the image's
    # routes would need a built image, and this guard must stay sub-second and
    # offline.
    ML_SERVICE = "ml"
    ML_ROOT = (ROOT / "ml").resolve()

    def build_paths(service):
        """Return (context_dir, dockerfile_path) from a RESOLVED build block."""
        build = service.get("build")
        if not isinstance(build, dict):
            return None, None
        context = build.get("context")
        if not context:
            return None, None
        context_dir = Path(os.path.normpath(str(context))).resolve()
        dockerfile = Path(os.path.normpath(str(build.get("dockerfile") or "Dockerfile")))
        return context_dir, (context_dir / dockerfile).resolve()

    def inside(path, root):
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    for label in ("prod", "dev"):
        primary = prod_primary if label == "prod" else dev_primary
        services_doc = documents[label].get("services") or {}
        service = services_doc.get(ML_SERVICE)
        if service is None:
            report.fail(
                label + " stack has no service named '" + ML_SERVICE + "'. The API"
                " calls the sidecar on the upload path, so a stack without it"
                " starts, passes its own healthchecks and answers 503 to every"
                " inference request (docs/KNOWN_GAPS.md)"
            )
            continue
        declared = service.get("profiles") or []
        if primary and primary not in declared:
            report.fail(
                label + " service '" + ML_SERVICE + "' is in profile(s) "
                + (", ".join(declared) if declared else "(none)")
                + " but the stack's core profile is '" + primary + "'. ml is a CORE"
                " service now: 'docker compose --profile " + str(primary) + " up'"
                " must start it, or the product comes up without inference"
                " (docs/KNOWN_GAPS.md)"
            )
        else:
            report.good(
                label + " service '" + ML_SERVICE + "' is in the core profile '"
                + str(primary) + "'"
            )
        context_dir, dockerfile_path = build_paths(service)
        if context_dir is None:
            report.fail(
                label + " service '" + ML_SERVICE + "' has no build context (it"
                " references an image by tag), so it cannot be the sidecar built"
                " from the ml submodule (docs/KNOWN_GAPS.md)"
            )
            continue
        if context_dir == ML_ROOT or inside(context_dir, ML_ROOT):
            report.good(
                label + " service '" + ML_SERVICE + "' builds from the ml submodule"
                " (context " + str(context_dir) + ", dockerfile "
                + str(dockerfile_path) + ")"
            )
        else:
            report.fail(
                label + " service '" + ML_SERVICE + "' builds with context "
                + str(context_dir) + " instead of the ml submodule ("
                + str(ML_ROOT) + "). A root context ships the whole superproject to"
                " the build daemon, lets the sidecar Dockerfile COPY backend/ (which"
                " is how it kept importing src.modelling), and ignores"
                " ml/.dockerignore. Use context: ../ml, dockerfile: docker/Dockerfile"
                " (docs/KNOWN_GAPS.md)"
            )
        for name, other in sorted(services_doc.items()):
            if name == ML_SERVICE:
                continue
            other_context, other_dockerfile = build_paths(other)
            if other_context is None:
                continue
            if (other_context == ML_ROOT or inside(other_context, ML_ROOT)
                    or (other_dockerfile is not None and inside(other_dockerfile, ML_ROOT))):
                report.fail(
                    label + " service '" + name + "' also builds from the ml"
                    " submodule (context " + str(other_context) + ", dockerfile "
                    + str(other_dockerfile) + "). Exactly one service may be the"
                    " sidecar: a second one is the old 'second backend replica'"
                    " defect with a new path, and it will drift from the first"
                    " (docs/KNOWN_GAPS.md)"
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
